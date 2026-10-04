import { createHash, randomUUID } from 'node:crypto';
import { valid, WorkerError } from './schema.mjs';

export const VERSIONS = Object.freeze({ node: process.versions.node, playwright: '1.63.0', sparticuz: '153.0.0', opencli: '1.8.8', ajv: '8.20.0' });
const CODES = new Set(['needs_reauth', 'unsupported', 'rate_limited', 'blocked', 'empty_verified', 'timeout', 'session_conflict', 'invalid_input', 'budget_exhausted', 'internal']);
export const errorCode = error => error instanceof WorkerError && CODES.has(error.code) ? error.code : 'internal';

export function recordsFromObserved(rows, query, observedAt) {
  if (!Array.isArray(rows) || rows.length !== 3) throw new WorkerError('internal');
  return rows.map(fields => {
    const content_hash = createHash('sha256').update(JSON.stringify(fields), 'utf8').digest('hex');
    const record = { fields, evidence: { url: `https://localhost/listings?query=${query}`, observed_at: observedAt, content_hash } };
    if (!valid('browser.result.v1', { schema_version: 1, status: 'succeeded', records: [record], timings: { total_ms: 0 }, versions: VERSIONS })) throw new WorkerError('internal');
    return record;
  });
}

export function resultEnvelope(event, payload) {
  const result = {
    schema_version: event.schema_version, message_id: randomUUID(), operation_id: event.operation_id,
    correlation_id: event.correlation_id, causation_id: event.message_id,
    owner_ref: event.owner_ref, kind: 'browser.result', deadline: event.deadline,
    attempt: event.attempt, payload,
    ...(event.session_ref !== undefined ? { session_ref: event.session_ref } : {}),
    ...(event.expected_version !== undefined ? { expected_version: event.expected_version } : {}),
  };
  if (!valid(`envelope.v${event.schema_version}`, result)) {
    result.payload = { schema_version: 1, status: 'failed', records: [], error: { code: 'internal' }, timings: payload.timings, versions: VERSIONS };
  }
  if (!valid(`envelope.v${event.schema_version}`, result)) throw new WorkerError('internal');
  return result;
}
