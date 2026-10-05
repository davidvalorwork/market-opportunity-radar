// Pure helpers for the instagram-dm OpenCLI plugin. No browser, no I/O:
// everything here is covered by `node selftest.js` with fake fixtures.

export function parseLimit(raw, def, max, label) {
  const n = raw === undefined || raw === null || raw === '' ? def : Number(raw);
  if (!Number.isInteger(n) || n < 1 || n > max) {
    throw new RangeError(`${label} must be an integer between 1 and ${max} (got ${raw})`);
  }
  return n;
}

// Instagram thread ids are long decimal strings (39 digits seen live).
export function isValidThreadId(id) {
  return typeof id === 'string' && /^\d{5,64}$/.test(id);
}

// Instagram timestamps are microseconds (16 digits); accept ms / s too.
export function tsToIso(v) {
  const s = String(v ?? '');
  if (!/^\d{9,17}$/.test(s)) return null;
  const n = Number(s);
  const ms = s.length >= 15 ? n / 1000 : s.length >= 12 ? n : n * 1000;
  return new Date(Math.floor(ms)).toISOString();
}

const clip = (s, n) => {
  const t = String(s || '').replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
};

// Visible text of one direct item, whatever its type.
export function itemText(it) {
  const t = it?.text ?? it?.link?.text ?? it?.reel_share?.text ?? it?.story_share?.text
    ?? it?.action_log?.description ?? it?.placeholder?.message ?? '';
  return typeof t === 'string' ? t : '';
}

export function itemMedia(it) {
  const type = String(it?.item_type || '');
  return type && type !== 'text' ? type : null;
}

// raw = one entry of inbox.threads (newest item first in `items`).
export function normalizeThread(t) {
  const last = t.items?.[0] || t.last_permanent_item || null;
  const name = t.thread_title || (t.users || []).map((u) => u.username).filter(Boolean).join(', ');
  const text = last ? itemText(last) || `[${last.item_type || 'item'}]` : '';
  return {
    thread_id: String(t.thread_id || ''),
    name: clip(name, 200),
    last_text: clip(text, 200),
    last_time: tsToIso(t.last_activity_at ?? last?.timestamp),
    unread: t.read_state === 1 || t.marked_as_unread === true,
  };
}

// thread = response.thread of /direct_v2/threads/<id>/ ; returns oldest first.
export function normalizeMessages(thread, threadId, items = thread.items || []) {
  const names = new Map((thread.users || []).map((u) => [String(u.pk ?? u.pk_id ?? u.id), u.username || u.full_name || '']));
  const viewer = String(thread.viewer_id ?? '');
  return [...items]
    .sort((a, b) => Number(a.timestamp) - Number(b.timestamp))
    .map((it) => {
      const isMe = it.is_sent_by_viewer === true || (viewer !== '' && String(it.user_id) === viewer);
      return {
        thread_id: threadId,
        message_id: it.item_id ? String(it.item_id) : null,
        author: isMe ? 'me' : names.get(String(it.user_id)) || String(it.user_id || ''),
        is_me: isMe,
        text: itemText(it),
        time: tsToIso(it.timestamp),
        media: itemMedia(it),
      };
    });
}

// res = { status, finalPath, body } from the in-page fetch.
// Returns null when OK, else { kind, detail } (kind: locked|auth|rate|notfound|http).
export function classifyResponse(res) {
  const b = res?.body && typeof res.body === 'object' ? res.body : {};
  const msg = String(b.message || '');
  const path = String(res?.finalPath || '');
  if (/^\/(challenge|checkpoint|accounts\/suspended|two_factor)/.test(path)
    || b.challenge || b.two_factor_required || b.checkpoint_url
    || /checkpoint|challenge|two_factor|passcode|\bpin\b|verif/i.test(msg)) {
    return { kind: 'locked', detail: msg || path || 'verification required' };
  }
  if (/^\/accounts\/login/.test(path) || res?.status === 401 || res?.status === 403 || /login_required/i.test(msg)) {
    return { kind: 'auth', detail: msg || `HTTP ${res?.status}` };
  }
  if (res?.status === 429 || /wait a few minutes|rate.?limit|feedback_required|spam/i.test(msg)) {
    return { kind: 'rate', detail: msg || `HTTP ${res?.status}` };
  }
  if (res?.status === 404) return { kind: 'notfound', detail: 'HTTP 404' };
  if (res?.status !== 200 || b.status !== 'ok') return { kind: 'http', detail: `HTTP ${res?.status} ${msg}`.trim() };
  return null;
}

export function unwrap(v) {
  return v && typeof v === 'object' && 'session' in v && 'data' in v ? v.data : v;
}
