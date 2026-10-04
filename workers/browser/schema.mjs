import { readdirSync, readFileSync } from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

export const MAX_BYTES = 32768;
const root = new URL('../../src/radar/schemas/', import.meta.url);
const ajv = new Ajv2020({ allErrors: true, strict: true, strictTypes: false, strictRequired: false });
addFormats(ajv, ['date', 'date-time']);
for (const name of readdirSync(root).filter(name => /\.v\d+\.json$/.test(name))) {
  ajv.addSchema(JSON.parse(readFileSync(new URL(name, root), 'utf8')));
}
export function valid(name, value) {
  try {
    return Buffer.byteLength(JSON.stringify(value)) <= MAX_BYTES &&
      ajv.getSchema(`https://market-opportunity-radar.invalid/contracts/${name}.json`)(value) === true;
  } catch { return false; }
}

export class WorkerError extends Error {
  constructor(code) { super(code); this.code = code; }
}
export function requireEnvelope(input) {
  if (![1, 2].includes(input?.schema_version) || !valid(`envelope.v${input.schema_version}`, input) || input.kind !== 'browser.read') throw new WorkerError('invalid_input');
  // Own immutable JSON snapshot: later changes by the caller cannot replace bindings.
  return JSON.parse(JSON.stringify(input));
}
