import { request } from 'node:http';
import { validateEvent } from './contract.mjs';

// Designed for docker exec -i with --network none; stdout contains only one JSON.
try {
  let bytes = 0;
  const chunks = [];
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    if (bytes > 4096) throw new Error('event_size_exceeded');
    chunks.push(chunk);
  }
  const event = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  validateEvent(event);
  const payload = JSON.stringify(event);
  const output = await new Promise((resolve, reject) => {
    const req = request('http://127.0.0.1:8080/2015-03-31/functions/function/invocations', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
    }, res => {
      let size = 0;
      const body = [];
      res.on('data', chunk => {
        size += chunk.length;
        if (size > 65536) return req.destroy(new Error('result_size_exceeded'));
        body.push(chunk);
      });
      res.on('error', reject);
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new Error('rie_http_failed'));
        try { resolve(JSON.parse(Buffer.concat(body).toString('utf8'))); } catch { reject(new Error('rie_invalid_json')); }
      });
    });
    req.setTimeout(85000, () => req.destroy(new Error('rie_timeout')));
    req.on('error', reject);
    req.end(payload);
  });
  process.stdout.write(`${JSON.stringify(output)}\n`);
  if (output.schema_version !== 1 || output.suite !== 'browser' || output.status !== 'passed') process.exitCode = 1;
} catch (error) {
  process.stdout.write(`${JSON.stringify({ schema_version: 1, suite: 'browser', status: 'failed', useful_records: 0, error: { code: error.message } })}\n`);
  process.exitCode = 1;
}
