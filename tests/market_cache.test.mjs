import assert from 'node:assert/strict';
import test from 'node:test';
import { createMarketCache } from '../src/marketCache.ts';
const now=Date.parse('2026-10-09T14:00:00+08:00');
const bundle=code=>({dashboard:{stock:{code}},minutes:{'1':{market:{server_time:'2026-10-09T14:00:00+08:00'}}}});
test('shared quote cache survives readers leaving and never crosses stock codes',()=>{
  const cache=createMarketCache(); const first=bundle('600519'); cache.put(first,now);
  assert.equal(cache.get('600519',now+1000),first);
  assert.equal(cache.get('300750',now+1000),null);
});
test('expired and previous Shanghai-day snapshots are not replayed as current intraday views',()=>{
  const cache=createMarketCache(); cache.put(bundle('600519'),now);
  assert.equal(cache.get('600519',now+5*60_000+1),null);
  const midnight=Date.parse('2026-10-09T23:59:00+08:00');
  cache.put(bundle('600519'),midnight);
  assert.equal(cache.get('600519',midnight+60_000),null);
});
test('bounded cache keeps recently revisited stock and evicts oldest unused stock',()=>{
  const cache=createMarketCache(2); cache.put(bundle('600519'),now); cache.put(bundle('300750'),now);
  cache.get('600519',now+1); cache.put(bundle('688981'),now+2);
  assert.equal(cache.get('300750',now+3),null); assert.ok(cache.get('600519',now+3)); assert.ok(cache.get('688981',now+3));
});
