// Explicit Linux-only suite: run in the restricted Docker profile, never skip.
import test from 'node:test';
import assert from 'node:assert/strict';
import { liveGroupMembers, runCli } from './processes.mjs';

assert.equal(process.platform, 'linux', 'This suite verifies Linux process groups in Docker');
for (const timeout of [false, true]) test(`own CLI descendant cleanup after ${timeout ? 'timeout' : 'leader exit'}`, { timeout: 8000 }, async () => {
  const children = new Set();
  children.groups = new Set();
  const script = "require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},10000)'],{stdio:'ignore'});" +
    (timeout ? "setInterval(()=>{},10000)" : "process.stdout.write('[]');process.exit(0)");
  if (timeout) await assert.rejects(runCli(process.execPath, ['-e', script], { timeout: 300, env: {} }, children));
  else assert.deepEqual(await runCli(process.execPath, ['-e', script], { timeout: 3000, env: {} }, children), []);
  for (const group of children.groups) assert.deepEqual(await liveGroupMembers(group), []);
  assert.equal(children.size, 0);
});
