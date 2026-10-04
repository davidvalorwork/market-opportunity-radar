import test from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { validateEvent, storageChecks, listingsFor } from './contract.mjs';
import { startFixture } from './fixture.mjs';
import { safeDiagnostic } from './diagnostics.mjs';

test('synthetic startup diagnostics are bounded and redact credential forms', () => {
  const diagnostic = safeDiagnostic('Cookie: radar_session=private\nAuthorization: Bearer private\ntoken=private\nws://127.0.0.1:123/devtools/page/abcDEF12\n' + 'x'.repeat(3000));
  assert.ok(diagnostic.length <= 2048);
  assert.ok(!diagnostic.includes('private'));
  assert.ok(!diagnostic.includes('abcDEF12'));
});

const valid = { schema_version: 1, suite: 'browser', fixture_only: true };
test('contract rejects unsafe scope and invalid budgets before browser startup', () => {
  assert.equal(validateEvent(valid).session_mode, 'full');
  for (const event of [{ ...valid, fixture_only: false }, { ...valid, schema_version: 2 }, { ...valid, batch: 11 }, { ...valid, repeats: 0 }, { ...valid, url: 'https://example.com' }, { ...valid, deadline_ms: 60001 }]) assert.throws(() => validateEvent(event));
});
test('cookie alone is insufficient and sessionStorage portability is explicit', () => {
  assert.deepEqual(storageChecks({ cookie: true, localStorage: false, indexedDB: false, sessionStorage: false }), { cookie_restored: true, local_storage_restored: false, indexed_db_restored: false, session_storage_absent: true });
  assert.equal(storageChecks({ cookie: true, localStorage: true, indexedDB: true, sessionStorage: true }).session_storage_absent, false);
});
test('internal loopback fixture requires synthetic authorization and bounded query', async () => {
  const fixture = await startFixture();
  try {
    assert.equal(new URL(fixture.origin).hostname, '127.0.0.1');
    assert.deepEqual(await fetch(`${fixture.origin}/api/access`).then(r => r.json()), { authorized: false });
    assert.deepEqual(await fetch(`${fixture.origin}/api/access`, { headers: { Cookie: 'radar_session=synthetic-account' } }).then(r => r.json()), { authorized: true });
    assert.deepEqual(await fetch(`${fixture.origin}/api/listings?query=3`).then(r => r.json()), listingsFor(3));
    assert.equal((await fetch(`${fixture.origin}/api/listings?query=11`)).status, 400);
    assert.equal((await fetch(`${fixture.origin}/unknown`)).status, 404);
    const status = await new Promise((resolve, reject) => {
      const req = request(`${fixture.origin}/seed`, { headers: { Host: 'example.com' } }, res => { res.resume(); resolve(res.statusCode); });
      req.on('error', reject); req.end();
    });
    assert.equal(status, 403);
  } finally { await fixture.close(); }
});
