// Synthetic raw fields only. Money and equivalence remain in Python.
export function listingsFor(query) {
  return [
    { id: `synthetic-${query}-perfume-30`, title: 'Fixture fragrance 30 ml', variant: '30 ml', condition: 'new', quantity: '1', currency: 'USD', published_price: '25.50', source: 'synthetic_fixture' },
    { id: `synthetic-${query}-perfume-100`, title: 'Fixture fragrance 100 ml', variant: '100 ml', condition: 'new', quantity: '1', currency: 'USD', published_price: '60.00', source: 'synthetic_fixture' },
    { id: `synthetic-${query}-watch`, title: 'Fixture watch model A', variant: 'model A', condition: 'used', quantity: '1', currency: 'USD', published_price: '42.75', source: 'synthetic_fixture' },
  ];
}
export function storageChecks(probe) {
  return probe?.cookie === true && probe?.localStorage === true && probe?.indexedDB === true && probe?.sessionStorage === false;
}
