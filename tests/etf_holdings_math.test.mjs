import assert from 'node:assert/strict';
import test from 'node:test';
import { disclosureEvents, holdingPeriods, indexRangePosition } from '../src/etfHoldingsMath.ts';

const fund = (reports, conversions) => ({ code: '510300', name: '沪深300 ETF', reports, conversions });
const report = (date, shares, total = 1_000, name = '汇金账户') => ({
  report_date: date, publication_date: date, status: shares == null ? 'anonymous' : 'disclosed',
  source_url: null, total_shares: total, holders: shares == null ? [] : [{ name, shares, group: 'huijin' }],
});

test('ETF total share changes do not count as named holder trades', () => {
  const periods = holdingPeriods(fund([report('2025-06-30', 500), report('2025-12-31', 500, 800)]), 'all');
  assert.equal(periods[1].ownershipPercent, 62.5);
  assert.equal(periods[1].change, 0);
});

test('named holder comparison adjusts split units and skips changed or anonymous disclosures', () => {
  const periods = holdingPeriods(fund([
    report('2024-06-30', 100), report('2024-12-31', 220), report('2025-06-30', 80, 1_000, '另一账户'),
    report('2025-12-31', null), report('2026-06-30', 200, 1_000, '另一账户'),
  ], [{ date: '2024-11-01', ratio: '1 份拆为 2 份' }]), 'huijin');
  assert.deepEqual(periods.map(({ change }) => change), [null, 20, null, null, null]);
  assert.equal(periods[2].changeNote, '具名账户名单变化');
});

test('market dates use the preceding session for a report period and the following session for disclosure', () => {
  const periods = holdingPeriods(fund([
    report('2025-12-31', 200), { ...report('2026-06-30', 230), publication_date: '2026-08-31' },
  ]), 'all');
  const index = [
    { date: '2026-06-29', close: 3_000 }, { date: '2026-07-01', close: 3_100 },
    { date: '2026-08-31', close: 3_400 }, { date: '2026-09-01', close: 3_500 },
  ];
  assert.equal(disclosureEvents(periods, index, 'period-end')[0].date, '2026-06-29');
  assert.equal(disclosureEvents(periods, index, 'published')[0].date, '2026-09-01');
  assert.equal(disclosureEvents(periods, index.slice(0, -1), 'published').length, 0);
});

test('the last 252 trading-session range excludes future closes', () => {
  const index = Array.from({ length: 252 }, (_, i) => ({ close: i === 0 ? 100 : i === 1 ? 200 : 150 }));
  index.push({ close: 1_000 });
  assert.equal(indexRangePosition(index, 250), null);
  assert.equal(indexRangePosition(index, 251), 50);
});
