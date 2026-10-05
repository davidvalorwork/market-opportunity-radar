// Offline check of normalize.js with FAKE data. Run: node selftest.js
import assert from 'node:assert/strict';
import {
  isValidThreadId, mergeSnapshot, normalizeMessage, normalizeThread, parseLimit, parseMessageTime,
  relativeLabelToIso, splitMessageLabel, threadIdFromHref, threadPath, unwrap,
} from './normalize.js';

const NOW = new Date(2026, 9, 5, 12, 0).getTime(); // Mon 2026-10-05 12:00 local
const local = (y, mo, d, h, mi) => new Date(y, mo, d, h, mi).toISOString();

assert.equal(threadIdFromHref('/messages/t/1111/'), '1111');
assert.equal(threadIdFromHref('/messages/e2ee/t/2222/'), 'e2ee:2222');
assert.equal(threadIdFromHref('/marketplace/item/3/'), null);
assert.equal(threadPath('1111'), '/messages/t/1111/');
assert.equal(threadPath('e2ee:2222'), '/messages/e2ee/t/2222/');
assert.equal(isValidThreadId('e2ee:2222'), true);
assert.equal(isValidThreadId('../x'), false);
assert.equal(isValidThreadId('a/b'), false);
assert.equal(isValidThreadId('1?x=1'), false);

assert.equal(parseLimit(undefined, 20, 50, 'l'), 20);
assert.equal(parseLimit('7', 20, 50, 'l'), 7);
assert.throws(() => parseLimit(51, 20, 50, 'l'), RangeError);
assert.throws(() => parseLimit(0, 20, 50, 'l'), RangeError);

const H = 3600e3;
assert.equal(relativeLabelToIso('hace 3 horas', NOW), new Date(NOW - 3 * H).toISOString());
assert.equal(relativeLabelToIso('hace una semana', NOW), new Date(NOW - 168 * H).toISOString());
assert.equal(relativeLabelToIso('hace 2 semanas', NOW), new Date(NOW - 336 * H).toISOString());
assert.equal(relativeLabelToIso('hace 5 días', NOW), new Date(NOW - 120 * H).toISOString());
assert.equal(relativeLabelToIso('hace un minuto', NOW), new Date(NOW - 60e3).toISOString());
assert.equal(relativeLabelToIso('2 sem', NOW), new Date(NOW - 336 * H).toISOString());
assert.equal(relativeLabelToIso('4 h', NOW), new Date(NOW - 4 * H).toISOString());
assert.equal(relativeLabelToIso('2 meses', NOW), new Date(NOW - 1440 * H).toISOString());
assert.equal(relativeLabelToIso('ayer', NOW), null);

assert.equal(parseMessageTime('9:53 am', NOW), local(2026, 9, 5, 9, 53));
assert.equal(parseMessageTime('9:53 p. m.', NOW), local(2026, 9, 5, 21, 53));
assert.equal(parseMessageTime('12:05 am', NOW), local(2026, 9, 5, 0, 5));
assert.equal(parseMessageTime('sábado 20:44', NOW), local(2026, 9, 3, 20, 44));
assert.equal(parseMessageTime('lunes 8:00', NOW), local(2026, 8, 28, 8, 0)); // same weekday = last week
assert.equal(parseMessageTime('ayer 10:00', NOW), local(2026, 9, 4, 10, 0));
assert.equal(parseMessageTime('29 de septiembre de 2026, 9:15 pm', NOW), local(2026, 8, 29, 21, 15));
assert.equal(parseMessageTime('29 de diciembre, 9:15', NOW), local(2025, 11, 29, 9, 15)); // no year, future → last year
assert.equal(parseMessageTime('September 29, 2026, 9:15 PM', NOW), local(2026, 8, 29, 21, 15));
assert.equal(parseMessageTime('Saturday 8:44 PM', NOW), local(2026, 9, 3, 20, 44));
assert.equal(parseMessageTime('sin hora', NOW), null);

assert.deepEqual(splitMessageLabel('A las sábado 20:44, Fake Person: hola: qué tal'),
  { timeLabel: 'sábado 20:44', author: 'Fake Person', text: 'hola: qué tal' });
assert.deepEqual(splitMessageLabel('A las 29 de septiembre de 2026, 9:15 pm, Tú: listo'),
  { timeLabel: '29 de septiembre de 2026, 9:15 pm', author: 'Tú', text: 'listo' });
assert.deepEqual(splitMessageLabel('A las 9:53 am, Fake Person'),
  { timeLabel: '9:53 am', author: 'Fake Person', text: '' });
assert.equal(splitMessageLabel(''), null);

const t = normalizeThread({
  href: '/messages/e2ee/t/2222/',
  timeAria: 'hace 3 horas',
  leaves: [{ text: 'Fake Person', bold: false }, { text: 'Tú: hola que tal', bold: false }, { text: '·' }, { text: '3 h', bold: false }],
}, NOW);
assert.deepEqual(t, {
  thread_id: 'e2ee:2222', name: 'Fake Person', last_text: 'Tú: hola que tal',
  last_time: new Date(NOW - 3 * H).toISOString(), unread: false,
});
const u = normalizeThread({ href: '/messages/t/9/', leaves: [{ text: 'A', bold: true }, { text: 'nuevo', bold: true }, { text: '5 min' }] }, NOW);
assert.equal(u.unread, true);
assert.equal(u.last_text, 'nuevo');
assert.equal(u.last_time, new Date(NOW - 300e3).toISOString());
const odd = normalizeThread({ href: '/messages/t/9/', timeAria: 'el martes', leaves: [{ text: 'A' }, { text: 'x'.repeat(300) }] }, NOW);
assert.equal(odd.last_text.length, 200);
assert.equal(odd.last_time, 'el martes'); // unparsed → visible label kept

const m1 = normalizeMessage({ id: 'mid.$fake1', label: 'A las 9:53 am, Tú: hola', side: 'R', media: null }, '9', NOW);
assert.deepEqual(m1, {
  thread_id: '9', message_id: 'mid.$fake1', author: 'Tú', is_me: true,
  text: 'hola', time: local(2026, 9, 5, 9, 53), media: null,
});
const m2 = normalizeMessage({ id: 'mid.$fake2', label: 'A las 9:54 am, Fake Person', side: 'L', media: 'audio' }, '9', NOW);
assert.equal(m2.is_me, false);
assert.equal(m2.text, '');
assert.equal(m2.media, 'audio');
const m4 = normalizeMessage({ id: 'mid.$fake4', label: 'A las 9:55 am, Fake Person', side: 'L', domText: 'texto  solo en DOM' }, '9', NOW);
assert.equal(m4.text, 'texto solo en DOM');
const m3 = normalizeMessage({ id: '', label: '', side: 'R', domText: ' sin etiqueta ' }, '9', NOW);
assert.deepEqual([m3.message_id, m3.is_me, m3.text, m3.time], [null, true, 'sin etiqueta', null]);

const r = (id) => ({ id });
assert.deepEqual(mergeSnapshot([], [r('c'), r('d')]).map((x) => x.id), ['c', 'd']);
assert.deepEqual(mergeSnapshot([r('c'), r('d')], [r('a'), r('b'), r('c')]).map((x) => x.id), ['a', 'b', 'c', 'd']);
assert.deepEqual(mergeSnapshot([r('c'), r('d')], [r('a'), r('b')]).map((x) => x.id), ['a', 'b', 'c', 'd']);

assert.deepEqual(unwrap({ session: 's', data: { a: 1 } }), { a: 1 });

console.log('messenger selftest: all assertions passed');
