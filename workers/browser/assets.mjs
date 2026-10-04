import { createReadStream, constants } from 'node:fs';
import { readFile, stat, access } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { WorkerError } from './schema.mjs';

export async function immutableAssets() {
  const binary = '/opt/radar-chromium/chromium';
  const manifest = JSON.parse(await readFile('/opt/radar-chromium/assets.json', 'utf8'));
  const info = await stat(binary);
  if (manifest.schema_version !== 1 || manifest.executable !== binary || manifest.package_version !== '153.0.0' || info.size !== manifest.binary_bytes || (info.mode & 0o222)) throw new WorkerError('internal');
  await access(binary, constants.X_OK);
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(binary)) hash.update(chunk);
  if (hash.digest('hex') !== manifest.chromium_sha256) throw new WorkerError('internal');
  return binary;
}
