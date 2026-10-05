// Pure helpers for the messenger OpenCLI plugin. No browser, no I/O: everything
// here is covered by `node selftest.js` with fake fixtures.

const MIN = 60e3;
const UNIT_MS = {
  // "hace 3 días" (aria-label) and the short visible labels ("3 d", "2 sem", "5 min").
  segundo: 1e3, s: 1e3, second: 1e3,
  minuto: MIN, min: MIN, m: MIN, minute: MIN,
  hora: 60 * MIN, h: 60 * MIN, hour: 60 * MIN,
  'día': 1440 * MIN, dia: 1440 * MIN, d: 1440 * MIN, day: 1440 * MIN,
  semana: 10080 * MIN, sem: 10080 * MIN, w: 10080 * MIN, week: 10080 * MIN,
  mes: 43200 * MIN, month: 43200 * MIN,
  'año': 525600 * MIN, a: 525600 * MIN, year: 525600 * MIN, y: 525600 * MIN,
};
const MONTHS = {
  enero: 0, febrero: 1, marzo: 2, abril: 3, mayo: 4, junio: 5, julio: 6, agosto: 7,
  septiembre: 8, setiembre: 8, octubre: 9, noviembre: 10, diciembre: 11,
  january: 0, february: 1, march: 2, april: 3, may: 4, june: 5, july: 6, august: 7,
  september: 8, october: 9, november: 10, december: 11,
};
const WEEKDAYS = {
  domingo: 0, lunes: 1, martes: 2, 'miércoles': 3, miercoles: 3, jueves: 4, viernes: 5, 'sábado': 6, sabado: 6,
  sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6,
};
const ME = new Set(['Tú', 'Tu', 'You']);

export function parseLimit(raw, def, max, label) {
  const n = raw === undefined || raw === null || raw === '' ? def : Number(raw);
  if (!Number.isInteger(n) || n < 1 || n > max) {
    throw new RangeError(`${label} must be an integer between 1 and ${max} (got ${raw})`);
  }
  return n;
}

// thread_id = "<n>" for /messages/t/<n>/ and "e2ee:<n>" for /messages/e2ee/t/<n>/.
export function isValidThreadId(id) {
  return typeof id === 'string' && /^(e2ee:)?[A-Za-z0-9._-]{1,64}$/.test(id) && !/\.\./.test(id);
}

export function threadIdFromHref(href) {
  const m = /\/messages\/(e2ee\/)?t\/([A-Za-z0-9._-]+)\/?(?:[?#].*)?$/.exec(href || '');
  return m ? `${m[1] ? 'e2ee:' : ''}${m[2]}` : null;
}

export function threadPath(id) {
  return id.startsWith('e2ee:') ? `/messages/e2ee/t/${id.slice(5)}/` : `/messages/t/${id}/`;
}

// "hace 3 días" / "hace una semana" / "3 d" / "2 sem" / "5 min" → approximate ISO.
// ponytail: inbox only exposes relative labels; precision = the unit.
export function relativeLabelToIso(label, nowMs = Date.now()) {
  const t = String(label || '').trim().toLowerCase().replace(/^hace\s+/, '').replace(/\s+ago$/, '');
  if (/^(ahora|justo ahora|now|just now)$/.test(t)) return new Date(nowMs).toISOString();
  const m = /^(\d+|un|una|uno|a|an)\s*([a-zñáéíóú]+?)\.?$/.exec(t);
  if (!m) return null;
  const n = /^\d+$/.test(m[1]) ? Number(m[1]) : 1;
  let unit = m[2];
  if (!(unit in UNIT_MS)) unit = unit.replace(/(es|s)$/, '');
  if (!(unit in UNIT_MS)) unit = m[2].replace(/s$/, '');
  return unit in UNIT_MS ? new Date(nowMs - n * UNIT_MS[unit]).toISOString() : null;
}

// Message time labels, Spanish (owner's locale) + basic English:
//   "9:53 am", "20:44", "sábado 20:44", "ayer 10:00",
//   "29 de septiembre de 2026, 9:15 pm", "29 de septiembre, 21:15", "September 29, 2026, 9:15 PM".
// Local time of the machine running the command. Unknown shapes → null.
export function parseMessageTime(label, nowMs = Date.now()) {
  const s = String(label || '').toLowerCase().replace(/[  ]/g, ' ').trim();
  const tm = /(\d{1,2}):(\d{2})\s*(?:([ap])\.?\s*m\.?)?\s*$/.exec(s);
  if (!tm) return null;
  let hh = Number(tm[1]);
  const mi = Number(tm[2]);
  if (tm[3] === 'p' && hh < 12) hh += 12;
  if (tm[3] === 'a' && hh === 12) hh = 0;
  const head = s.slice(0, tm.index).replace(/[,\s]+$/, '').replace(/\s+a las$/, '').trim();
  const now = new Date(nowMs);
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hh, mi);
  if (!head || head === 'hoy' || head === 'today') return d.toISOString();
  if (head === 'ayer' || head === 'yesterday') { d.setDate(d.getDate() - 1); return d.toISOString(); }
  if (head in WEEKDAYS) {
    let back = (now.getDay() - WEEKDAYS[head] + 7) % 7;
    if (back === 0) back = 7; // same weekday name = last week (today shows only the time)
    d.setDate(d.getDate() - back);
    return d.toISOString();
  }
  const es = /^(\d{1,2}) de ([a-z]+)(?: de (\d{4}))?$/.exec(head);
  const en = /^([a-z]+)\.? (\d{1,2})(?:, (\d{4}))?$/.exec(head);
  const parts = es ? [es[1], es[2], es[3]] : en ? [en[2], en[1], en[3]] : null;
  if (!parts) return null;
  const mon = MONTHS[parts[1]] ?? Object.entries(MONTHS).find(([k]) => k.startsWith(parts[1]) && parts[1].length >= 3)?.[1];
  if (mon === undefined) return null;
  let year = parts[2] ? Number(parts[2]) : now.getFullYear();
  let out = new Date(year, mon, Number(parts[0]), hh, mi);
  if (!parts[2] && out.getTime() > nowMs + 86400e3) out = new Date(--year, mon, Number(parts[0]), hh, mi);
  return out.toISOString();
}

// Message aria-label: "A las <time>, <author>: <text>" or "A las <time>, <author>" (attachment only).
export function splitMessageLabel(label) {
  const m = /^(?:A las|At)\s+(.*?\d{1,2}:\d{2}(?:\s*[ap]\.?\s*m\.?)?),\s*([\s\S]*)$/i.exec(String(label || '').trim());
  if (!m) return null;
  const rest = m[2];
  const i = rest.indexOf(': ');
  return i >= 0
    ? { timeLabel: m[1], author: rest.slice(0, i).trim(), text: rest.slice(i + 2).trim() }
    : { timeLabel: m[1], author: rest.trim(), text: '' };
}

const clip = (s, n) => {
  const t = String(s || '').replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
};
const SHORT_TIME = /^(\d+\s*[a-zñáéíóú]{1,4}\.?|ahora|now|ayer|yesterday)$/i;

// raw = { href, leaves:[{text,bold}], timeAria, unreadHint }
export function normalizeThread(raw, nowMs = Date.now()) {
  const leaves = (raw.leaves || []).filter((l) => l && l.text && l.text.trim() && l.text.trim() !== '·');
  const name = leaves[0]?.text || '';
  let timeIdx = -1;
  for (let i = leaves.length - 1; i > 0; i--) if (SHORT_TIME.test(leaves[i].text.trim())) { timeIdx = i; break; }
  const rest = leaves.filter((_, i) => i !== 0 && i !== timeIdx);
  const visibleTime = timeIdx > 0 ? leaves[timeIdx].text.trim() : '';
  const label = String(raw.timeAria || '').trim() || visibleTime;
  return {
    thread_id: threadIdFromHref(raw.href),
    name: clip(name, 200),
    last_text: clip(rest.map((l) => l.text).join(' '), 200),
    last_time: relativeLabelToIso(label, nowMs) ?? (label || null),
    unread: Boolean(raw.unreadHint || leaves.some((l) => l.bold)),
  };
}

// raw = { id, label, side ('L'|'R'|'C'|''), media, domText }
export function normalizeMessage(raw, threadId, nowMs = Date.now()) {
  const p = splitMessageLabel(raw.label);
  const author = p ? p.author : '';
  return {
    thread_id: threadId,
    message_id: raw.id || null,
    author,
    is_me: ME.has(author) || (!p && raw.side === 'R'),
    text: (p && p.text) || (raw.media ? '' : String(raw.domText || '').replace(/\s+/g, ' ').trim()),
    time: p ? parseMessageTime(p.timeLabel, nowMs) : null,
    media: raw.media || null,
  };
}

// Merge a top-to-bottom snapshot of visible rows into the accumulated list.
// Rows seen before the first known id are older (loaded by scrolling up).
export function mergeSnapshot(all, snap) {
  const known = new Set(all.map((r) => r.id));
  const anchor = snap.findIndex((r) => known.has(r.id));
  if (anchor === -1) return [...snap, ...all]; // only ever scroll up: disjoint = older
  const older = snap.slice(0, anchor).filter((r) => !known.has(r.id));
  const newer = snap.slice(anchor).filter((r) => !known.has(r.id));
  return [...older, ...all, ...newer];
}

export function unwrap(v) {
  return v && typeof v === 'object' && 'session' in v && 'data' in v ? v.data : v;
}
