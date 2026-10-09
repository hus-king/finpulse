import assert from 'node:assert/strict';
import test from 'node:test';
import { sessionSlots, sessionIndex, selectSessionDay, buildIntraday, initialChartPeriod, sessionPreviousClose } from '../src/intradayMath.ts';
const bar=(date,close=101)=>({date,open:100,high:102,low:99,close,volume:100});
const trading={is_trade_day:true,state:'trading',server_time:'2026-10-09T14:15:30+08:00',expected_data_time:'2026-10-09T14:13:30+08:00'};

test('full opening-to-close axis compresses lunch and keeps both real session endpoints',()=>{
  const slots=sessionSlots();assert.equal(slots.length,242);
  assert.equal(slots[0],'09:30');assert.equal(slots[120],'11:30');assert.equal(slots[121],'13:00');assert.equal(slots.at(-1),'15:00');
  assert.equal(sessionIndex('14:00'),181);assert.equal(sessionIndex('12:00'),null);assert.equal(sessionIndex('15:01'),null);
});
test('saved historical preference cannot override a new visit default',()=>{
  assert.equal(initialChartPeriod({period:'日 K'}),'分时');assert.equal(initialChartPeriod({period:'60 分钟'}),'分时');
});
test('trading day selects today even when only yesterday is cached',()=>{
  assert.equal(selectSessionDay(trading,[bar('2026-10-08 15:00:00')]),'2026-10-09');
  const session=buildIntraday([bar('2026-10-08 15:00:00')],trading,100);
  assert.equal(session.rows.length,0);assert.equal(session.prices.length,242);assert.ok(session.prices.every(v=>v===null));assert.equal(session.quote.price,null);
});
test('weekends and holidays select the calendar previous trading session',()=>{
  for(const [clock,previous] of [['2026-10-11T12:00:00+08:00','2026-10-09'],['2026-10-06T12:00:00+08:00','2026-09-30']]){
    const m={...trading,is_trade_day:false,state:'closed',server_time:clock,expected_data_time:previous+'T15:00:00+08:00'};
    assert.equal(selectSessionDay(m,[bar('2026-09-01 15:00:00')]),previous);
  }
});
test('intraday data filters other days, lunch, invalid bars and future sessions; gaps remain empty',()=>{
  const rows=[bar('2026-10-08 15:00:00'),bar('2026-10-09 09:31:00'),bar('2026-10-09 09:33:00',102),bar('2026-10-09 12:00:00'),bar('2026-10-09 14:16:00'),bar('2026-10-09 14:17:00'),bar('2026-10-09 14:55:00'),bar('2026-10-09 10:00:00',NaN)];
  const s=buildIntraday(rows,trading,100);assert.equal(s.rows.length,3);
  assert.equal(s.prices[1],101);assert.equal(s.prices[2],null);assert.equal(s.prices.at(-1),null);assert.equal(s.volumes.at(-1),null);assert.equal(s.quote.volume,300);
  assert.equal(s.quote.change,1);assert.equal(s.previousClose,100);
});
test('full session domain does not grow as new minutes arrive',()=>{
  const first=buildIntraday([bar('2026-10-09 09:31:00')],trading,100);
  const second=buildIntraday([bar('2026-10-09 09:31:00'),bar('2026-10-09 14:15:00')],trading,100);
  assert.deepEqual(first.times,second.times);assert.equal(first.times.at(-1),'15:00');
});
test('pre-open keeps today empty; unknown calendar falls back only to known data',()=>{
  assert.equal(selectSessionDay({...trading,state:'pre_open',server_time:'2026-10-09T08:00:00+08:00',expected_data_time:'2026-10-08T15:00:00+08:00'},[bar('2026-10-08 15:00:00')]),'2026-10-09');
  const unknown={...trading,is_trade_day:null,state:'calendar_unknown',expected_data_time:null};
  assert.equal(selectSessionDay(unknown,[bar('2026-10-08 15:00:00')]),'2026-10-08');
});

test('previous-close reference excludes current-day daily prices and prefers a full prior minute session',()=>{
  const daily=[bar('2026-10-08',100),bar('2026-10-09',130)];
  assert.equal(sessionPreviousClose('2026-10-09',daily,[bar('2026-10-08 15:00:00',101)]),101);
  assert.equal(sessionPreviousClose('2026-10-09',daily,[bar('2026-10-08 14:00:00',99)]),100);
  assert.equal(sessionPreviousClose('2026-10-09',[bar('2026-10-09',130)],[]),null);
});
