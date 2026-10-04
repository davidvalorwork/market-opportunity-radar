import { inflate } from '@sparticuz/chromium';
import { cp, mkdir, readdir, stat, chmod, readFile, writeFile } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

// Build-time only: Docker's ordinary build capabilities can unpack tar ownership.
// The restricted runtime never calls inflate or writes executable public assets.
const here = dirname(fileURLToPath(import.meta.url));
const bin = join(here, 'node_modules', '@sparticuz', 'chromium', 'bin');
await Promise.all(['chromium.br', 'fonts.tar.br', 'swiftshader.tar.br', 'al2023.tar.br'].map(name => inflate(join(bin, name))));
const assetDir = '/opt/radar-chromium';
await mkdir(assetDir, { recursive: true });
await cp(join(tmpdir(), 'chromium'), join(assetDir, 'chromium'));
await cp(join(tmpdir(), 'fonts'), '/opt/fonts', { recursive: true });
await cp(join(tmpdir(), 'al2023'), '/opt/al2023', { recursive: true });
// SwiftShader must live beside the binary; determine the shipped files after
// extraction rather than assuming the pack contains a fixed historical list.
const graphicsFiles = (await readdir(tmpdir())).filter(name => /^(?:lib[^/]+\.so(?:\.\d+)*|vk_swiftshader_icd\.json)$/.test(name));
if (!graphicsFiles.includes('libGLESv2.so')) throw new Error('build_graphics_assets_missing');
for (const name of graphicsFiles) await cp(join(tmpdir(), name), join(assetDir, name));
const executable = join(assetDir, 'chromium');
await chmod(executable, 0o755);
await stat('/opt/fonts/fonts.conf');
await stat('/opt/al2023/lib');
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const manifest = {
  schema_version: 1, executable, chromium_sha256: hash.digest('hex'),
  binary_bytes: (await stat(executable)).size,
  package_version: JSON.parse(await readFile(join(here, 'node_modules', '@sparticuz', 'chromium', 'package.json'), 'utf8')).version,
  graphics_files: graphicsFiles,
  fontconfig_path: '/opt/fonts', library_path: '/opt/al2023/lib',
};
await writeFile(join(assetDir, 'assets.json'), `${JSON.stringify(manifest)}\n`, { mode: 0o644 });
console.log(JSON.stringify({ status: 'public_assets_prepared', ...manifest }));
