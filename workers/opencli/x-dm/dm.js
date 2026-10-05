// x-dm: list / read / send X (Twitter) direct messages from the owner's
// already-logged-in Chrome, via the XChat web UI (DMs are end-to-end
// encrypted; the GraphQL payloads are opaque blobs, so we read the DOM the
// page already decrypted). Strategy UI_SELECTOR, contract visible-ui.
//
// Safety: never types or stores any passcode/PIN. If the chat area asks for
// one, commands fail with code `dm_locked` and the owner unlocks it in Chrome.
import { cli, Strategy } from '@jackwener/opencli/registry';
import {
  ArgumentError, AuthRequiredError, CliError, CommandExecutionError, EmptyResultError, EXIT_CODES,
} from '@jackwener/opencli/errors';
import {
  isValidThreadId, mergeSnapshot, normalizeMessage, normalizeThread, parseLimit, unwrap,
} from './normalize.js';

const CHAT_URL = 'https://x.com/i/chat';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const COMMON = {
  site: 'x-dm',
  domain: 'x.com',
  strategy: Strategy.UI,
  browser: true,
  navigateBefore: false,
  siteSession: 'ephemeral', // fresh tab per command, closed when it ends
  defaultWindowMode: 'background',
};

// ── in-page functions (serialized by page.evaluate; must be self-contained) ──

function pageProbe() {
  const host = document.querySelector('[data-testid="xchatEmbedRoute"]');
  const root = host && host.shadowRoot;
  const path = location.pathname;
  const ready = !!(root && root.querySelector('[data-testid="dm-inbox-panel"],[data-testid="dm-conversation-panel"]'));
  let lockUi = false;
  if (root && !ready) {
    lockUi = !!root.querySelector('input[type="password"],input[autocomplete="one-time-code"],input[inputmode="numeric"]')
      || /passcode|c[oó]digo de acceso|desbloque|unlock|\bPIN\b/i.test(root.textContent || '');
  }
  return {
    path,
    ready,
    locked: /^\/i\/chat\/pin/.test(path) || lockUi,
    loggedOut: /^\/(i\/flow\/login|login)/.test(path) || !!document.querySelector('[data-testid="loginButton"]'),
  };
}

async function pageThreads(limit) {
  const root = document.querySelector('[data-testid="xchatEmbedRoute"]')?.shadowRoot;
  if (!root) return { error: 'no_root' };
  const sel = '[data-testid^="dm-conversation-item-"]';
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 12 && !root.querySelector(sel); i++) await wait(500);
  const seen = new Map();
  const grab = () => {
    for (const it of root.querySelectorAll(sel)) {
      const key = it.getAttribute('data-testid');
      if (seen.has(key)) continue;
      const leaves = [];
      for (const el of it.querySelectorAll('*')) {
        if (el.closest('[aria-hidden="true"],[role="img"],[hidden]')) continue;
        const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
        if (own) leaves.push({ text: own, bold: Number(getComputedStyle(el).fontWeight) >= 600 });
      }
      const label = `${it.getAttribute('aria-description') || ''} ${it.getAttribute('aria-label') || ''}`;
      seen.set(key, {
        idx: Number(it.getAttribute('data-index') || 0),
        href: it.querySelector('a[href*="/i/chat/"]')?.getAttribute('href') || '',
        desc: it.getAttribute('aria-description') || '',
        leaves,
        unreadHint: /no le[ií]d|unread/i.test(label) || !!it.querySelector('[data-testid*="unread" i],[aria-label*="unread" i],[aria-label*="no leíd" i]'),
      });
    }
  };
  grab();
  const scroller = root.querySelector('[data-testid="dm-conversation-scroller"]');
  for (let s = 0; s < 8 && seen.size < limit && scroller; s++) {
    const before = seen.size;
    scroller.scrollTop = scroller.scrollHeight;
    await wait(1200);
    grab();
    if (seen.size === before) break;
  }
  return { rows: [...seen.values()].sort((a, b) => a.idx - b.idx).slice(0, limit) };
}

function pageRows() {
  const root = document.querySelector('[data-testid="xchatEmbedRoute"]')?.shadowRoot;
  if (!root) return { error: 'no_root' };
  const nowSec = Date.now() / 1000;
  const findSecs = (v, d, out) => {
    if (v == null || d > 3 || out.length > 20) return;
    if (typeof v === 'bigint') {
      const n = Number(v);
      if (n > 1.2e9 && n < nowSec + 86400) out.push(n);
      return;
    }
    if (typeof v !== 'object' || v instanceof Node || v.$$typeof) return;
    for (const k of Object.keys(v).slice(0, 40)) { try { findSecs(v[k], d + 1, out); } catch { /* getter */ } }
  };
  const tsFor = (row, label) => {
    // Pick the epoch-seconds value whose local H:MM matches the visible time label.
    const m = /(\d{1,2}):(\d{2})/.exec(label || '');
    if (!m) return null;
    const fk = Object.keys(row).find((k) => k.startsWith('__reactFiber'));
    let f = fk && row[fk];
    for (let i = 0; i < 8 && f; i++, f = f.return) {
      const p = f.memoizedProps;
      const item = p && (p.messageItem || p.chatItem);
      if (!item) continue;
      const cands = [];
      findSecs(item, 0, cands);
      const hit = cands.find((s) => {
        const dt = new Date(s * 1000);
        return dt.getMinutes() === Number(m[2]) && dt.getHours() % 12 === Number(m[1]) % 12;
      });
      return hit ?? null;
    }
    return null;
  };
  const rows = [...root.querySelectorAll('[data-dm-message-row]')].map((r) => {
    const q = (s) => r.querySelector(s);
    const txt = q('[data-testid^="message-text-"]');
    const timeLabel = q('[id$="-time"]')?.textContent || '';
    let ts = null;
    try { ts = tsFor(r, timeLabel); } catch { ts = null; }
    return {
      id: r.getAttribute('data-dm-message-row') || '',
      sender: q('[id$="-sender"]')?.textContent || '',
      timeLabel,
      kind: q('[id$="-kind"]')?.textContent || '',
      side: /justify-end/.test(r.className) ? 'end' : /justify-start/.test(r.className) ? 'start' : (q('[data-side]')?.getAttribute('data-side') || ''),
      text: txt ? ((txt.querySelector('[dir="auto"]') || txt).innerText || '') : '',
      hasVideo: !!q('video'),
      hasImg: !!q('[role="article"] img,[role="group"] img'),
      ts,
    };
  });
  return {
    panel: !!root.querySelector('[data-testid="dm-conversation-panel"]'),
    spinner: !!root.querySelector('[data-testid="dm-message-list-spinner-slot"] *'),
    rows,
  };
}

function pageScrollUp() {
  const root = document.querySelector('[data-testid="xchatEmbedRoute"]')?.shadowRoot;
  const sc = root?.querySelector('[data-testid="dm-message-scroller"]');
  if (sc) sc.scrollTop = 0;
  return { ok: !!sc };
}

function pageComposer() {
  const root = document.querySelector('[data-testid="xchatEmbedRoute"]')?.shadowRoot;
  const ta = root?.querySelector('[data-testid="dm-composer-textarea"]');
  const nameEl = root?.querySelector('[data-testid="dm-conversation-username"]');
  return {
    panel: !!root?.querySelector('[data-testid="dm-conversation-panel"]'),
    name: (nameEl?.innerText || '').split('\n')[0].trim(),
    composer: !!ta && !ta.disabled && !ta.readOnly,
    draftLen: ta ? (ta.value || ta.textContent || '').length : -1,
  };
}

function pageFocusComposer() {
  const ta = document.querySelector('[data-testid="xchatEmbedRoute"]')?.shadowRoot?.querySelector('[data-testid="dm-composer-textarea"]');
  if (!ta) return { ok: false };
  ta.focus();
  return { ok: ta.getRootNode().activeElement === ta };
}

function pageComposerValue(setTo) {
  const ta = document.querySelector('[data-testid="xchatEmbedRoute"]')?.shadowRoot?.querySelector('[data-testid="dm-composer-textarea"]');
  if (!ta) return { value: null };
  if (typeof setTo === 'string') {
    const proto = ta instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(ta, setTo);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  return { value: ta.value };
}

function pageClickSend() {
  const root = document.querySelector('[data-testid="xchatEmbedRoute"]')?.shadowRoot;
  const form = root?.querySelector('[data-testid="dm-composer-form"]');
  if (!form) return { clicked: false, reason: 'no_form' };
  const btns = [...form.querySelectorAll('button')]
    .filter((b) => !/voice|attachment|emoji|gif/i.test(b.getAttribute('data-testid') || ''));
  const btn = btns.find((b) => /send/i.test(b.getAttribute('data-testid') || ''))
    || btns.find((b) => b.type === 'submit')
    || btns.find((b) => /enviar|send/i.test(b.getAttribute('aria-label') || ''));
  if (!btn) return { clicked: false, reason: 'no_send_button' };
  if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return { clicked: false, reason: 'send_disabled' };
  btn.click();
  return { clicked: true };
}

// ── node-side helpers ──

const ev = async (page, fn, ...args) => unwrap(await page.evaluate(fn, ...args));

function lockedError() {
  return new CliError('dm_locked', 'Desbloquea los mensajes de X en Chrome',
    'X pide el código de acceso de los mensajes cifrados (XChat). Ábrelo en Chrome y desbloquéalo tú mismo; este comando nunca introduce códigos.',
    EXIT_CODES.NOPERM);
}

async function openChat(page, url, timeoutS = 20) {
  await page.goto(url);
  let probe = null;
  for (let i = 0; i < timeoutS; i++) {
    probe = await ev(page, pageProbe);
    if (probe?.loggedOut) throw new AuthRequiredError('x.com', 'Not logged in to x.com in Chrome');
    if (probe?.ready && !/^\/i\/chat\/pin/.test(probe.path)) return probe;
    await sleep(1000);
  }
  if (probe?.locked) throw lockedError();
  throw new CommandExecutionError(`X chat UI did not load within ${timeoutS}s`, 'X may have changed the XChat layout, or the account is rate-limited.');
}

async function openThread(page, threadId) {
  if (!isValidThreadId(threadId)) throw new ArgumentError('thread_id must look like the id printed by dm-threads (letters, digits, - or _)');
  const probe = await openChat(page, `${CHAT_URL}/${threadId}`);
  if (probe.path.replace(/\/$/, '') !== `/i/chat/${threadId}`) {
    throw new EmptyResultError('x-dm', `Conversation not found (redirected away from thread)`);
  }
  for (let i = 0; i < 10; i++) {
    const c = await ev(page, pageComposer);
    if (c?.panel) return c;
    await sleep(800);
  }
  throw new EmptyResultError('x-dm', 'Conversation panel did not appear for that thread_id');
}

async function readRows(page, limit) {
  let all = [];
  for (let i = 0; i < 10; i++) { // wait for first rows to render
    const snap = await ev(page, pageRows);
    if (snap?.rows?.length) { all = snap.rows; break; }
    await sleep(700);
  }
  for (let i = 0; i < 8 && all.length && all.length < limit; i++) {
    await ev(page, pageScrollUp);
    await sleep(1500);
    const snap = await ev(page, pageRows);
    const next = mergeSnapshot(all, snap?.rows || []);
    if (next.length === all.length) break;
    all = next;
  }
  return all;
}

// ── commands ──

cli({
  ...COMMON,
  name: 'dm-threads',
  access: 'read',
  description: 'List recent X DM conversations (XChat inbox), newest first',
  args: [{ name: 'limit', type: 'int', default: 20, help: 'Conversations to return (1-50, default 20)' }],
  columns: ['thread_id', 'name', 'last_text', 'last_time', 'unread'],
  func: async (page, kwargs) => {
    let limit;
    try { limit = parseLimit(kwargs.limit, 20, 50, '--limit'); } catch (e) { throw new ArgumentError(e.message); }
    await openChat(page, CHAT_URL);
    const res = await ev(page, pageThreads, limit);
    if (res?.error) throw new CommandExecutionError(`inbox extraction failed: ${res.error}`);
    const now = Date.now();
    const rows = (res?.rows || []).map((r) => normalizeThread(r, now)).filter((r) => r.thread_id);
    if (!rows.length) throw new EmptyResultError('x-dm dm-threads', 'No conversations visible in the XChat inbox');
    return rows;
  },
});

cli({
  ...COMMON,
  name: 'dm-read',
  access: 'read',
  description: 'Read the latest messages of one X DM conversation (oldest first, newest last)',
  args: [
    { name: 'thread_id', type: 'string', required: true, positional: true, help: 'thread_id from dm-threads' },
    { name: 'limit', type: 'int', default: 30, help: 'Messages to return (1-100, default 30)' },
  ],
  columns: ['thread_id', 'message_id', 'author', 'is_me', 'text', 'time', 'media'],
  func: async (page, kwargs) => {
    let limit;
    try { limit = parseLimit(kwargs.limit, 30, 100, '--limit'); } catch (e) { throw new ArgumentError(e.message); }
    const threadId = String(kwargs.thread_id || '');
    await openThread(page, threadId);
    const rows = (await readRows(page, limit)).slice(-limit).map((r) => normalizeMessage(r, threadId));
    if (!rows.length) throw new EmptyResultError('x-dm dm-read', 'No messages rendered for that conversation');
    return rows;
  },
});

cli({
  ...COMMON,
  name: 'dm-send',
  access: 'write',
  description: 'Send ONE text message to ONE X DM conversation (requires --yes; --dry-run checks without typing)',
  args: [
    { name: 'thread_id', type: 'string', required: true, positional: true, help: 'thread_id from dm-threads' },
    { name: 'text', type: 'string', required: true, positional: true, help: 'Exact message text' },
    { name: 'yes', type: 'boolean', default: false, help: 'Confirm the real send' },
    { name: 'dry-run', type: 'boolean', default: false, help: 'Open the thread and check the composer; type nothing' },
  ],
  columns: ['sent', 'would_send', 'thread_id', 'name', 'verified'],
  func: async (page, kwargs) => {
    const threadId = String(kwargs.thread_id || '');
    const text = String(kwargs.text ?? '');
    const dryRun = kwargs['dry-run'] === true;
    if (!text.trim()) throw new ArgumentError('text must not be empty');
    if (text.length > 10000) throw new ArgumentError('text longer than 10000 characters');
    if (!dryRun && kwargs.yes !== true) {
      throw new ArgumentError('Refusing to send without --yes', 'Re-run with --yes to send exactly one message, or --dry-run to check only.');
    }
    const c = await openThread(page, threadId);
    if (!c.composer) throw new CommandExecutionError('Message composer not reachable in this conversation (read-only or blocked?)');
    if (dryRun) return [{ would_send: true, thread_id: threadId, name: c.name }];

    if (c.draftLen !== 0) throw new CommandExecutionError('Composer already contains a draft; refusing to merge it into the message. Clear it in Chrome first.');
    const before = new Set(((await ev(page, pageRows))?.rows || []).map((r) => r.id));
    const focus = await ev(page, pageFocusComposer);
    if (!focus?.ok) throw new CommandExecutionError('Could not focus the message composer');
    try { await page.insertText(text); } catch { /* fallback below */ }
    let val = (await ev(page, pageComposerValue))?.value;
    if (val !== text) val = (await ev(page, pageComposerValue, text))?.value; // React-controlled fallback
    if (val !== text) {
      await ev(page, pageComposerValue, '');
      throw new CommandExecutionError('Composer text did not match exactly; cleared it and sent nothing');
    }
    await sleep(400);
    const click = await ev(page, pageClickSend); // exactly one click, never retried
    if (!click?.clicked) {
      await ev(page, pageComposerValue, '');
      throw new CommandExecutionError(`Send button not usable (${click?.reason}); cleared composer, nothing sent`);
    }
    let verified = false;
    for (let i = 0; i < 10 && !verified; i++) {
      await sleep(1000);
      const rows = (await ev(page, pageRows))?.rows || [];
      const mine = rows.filter((r) => !before.has(r.id) && (r.side === 'end' || r.side === 'right'));
      const norm = (t) => String(t).replace(/\s+/g, ' ').trim();
      verified = mine.some((r) => norm(r.text) === norm(text));
    }
    return [{ sent: true, thread_id: threadId, verified }];
  },
});
