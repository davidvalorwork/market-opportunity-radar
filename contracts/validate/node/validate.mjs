// Validate contracts/examples golden files (and capabilities.json) with Ajv draft 2020-12.
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const MAX_BYTES = 32768;
const contracts = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const schemas = join(contracts, '..', 'src', 'radar', 'schemas'); // single source, also shipped in the Python wheel
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));
const ajv = new Ajv2020({ allErrors: true, strict: true, strictTypes: false, strictRequired: false });
addFormats(ajv, ['date', 'date-time']);
const names = readdirSync(schemas).filter(name => /\.v\d+\.json$/.test(name)).map(name => name.slice(0, -5)).sort();
for (const name of names) ajv.addSchema(readJson(join(schemas, `${name}.json`)));

function check(name, instance) {
  const size = Buffer.byteLength(JSON.stringify(instance), 'utf8');
  if (size > MAX_BYTES) return `serialized size ${size} exceeds ${MAX_BYTES} bytes`;
  const validate = ajv.getSchema(`https://market-opportunity-radar.invalid/contracts/${name}.json`);
  return validate(instance) ? null : ajv.errorsText(validate.errors);
}

let failures = 0;
let total = 0;
function expect(label, name, instance, valid) {
  total += 1;
  const error = check(name, instance);
  if ((error === null) !== valid) {
    failures += 1;
    console.log(`FAIL ${label}: expected ${valid ? 'valid' : 'invalid'}${error ? ` (${error})` : ''}`);
  }
}

for (const name of names.filter(name => name !== 'common.v1')) {
  for (const group of ['valid', 'invalid']) {
    const folder = join(contracts, 'examples', group, name);
    const files = readdirSync(folder).filter(file => file.endsWith('.json')).sort();
    if (files.length < (group === 'valid' ? 2 : 3)) { failures += 1; console.log(`FAIL ${group}/${name}: too few examples`); }
    for (const file of files) expect(`${group}/${name}/${file}`, name, readJson(join(folder, file)), group === 'valid');
  }
}
expect('capabilities.json', 'capabilities.v1', readJson(join(contracts, 'capabilities.json')), true);

console.log(`node/ajv: ${total - failures}/${total} examples behaved as expected across ${names.length} schemas`);
process.exit(failures ? 1 : 0);
