import test from 'node:test';
import assert from 'node:assert/strict';
import { readdir, access } from 'node:fs/promises';
import { createHandler } from './handler.mjs';
import { FixtureRuntime } from './runtime.mjs';
import { eventFor } from './test-fixtures.mjs';
import { listingsFor } from './contract.mjs';
import { valid } from './schema.mjs';
import { liveGroupMembers, runCli } from './processes.mjs';

const leftovers = async () => (await readdir('/tmp')).filter(name => name.startsWith('radar-browser-')).sort();
async function verifyClosed(runtime, initial) {
  assert.equal(runtime.contexts.size, 0);
  for (const child of runtime.children) assert.ok(child.exitCode !== null || child.signalCode !== null);
  for (const group of runtime.processGroups) assert.deepEqual(await liveGroupMembers(group), [], `live descendants of own process group ${group}`);
  if (runtime.work) await assert.rejects(access(runtime.work));
  if (runtime.fixture) await assert.rejects(fetch(`${runtime.fixture.origin}/listings`, { signal: AbortSignal.timeout(1000) }));
  assert.deepEqual(await leftovers(), initial);
}

for (const mode of ['direct', 'opencli']) for (const version of [1, 2]) {
  test(`real fixture ${mode}, envelope ${version}, fresh restored contexts and egress denial`, { timeout: 85000 }, async () => {
    const initial = await leftovers();
    let runtime;
    const handle = createHandler({ localFixtureTest: true, fixtureExternalProbe: true }, config => (runtime = new FixtureRuntime(config)));
    const event = eventFor(mode, version, 2);
    const output = await handle(event);
    assert.equal(output.payload.status, 'succeeded', JSON.stringify(output));
    assert.ok(valid(`envelope.v${version}`, output));
    assert.equal(output.payload.records.length, 6);
    assert.deepEqual(output.payload.records.slice(0, 3).map(record => record.fields), listingsFor(1));
    assert.deepEqual(output.payload.records.slice(3, 6).map(record => record.fields), listingsFor(2));
    assert.equal(output.causation_id, event.message_id);
    assert.equal(runtime.blockedRequests, 2);
    await verifyClosed(runtime, initial);
  });
}
for (const state of ['cookies_only', 'none']) test(`incomplete fixture ${state} fails and cleans up`, { timeout: 85000 }, async () => {
  const initial = await leftovers();
  let runtime;
  const output = await createHandler({ localFixtureTest: true, fixtureState: state }, config => (runtime = new FixtureRuntime(config)))(eventFor());
  assert.equal(output.payload.status, 'failed');
  assert.equal(output.payload.error.code, 'needs_reauth');
  assert.ok(valid('envelope.v1', output));
  await verifyClosed(runtime, initial);
});
test('real initialization deadline cancellation leaves no server/process/profile', { timeout: 20000 }, async () => {
  const initial = await leftovers();
  let runtime;
  const output = await createHandler({ localFixtureTest: true }, config => (runtime = new FixtureRuntime(config)))(eventFor(), { getRemainingTimeInMillis: () => 9100 });
  assert.equal(output.payload.status, 'failed');
  assert.equal(output.payload.error.code, 'timeout');
  await verifyClosed(runtime, initial);
});
test('cross-tenant/deadline preflight rejects without temporary resources', async () => {
  const initial = await leftovers();
  for (const mutate of [event => { event.owner_ref = 'user:other'; }, event => { event.session_ref = 'browser:other'; }, event => { event.payload.capability_id = 'fixture.other.read'; }, event => { event.deadline = '2026-01-01T00:00:00Z'; }]) {
    const event = eventFor(); mutate(event);
    let factories = 0;
    const output = await createHandler({ localFixtureTest: true }, config => { factories++; return new FixtureRuntime(config); })(event);
    assert.equal(output.payload.status, 'failed');
    assert.equal(factories, 0);
    assert.ok(valid('envelope.v1', output));
  }
  assert.deepEqual(await leftovers(), initial);
});
test('CLI descendants die when the leader exits or times out', { timeout: 15000 }, async () => {
  for (const timeout of [false, true]) {
    const children = new Set();
    children.groups = new Set();
    const script = "require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},10000)'],{stdio:'ignore'});" +
      (timeout ? "setInterval(()=>{},10000)" : "process.stdout.write('[]');process.exit(0)");
    if (timeout) await assert.rejects(runCli(process.execPath, ['-e', script], { timeout: 300, env: {} }, children));
    else assert.deepEqual(await runCli(process.execPath, ['-e', script], { timeout: 3000, env: {} }, children), []);
    for (const group of children.groups) assert.deepEqual(await liveGroupMembers(group), []);
    assert.equal(children.size, 0);
  }
});

// Runs only when explicitly invoked with the official RIE entrypoint active.
if (process.env.RADAR_TEST_RIE === '1') {
  for (const mode of ['direct', 'opencli']) test(`RIE invocation ${mode} envelope v2`, { timeout: 85000 }, async () => {
    const event = eventFor(mode, 2);
    const response = await fetch('http://127.0.0.1:8080/2015-03-31/functions/function/invocations', {
      method: 'POST', body: JSON.stringify(event), signal: AbortSignal.timeout(80000),
    });
    const output = await response.json();
    assert.equal(output.payload?.status, 'succeeded', JSON.stringify(output));
    assert.equal(output.schema_version, 2);
    assert.equal(output.causation_id, event.message_id);
    assert.ok(valid('envelope.v2', output));
  });
}
