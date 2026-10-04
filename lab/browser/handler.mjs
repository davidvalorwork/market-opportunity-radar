import chromium from '@sparticuz/chromium';
import { chromium as playwright } from 'playwright-core';
import { spawn, execFile } from 'node:child_process';
import { mkdtemp, readFile, mkdir, copyFile, rm, access, stat } from 'node:fs/promises';
import { createReadStream, constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { validateEvent, listingsFor, storageChecks } from './contract.mjs';
import { startFixture } from './fixture.mjs';
import { safeDiagnostic } from './diagnostics.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const ms = start => Math.round((performance.now() - start) * 100) / 100;
const packageVersion = async name => JSON.parse(await readFile(join(here, 'node_modules', name, 'package.json'), 'utf8')).version;
const bounded = async (promise, timeout, code) => {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(code)), timeout); })]);
  } finally { clearTimeout(timer); }
};

async function hashFile(path) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}

async function chromiumPort(profile, child, remaining) {
  while (remaining() > 0) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error('chromium_exited_during_startup');
    try {
      const [value] = (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n');
      const port = Number(value);
      if (Number.isInteger(port) && port > 0 && port <= 65535) return port;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('deadline_exceeded');
}

async function protectedContext(browser, origin, storageState, remaining, checks) {
  const context = await browser.newContext({ storageState, serviceWorkers: 'block' });
  context.setDefaultTimeout(Math.min(10000, remaining()));
  context.setDefaultNavigationTimeout(Math.min(10000, remaining()));
  await context.route('**/*', route => {
    const target = new URL(route.request().url());
    if (target.origin === origin && !target.username && !target.password) return route.continue();
    checks.external_requests_blocked += 1;
    return route.abort('blockedbyclient');
  });
  return context;
}

async function runOpencli(endpoint, pageUrl, home, remaining, children) {
  const cli = join(here, 'node_modules', '@jackwener', 'opencli', 'dist', 'src', 'main.js');
  return new Promise((resolve, reject) => {
    const child = execFile(process.execPath, [cli, 'radar-fixture', 'extract', '-f', 'json'], {
      timeout: Math.min(12000, remaining()), killSignal: 'SIGKILL', maxBuffer: 65536,
      env: {
        PATH: process.env.PATH, HOME: home, TMPDIR: tmpdir(), CI: '1',
        OPENCLI_CDP_ENDPOINT: endpoint, RADAR_FIXTURE_PAGE: pageUrl,
        OPENCLI_BROWSER_CONNECT_TIMEOUT: '5', OPENCLI_BROWSER_COMMAND_TIMEOUT: '10',
      },
    }, (error, stdout) => {
      children.delete(child);
      if (error) return reject(new Error(error.killed ? 'opencli_timeout' : 'opencli_execution_failed'));
      try { resolve(JSON.parse(stdout)); } catch { reject(new Error('opencli_invalid_json')); }
    });
    children.add(child);
  });
}

export async function handler(input, lambdaContext = {}) {
  const start = performance.now();
  const result = {
    schema_version: 1, suite: 'browser', mode: 'direct', status: 'failed',
    timings: { init_ms: 0, execution_ms: 0, cleanup_ms: 0, total_ms: 0 },
    checks: { fixture_only: true, external_requests_blocked: 0, cleanup_complete: false },
    useful_records: 0, records: [], versions: { node: process.versions.node, architecture: process.arch },
  };
  let event;
  try {
    event = validateEvent(input);
    result.mode = event.mode;
  } catch (error) {
    result.error = { code: error.message };
    result.timings.total_ms = ms(start);
    return result;
  }
  result.config = { batch: event.batch, repeats: event.repeats, session_mode: event.session_mode, fixture_only: true };
  let fixture, browser, work, watchdog;
  let executionStart;
  let phase = 'initialization';
  let startupChild;
  let startupStderr = '';
  let captureStartupStderr = true;
  const startupDiagnostics = {};
  const children = new Set();
  const budget = Math.min(event.deadline_ms, Math.max(1, (lambdaContext.getRemainingTimeInMillis?.() ?? 90000) - 10000));
  const remaining = () => {
    const value = Math.floor(budget - (performance.now() - start));
    if (value <= 0) throw new Error('deadline_exceeded');
    return value;
  };
  try {
    phase = 'work_directory';
    work = await mkdtemp(join(tmpdir(), 'radar-browser-'));
    startupDiagnostics.tmp_writable = await access(tmpdir(), constants.W_OK).then(() => true, () => false);
    startupDiagnostics.home_writable = process.env.HOME ? await access(process.env.HOME, constants.W_OK).then(() => true, () => false) : null;
    phase = 'fixture_start';
    fixture = await startFixture();
    const profile = join(work, 'chromium');
    phase = 'profile_directory';
    await mkdir(profile);
    phase = 'immutable_assets';
    // No extraction in the cap-drop/read-only runtime; incomplete warm /tmp
    // binaries are irrelevant. This path is populated atomically by image build.
    const binary = '/opt/radar-chromium/chromium';
    const assets = JSON.parse(await readFile('/opt/radar-chromium/assets.json', 'utf8'));
    if (assets.schema_version !== 1 || assets.executable !== binary) throw new Error('immutable_asset_manifest_invalid');
    startupDiagnostics.binary_executable = await access(binary, constants.X_OK).then(() => true, () => false);
    startupDiagnostics.binary_bytes = (await stat(binary)).size;
    if (!startupDiagnostics.binary_executable || startupDiagnostics.binary_bytes !== assets.binary_bytes) throw new Error('immutable_assets_invalid');
    phase = 'runtime_versions';
    const versions = await Promise.all([
      packageVersion('@sparticuz/chromium'), packageVersion('playwright-core'), packageVersion('@jackwener/opencli'), hashFile(binary),
    ]);
    Object.assign(result.versions, { sparticuz: versions[0], playwright: versions[1], opencli: versions[2], chromium_sha256: versions[3] });
    if (versions[3] !== assets.chromium_sha256 || versions[0] !== assets.package_version) throw new Error('immutable_assets_hash_mismatch');
    result.fixture_sha256 = createHash('sha256').update(JSON.stringify(Array.from({ length: 10 }, (_, i) => listingsFor(i + 1)))).digest('hex');
    // Independent seeded/restored contexts require separate renderer processes.
    // The recommended single-process flag closed new pages in the measured lab.
    // Retain all other package flags; CDP remains loopback with an ephemeral port.
    const launchArgs = chromium.args.filter(argument => argument !== '--single-process');
    phase = 'chromium_spawn';
    const child = spawn(binary, [
      ...launchArgs, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
      '--disable-background-networking', '--disable-component-update', '--disable-sync',
      `--user-data-dir=${profile}`, 'about:blank',
    ], { stdio: ['ignore', 'ignore', 'pipe'], env: { ...process.env, HOME: work, XDG_CACHE_HOME: join(work, 'cache'), XDG_CONFIG_HOME: join(work, 'config') } });
    startupChild = child;
    startupDiagnostics.single_process = launchArgs.includes('--single-process');
    child.stderr.on('data', chunk => {
      if (captureStartupStderr && Buffer.byteLength(startupStderr) < 8192) startupStderr += chunk.toString('utf8').slice(0, 8192 - startupStderr.length);
    });
    children.add(child);
    child.once('error', () => {});
    watchdog = setTimeout(() => { for (const process of children) process.kill('SIGKILL'); }, remaining());
    await bounded(once(child, 'spawn'), Math.min(3000, remaining()), 'deadline_exceeded');
    phase = 'cdp_port';
    const port = await chromiumPort(profile, child, remaining);
    const cdpOrigin = `http://127.0.0.1:${port}`;
    phase = 'cdp_connect';
    browser = await playwright.connectOverCDP(cdpOrigin, { timeout: Math.min(10000, remaining()) });
    captureStartupStderr = false;
    result.versions.chromium = browser.version();
    phase = 'seed_storage';
    const source = await protectedContext(browser, fixture.origin, undefined, remaining, result.checks);
    const seeded = await source.newPage();
    await seeded.goto(`${fixture.origin}/seed`, { waitUntil: 'domcontentloaded' });
    await seeded.locator('html[data-fixture-ready="true"]').waitFor();
    const seedProbe = await seeded.evaluate(() => window.__fixtureProbe);
    result.checks.seeded_all_storage = Object.values(seedProbe).every(value => value === true);
    const exported = await source.storageState({ indexedDB: true });
    const stateBytes = Buffer.byteLength(JSON.stringify(exported));
    if (stateBytes > 65536 || exported.origins.some(value => value.origin !== fixture.origin) || exported.cookies.some(cookie => cookie.domain !== '127.0.0.1')) throw new Error('storage_state_scope_or_size_rejected');
    result.checks.storage_state_bytes = stateBytes;
    await source.close();
    let state = event.session_mode === 'full' ? exported : event.session_mode === 'cookies_only' ? { cookies: exported.cookies, origins: [] } : undefined;
    let home;
    if (event.mode === 'opencli') {
      home = join(work, 'opencli-home');
      const adapters = join(home, '.opencli', 'clis', 'radar-fixture');
      await mkdir(adapters, { recursive: true });
      await copyFile(join(here, 'opencli-fixture.js'), join(adapters, 'extract.js'));
    }
    result.timings.init_ms = ms(start);
    executionStart = performance.now();
    result.checks.runs = [];
    for (let repeat = 1; repeat <= event.repeats; repeat += 1) {
      phase = 'restore_storage';
      remaining();
      const restored = await protectedContext(browser, fixture.origin, state, remaining, result.checks);
      try {
        const page = await restored.newPage();
        for (let query = 1; query <= event.batch; query += 1) {
          remaining();
          const pageUrl = `${fixture.origin}/listings?query=${query}`;
          await page.goto(pageUrl, { waitUntil: 'domcontentloaded' });
          await page.locator('html[data-fixture-ready="true"]').waitFor();
          const probe = await page.evaluate(() => window.__fixtureProbe);
          const storage = storageChecks(probe);
          const run = { repeat, query, ...storage, exact_records: false };
          result.checks.runs.push(run);
          if (!Object.values(storage).every(Boolean)) continue;
          let records;
          if (event.mode === 'opencli') {
            phase = 'opencli_extract';
            const session = await restored.newCDPSession(page);
            const { targetInfo } = await session.send('Target.getTargetInfo');
            await session.detach();
            if (targetInfo.type !== 'page' || targetInfo.url !== pageUrl || !/^[A-Fa-f0-9]+$/.test(targetInfo.targetId)) throw new Error('cdp_target_mismatch');
            const endpoint = `ws://127.0.0.1:${port}/devtools/page/${targetInfo.targetId}`;
            records = await runOpencli(endpoint, pageUrl, home, remaining, children);
            run.explicit_cdp_target = true;
          } else {
            phase = 'direct_extract';
            records = await page.locator('[data-testid="listing"]').evaluateAll(rows => rows.slice(0, 3).map(row => JSON.parse(row.dataset.record)));
          }
          run.exact_records = JSON.stringify(records) === JSON.stringify(listingsFor(query));
          if (run.exact_records) {
            result.useful_records += records.length;
            result.records.push({ repeat, query, listings: records });
          }
        }
      } finally { await bounded(restored.close(), Math.min(5000, remaining()), 'context_cleanup_timeout'); }
    }
    state = undefined;
    result.checks.expected_useful_records = event.batch * event.repeats * 3;
    result.status = result.checks.seeded_all_storage && result.useful_records === result.checks.expected_useful_records ? 'passed' : 'failed';
    if (result.status === 'failed') result.error = { code: 'portable_session_or_extraction_failed' };
  } catch (error) {
    // Diagnostics contain startup stderr only, collected before opening the
    // synthetic fixture. Never return state, cookie values, env, or stack traces.
    const known = /^(deadline_exceeded|chromium_exited_during_startup|storage_state_scope_or_size_rejected|opencli_timeout|opencli_execution_failed|opencli_invalid_json|cdp_target_mismatch|context_cleanup_timeout|immutable_asset_manifest_invalid|immutable_assets_invalid|immutable_assets_hash_mismatch)$/;
    result.error = { code: known.test(error.message) ? error.message : 'browser_lab_execution_failed', type: error.name, phase,
      diagnostic: {
        message: safeDiagnostic(error.message), errno: typeof error.code === 'string' ? error.code : null,
        ...startupDiagnostics,
        chromium_exit_code: startupChild?.exitCode ?? null, chromium_signal: startupChild?.signalCode ?? null,
        chromium_startup_stderr: safeDiagnostic(startupStderr),
      },
    };
    result.status = 'failed';
  } finally {
    if (executionStart !== undefined) result.timings.execution_ms = ms(executionStart);
    else result.timings.init_ms = ms(start);
    clearTimeout(watchdog);
    const cleanupStart = performance.now();
    let clean = true;
    try { if (browser) await bounded(browser.close(), 3000, 'browser_cleanup_timeout'); } catch { clean = false; }
    for (const child of children) {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill('SIGKILL');
        try { await bounded(once(child, 'exit'), 2000, 'child_cleanup_timeout'); } catch { clean = false; }
      }
    }
    try { if (fixture) await bounded(fixture.close(), 2000, 'fixture_cleanup_timeout'); } catch { clean = false; }
    try { if (work) await rm(work, { recursive: true, force: true }); } catch { clean = false; }
    result.checks.cleanup_complete = clean;
    if (!clean) { result.status = 'failed'; result.error ??= { code: 'cleanup_failed' }; }
    result.bytes_io = fixture?.bytes ?? 0;
    result.timings.cleanup_ms = ms(cleanupStart);
    result.timings.total_ms = ms(start);
  }
  if (Buffer.byteLength(JSON.stringify(result)) > 65536) {
    result.records = [];
    result.status = 'failed';
    result.error = { code: 'result_size_exceeded' };
  }
  return result;
}
