// messenger: READ-ONLY list / read of the owner's Facebook Messenger
// conversations from his already-logged-in Chrome (facebook.com/messages).
// Many chats are end-to-end encrypted; their content only exists in the DOM
// the page already decrypted, so this reads the DOM. Strategy UI_SELECTOR,
// contract visible-ui.
//
// Safety: there is NO send command. Nothing here types, focuses or clicks the
// composer. Never types or stores any PIN: if Messenger asks for the E2EE PIN
// to restore chats, commands fail with code `dm_locked`.
import { cli, Strategy } from '@jackwener/opencli/registry';
import {
  ArgumentError, AuthRequiredError, CliError, CommandExecutionError, EmptyResultError, EXIT_CODES,
} from '@jackwener/opencli/errors';
import {
  isValidThreadId, mergeSnapshot, normalizeMessage, normalizeThread, parseLimit, threadPath, unwrap,
} from './normalize.js';

const ORIGIN = 'https://www.facebook.com';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const COMMON = {
  site: 'messenger',
  domain: 'www.facebook.com',
  strategy: Strategy.UI,
  browser: true,
  navigateBefore: false,
  siteSession: 'ephemeral', // fresh tab per command, closed when it ends
  defaultWindowMode: 'background',
};

// ── in-page functions (serialized by page.evaluate; must be self-contained) ──

function pageProbe() {
  const path = location.pathname;
  const lockRe = /\bPIN\b|restaura\w* (tu |el |los )?(historial|mensajes|chats)|restore (your )?(chat|message)s?( history)?|c[oó]digo de recuperaci[oó]n|recovery code|desbloquea/i;
  const scopes = [...document.querySelectorAll('[role=dialog],[role=main]')];
  const pinInput = scopes.some((s) => s.querySelector('input[type=password],input[inputmode=numeric],input[autocomplete=one-time-code]'));
  const lockText = [...document.querySelectorAll('[role=dialog]')].some((d) => lockRe.test(d.innerText || ''))
    || (!document.querySelector('[role=log]') && lockRe.test(document.querySelector('[role=main]')?.innerText || ''));
  return {
    path,
    inbox: !!document.querySelector('[role=grid] a[href*="/messages/"]'),
    log: !!document.querySelector('[role=log]'),
    msgs: document.querySelectorAll('[role=log] [aria-roledescription][data-message-id]').length,
    locked: pinInput || lockText,
    loggedOut: /^\/(login|r\.php)/.test(path) || !!document.querySelector('form#login_form,input[name=pass]'),
    checkpoint: /^\/checkpoint/.test(path),
  };
}

async function pageThreads(limit) {
  const grid = document.querySelector('[role=grid]');
  if (!grid) return { error: 'no_grid' };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const seen = new Map();
  const grab = () => {
    for (const row of grid.querySelectorAll('[role=row]')) {
      const a = row.querySelector('a[href*="/messages/"]');
      const href = a?.getAttribute('href') || '';
      if (!href || seen.has(href)) continue;
      const leaves = [];
      for (const el of row.querySelectorAll('*')) {
        if (el.closest('[aria-hidden="true"],[role="img"],[hidden]')) continue;
        const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
        if (own) leaves.push({ text: own, bold: Number(getComputedStyle(el).fontWeight) >= 600 });
      }
      const labels = [...row.querySelectorAll('[aria-label]')].map((e) => e.getAttribute('aria-label') || '');
      seen.set(href, {
        href,
        leaves,
        timeAria: labels.find((l) => /^hace\s|\sago$/i.test(l)) || '',
        unreadHint: labels.some((l) => /no le[ií]d|unread|marcar como le[ií]d|mark as read/i.test(l)),
      });
    }
  };
  grab();
  const sc = [...grid.querySelectorAll('*')].find((e) => e.scrollHeight > e.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(e).overflowY));
  let idle = 0;
  for (let s = 0; s < 8 && seen.size < limit && sc && idle < 2; s++) {
    const before = seen.size;
    sc.scrollTop = sc.scrollHeight;
    sc.dispatchEvent(new Event('scroll'));
    await wait(2000);
    grab();
    idle = seen.size === before ? idle + 1 : 0;
  }
  if (sc) sc.scrollTop = 0;
  return { rows: [...seen.values()].slice(0, limit) };
}

function pageMessages() {
  const log = document.querySelector('[role=log]');
  if (!log) return { error: 'no_log' };
  const lr = log.getBoundingClientRect();
  const rows = [...log.querySelectorAll('[aria-roledescription][data-message-id]')].map((m) => {
    const t = m.querySelector('[dir=auto]');
    const r = (t || m).getBoundingClientRect();
    const gapL = r.left - lr.left;
    const gapR = lr.right - r.right;
    const imgs = [...m.querySelectorAll('img')].filter((i) => !i.closest('[aria-hidden="true"]') && i.getBoundingClientRect().width > 48);
    const media = m.querySelector('video') ? 'video'
      : m.querySelector('audio,[aria-label*="audio" i]') ? 'audio'
        : imgs.length ? 'image' : null;
    return {
      id: m.getAttribute('data-message-id') || '',
      label: m.getAttribute('aria-label') || '',
      side: gapL > gapR + 40 ? 'R' : gapR > gapL + 40 ? 'L' : 'C',
      media,
      // Fallback when the aria-label carries no text (seen on some Marketplace rows).
      domText: (() => {
        const out = [];
        const w = document.createTreeWalker(m, NodeFilter.SHOW_TEXT);
        for (let n = w.nextNode(); n; n = w.nextNode()) {
          if (n.parentElement.closest('[aria-hidden="true"],h1,h2,h3,h4,h5,h6,[role=toolbar]')) continue;
          const t = n.textContent.trim();
          if (t) out.push(t);
        }
        return out.join(' ');
      })(),
    };
  });
  return { rows };
}

async function pageScrollUp() {
  const log = document.querySelector('[role=log]');
  if (!log) return { ok: false };
  const isSc = (e) => e && e.scrollHeight > e.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(e).overflowY);
  let sc = log;
  while (sc && !isSc(sc)) sc = sc.parentElement;
  if (!sc) sc = [...log.querySelectorAll('*')].find(isSc);
  if (!sc) return { ok: false };
  sc.scrollTop = 0;
  sc.dispatchEvent(new Event('scroll'));
  return { ok: true };
}

// ── node-side helpers ──

const ev = async (page, fn, ...args) => unwrap(await page.evaluate(fn, ...args));

function lockedError() {
  return new CliError('dm_locked', 'Desbloquea los mensajes de Messenger en Chrome',
    'Messenger pide el PIN / código para restaurar los chats cifrados. Ábrelo en Chrome y desbloquéalo tú mismo; este comando nunca introduce códigos.',
    EXIT_CODES.NOPERM);
}

async function open(page, url, ready, timeoutS = 25) {
  await page.goto(url);
  let probe = null;
  for (let i = 0; i < timeoutS; i++) {
    await sleep(1000);
    probe = await ev(page, pageProbe);
    if (probe?.loggedOut) throw new AuthRequiredError('facebook.com', 'Not logged in to facebook.com in Chrome');
    if (probe?.checkpoint) throw new AuthRequiredError('facebook.com', 'Facebook shows a security checkpoint; resolve it in Chrome');
    if (ready(probe)) return probe;
    if (probe?.locked) throw lockedError();
  }
  throw new CommandExecutionError(`Messenger UI did not load within ${timeoutS}s`, 'Facebook may have changed the layout, or the account is rate-limited.');
}

// ── commands (read-only; there is deliberately no send command) ──

cli({
  ...COMMON,
  name: 'dm-threads',
  access: 'read',
  description: 'List recent Facebook Messenger conversations, newest first (read-only)',
  args: [{ name: 'limit', type: 'int', default: 20, help: 'Conversations to return (1-50, default 20)' }],
  columns: ['thread_id', 'name', 'last_text', 'last_time', 'unread'],
  func: async (page, kwargs) => {
    let limit;
    try { limit = parseLimit(kwargs.limit, 20, 50, '--limit'); } catch (e) { throw new ArgumentError(e.message); }
    // ponytail: /messages/ auto-opens the newest thread next to the inbox; no URL shows the inbox alone.
    await open(page, `${ORIGIN}/messages/`, (p) => p?.inbox);
    const res = await ev(page, pageThreads, limit);
    if (res?.error) throw new CommandExecutionError(`inbox extraction failed: ${res.error}`);
    const now = Date.now();
    const rows = (res?.rows || []).map((r) => normalizeThread(r, now)).filter((r) => r.thread_id);
    if (!rows.length) throw new EmptyResultError('messenger dm-threads', 'No conversations visible in the Messenger inbox');
    return rows;
  },
});

cli({
  ...COMMON,
  name: 'dm-read',
  access: 'read',
  description: 'Read the latest messages of one Messenger conversation, oldest first (read-only)',
  args: [
    { name: 'thread_id', type: 'string', required: true, positional: true, help: 'thread_id from dm-threads ("123…" or "e2ee:123…")' },
    { name: 'limit', type: 'int', default: 30, help: 'Messages to return (1-100, default 30)' },
  ],
  columns: ['thread_id', 'message_id', 'author', 'is_me', 'text', 'time', 'media'],
  func: async (page, kwargs) => {
    let limit;
    try { limit = parseLimit(kwargs.limit, 30, 100, '--limit'); } catch (e) { throw new ArgumentError(e.message); }
    const threadId = String(kwargs.thread_id || '');
    if (!isValidThreadId(threadId)) throw new ArgumentError('thread_id must look like the id printed by dm-threads ("123…" or "e2ee:123…")');
    const probe = await open(page, ORIGIN + threadPath(threadId), (p) => p?.log && p.msgs > 0);
    const rawId = threadId.replace(/^e2ee:/, '');
    if (!probe.path.includes(`/t/${rawId}`)) throw new EmptyResultError('messenger dm-read', 'Conversation not found (redirected away from thread)');

    // The log renders progressively: wait until the row count stops growing.
    let all = [];
    for (let i = 0, stable = 0; i < 10 && stable < 2; i++) {
      const rows = (await ev(page, pageMessages))?.rows || [];
      stable = rows.length === all.length ? stable + 1 : 0;
      all = rows;
      await sleep(800);
    }
    for (let i = 0; i < 8 && all.length < limit; i++) {
      if (!(await ev(page, pageScrollUp))?.ok) break;
      await sleep(1800);
      const next = mergeSnapshot(all, (await ev(page, pageMessages))?.rows || []);
      if (next.length === all.length) break;
      all = next;
    }
    const now = Date.now();
    const rows = all.slice(-limit).map((r) => normalizeMessage(r, threadId, now));
    if (!rows.length) throw new EmptyResultError('messenger dm-read', 'No messages rendered for that conversation');
    return rows;
  },
});
