import assert from 'node:assert/strict';
import test from 'node:test';
import { chartTimestamp, initialChartRange } from '../src/tradingChartData.ts';
import { preparePlotData } from '../src/chartPresentation.ts';
const bar=(date)=>({date,open:10,close:11,high:12,low:9,volume:100});
test('exchange wall times stay on the same displayed day, regardless of local timezone',()=>{
 assert.equal(new Date(chartTimestamp('2026-10-30 09:30:00')*1000).toISOString(),'2026-10-30T09:30:00.000Z');
 assert.equal(chartTimestamp('2026-10-30'),chartTimestamp('2026-10-30 00:00:00'));
 assert.equal(chartTimestamp('2026-10-30 09:31:00')-chartTimestamp('2026-10-30 09:30:00'),60);
});
test('fixed intraday range includes missing and future slots, with no generated prices',()=>{
 const rows=[bar('2026-10-30 09:31:00'),bar('2026-10-30 11:30:00')];
 const plot=preparePlotData(rows,'分时',{day:'2026-10-30',times:['09:30','09:31','11:30','13:00','15:00'],rows});
 assert.deepEqual(initialChartRange(plot.dates,'分时','当日'),{from:-.5,to:12.5});
 assert.deepEqual(plot.display.map(row=>row?.close??null),[null,11,11,null,null]);
 assert.equal(chartTimestamp(plot.dates[3])-chartTimestamp(plot.dates[2]),90*60);
 assert.deepEqual(initialChartRange([],'分时','当日'),null);
});
test('daily/month and minute/day resets end at the newest candle without future space',()=>{
 const dates=['2026-06-01','2026-09-01','2026-10-01','2026-10-30'];
 assert.deepEqual(initialChartRange(dates,'日 K','近 1 月'),{from:1.5,to:3});
 assert.deepEqual(initialChartRange(dates,'日 K','全部'),{from:-.5,to:3});
 assert.deepEqual(initialChartRange(['2026-10-29 15:00:00','2026-10-30 09:31:00'],'1 分钟','当日'),{from:.5,to:1});
});

test('weekly and single-candle ranges also stop at the newest index',()=>{
 assert.equal(initialChartRange(['2026-10-19','2026-10-26'],'周 K','全部').to,1);
 assert.deepEqual(initialChartRange(['2026-10-30'],'日 K','全部'),{from:-.5,to:0});
});
