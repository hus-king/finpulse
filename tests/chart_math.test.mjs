import assert from 'node:assert/strict';
import test from 'node:test';
import {
  movingAverage,
  exponentialMovingAverage,
  bollingerBands,
  heikinAshi,
  weeklyCandles,
  relativeStrengthIndex,
} from '../src/chartMath.ts';

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} ≈ ${expected}`);
const candle = (date, open, close, low, high, volume, partial) => ({ date, open, close, low, high, volume, ...(partial == null ? {} : { partial }) });

test('empty sequences produce empty indicator and candle outputs', () => {
  assert.deepEqual(movingAverage([], 5), []);
  assert.deepEqual(exponentialMovingAverage([], 5), []);
  assert.deepEqual(bollingerBands([]), { middle: [], upper: [], lower: [] });
  assert.deepEqual(heikinAshi([]), []);
  assert.deepEqual(weeklyCandles([]), []);
  assert.deepEqual(relativeStrengthIndex([]), []);
});

test('MA waits for a full window and preserves sub-cent precision', () => {
  assert.deepEqual(movingAverage([1.001, 1.003], 3), [null, null]);
  assert.deepEqual(movingAverage([10, 13, 16, 25], 3), [null, null, 13, 18]);
  near(movingAverage([1.001, 1.003], 2)[1], 1.002);
});

test('EMA seeds from the first price and recursively weights later prices', () => {
  assert.deepEqual(exponentialMovingAverage([10, 14, 18, 14], 3), [10, 12, 15, 14.5]);
  assert.deepEqual(exponentialMovingAverage([7], 20), [7]);
});

test('BOLL waits for the complete period and uses population standard deviation', () => {
  assert.deepEqual(bollingerBands([1, 2], 3), { middle: [null, null], upper: [null, null], lower: [null, null] });
  const bands = bollingerBands([1, 2, 3, 4], 3);
  assert.deepEqual(bands.middle, [null, null, 2, 3]);
  near(bands.upper[2], 3.632993161855452);
  near(bands.lower[2], 0.36700683814454793);
  near(bands.upper[3], 4.632993161855452);
  near(bands.lower[3], 1.367006838144548);
});

test('BOLL upper and lower bands coincide for constant prices', () => {
  const bands = bollingerBands(Array(21).fill(12.5));
  assert.deepEqual(bands.middle.slice(0, 19), Array(19).fill(null));
  assert.deepEqual(bands.middle.slice(19), [12.5, 12.5]);
  assert.deepEqual(bands.upper, bands.middle);
  assert.deepEqual(bands.lower, bands.middle);
});

test('Heikin Ashi recursively uses transformed prices while retaining volume and partial state', () => {
  const bars = [
    candle('2026-10-01', 10, 14, 8, 16, 100, true),
    candle('2026-10-02', 14, 16, 13, 18, 200),
    candle('2026-10-05', 20, 18, 17, 21, 300),
    candle('2026-10-06', 5, 6, 4, 7, 400),
  ];
  assert.deepEqual(heikinAshi(bars), [
    candle('2026-10-01', 12, 12, 8, 16, 100, true),
    candle('2026-10-02', 12, 15.25, 12, 18, 200),
    candle('2026-10-05', 13.625, 19, 13.625, 21, 300),
    candle('2026-10-06', 16.3125, 5.5, 4, 16.3125, 400),
  ]);
});

test('weekly aggregation crosses year boundaries and preserves chronological OHLC and summed volume', () => {
  const bars = [
    candle('2026-01-02', 14, 15, 11, 17, 300, true),
    candle('2025-12-30', 10, 12, 9, 13, 100),
    candle('2026-01-05', 16, 18, 15, 19, 400),
    candle('2025-12-31', 12, 14, 10, 16, 200),
  ];
  assert.deepEqual(weeklyCandles(bars), [
    candle('2025-12-29', 10, 15, 9, 17, 600, true),
    candle('2026-01-05', 16, 18, 15, 19, 400),
  ]);
});

test('math transformations do not change source arrays or candle objects', () => {
  const values = Object.freeze([10, 14, 18, 14]);
  const bars = Object.freeze([
    Object.freeze(candle('2026-10-02', 14, 16, 13, 18, 200)),
    Object.freeze(candle('2026-10-01', 10, 14, 8, 16, 100)),
  ]);
  const snapshot = structuredClone(bars);
  movingAverage(values, 3);
  exponentialMovingAverage(values, 3);
  bollingerBands(values, 3);
  relativeStrengthIndex(values, 3);
  const transformed = heikinAshi(bars);
  weeklyCandles(bars);
  assert.deepEqual(bars, snapshot);
  assert.deepEqual(values, [10, 14, 18, 14]);
  assert.notEqual(transformed[0], bars[0]);
});

test('RSI warmup and flat, rising, and falling prices avoid NaN', () => {
  assert.deepEqual(relativeStrengthIndex([5, 5], 3), [null, null]);
  assert.deepEqual(relativeStrengthIndex([5, 5, 5, 5], 3), [null, null, null, 50]);
  assert.deepEqual(relativeStrengthIndex([1, 2, 3, 4], 3), [null, null, null, 100]);
  assert.deepEqual(relativeStrengthIndex([4, 3, 2, 1], 3), [null, null, null, 0]);
});

test('invalid indicator periods fail explicitly instead of returning misleading values', () => {
  for (const period of [0, -1, 1.5, NaN, Infinity]) {
    assert.throws(() => movingAverage([1, 2], period), RangeError);
    assert.throws(() => exponentialMovingAverage([1, 2], period), RangeError);
    assert.throws(() => bollingerBands([1, 2], period), RangeError);
    assert.throws(() => relativeStrengthIndex([1, 2], period), RangeError);
  }
});
