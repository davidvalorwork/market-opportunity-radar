// Offline check of normalize.js with FAKE data. Run: node selftest.js
import assert from 'node:assert/strict';
import {
  isValidThreadId, mergeSnapshot, normalizeMessage, normalizeThread, parseLimit,
  relativeLabelToIso, snowflakeToIso, threadIdFromHref, unwrap,
} from './normalize.js';

const NOW = Date.parse('2026-01-15T12:00:00Z');

assert.equal(threadIdFromHref('/i/chat/111-222'), '111-222');
assert.equal(threadIdFromHref('/i/chat/111-222/info'), null);
assert.equal(isValidThreadId('111-222'), true);
assert.equal(isValidThreadId('../x'), false);
assert.equal(isValidThreadId('a/b'), false);

assert.equal(parseLimit(undefined, 20, 50, 'l'), 20);
assert.equal(parseLimit('7', 20, 50, 'l'), 7);
assert.throws(() => parseLimit(51, 20, 50, 'l'), RangeError);
assert.throws(() => parseLimit(0, 20, 50, 'l'), RangeError);

assert.equal(relativeLabelToIso('3 h', NOW), '2026-01-15T09:00:00.000Z');
assert.equal(relativeLabelToIso('2 s', NOW), '2026-01-01T12:00:00.000Z'); // semanas
assert.equal(relativeLabelToIso('5d', NOW), '2026-01-10T12:00:00.000Z');
assert.equal(relativeLabelToIso('ayer', NOW), null);

// Snowflake from a public tweet id (decodes to 2019-12-31.
assert.match(snowflakeToIso('1212092628029698048'), /^2019-12-31T/);
assert.equal(snowflakeToIso('3f2b-uuid'), null);

const t = normalizeThread({
  href: '/i/chat/111-222',
  desc: 'Fake Person hola que tal 3 h',
  leaves: [{ text: 'Fake Person', bold: true }, { text: '3 h', bold: false }, { text: 'hola que tal', bold: false }],
}, NOW);
assert.deepEqual(t, {
  thread_id: '111-222', name: 'Fake Person', last_text: 'hola que tal',
  last_time: '2026-01-15T09:00:00.000Z', unread: false,
});
const u = normalizeThread({ href: '/i/chat/9', desc: 'x', leaves: [{ text: 'A' }, { text: 'nuevo', bold: true }] }, NOW);
assert.equal(u.unread, true);
assert.equal(u.last_time, null);
assert.equal(normalizeThread({ href: '/i/chat/9', leaves: [{ text: 'A' }, { text: 'x'.repeat(300) }] }).last_text.length, 200);

const m1 = normalizeMessage({ id: 'aaaa-uuid', sender: 'Fake', side: 'start', text: 'hola', ts: 1768478400 }, '111-222');
assert.deepEqual(m1, {
  thread_id: '111-222', message_id: 'aaaa-uuid', author: 'Fake', is_me: false,
  text: 'hola', time: '2026-01-15T12:00:00.000Z', media: null,
});
const m2 = normalizeMessage({ id: '1212092628029698048', sender: 'Tú', side: 'end', text: '', kind: 'Vídeo', hasVideo: true }, 't');
assert.equal(m2.is_me, true);
assert.equal(m2.media, 'Vídeo');
assert.match(m2.time, /^2019-12-31T/);
assert.equal(normalizeMessage({ id: 'x', hasImg: true }, 't').media, 'image');

// Scrolling up yields older rows above the known ones.
const r = (id) => ({ id });
assert.deepEqual(mergeSnapshot([], [r('c'), r('d')]).map((x) => x.id), ['c', 'd']);
assert.deepEqual(mergeSnapshot([r('c'), r('d')], [r('a'), r('b'), r('c')]).map((x) => x.id), ['a', 'b', 'c', 'd']);
assert.deepEqual(mergeSnapshot([r('c'), r('d')], [r('a'), r('b')]).map((x) => x.id), ['a', 'b', 'c', 'd']);
assert.deepEqual(mergeSnapshot([r('c')], [r('c'), r('e')]).map((x) => x.id), ['c', 'e']);

assert.deepEqual(unwrap({ session: 's', data: { a: 1 } }), { a: 1 });
assert.deepEqual(unwrap({ a: 1 }), { a: 1 });

console.log('x-dm selftest: all assertions passed');
