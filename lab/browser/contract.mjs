export function validateEvent(event) {
  if (!event || typeof event !== 'object' || Array.isArray(event)) throw new Error('invalid_event');
  if (event.schema_version !== 1 || event.suite !== 'browser' || event.fixture_only !== true) {
    throw new Error('requires_schema_1_browser_fixture_only');
  }
  const allowed = new Set(['schema_version', 'suite', 'fixture_only', 'mode', 'batch', 'repeats', 'session_mode', 'deadline_ms']);
  if (Object.keys(event).some(key => !allowed.has(key))) throw new Error('unsupported_event_field');
  const result = { mode: 'direct', batch: 3, repeats: 1, session_mode: 'full', deadline_ms: 60000, ...event };
  if (!['direct', 'opencli'].includes(result.mode)) throw new Error('invalid_mode');
  if (!['full', 'cookies_only', 'none'].includes(result.session_mode)) throw new Error('invalid_session_mode');
  for (const [key, min, max] of [['batch', 1, 10], ['repeats', 1, 5], ['deadline_ms', 1000, 60000]]) {
    if (!Number.isInteger(result[key]) || result[key] < min || result[key] > max) throw new Error(`invalid_${key}`);
  }
  return result;
}

export function listingsFor(query) {
  return [
    { id: `synthetic-${query}-perfume-30`, title: 'Fixture fragrance 30 ml', variant: '30 ml', condition: 'new', quantity: 1, currency: 'USD', published_price: '25.50' },
    { id: `synthetic-${query}-perfume-100`, title: 'Fixture fragrance 100 ml', variant: '100 ml', condition: 'new', quantity: 1, currency: 'USD', published_price: '60.00' },
    { id: `synthetic-${query}-watch`, title: 'Fixture watch model A', variant: 'model A', condition: 'used', quantity: 1, currency: 'USD', published_price: '42.75' },
  ];
}

export function storageChecks(probe) {
  return {
    cookie_restored: probe?.cookie === true,
    local_storage_restored: probe?.localStorage === true,
    indexed_db_restored: probe?.indexedDB === true,
    session_storage_absent: probe?.sessionStorage === false,
  };
}
