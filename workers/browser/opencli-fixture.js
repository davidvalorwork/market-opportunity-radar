import { cli, Strategy } from '@jackwener/opencli/registry';

// Only this explicit synthetic page is eligible; no navigation or tab selection.
cli({
  site: 'radar-fixture', name: 'extract', access: 'read', strategy: Strategy.UI,
  navigateBefore: false, siteSession: 'ephemeral', defaultFormat: 'json',
  description: 'Extract listings from the exact loopback synthetic fixture target',
  args: [],
  func: async page => {
    const expected = process.env.RADAR_FIXTURE_PAGE;
    const url = new URL(expected);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.pathname !== '/listings') throw new Error('fixture_origin_rejected');
    return page.evaluate(`(() => {
      if (location.href !== ${JSON.stringify(expected)} || document.title !== 'Radar synthetic fixture') throw new Error('fixture_target_mismatch');
      if (document.documentElement.dataset.fixtureReady !== 'true') throw new Error('fixture_not_ready');
      return Array.from(document.querySelectorAll('[data-testid="listing"]')).slice(0,3).map(row => JSON.parse(row.dataset.record));
    })()`);
  },
});
