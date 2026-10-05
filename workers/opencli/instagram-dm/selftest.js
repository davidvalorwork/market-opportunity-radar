// Offline check of normalize.js with FAKE data. Run: node selftest.js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  classifyResponse, isValidThreadId, itemMedia, itemText, normalizeMessages, normalizeThread,
  parseLimit, tsToIso, unwrap,
} from './normalize.js';

const TID = '340282366841710300949128000000000001';

assert.equal(isValidThreadId(TID), true);
assert.equal(isValidThreadId('../x'), false);
assert.equal(isValidThreadId('12a45'), false);
assert.equal(isValidThreadId(''), false);

assert.equal(parseLimit(undefined, 20, 50, 'l'), 20);
assert.equal(parseLimit('7', 20, 50, 'l'), 7);
assert.throws(() => parseLimit(51, 20, 50, 'l'), RangeError);
assert.throws(() => parseLimit(0, 20, 50, 'l'), RangeError);

assert.equal(tsToIso(1768478400000000), '2026-01-15T12:00:00.000Z'); // µs
assert.equal(tsToIso('1768478400000'), '2026-01-15T12:00:00.000Z'); // ms
assert.equal(tsToIso(1768478400), '2026-01-15T12:00:00.000Z'); // s
assert.equal(tsToIso(null), null);
assert.equal(tsToIso('ayer'), null);

assert.equal(itemText({ item_type: 'link', link: { text: 'mira esto' } }), 'mira esto');
assert.equal(itemText({ item_type: 'clip' }), '');
assert.equal(itemText({ item_type: 'placeholder', placeholder: { message: 'no disponible' } }), 'no disponible');
assert.equal(itemMedia({ item_type: 'text' }), null);
assert.equal(itemMedia({ item_type: 'voice_media' }), 'voice_media');

const t = normalizeThread({
  thread_id: TID, thread_title: 'fake_person', read_state: 0, last_activity_at: 1768478400000000,
  users: [{ pk: '111', username: 'fake_person' }],
  items: [{ item_id: 'i2', item_type: 'text', text: 'hola  que\ntal', timestamp: 1768478400000000, user_id: '111' }],
});
assert.deepEqual(t, {
  thread_id: TID, name: 'fake_person', last_text: 'hola que tal',
  last_time: '2026-01-15T12:00:00.000Z', unread: false,
});
const u = normalizeThread({
  thread_id: TID, read_state: 1, users: [{ username: 'a' }, { username: 'b' }],
  items: [{ item_type: 'clip', timestamp: 1768478400000000 }],
});
assert.equal(u.unread, true);
assert.equal(u.name, 'a, b');
assert.equal(u.last_text, '[clip]');
assert.equal(u.last_time, '2026-01-15T12:00:00.000Z');
assert.equal(normalizeThread({ thread_id: TID, marked_as_unread: true }).unread, true);
assert.equal(normalizeThread({ thread_id: TID, items: [{ item_type: 'text', text: 'x'.repeat(300) }] }).last_text.length, 200);

const thread = {
  viewer_id: '999', users: [{ pk: 111, username: 'fake_person' }],
  items: [ // API order: newest first
    { item_id: 'c', item_type: 'media', user_id: '999', is_sent_by_viewer: true, timestamp: 1768478460000000 },
    { item_id: 'b', item_type: 'text', text: 'hola', user_id: '111', is_sent_by_viewer: false, timestamp: 1768478400000000 },
  ],
};
const msgs = normalizeMessages(thread, TID);
assert.deepEqual(msgs, [
  { thread_id: TID, message_id: 'b', author: 'fake_person', is_me: false, text: 'hola', time: '2026-01-15T12:00:00.000Z', media: null },
  { thread_id: TID, message_id: 'c', author: 'me', is_me: true, text: '', time: '2026-01-15T12:01:00.000Z', media: 'media' },
]);
assert.equal(normalizeMessages({ items: [{ user_id: '5', text: 'x' }] }, TID)[0].author, '5');

const ok = { status: 200, finalPath: '/api/v1/direct_v2/inbox/', body: { status: 'ok' } };
assert.equal(classifyResponse(ok), null);
assert.equal(classifyResponse({ status: 400, body: { message: 'checkpoint_required', status: 'fail' } }).kind, 'locked');
assert.equal(classifyResponse({ status: 200, finalPath: '/challenge/x/', body: null }).kind, 'locked');
assert.equal(classifyResponse({ status: 200, finalPath: '/accounts/login/', body: null }).kind, 'auth');
assert.equal(classifyResponse({ status: 403, body: { message: 'login_required' } }).kind, 'auth');
assert.equal(classifyResponse({ status: 429, body: null }).kind, 'rate');
assert.equal(classifyResponse({ status: 400, body: { message: 'Please wait a few minutes before you try again.' } }).kind, 'rate');
assert.equal(classifyResponse({ status: 404, body: null }).kind, 'notfound');
assert.equal(classifyResponse({ status: 500, body: null }).kind, 'http');

assert.deepEqual(unwrap({ session: 's', data: { a: 1 } }), { a: 1 });
assert.deepEqual(unwrap({ a: 1 }), { a: 1 });

// Read-only guarantee: no write command, no POST, no typing/clicking APIs.
const src = readFileSync(new URL('./dm.js', import.meta.url), 'utf8');
assert.doesNotMatch(src, /access:\s*'write'/);
assert.doesNotMatch(src, /method:\s*'POST'|insertText|\.click\(|\.type\(|dm-send/);

console.log('instagram-dm selftest: all assertions passed');
