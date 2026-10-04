import { randomUUID } from 'node:crypto';
import { CAPABILITIES, FIXTURE_OWNER, FIXTURE_SESSION } from './preflight.mjs';

export function eventFor(mode = 'direct', version = 1, batch = 1) {
  return {
    schema_version: version, message_id: randomUUID(), operation_id: randomUUID(), correlation_id: randomUUID(),
    owner_ref: FIXTURE_OWNER, kind: 'browser.read', deadline: new Date(Date.now() + 90000).toISOString(), attempt: 1,
    session_ref: FIXTURE_SESSION, expected_version: 1,
    payload: { schema_version: 1, capability_id: CAPABILITIES[mode], target: { origin: 'https://localhost', path: '/listings' }, fixture_only: true, mode, batch, deadline_ms: 60000 },
  };
}
