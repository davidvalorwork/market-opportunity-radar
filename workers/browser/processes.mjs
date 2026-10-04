import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { readdir, readFile } from 'node:fs/promises';
import { WorkerError } from './schema.mjs';

export async function bounded(promise, milliseconds, code = 'timeout') {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new WorkerError(code)), Math.max(1, milliseconds));
    })]);
  } finally { clearTimeout(timer); }
}
export async function stopChildren(children) {
  for (const child of children) {
    const exit = child.exitCode === null && child.signalCode === null ? once(child, 'exit') : undefined;
    await stopGroup(child);
    if (exit) await bounded(exit, 2000, 'internal');
  }
}
export async function liveGroupMembers(group) {
  if (process.platform === 'win32' || !Number.isInteger(group) || group < 1) return [];
  const members = [];
  for (const name of (await readdir('/proc')).filter(name => /^[0-9]+$/.test(name))) {
    try {
      const stat = await readFile(`/proc/${name}/stat`, 'utf8');
      const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
      // Our detached children each own both process group and session. Zombies
      // are exited processes awaiting PID 1; never count them as running effects.
      if (Number(fields[2]) === group && Number(fields[3]) === group && fields[0] !== 'Z') members.push(Number(name));
    } catch (error) { if (error.code !== 'ENOENT' && error.code !== 'ESRCH') throw error; }
  }
  return members;
}
export async function stopGroup(child) {
  if (process.platform === 'win32') {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    return;
  }
  if ((await liveGroupMembers(child.pid)).length) killChild(child);
  const deadline = Date.now() + 1500;
  while ((await liveGroupMembers(child.pid)).length) {
    if (Date.now() >= deadline) throw new WorkerError('internal');
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}
export function killChild(child) {
  // On Linux each child owns a process group, including Chromium renderers.
  if (process.platform !== 'win32' && child.pid) {
    try { process.kill(-child.pid, 'SIGKILL'); return; } catch (error) {
      if (error.code !== 'ESRCH') throw error;
    }
  }
  child.kill('SIGKILL');
}
// args are operator-owned arrays; no shell, startup scripts, inherited secrets or stderr output.
export function runCli(executable, args, options, children) {
  return new Promise((resolve, reject) => {
    const child = execFile(executable, args, {
      ...options, detached: process.platform !== 'win32', shell: false, maxBuffer: 32768, killSignal: 'SIGKILL', encoding: 'utf8',
    }, async (error, stdout) => {
      try {
        const exit = child.exitCode === null && child.signalCode === null ? once(child, 'exit') : undefined;
        await stopGroup(child);
        if (exit) await bounded(exit, 2000, 'internal');
      } catch { return reject(new WorkerError('internal')); }
      children.delete(child);
      if (error) return reject(new WorkerError(error.killed || error.name === 'AbortError' ? 'timeout' : 'internal'));
      try {
        const output = JSON.parse(stdout);
        if (!Array.isArray(output)) throw new Error();
        resolve(output);
      } catch { reject(new WorkerError('internal')); }
    });
    children.add(child);
    children.groups?.add(child.pid);
  });
}
