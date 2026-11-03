import assert from 'node:assert/strict';
import test from 'node:test';
import { buildReplayEvents, analysisTiming } from '../src/newsReplay.ts';

const bar = (date, close, extra = {}) => ({ date, close, open: close, high: close, low: close, volume: 100, ...extra });
const article = (id, extra = {}) => ({ id, title: '贵州茅台发布三季度业绩', source: '新闻来源', url: `https://example.com/${id}`, content: '贵州茅台业绩公告', time: '2026-10-23', score: 50, analysis_status: 'completed', date_status: 'body_verified', sources: [], ...extra });
const calendar = Array.from({ length: 21 }, (_, i) => {
  const date = new Date(Date.UTC(2026, 9, 23 + i));
  return { date: date.toISOString().slice(0, 10), is_trading: ![0, 6].includes(date.getUTCDay()) };
});
const candles = [bar('2026-10-26', 100), bar('2026-10-27', 105), bar('2026-10-28', 102), bar('2026-10-29', 110), bar('2026-10-30', 108), bar('2026-11-02', 115)];
const context = (extra = {}) => ({ calendar, completed_through: '2026-11-02', server_time: '2026-11-03T10:00:00+08:00', benchmark: { candles: candles.map(b => bar(b.date, 200 + candles.indexOf(b) * 2)), refreshing: false, error: null }, ...extra });
const dashboard = (news = [article('1')], bars = candles) => ({ stock: { code: '600519', name: '贵州茅台', industry: '白酒' }, news, candles: bars });
const replay = (data = dashboard(), ctx = context(), horizon = 3) => buildReplayEvents(data, ctx, horizon);

test('Friday publication uses Monday close as D0; D+3 and benchmark share exact dates', () => {
  const row = replay()[0];
  assert.equal(row.status, 'ready');
  assert.equal(row.baseDate, '2026-10-26');
  assert.ok(Math.abs(row.stockReturn - 10) < 1e-8);
  assert.ok(Math.abs(row.benchmarkReturn - 3) < 1e-8);
  assert.ok(Math.abs(row.excessReturn - 7) < 1e-8);
  assert.deepEqual(row.points.map(p => p.date), ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29']);
});

test('1/3/5 trading-day horizons count sessions, including holidays, rather than calendar days', () => {
  assert.ok(Math.abs(replay(dashboard(), context(), 1)[0].stockReturn - 5) < 1e-8);
  assert.ok(Math.abs(replay(dashboard(), context(), 5)[0].stockReturn - 15) < 1e-8);
  const holidayCalendar = calendar.map(day => day.date === '2026-10-26' ? { ...day, is_trading: false } : day);
  assert.equal(replay(dashboard(), context({ calendar: holidayCalendar }), 1)[0].baseDate, '2026-10-27');
});

test('today before close and explicitly partial bars never become completed observations', () => {
  const today = article('today', { time: '2026-11-02' });
  const row = replay(dashboard([today], [...candles, bar('2026-11-03', 200)]))[0];
  assert.equal(row.status, 'observing');
  assert.equal(row.basePrice, null);
  assert.equal(row.stockReturn, null);
  assert.equal(row.points[0].stock, null);
  const partial = replay(dashboard(undefined, candles.map(b => b.date === '2026-10-29' ? { ...b, partial: true } : b)))[0];
  assert.equal(partial.status, 'missing');
  assert.equal(partial.stockReturn, null);
});

test('missing or suspended stock sessions stay missing instead of shifting the baseline or horizon', () => {
  const row = replay(dashboard(undefined, candles.filter(b => b.date !== '2026-10-26')))[0];
  assert.equal(row.status, 'missing');
  assert.equal(row.baseDate, '2026-10-26');
  assert.equal(row.basePrice, null);
  assert.equal(row.stockReturn, null);
  assert.match(row.reason, /停牌|缺失/);
  const gap = replay(dashboard(undefined, candles.filter(b => b.date !== '2026-10-27')))[0];
  assert.equal(gap.status, 'missing');
  assert.equal(gap.points[1].stock, null);
});

test('observing events expose only elapsed closes, with no future interpolation', () => {
  const row = replay(dashboard(), context({ completed_through: '2026-10-28' }), 5)[0];
  assert.equal(row.status, 'observing');
  assert.equal(row.elapsedDays, 2);
  assert.equal(row.stockReturn, null);
  assert.ok(Math.abs(row.latestReturn - 2) < 1e-8);
  assert.equal(row.points[3].stock, null);
});

test('missing benchmark does not erase a valid stock return or turn benchmark gaps into zeros', () => {
  const ctx = context({ benchmark: { candles: [bar('2026-10-26', 200), bar('2026-10-30', 250)], refreshing: false, error: '行情更新失败' } });
  const row = replay(dashboard(), ctx)[0];
  assert.equal(row.status, 'ready');
  assert.ok(Math.abs(row.stockReturn - 10) < 1e-8);
  assert.equal(row.benchmarkReturn, null);
  assert.equal(row.excessReturn, null);
  assert.equal(row.points[3].benchmark, null);
});

test('date conflicts, missing dates, unknown calendar and obvious tutorials require verification', () => {
  for (const extra of [{ date_status: 'conflict' }, { time: '' }, { time: '2026-02-30' }, { time: '2026-11-04' }, { title: '2650股票是什么？代码对应标的及查询方法解析' }, { title: '千仞岗羽绒服质量好不好？' }, { title: '华泰柏瑞中证全指红利质量交易型开放式指数基金' }]) {
    assert.equal(replay(dashboard([article('bad', extra)]))[0].status, 'verify');
  }
  assert.equal(replay(dashboard(), context({ calendar: calendar.map(day => day.date === '2026-10-27' ? { ...day, is_trading: null } : day) }))[0].status, 'verify');
  assert.equal(replay(dashboard(), context({ completed_through: null }))[0].status, 'verify');
  const industry = article('oil', { title: '国际油价上涨', content: '原油供给收缩', news_scope: 'industry', related_factors: ['原油价格'] });
  assert.equal(replay(dashboard([industry]))[0].status, 'ready');
  assert.equal(replay(dashboard([article('holding', { title: '交易型开放式指数基金增持贵州茅台' })]))[0].status, 'ready');
});

test('duplicates merge only on same-day URL or normalized title; newest saved assessment represents group', () => {
  const first = article('a', { analyzed_at: '2026-10-23T10:00:00+08:00' });
  const second = article('b', { url: 'https://example.com/a?utm_source=test', title: '另一媒体转载标题', score: -60, analyzed_at: '2026-10-23T11:00:00+08:00' });
  const third = article('c', { time: '2026-10-26' });
  const rows = replay(dashboard([first, second, third]));
  assert.equal(rows.length, 2);
  assert.equal(rows.find(r => r.members.length === 2).news.id, 'b');
  assert.equal(rows.find(r => r.members.length === 2).news.score, -60);
});

test('analysis time distinguishes after-baseline inference and does not infer time when absent', () => {
  assert.equal(analysisTiming(article('1', { analyzed_at: '2026-10-26T15:01:00+08:00' }), '2026-10-26'), 'after');
  assert.equal(analysisTiming(article('1', { analyzed_at: '2026-10-26T14:59:00+08:00' }), '2026-10-26'), 'before');
  assert.equal(analysisTiming(article('1'), '2026-10-26'), 'unknown');
});
