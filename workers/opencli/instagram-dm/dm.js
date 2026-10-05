// instagram-dm: READ-ONLY listing and reading of the owner's Instagram Direct
// conversations from his already-logged-in Chrome. There is deliberately no
// send command: this plugin never types, clicks or POSTs anything.
//
// Strategy PAGE_FETCH (opencli Strategy.COOKIE), contract internal-unstable:
// same-origin GET to the web app's own /api/v1/direct_v2/* endpoints from a
// background tab parked on instagram.com/robots.txt. Why not the DOM: opening a
// conversation in the Direct UI sends a "seen" receipt to the other side; the
// GET endpoints do not (seen is a separate POST we never call), and they also
// avoid loading the full app (lower traffic).
//
// Safety: never types or stores any PIN/code. Checkpoint / challenge / 2FA /
// verification answers fail with code `dm_locked`; the owner resolves it in Chrome.
import { cli, Strategy } from '@jackwener/opencli/registry';
import {
  ArgumentError, AuthRequiredError, CliError, CommandExecutionError, EmptyResultError, EXIT_CODES,
} from '@jackwener/opencli/errors';
import {
  classifyResponse, isValidThreadId, normalizeMessages, normalizeThread, parseLimit, unwrap,
} from './normalize.js';

// ponytail: tiny static same-origin page; the session cookies ride along with fetch.
const PARK_URL = 'https://www.instagram.com/robots.txt';
const PAGE_PAUSE_MS = 1500; // between paginated requests
const MAX_PAGES = 5;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const COMMON = {
  site: 'instagram-dm',
  domain: 'www.instagram.com',
  strategy: Strategy.COOKIE,
  browser: true,
  navigateBefore: false,
  siteSession: 'ephemeral', // fresh tab per command, closed when it ends
  defaultWindowMode: 'background',
};

// ── in-page function (serialized by page.evaluate; must be self-contained) ──

async function pageGet(path) {
  try {
    const r = await fetch(path, {
      credentials: 'include',
      headers: { 'X-IG-App-ID': '936619743392459', Accept: 'application/json' },
    });
    let body = null;
    if ((r.headers.get('content-type') || '').includes('json')) { try { body = await r.json(); } catch { body = null; } }
    return { status: r.status, finalPath: new URL(r.url).pathname, body };
  } catch (e) {
    return { status: 0, finalPath: '', body: null, error: String(e && e.message || e) };
  }
}

// ── node-side helpers ──

const ev = async (page, fn, ...args) => unwrap(await page.evaluate(fn, ...args));

function raiseFor(problem) {
  switch (problem.kind) {
    case 'locked':
      throw new CliError('dm_locked', 'Desbloquea los mensajes de Instagram en Chrome',
        'Instagram pide una verificación (checkpoint/PIN/código). Ábrelo en Chrome y resuélvelo tú mismo; este comando nunca introduce códigos.',
        EXIT_CODES.NOPERM);
    case 'auth':
      throw new AuthRequiredError('www.instagram.com', `Not logged in to Instagram in Chrome (${problem.detail})`);
    case 'rate':
      throw new CliError('rate_limited', 'Instagram is rate-limiting this session (HTTP 429 or "wait a few minutes")',
        'Wait (minutes to hours) before trying again; do not retry in a loop.', EXIT_CODES.TEMPFAIL);
    case 'notfound':
      throw new EmptyResultError('instagram-dm', 'Conversation not found');
    default:
      throw new CommandExecutionError(`Instagram Direct request failed: ${problem.detail}`,
        'Instagram may have changed its internal direct_v2 endpoints.');
  }
}

async function get(page, path) {
  const res = await ev(page, pageGet, path);
  if (res?.error) throw new CommandExecutionError(`fetch failed in page: ${res.error}`);
  const problem = classifyResponse(res);
  if (problem) raiseFor(problem);
  return res.body;
}

// ── commands ──

cli({
  ...COMMON,
  name: 'dm-threads',
  access: 'read',
  description: 'List recent Instagram Direct conversations (primary inbox), newest first. Read-only.',
  args: [{ name: 'limit', type: 'int', default: 20, help: 'Conversations to return (1-50, default 20)' }],
  columns: ['thread_id', 'name', 'last_text', 'last_time', 'unread'],
  func: async (page, kwargs) => {
    let limit;
    try { limit = parseLimit(kwargs.limit, 20, 50, '--limit'); } catch (e) { throw new ArgumentError(e.message); }
    await page.goto(PARK_URL);
    const threads = [];
    let cursor = '';
    for (let p = 0; p < MAX_PAGES && threads.length < limit; p++) {
      if (p) await sleep(PAGE_PAUSE_MS);
      const n = Math.min(20, limit - threads.length);
      const q = `folder=&limit=${n}&thread_message_limit=1${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;
      const inbox = (await get(page, `/api/v1/direct_v2/inbox/?${q}`))?.inbox;
      const batch = inbox?.threads || [];
      threads.push(...batch);
      if (!inbox?.has_older || !inbox.oldest_cursor || !batch.length) break;
      cursor = inbox.oldest_cursor;
    }
    const seen = new Set();
    const rows = threads.map(normalizeThread)
      .filter((r) => r.thread_id && !seen.has(r.thread_id) && seen.add(r.thread_id))
      .slice(0, limit);
    if (!rows.length) throw new EmptyResultError('instagram-dm dm-threads', 'No conversations in the Instagram Direct inbox');
    return rows;
  },
});

cli({
  ...COMMON,
  name: 'dm-read',
  access: 'read',
  description: 'Read the latest messages of one Instagram Direct conversation (oldest first). Read-only; sends no seen receipt.',
  args: [
    { name: 'thread_id', type: 'string', required: true, positional: true, help: 'thread_id from dm-threads' },
    { name: 'limit', type: 'int', default: 30, help: 'Messages to return (1-100, default 30)' },
  ],
  columns: ['thread_id', 'message_id', 'author', 'is_me', 'text', 'time', 'media'],
  func: async (page, kwargs) => {
    let limit;
    try { limit = parseLimit(kwargs.limit, 30, 100, '--limit'); } catch (e) { throw new ArgumentError(e.message); }
    const threadId = String(kwargs.thread_id || '');
    if (!isValidThreadId(threadId)) throw new ArgumentError('thread_id must be the numeric id printed by dm-threads');
    await page.goto(PARK_URL);
    let thread = null;
    const items = new Map();
    let cursor = '';
    for (let p = 0; p < MAX_PAGES && items.size < limit; p++) {
      if (p) await sleep(PAGE_PAUSE_MS);
      const n = Math.min(20, limit - items.size);
      const q = `limit=${n}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;
      const t = (await get(page, `/api/v1/direct_v2/threads/${threadId}/?${q}`))?.thread;
      if (!t) break;
      thread ??= t;
      const before = items.size;
      for (const it of t.items || []) if (it.item_id && !items.has(it.item_id)) items.set(it.item_id, it);
      if (!t.has_older || !t.oldest_cursor || items.size === before) break;
      cursor = t.oldest_cursor;
    }
    if (!thread) throw new EmptyResultError('instagram-dm dm-read', 'Conversation not found');
    const rows = normalizeMessages(thread, threadId, [...items.values()]).slice(-limit);
    if (!rows.length) {
      throw new EmptyResultError('instagram-dm dm-read',
        thread.e2ee_cutover_status ? 'No messages returned (end-to-end encrypted chat? those are not served to the web API)' : 'No messages in that conversation');
    }
    return rows;
  },
});
