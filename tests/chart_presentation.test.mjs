import assert from 'node:assert/strict';
import test from 'node:test';
const { preparePlotData, projectValues, readoutIndex, formatIndicatorValue, chartLayout } = await import('../src/chartPresentation.ts').catch(() => ({}));
const bar=(date,close=10)=>({date,open:close,close,high:close+1,low:close-1,volume:100});
test('intraday keeps prior-session source bars but leaves future and missing slots empty',()=>{
 assert.equal(typeof preparePlotData,'function');
 const source=[bar('2026-10-19 15:00:00',9),bar('2026-10-20 09:31:00',10),bar('2026-10-20 09:33:00',12),bar('2026-10-20 09:34:00',99)];
 const session={day:'2026-10-20',times:['09:30','09:31','09:32','09:33','09:34'],rows:source.slice(1,3),previousClose:9};
 const data=preparePlotData(source,'分时',session);
 assert.deepEqual(data.source.map(row=>row.close),[9,10,12]);
 assert.deepEqual(data.display.map(row=>row?.close??null),[null,10,null,12,null]);
 assert.deepEqual(projectValues(data,[9,9.5,10.75]),[null,9.5,null,10.75,null]);
 assert.equal(data.latestIndex,3);
});
test('pre-open session has no invented current price despite previous-day history',()=>{
 assert.equal(typeof preparePlotData,'function');
 const data=preparePlotData([bar('2026-10-19 15:00:00')],'分时',{day:'2026-10-20',times:['09:30','09:31'],rows:[],previousClose:10});
 assert.deepEqual(data.display,[null,null]);
 assert.equal(data.latestIndex,-1);
 assert.deepEqual(projectValues(data,[10]),[null,null]);
});
test('weekly calculations receive chronological aggregated raw candles',()=>{
 assert.equal(typeof preparePlotData,'function');
 const source=Object.freeze([Object.freeze(bar('2026-10-20',12)),Object.freeze(bar('2026-10-19',10)),Object.freeze(bar('2026-10-16',8))]);
 const data=preparePlotData(source,'周 K');
 assert.deepEqual(data.source.map(row=>[row.date,row.open,row.close,row.volume]),[['2026-10-12',8,8,100],['2026-10-19',10,12,200]]);
 assert.equal(source[0].date,'2026-10-20');
});
test('crosshair index accepts first category and exact dates, rejects invalid values',()=>{
 assert.equal(typeof readoutIndex,'function');
 const dates=['2026-10-19','2026-10-20'];
 assert.equal(readoutIndex(0,dates),0);
 assert.equal(readoutIndex('2026-10-20',dates),1);
 for(const value of [-1,2,NaN,undefined,'wrong']) assert.equal(readoutIndex(value,dates),null);
});
test('readouts use finite two-decimal numbers, explicit gaps, and readable share units',()=>{
 assert.equal(typeof formatIndicatorValue,'function');
 assert.equal(formatIndicatorValue(394.2700000001,'price'),'394.27');
 assert.equal(formatIndicatorValue(-0.0001,'number'),'0.00');
 assert.equal(formatIndicatorValue(123456,'volume'),'12.35 万股');
 assert.equal(formatIndicatorValue(100,'volume'),'100 股');
 for(const value of [null,undefined,NaN,Infinity]) assert.equal(formatIndicatorValue(value,'ratio'),'—');
});
test('multiple panes retain readable independent plot space and a bottom time axis',()=>{
 assert.equal(typeof chartLayout,'function');
 for(const count of [1,2,4]) {
  const layout=chartLayout(count,false);
  assert.equal(layout.panes.length,count);
  assert.ok(layout.priceHeight>=220);
  let end=layout.priceTop+layout.priceHeight;
  for(const pane of layout.panes) {
   assert.ok(pane.headerTop>=end);
   assert.ok(pane.gridTop>=pane.headerTop+44);
   assert.ok(pane.height>=100);
   end=pane.gridTop+pane.height;
  }
  assert.ok(layout.height>=end+24);
 }
});
test('index source volumes never get displayed as equity shares', () => {
 assert.equal(formatIndicatorValue(12000, 'volume', '源单位'), '1.20 万源单位');
 assert.equal(formatIndicatorValue(123, 'volume', '源单位'), '123 源单位');
 assert.equal(formatIndicatorValue(12000, 'volume'), '1.20 万股');
 assert.equal(formatIndicatorValue(null, 'volume', '源单位'), '—');
});
