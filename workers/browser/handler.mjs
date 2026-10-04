import { performance } from 'node:perf_hooks';
import { requireEnvelope, WorkerError } from './schema.mjs';
import { preflight } from './preflight.mjs';
import { bounded } from './processes.mjs';
import { VERSIONS, recordsFromObserved, resultEnvelope, errorCode } from './result.mjs';

const ms = start => Math.max(0, Math.round(performance.now() - start));

export function createHandler(config = {}, runtimeFactory) {
  const options = Object.freeze({ ...config });
  return async function handle(input, lambdaContext = {}) {
    const start = performance.now();
    // Invalid transport cannot create a correlated envelope. No I/O or raw error data.
    const event = requireEnvelope(input);
    const payload = { schema_version: 1, status: 'failed', records: [], timings: { total_ms: 0 }, versions: VERSIONS };
    let runtime, pending;
    const controller = new AbortController();
    let deadlineTimer;
    try {
      const budget = preflight(event, options, lambdaContext);
      const remaining = () => {
        if (controller.signal.aborted) throw new WorkerError('timeout');
        const value = Math.floor(budget - (performance.now() - start));
        if (value <= 0) throw new WorkerError('timeout');
        return value;
      };
      const factory = runtimeFactory ?? (async config => {
        const { FixtureRuntime } = await import('./runtime.mjs');
        return new FixtureRuntime(config);
      });
      runtime = await factory(options);
      deadlineTimer = setTimeout(() => controller.abort(), remaining());
      pending = runtime.open(remaining, controller.signal);
      await bounded(pending, remaining());
      pending = undefined;
      payload.timings.init_ms = ms(start);
      const executionStart = performance.now();
      for (let query = 1; query <= event.payload.batch; query++) {
        pending = runtime.read(query, event.payload.mode, remaining, controller.signal);
        const rows = await bounded(pending, remaining());
        pending = undefined;
        payload.records.push(...recordsFromObserved(rows, query, new Date().toISOString()));
      }
      payload.timings.execution_ms = ms(executionStart);
      payload.status = 'succeeded';
    } catch (error) {
      payload.status = payload.records.length ? 'partial' : 'failed';
      payload.error = { code: errorCode(error) };
    } finally {
      clearTimeout(deadlineTimer);
      if (payload.error?.code === 'timeout') controller.abort();
      const cleanupStart = performance.now();
      if (runtime) {
        let cleanupFailed = false;
        if (pending) {
          try { await bounded(Promise.resolve(pending).catch(() => undefined), 2000, 'internal'); }
          catch { cleanupFailed = true; }
        }
        try {
          // Allow cooperative abort to settle before cleanup. Acquisitions in the
          // runtime release late resources themselves when the signal is aborted.
          await bounded(runtime.close(), 5500, 'internal');
        }
        catch { cleanupFailed = true; }
        if (cleanupFailed) { payload.status = 'failed'; payload.records = []; payload.error = { code: 'internal' }; }
      }
      payload.timings.cleanup_ms = ms(cleanupStart);
      payload.timings.total_ms = ms(start);
    }
    return resultEnvelope(event, payload);
  };
}

// AWS/default execution remains closed; only operator opt-in starts synthetic fixtures.
export const handler = createHandler({ localFixtureTest: process.env.RADAR_LOCAL_FIXTURE_TEST === '1' });
