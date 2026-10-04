import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createHandler } from './handler.mjs';
import { valid, WorkerError, MAX_BYTES } from './schema.mjs';
import { eventFor } from './test-fixtures.mjs';
import { listingsFor } from './contract.mjs';
import { runCli } from './processes.mjs';
import { FixtureRuntime } from './runtime.mjs';

function fake(options = {}) {
  return { opened: 0, reads: 0, closed: 0,
    async open() { this.opened++; if (options.openError) throw options.openError; },
    async read(query) { this.reads++; if (query === options.failQuery) throw options.readError ?? new WorkerError('timeout'); return options.rows ?? listingsFor(query); },
    async close() { this.closed++; if (options.closeError) throw options.closeError; },
  };
}

test('same canonical golden examples, including negative payload and envelope cases', () => {
  let total = 0;
  for (const name of ['envelope.v1', 'envelope.v2', 'browser.read.v1', 'browser.result.v1']) {
    for (const group of ['valid', 'invalid']) {
      const root = new URL(`../../contracts/examples/${group}/${name}/`, import.meta.url);
      for (const file of readdirSync(root)) {
        assert.equal(valid(name, JSON.parse(readFileSync(new URL(file, root)))), group === 'valid', `${group}/${name}/${file}`);
        total++;
      }
    }
  }
  assert.ok(total >= 30);
});

for (const version of [1, 2]) for (const mode of ['direct', 'opencli']) {
  test(`correlated version ${version} ${mode} result contains raw fields and hash of observed bytes`, async () => {
    const event = eventFor(mode, version, 2);
    const runtime = fake();
    const output = await createHandler({ localFixtureTest: true }, () => runtime)(event);
    assert.equal(output.schema_version, version);
    assert.equal(output.payload.schema_version, 1);
    assert.equal(output.payload.status, 'succeeded');
    assert.equal(output.causation_id, event.message_id);
    assert.equal(output.correlation_id, event.correlation_id);
    assert.equal(output.operation_id, event.operation_id);
    assert.equal(output.expected_version, event.expected_version);
    assert.notEqual(output.message_id, event.message_id);
    assert.equal(output.payload.records.length, 6);
    assert.equal(runtime.closed, 1);
    const record = output.payload.records[0];
    assert.equal(record.evidence.content_hash, createHash('sha256').update(JSON.stringify(record.fields)).digest('hex'));
    assert.equal(record.fields.quantity, '1');
    assert.ok(valid(`envelope.v${version}`, output));
  });
}

const rejects = [
  ['operator opt-in absent', event => event, {}, 'unsupported'],
  ['owner mismatch', event => { event.owner_ref = 'user:other'; }, undefined, 'blocked'],
  ['session mismatch', event => { event.session_ref = 'browser:other'; }, undefined, 'session_conflict'],
  ['session missing', event => { delete event.session_ref; }, undefined, 'session_conflict'],
  ['session version mismatch', event => { event.expected_version = 2; }, undefined, 'session_conflict'],
  ['session version missing', event => { delete event.expected_version; }, undefined, 'session_conflict'],
  ['capability mismatch', event => { event.payload.capability_id = 'fixture.opencli.read'; }, undefined, 'blocked'],
  ['external source closed', event => { event.payload.fixture_only = false; event.payload.target.origin = 'https://example.invalid'; }, undefined, 'unsupported'],
  ['unowned localhost port', event => { event.payload.target.origin = 'http://localhost:9222'; }, undefined, 'blocked'],
  ['loopback ip does not connect', event => { event.payload.target.origin = 'http://127.0.0.1'; }, undefined, 'blocked'],
  ['URL query rejected', event => { event.payload.target.path = '/listings?token=synthetic-secret'; }, undefined, 'blocked'],
  ['path traversal rejected', event => { event.payload.target.path = '/listings/../seed'; }, undefined, 'blocked'],
  ['private blob branch closed', event => { event.refs = [{ blob_key: 'private/synthetic.age', sha256: 'a'.repeat(64) }]; }, undefined, 'unsupported'],
  ['expired absolute deadline', event => { event.deadline = '2026-01-01T00:00:00Z'; }, undefined, 'timeout'],
];
for (const [name, mutate, config = { localFixtureTest: true }, code] of rejects) test(`${name} rejects before I/O`, async () => {
  const event = eventFor(); mutate(event);
  let factories = 0;
  const output = await createHandler(config, () => { factories++; return fake(); })(event);
  assert.equal(output.payload.status, 'failed');
  assert.equal(output.payload.error.code, code);
  assert.equal(factories, 0);
  assert.ok(valid('envelope.v1', output));
});

test('Lambda time and cleanup reserve gate before I/O', async () => {
  let factories = 0;
  const handler = createHandler({ localFixtureTest: true }, () => { factories++; return fake(); });
  const output = await handler(eventFor(), { getRemainingTimeInMillis: () => 9050 });
  assert.equal(output.payload.error.code, 'timeout');
  assert.equal(factories, 0);
});

for (const [name, mutate] of [
  ['unknown properties', event => { event.payload.cookies = ['synthetic-secret']; }],
  ['oversize UTF-8 envelope', event => { event.refs = Array.from({ length: 16 }, () => ({ blob_key: 'x'.repeat(256), sha256: 'a'.repeat(64) })); event.payload.target.path = '/x'.repeat(1024); event.extra = 'é'.repeat(MAX_BYTES); }],
  ['unknown transport version', event => { event.schema_version = 3; }],
  ['invalid causation null', event => { event.causation_id = null; }],
  ['storageState forbidden', event => { event.payload.storageState = {}; }],
]) test(`${name} rejects malformed transport with static error`, async () => {
  const event = eventFor(); mutate(event);
  let factories = 0;
  await assert.rejects(createHandler({ localFixtureTest: true }, () => { factories++; })(event), error => error instanceof WorkerError && error.message === 'invalid_input');
  assert.equal(factories, 0);
});

test('useful partial and failed results stay valid and cleanup occurs', async () => {
  for (const query of [1, 2]) {
    const runtime = fake({ failQuery: query });
    const output = await createHandler({ localFixtureTest: true }, () => runtime)(eventFor('direct', 2, 2));
    assert.equal(output.payload.status, query === 1 ? 'failed' : 'partial');
    assert.equal(output.payload.records.length, query === 1 ? 0 : 3);
    assert.equal(output.payload.error.code, 'timeout');
    assert.equal(runtime.closed, 1);
    assert.ok(valid('envelope.v2', output));
  }
});
test('raw errors and oversized/malformed extraction do not leak or escape validation', async () => {
  for (const options of [
    { openError: new Error('https://private.invalid/?token=synthetic-secret ENV_SECRET stderr') },
    { rows: { stdout: 'synthetic-secret' } },
    { rows: [{ token: 'synthetic-secret' }] },
    { rows: Array.from({ length: 3 }, () => Object.fromEntries(Array.from({ length: 32 }, (_, i) => [`field_${i}`, 'x'.repeat(4096)]))) },
    { closeError: new Error('stderr synthetic-secret') },
  ]) {
    const runtime = fake(options);
    const output = await createHandler({ localFixtureTest: true }, () => runtime)(eventFor());
    assert.equal(output.payload.status, 'failed');
    assert.equal(output.payload.error.code, 'internal');
    assert.equal(output.payload.records.length, 0);
    assert.equal(runtime.closed, 1);
    assert.ok(valid('envelope.v1', output));
    assert.ok(Buffer.byteLength(JSON.stringify(output)) <= MAX_BYTES);
    assert.doesNotMatch(JSON.stringify(output), /synthetic-secret|ENV_SECRET|private\.invalid|stderr/);
  }
});
test('payload mutation cannot replace the validated owner or target during I/O', async () => {
  const event = eventFor();
  const runtime = fake();
  runtime.open = async () => { event.owner_ref = 'user:other'; event.payload.target.path = '/private'; };
  const output = await createHandler({ localFixtureTest: true }, () => runtime)(event);
  assert.equal(output.owner_ref, 'user:fixture-owner');
  assert.equal(output.payload.status, 'succeeded');
});
test('late open/read resources settle after cancellation and are cleaned before returning', async () => {
  for (const phase of ['open', 'read']) {
    let resources = 0, aborted = false, closed = false;
    const runtime = fake();
    runtime[phase] = async (...args) => {
      const signal = args.at(-1);
      await new Promise(resolve => setTimeout(resolve, 200));
      aborted = signal.aborted;
      resources++;
      if (phase === 'read') return listingsFor(1);
    };
    runtime.close = async () => { resources = 0; closed = true; };
    const output = await createHandler({ localFixtureTest: true }, () => runtime)(eventFor(), { getRemainingTimeInMillis: () => 9100 });
    assert.equal(output.payload.error.code, 'timeout');
    assert.equal(aborted, true);
    assert.equal(resources, 0);
    assert.equal(closed, true);
    assert.ok(output.payload.timings.cleanup_ms >= 50);
  }
});
test('cancelled or closed runtime releases late acquired resources before rejecting', async () => {
  for (const closed of [false, true]) {
    const controller = new AbortController();
    const runtime = new FixtureRuntime({});
    runtime.closed = closed;
    let resources = 0;
    const promise = new Promise(resolve => setTimeout(() => { resources++; resolve({}); }, 30));
    const pending = runtime.acquire(promise, async () => { resources--; }, controller.signal);
    controller.abort();
    await assert.rejects(pending, error => error.message === 'timeout');
    assert.equal(resources, 0);
  }
});
test('CLI malformed stdout, secret stderr, bounded buffers and timeout are static', async () => {
  for (const script of [
    "process.stderr.write('synthetic-secret');process.exit(2)",
    "process.stdout.write('bad json synthetic-secret')",
    "process.stdout.write('x'.repeat(40000))",
    "setTimeout(()=>{},10000)",
  ]) {
    const children = new Set();
    await assert.rejects(runCli(process.execPath, ['-e', script], { timeout: 100, env: {} }, children), error => error instanceof WorkerError && ['timeout', 'internal'].includes(error.message));
    assert.equal(children.size, 0);
  }
});
