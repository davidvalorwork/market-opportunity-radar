// Pure helpers for the x-dm OpenCLI plugin. No browser, no I/O: everything
// here is covered by `node selftest.js` with fake fixtures.

const TWITTER_EPOCH_MS = 1288834974657n;
const UNIT_MS = {
  // Spanish + English short labels used by the X inbox ("6 s" = 6 semanas).
  min: 60e3, m: 60e3,
  h: 3600e3,
  d: 86400e3,
  s: 7 * 86400e3, sem: 7 * 86400e3, w: 7 * 86400e3,
};

export function parseLimit(raw, def, max, label) {
  const n = raw === undefined || raw === null || raw === '' ? def : Number(raw);
  if (!Number.isInteger(n) || n < 1 || n > max) {
    throw new RangeError(`${label} must be an integer between 1 and ${max} (got ${raw})`);
  }
  return n;
}

// Thread ids are the path segment X uses: /i/chat/<a>-<b> (1:1) or other
// opaque tokens for groups. Never allow slashes, dots or query chars.
export function isValidThreadId(id) {
  return typeof id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(id);
}

export function threadIdFromHref(href) {
  const m = /\/i\/chat\/([A-Za-z0-9_-]+)\/?$/.exec(href || '');
  return m ? m[1] : null;
}

// ponytail: inbox only shows relative labels ("6 s", "3 h"); precision = unit.
export function relativeLabelToIso(label, nowMs = Date.now()) {
  const m = /^(\d+)\s*([a-zA-Z]+)\.?$/.exec(String(label || '').trim());
  if (!m) return null;
  const unit = UNIT_MS[m[2].toLowerCase()];
  return unit ? new Date(nowMs - Number(m[1]) * unit).toISOString() : null;
}

export function snowflakeToIso(id) {
  if (!/^\d{15,20}$/.test(String(id || ''))) return null;
  const ms = Number((BigInt(id) >> 22n) + TWITTER_EPOCH_MS);
  return new Date(ms).toISOString();
}

const clip = (s, n) => {
  const t = String(s || '').replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
};

// raw = { href, desc, leaves:[{text,bold}], unreadHint }
export function normalizeThread(raw, nowMs = Date.now()) {
  const leaves = (raw.leaves || []).filter((l) => l && l.text);
  const desc = String(raw.desc || '').trim();
  const name = leaves[0]?.text || '';
  const timeIdx = leaves.findIndex((l, i) => i > 0 && desc.endsWith(l.text));
  const rest = leaves.filter((_, i) => i !== 0 && i !== timeIdx);
  return {
    thread_id: threadIdFromHref(raw.href),
    name: clip(name, 200),
    last_text: clip(rest.map((l) => l.text).join(' '), 200),
    last_time: timeIdx > 0 ? relativeLabelToIso(leaves[timeIdx].text, nowMs) : null,
    unread: Boolean(raw.unreadHint || rest.some((l) => l.bold)),
  };
}

// raw = { id, sender, timeLabel, kind, side, text, hasVideo, hasImg, ts }
export function normalizeMessage(raw, threadId) {
  const isMe = raw.side === 'end' || raw.side === 'right';
  let time = null;
  if (typeof raw.ts === 'number' && raw.ts > 0) time = new Date(raw.ts * 1000).toISOString();
  else time = snowflakeToIso(raw.id);
  const kind = String(raw.kind || '').trim();
  const media = kind || (raw.hasVideo ? 'video' : raw.hasImg ? 'image' : null);
  return {
    thread_id: threadId,
    message_id: raw.id || null,
    author: String(raw.sender || '').trim(),
    is_me: isMe,
    text: String(raw.text || ''),
    time,
    media: media || null,
  };
}

// Merge a top-to-bottom snapshot of visible rows into the accumulated list.
// Rows seen before the first known id are older (loaded by scrolling up).
export function mergeSnapshot(all, snap) {
  const known = new Set(all.map((r) => r.id));
  const anchor = snap.findIndex((r) => known.has(r.id));
  // No overlap: we only ever scroll up, so a disjoint snapshot is older.
  if (anchor === -1) return [...snap, ...all];
  const older = snap.slice(0, anchor).filter((r) => !known.has(r.id));
  const newer = snap.slice(anchor).filter((r) => !known.has(r.id));
  return [...older, ...all, ...newer];
}

export function unwrap(v) {
  return v && typeof v === 'object' && 'session' in v && 'data' in v ? v.data : v;
}
