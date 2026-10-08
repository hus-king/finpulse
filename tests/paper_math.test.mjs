import test from 'node:test';
import assert from 'node:assert/strict';
import { estimateFees, maxBuyQuantity, formatMoney, newRequestId } from '../src/paperMath.ts';

const main = { board:'主板', min_quantity:100, quantity_step:100, max_quantity:1_000_000 };
const star = { board:'科创板', min_quantity:200, quantity_step:1, max_quantity:50_000 };
test('fees include minimum commission and half-up taxes', () => {
  assert.deepEqual(estimateFees(100_000,'buy'),{commission_fen:500,transfer_fen:1,stamp_fen:0,fees_fen:501});
  assert.equal(estimateFees(105_000,'sell').stamp_fen,53);
  assert.equal(estimateFees(50_000,'buy').transfer_fen,1);
});
test('maximum affordable lots count fees against 200k', () => {
  assert.equal(maxBuyQuantity(20_000_000,133_800,main),100);
  assert.equal(maxBuyQuantity(100_500,1000,main),0);
  assert.equal(maxBuyQuantity(100_501,1000,main),100);
  assert.equal(maxBuyQuantity(20_000_000,0,main),0);
});
test('STAR requires 200 then allows one-share increments and caps size', () => {
  assert.equal(maxBuyQuantity(200_101,998,star),0);
  assert.equal(maxBuyQuantity(201_100,998,star),201);
  assert.equal(maxBuyQuantity(20_000_000,1,star),50_000);
});
test('money formatting always shows two decimals including missing prices', () => {
  assert.equal(formatMoney(20_000_000),'200,000.00');
  assert.equal(formatMoney(-1),'-0.01');
  assert.equal(formatMoney(null),'—');
});
test('request ids work on plain HTTP where crypto.randomUUID is unavailable', () => {
  const original = globalThis.crypto;
  Object.defineProperty(globalThis,'crypto',{value:{getRandomValues:original.getRandomValues.bind(original)},configurable:true});
  try {
    const first = newRequestId(), second = newRequestId();
    assert.match(first,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.notEqual(first,second);
  } finally { Object.defineProperty(globalThis,'crypto',{value:original,configurable:true}); }
});
