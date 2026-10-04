import chromium from '@sparticuz/chromium';
import { chromium as playwright } from 'playwright-core';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, copyFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startFixture } from './fixture.mjs';
import { immutableAssets } from './assets.mjs';
import { bounded, runCli, stopChildren, killChild } from './processes.mjs';
import { WorkerError } from './schema.mjs';
import { storageChecks } from './contract.mjs';

const here = dirname(fileURLToPath(import.meta.url));

async function portFor(profile, child, remaining) {
  while (remaining() > 0) {
    if (child.exitCode !== null || child.signalCode !== null) throw new WorkerError('internal');
    try {
      const port = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]);
      if (Number.isInteger(port) && port > 0 && port <= 65535) return port;
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new WorkerError('timeout');
}

export class FixtureRuntime {
  constructor(config) {
    this.config = config;
    this.children = new Set();
    this.processGroups = new Set();
    this.children.groups = this.processGroups;
    this.contexts = new Set();
    this.closed = false;
    this.blockedRequests = 0;
  }
  check(signal) {
    if (this.closed || signal?.aborted) throw new WorkerError('timeout');
  }
  async acquire(promise, release, signal) {
    const value = await promise;
    if (this.closed || signal?.aborted) {
      await bounded(release(value), 1000, 'internal');
      throw new WorkerError('timeout');
    }
    return value;
  }
  async context(state, remaining, signal) {
    this.check(signal);
    const context = await this.acquire(this.browser.newContext({ storageState: state, serviceWorkers: 'block' }), value => value.close(), signal);
    this.contexts.add(context);
    context.setDefaultTimeout(Math.min(10000, remaining()));
    context.setDefaultNavigationTimeout(Math.min(10000, remaining()));
    await context.route('**/*', route => {
      const target = new URL(route.request().url());
      const allowed = target.origin === this.fixture.origin && !target.username && !target.password &&
        ['/seed', '/listings', '/api/access', '/api/listings'].includes(target.pathname) && route.request().method() === 'GET';
      if (allowed) return route.continue();
      this.blockedRequests++;
      return route.abort('blockedbyclient');
    });
    await context.routeWebSocket('**/*', socket => { this.blockedRequests++; socket.close(); });
    this.check(signal);
    return context;
  }
  async open(remaining, signal) {
    this.check(signal);
    this.work = await this.acquire(mkdtemp(join(tmpdir(), 'radar-browser-')), value => rm(value, { recursive: true, force: true }), signal);
    this.fixture = await this.acquire(startFixture(), value => value.close(), signal);
    const profile = join(this.work, 'chromium');
    await mkdir(profile);
    this.check(signal);
    const binary = await immutableAssets();
    this.check(signal);
    remaining();
    const args = chromium.args.filter(arg => arg !== '--single-process');
    const child = spawn(binary, [...args, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
      '--disable-background-networking', '--disable-component-update', '--disable-sync',
      `--user-data-dir=${profile}`, 'about:blank'], {
      detached: process.platform !== 'win32', stdio: 'ignore', env: {
        PATH: process.env.PATH, LD_LIBRARY_PATH: process.env.LD_LIBRARY_PATH,
        FONTCONFIG_PATH: '/opt/fonts', HOME: this.work, TMPDIR: tmpdir(),
        XDG_CACHE_HOME: join(this.work, 'cache'), XDG_CONFIG_HOME: join(this.work, 'config'),
      },
    });
    this.children.add(child);
    this.processGroups.add(child.pid);
    const abort = () => { if (child.exitCode === null && child.signalCode === null) killChild(child); };
    signal.addEventListener('abort', abort, { once: true });
    child.once('exit', () => signal.removeEventListener('abort', abort));
    child.once('error', () => {});
    await bounded(once(child, 'spawn'), Math.min(3000, remaining()));
    this.port = await portFor(profile, child, remaining);
    this.check(signal);
    this.browser = await this.acquire(playwright.connectOverCDP(`http://127.0.0.1:${this.port}`, { timeout: Math.min(10000, remaining()) }), value => value.close(), signal);
    // Only our own synthetic state is created and transferred, entirely in memory.
    const source = await this.context(undefined, remaining, signal);
    const page = await source.newPage();
    this.check(signal);
    await page.goto(`${this.fixture.origin}/seed`, { waitUntil: 'domcontentloaded' });
    await page.locator('html[data-fixture-ready="true"]').waitFor();
    this.check(signal);
    const probe = await page.evaluate(() => window.__fixtureProbe);
    if (!probe?.cookie || !probe?.localStorage || !probe?.indexedDB) throw new WorkerError('needs_reauth');
    this.state = await source.storageState({ indexedDB: true });
    this.check(signal);
    if (Buffer.byteLength(JSON.stringify(this.state)) > 65536 || this.state.origins.some(origin => origin.origin !== this.fixture.origin) || this.state.cookies.some(cookie => cookie.domain !== '127.0.0.1')) throw new WorkerError('internal');
    await source.close();
    this.check(signal);
    this.contexts.delete(source);
    if (this.config.fixtureState === 'cookies_only') this.state = { cookies: this.state.cookies, origins: [] };
    if (this.config.fixtureState === 'none') this.state = undefined;
    this.home = join(this.work, 'opencli-home');
    const adapters = join(this.home, '.opencli', 'clis', 'radar-fixture');
    await mkdir(adapters, { recursive: true });
    this.check(signal);
    await copyFile(join(here, 'opencli-fixture.js'), join(adapters, 'extract.js'));
    this.check(signal);
  }
  async read(query, mode, remaining, signal) {
    this.check(signal);
    const restored = await this.context(this.state, remaining, signal);
    try {
      const page = await restored.newPage();
      this.check(signal);
      const pageUrl = `${this.fixture.origin}/listings?query=${query}`;
      await page.goto(pageUrl, { waitUntil: 'domcontentloaded' });
      await page.locator('html[data-fixture-ready="true"]').waitFor();
      this.check(signal);
      if (!storageChecks(await page.evaluate(() => window.__fixtureProbe))) throw new WorkerError('needs_reauth');
      if (this.config.fixtureExternalProbe === true) {
        // The route guard must reject these before a socket/DNS lookup; no external server.
        await page.evaluate(() => fetch('https://fixture-egress.invalid/private?token=synthetic-secret').catch(() => undefined));
      }
      if (mode === 'direct') return await page.locator('[data-testid="listing"]').evaluateAll(rows => rows.slice(0, 3).map(row => JSON.parse(row.dataset.record)));
      const session = await restored.newCDPSession(page);
      const { targetInfo } = await session.send('Target.getTargetInfo');
      await session.detach();
      if (targetInfo.type !== 'page' || targetInfo.url !== pageUrl || !/^[A-Fa-f0-9]+$/.test(targetInfo.targetId)) throw new WorkerError('internal');
      const endpoint = `ws://127.0.0.1:${this.port}/devtools/page/${targetInfo.targetId}`;
      this.check(signal);
      return await runCli(process.execPath, [join(here, 'node_modules', '@jackwener', 'opencli', 'dist', 'src', 'main.js'), 'radar-fixture', 'extract', '-f', 'json'], {
        timeout: Math.min(12000, remaining()), signal, env: {
          PATH: process.env.PATH, HOME: this.home, TMPDIR: tmpdir(), CI: '1',
          OPENCLI_CDP_ENDPOINT: endpoint, RADAR_FIXTURE_PAGE: pageUrl,
          OPENCLI_BROWSER_CONNECT_TIMEOUT: '5', OPENCLI_BROWSER_COMMAND_TIMEOUT: '10',
        },
      }, this.children);
    } finally {
      await bounded(restored.close(), 1500, 'internal');
      this.contexts.delete(restored);
    }
  }
  async close() {
    this.closed = true;
    let failed = false;
    for (const context of this.contexts) {
      try { await bounded(context.close(), 1000, 'internal'); } catch { failed = true; }
    }
    this.contexts.clear();
    try { if (this.browser) await bounded(this.browser.close(), 1500, 'internal'); } catch { failed = true; }
    try { await stopChildren(this.children); } catch { failed = true; }
    try { if (this.fixture) await bounded(this.fixture.close(), 1000, 'internal'); } catch { failed = true; }
    this.state = undefined;
    try { if (this.work) await bounded(rm(this.work, { recursive: true, force: true }), 1000, 'internal'); } catch { failed = true; }
    if (failed) throw new WorkerError('internal');
  }
}
