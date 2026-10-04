import { createServer } from 'node:http';
import { listingsFor } from './contract.mjs';

// Synthetic state only. This server is created and closed by every invocation.
const script = `
const db = await new Promise((resolve,reject) => {
  const request = indexedDB.open('radar_fixture',1);
  request.onupgradeneeded = () => request.result.createObjectStore('auth');
  request.onerror = () => reject(new Error('fixture_indexeddb_error'));
  request.onsuccess = () => resolve(request.result);
});
if (location.pathname === '/seed') {
  localStorage.setItem('radar_account','synthetic-account');
  sessionStorage.setItem('radar_ephemeral','synthetic-session');
  await new Promise((resolve,reject) => {
    const tx = db.transaction('auth','readwrite');
    tx.objectStore('auth').put('synthetic-account','account');
    tx.oncomplete = resolve; tx.onerror = () => reject(new Error('fixture_seed_error'));
  });
}
const indexed = await new Promise((resolve,reject) => {
  const request = db.transaction('auth').objectStore('auth').get('account');
  request.onsuccess = () => resolve(request.result === 'synthetic-account');
  request.onerror = () => reject(new Error('fixture_read_error'));
});
db.close();
const access = await fetch('/api/access').then(r => r.json());
window.__fixtureProbe = {cookie:access.authorized,localStorage:localStorage.getItem('radar_account')==='synthetic-account',indexedDB:indexed,sessionStorage:sessionStorage.getItem('radar_ephemeral')!==null};
if (Object.values(window.__fixtureProbe).slice(0,3).every(Boolean)) {
  const query = Number(new URL(location.href).searchParams.get('query') || 1);
  const records = await fetch('/api/listings?query='+query).then(r => r.json());
  for(const record of records) {
    const row = document.createElement('article'); row.dataset.testid='listing';
    row.dataset.record=JSON.stringify(record); row.textContent=record.title; document.querySelector('main').append(row);
  }
} else document.querySelector('main').textContent='Synthetic login wall: portable state incomplete';
document.documentElement.dataset.fixtureReady='true';
`;

export async function startFixture() {
  let origin;
  let bytes = 0;
  const server = createServer((req, res) => {
    const send = (status, type, body, extra = {}) => {
      bytes += Buffer.byteLength(body);
      res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store', ...extra });
      res.end(body);
    };
    if (req.method !== 'GET' || req.headers.host !== new URL(origin).host) return send(403, 'text/plain', 'fixture_request_rejected');
    const url = new URL(req.url, origin);
    if (url.pathname === '/api/access') return send(200, 'application/json', JSON.stringify({ authorized: /(?:^|;\s*)radar_session=synthetic-account(?:;|$)/.test(req.headers.cookie || '') }));
    if (url.pathname === '/api/listings') {
      const query = Number(url.searchParams.get('query'));
      if (!Number.isInteger(query) || query < 1 || query > 10) return send(400, 'text/plain', 'invalid_query');
      return send(200, 'application/json', JSON.stringify(listingsFor(query)));
    }
    if (!['/seed', '/listings'].includes(url.pathname)) return send(404, 'text/plain', 'fixture_not_found');
    const headers = url.pathname === '/seed' ? { 'Set-Cookie': 'radar_session=synthetic-account; HttpOnly; SameSite=Strict; Path=/' } : {};
    send(200, 'text/html', `<!doctype html><html><head><title>Radar synthetic fixture</title></head><body><main></main><script type="module">${script}</script></body></html>`, headers);
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${server.address().port}`;
  return {
    origin,
    get bytes() { return bytes; },
    close: () => new Promise((resolve, reject) => {
      server.close(error => error ? reject(error) : resolve());
      server.closeAllConnections();
    }),
  };
}
