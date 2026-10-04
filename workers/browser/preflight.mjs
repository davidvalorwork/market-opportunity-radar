import { WorkerError } from './schema.mjs';

export const FIXTURE_OWNER = 'user:fixture-owner';
export const FIXTURE_SESSION = 'browser:fixture';
export const CAPABILITIES = Object.freeze({ direct: 'fixture.playwright.read', opencli: 'fixture.opencli.read' });
export const CLEANUP_RESERVE_MS = 9000;

export function preflight(event, config, context, now = Date.now()) {
  const payload = event.payload;
  // Config belongs to the local host operator, never to the request.
  if (config.localFixtureTest !== true || payload.fixture_only !== true) throw new WorkerError('unsupported');
  if (event.owner_ref !== FIXTURE_OWNER || payload.capability_id !== CAPABILITIES[payload.mode]) throw new WorkerError('blocked');
  // This is an explicit fixture alias; all real sessions/private blobs remain closed.
  if (event.session_ref !== FIXTURE_SESSION || event.expected_version !== 1) throw new WorkerError('session_conflict');
  if (event.refs?.length) throw new WorkerError('unsupported');
  if (!['http://localhost', 'https://localhost'].includes(payload.target.origin) || payload.target.path !== '/listings') throw new WorkerError('blocked');
  const lambdaRemaining = context.getRemainingTimeInMillis?.() ?? 90000;
  const budget = Math.floor(Math.min(payload.deadline_ms, Date.parse(event.deadline) - now - CLEANUP_RESERVE_MS, lambdaRemaining - CLEANUP_RESERVE_MS));
  if (!Number.isFinite(budget) || budget < 100) throw new WorkerError('timeout');
  return budget;
}
