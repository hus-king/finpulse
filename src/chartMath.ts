import type { Candle } from './types';

export type ChartStyle = 'candles' | 'hollow' | 'heikin' | 'line' | 'area';
export type ChartOverlay = 'none' | 'MA' | 'EMA' | 'BOLL';

function checkPeriod(period: number) {
  if (!Number.isInteger(period) || period < 1) throw new RangeError('Indicator period must be a positive integer.');
}

export function movingAverage(values: readonly number[], period: number): (number | null)[] {
  checkPeriod(period);
  let sum = 0;
  return values.map((value, index) => {
    sum += value;
    if (index >= period) sum -= values[index - period];
    return index < period - 1 ? null : sum / period;
  });
}

export function exponentialMovingAverage(values: readonly number[], period: number): number[] {
  checkPeriod(period);
  const result: number[] = [];
  const alpha = 2 / (period + 1);
  values.forEach((value, index) => result.push(index === 0 ? value : alpha * value + (1 - alpha) * result[index - 1]));
  return result;
}

export function bollingerBands(values: readonly number[], period = 20) {
  const middle = movingAverage(values, period);
  const upper: (number | null)[] = [];
  const lower: (number | null)[] = [];
  middle.forEach((mean, index) => {
    if (mean == null) { upper.push(null); lower.push(null); return; }
    let variance = 0;
    for (let i = index - period + 1; i <= index; i++) variance += (values[i] - mean) ** 2;
    const width = 2 * Math.sqrt(variance / period);
    upper.push(mean + width);
    lower.push(mean - width);
  });
  return { middle, upper, lower };
}

export function heikinAshi(candles: readonly Candle[]): Candle[] {
  const result: Candle[] = [];
  candles.forEach((candle, index) => {
    const previous = result[index - 1];
    const open = previous ? (previous.open + previous.close) / 2 : (candle.open + candle.close) / 2;
    const close = (candle.open + candle.close + candle.low + candle.high) / 4;
    result.push({ ...candle, open, close, high: Math.max(candle.high, open, close), low: Math.min(candle.low, open, close) });
  });
  return result;
}

export function weeklyCandles(candles: readonly Candle[]): Candle[] {
  const groups = new Map<string, Candle>();
  for (const candle of [...candles].sort((a, b) => a.date.localeCompare(b.date))) {
    const day = new Date(candle.date + 'T12:00:00Z');
    day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
    const key = day.toISOString().slice(0, 10);
    const previous = groups.get(key);
    groups.set(key, previous ? {
      ...previous, close: candle.close, high: Math.max(previous.high, candle.high),
      low: Math.min(previous.low, candle.low), volume: previous.volume + candle.volume,
      ...(previous.partial || candle.partial ? { partial: true } : {}),
    } : { ...candle, date: key });
  }
  return [...groups.values()];
}

export function relativeStrengthIndex(values: readonly number[], period = 14): (number | null)[] {
  checkPeriod(period);
  let gain = 0, loss = 0;
  return values.map((value, index) => {
    if (index === 0) return null;
    const diff = value - values[index - 1];
    if (index <= period) { gain += Math.max(diff, 0) / period; loss += Math.max(-diff, 0) / period; }
    else { gain = (gain * (period - 1) + Math.max(diff, 0)) / period; loss = (loss * (period - 1) + Math.max(-diff, 0)) / period; }
    return index < period ? null : loss === 0 ? gain === 0 ? 50 : 100 : 100 - 100 / (1 + gain / loss);
  });
}
