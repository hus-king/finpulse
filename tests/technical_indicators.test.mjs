import assert from 'node:assert/strict';
import test from 'node:test';

import { calculateIndicator as calculate } from '../src/technicalIndicators.ts';
const ids = ['VOL', 'MACD', 'KDJ', 'RSI', 'BIAS', 'CCI', 'WR', 'ATR', 'BOLL', 'EXPMA', 'ENE', 'OBV', 'BBI', 'BRAR', 'TRIX', 'VR', 'DMI', 'DPO', 'DMA', 'CR', 'LWR', 'SKDJ', 'DKX', 'MTM', 'LON'];
const bars = (closes, extra = {}) => closes.map((close, i) => ({ date: `bar-${i}`, open: close, high: close + 1, low: close - 1, close, volume: 100, ...extra }));
const rising = bars(Array.from({ length: 120 }, (_, i) => i + 1));
const values = (id, key, input = rising) => {
  const line = calculate(id, input).lines.find(line => line.key === key);
  assert.ok(line, `${id} must expose ${key}`);
  return line.values;
};
const near = (actual, expected) => {
  assert.equal(typeof actual, 'number');
  assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} ≈ ${expected}`);
};

// Catches lost lines, NaN/Infinity, short arrays, source mutation, and absent indicators.
test('all public indicators preserve alignment, immutability, and finite-or-null results', () => {
  const fixtures = [[], rising.slice(0, 1), rising.slice(0, 5), rising,
    bars(Array(120).fill(5)), bars(Array(120).fill(0), { high: 0, low: 0, volume: 0 }),
    bars([3, 6, 2, 5], { partial: true })];
  for (const input of fixtures) {
    const frozen = Object.freeze(input.map(bar => Object.freeze({ ...bar })));
    const snapshot = structuredClone(frozen);
    for (const id of ids) {
      const result = calculate(id, frozen);
      assert.equal(result.id, id);
      assert.ok(result.label && result.parameters && result.description);
      assert.ok(result.lines.length > 0);
      assert.equal(new Set(result.lines.map(line => line.key)).size, result.lines.length);
      for (const line of result.lines) {
        assert.ok(line.label && line.description);
        assert.equal(line.values.length, input.length, `${id}/${line.key} alignment`);
        assert.ok(line.values.every(value => value === null || Number.isFinite(value)), `${id}/${line.key} finite`);
      }
    }
    assert.deepEqual(frozen, snapshot);
  }
});

// Catches fabricated early windows and off-by-one REF/window boundaries.
test('window and lag families wait for their required real observations', () => {
  for (const [id, key, first] of [
    ['VOL', 'MA5', 4], ['KDJ', 'K', 8], ['RSI', 'RSI6', 6], ['BIAS', 'BIAS6', 5],
    ['CCI', 'CCI', 13], ['WR', 'WR10', 9], ['ATR', 'ATR', 13], ['BOLL', 'MID', 19],
    ['ENE', 'UPPER', 9], ['BBI', 'BBI', 23], ['BRAR', 'AR', 25], ['BRAR', 'BR', 26],
    ['VR', 'VR', 26], ['DMI', 'PDI', 14], ['DMI', 'ADX', 19], ['DMI', 'ADXR', 25],
    ['DPO', 'DPO', 31], ['DMA', 'DIF', 49], ['CR', 'CR', 26], ['LWR', 'LWR1', 8],
    ['SKDJ', 'K', 8], ['SKDJ', 'D', 10], ['DKX', 'DKX', 19], ['MTM', 'MTM', 12], ['LON', 'LON', 1],
  ]) {
    const output = values(id, key, bars(Array(120).fill(10)));
    assert.deepEqual(output.slice(0, first), Array(first).fill(null), `${id}/${key} warmup`);
    assert.notEqual(output[first], null, `${id}/${key} first value`);
  }
});

// Catches wrong EMA alpha, signal seeding, and missing Chinese MACD factor of two.
test('MACD uses first-price EMA seeds and twice the signal difference', () => {
  const input = bars([10, 14, 18]);
  near(values('MACD', 'DIF', input)[1], 112 / 351);
  near(values('MACD', 'DEA', input)[1], 112 / 1755);
  near(values('MACD', 'MACD', input)[1], 896 / 1755);
  near(values('EXPMA', 'EMA12', input)[1], 138 / 13);
});

test('volume and OBV retain raw volume and ignore unchanged-price volume', () => {
  const input = bars([10, 12, 12, 11, 15]).map((bar, i) => ({ ...bar, volume: [100, 200, 300, 50, 70][i] }));
  assert.deepEqual(values('VOL', 'VOL', input), [100, 200, 300, 50, 70]);
  near(values('VOL', 'MA5', input)[4], 144);
  assert.deepEqual(values('OBV', 'OBV', input), [0, 200, 200, 150, 220]);
});

test('stochastic, Williams and slow stochastic use the high-low range', () => {
  // At the ninth bar: LL=0, HH=10, C=9; RSV=90, previous K=D=50.
  near(values('KDJ', 'K')[8], 190 / 3);
  near(values('KDJ', 'D')[8], 490 / 9);
  near(values('KDJ', 'J')[8], 730 / 9);
  near(values('WR', 'WR10')[9], 100 / 11);
  near(values('LWR', 'LWR1')[8], 110 / 3);
  near(values('LWR', 'LWR2')[8], 410 / 9);
  near(values('SKDJ', 'K')[8], 90);
  near(values('SKDJ', 'K')[9], 90);
  near(values('SKDJ', 'D')[10], 90);
});

test('RSI uses a full change seed then Wilder smoothing', () => {
  const input = bars([10, 11, 12, 13, 12, 11, 10, 12]);
  near(values('RSI', 'RSI6', input)[6], 50);
  near(values('RSI', 'RSI6', input)[7], 450 / 7);
  near(values('RSI', 'RSI6', bars(Array(7).fill(5)))[6], 50);
});

test('price mean families retain distinct windows and population deviation', () => {
  near(values('BIAS', 'BIAS6')[5], 500 / 7);
  near(values('CCI', 'CCI')[13], 2600 / 21);
  near(values('BOLL', 'MID')[19], 10.5);
  near(values('BOLL', 'UPPER')[19], 10.5 + Math.sqrt(133));
  near(values('BOLL', 'LOWER')[19], 10.5 - Math.sqrt(133));
  near(values('ENE', 'UPPER')[9], 6.105);
  near(values('ENE', 'LOWER')[9], 5.005);
  near(values('ENE', 'ENE')[9], 5.555);
  near(values('BBI', 'BBI')[23], 18.875);
  near(values('DKX', 'DKX')[19], 41 / 3);
});

test('ATR includes gaps and recursively smooths after a full initial window', () => {
  const input = bars(Array(14).fill(10).concat([20]));
  near(values('ATR', 'ATR', input)[13], 2);
  near(values('ATR', 'ATR', input)[14], 37 / 14);
});

test('lagged momentum and difference indicators compare the intended historical values', () => {
  near(values('MTM', 'MTM')[12], 12);
  near(values('MTM', 'MAMTM')[17], 12);
  near(values('DMA', 'DIF')[49], 20);
  near(values('DMA', 'AMA')[58], 20);
  near(values('DPO', 'DPO')[31], 21);
  near(values('DPO', 'MADPO')[36], 21);
  near(values('TRIX', 'TRIX', bars([10, 20]))[1], 100 * (2 / 13) ** 3);
});

test('ratio indicators separate open, prior-close, midprice and direction inputs', () => {
  // Rising bars: H-O=O-L=1, H-prevC=2, prevC-L=0.
  near(values('BRAR', 'AR')[25], 100);
  assert.equal(values('BRAR', 'BR')[26], null); // A positive / zero ratio is undefined.
  near(values('CR', 'CR', bars(Array(27).fill(10)))[26], 100);
  const alternating = bars(Array.from({ length: 27 }, (_, i) => i % 2 ? 12 : 10));
  near(values('VR', 'VR', alternating)[26], 100);
  near(values('DMI', 'PDI')[14], 50);
  near(values('DMI', 'MDI')[14], 0);
  near(values('DMI', 'ADX')[19], 100);
  near(values('DMI', 'ADXR')[25], 100);
});

test('flat denominators produce neutral oscillators or unavailable ratios', () => {
  const flat = bars(Array(120).fill(10), { high: 10, low: 10, volume: 0 });
  for (const [id, key, want] of [['KDJ', 'K', 50], ['LWR', 'LWR1', 50], ['CCI', 'CCI', 0], ['ATR', 'ATR', 0], ['DMI', 'PDI', 0], ['DMI', 'ADX', 0]]) {
    near(values(id, key, flat).at(-1), want);
  }
  for (const [id, key] of [['VR', 'VR'], ['BRAR', 'AR'], ['CR', 'CR']]) assert.equal(values(id, key, flat).at(-1), null);
});

test('LON publicly documented two-bar volume variant uses recursive weight-one smoothing', () => {
  const input = bars([10, 11, 12]);
  // Each increment = (100+100)/(3*100)*1 = 2/3; LONG=2/3,4/3.
  assert.deepEqual(values('LON', 'LON', input).slice(0, 2), [null, 0]);
  near(values('LON', 'LON', input)[2], 1 / 30);
});

test('partial bars contribute observed values without inventing a complete final period', () => {
  const input = bars([10, 11, 12, 13, 14], { partial: true });
  near(values('VOL', 'MA5', input)[4], 100);
  assert.deepEqual(values('BOLL', 'MID', input), Array(5).fill(null));
});

// These asymmetric fixtures distinguish tempting simplifications from each formula.
test('CCI averages typical prices when high-low asymmetry shifts their level', () => {
  const asymmetric = rising.map(bar => ({ ...bar, high: bar.close + 4 }));
  near(values('CCI', 'CCI', asymmetric)[13], 2600 / 21);
});

test('slow stochastic keeps both EMA stages after a price shock', () => {
  const shock = bars(Array(9).fill(10).concat([12, 12]));
  near(values('SKDJ', 'K', shock)[8], 50);
  near(values('SKDJ', 'K', shock)[9], 56.25);
  near(values('SKDJ', 'K', shock)[10], 62.5);
  near(values('SKDJ', 'D', shock)[10], 56.25);
});

test('BR and CR use their distinct prior-price references on asymmetric bars', () => {
  const alternating = bars(Array.from({ length: 27 }, (_, i) => i % 2 ? 12 : 10))
    .map(bar => ({ ...bar, high: bar.close + 2 }));
  near(values('BRAR', 'AR', alternating)[25], 200);
  near(values('BRAR', 'BR', alternating)[26], 400 / 3);
  // Prior midpoint is prior close+0.5; up distances are 3.5/0 and down 0/3.5.
  near(values('CR', 'CR', alternating)[26], 100);
});
