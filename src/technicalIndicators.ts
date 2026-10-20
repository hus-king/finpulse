import type { Candle } from './types';

export type IndicatorId = 'VOL' | 'MACD' | 'KDJ' | 'RSI' | 'BIAS' | 'CCI' | 'WR' | 'ATR' | 'BOLL' | 'EXPMA' | 'ENE' | 'OBV' | 'BBI' | 'BRAR' | 'TRIX' | 'VR' | 'DMI' | 'DPO' | 'DMA' | 'CR' | 'LWR' | 'SKDJ' | 'DKX' | 'MTM' | 'LON';
export interface IndicatorLine {
  key: string;
  label: string;
  description: string;
  values: (number | null)[];
  kind: 'line' | 'bar';
  colorIndex: number;
}
export interface IndicatorResult {
  id: IndicatorId;
  label: string;
  parameters: string;
  description: string;
  unit: 'price' | 'percent' | 'volume' | 'ratio' | 'number';
  lines: IndicatorLine[];
  range?: [number, number];
  zero?: boolean;
}

type Value = number | null;
type Series = readonly Value[];
const finite = (value: number): Value => Number.isFinite(value) ? value : null;
function combine(inputs: Series[], operation: (...values: number[]) => number): Value[] {
  return (inputs[0] ?? []).map((_, i) => {
    const values = inputs.map(input => input[i]);
    return values.some(value => value == null) ? null : finite(operation(...values as number[]));
  });
}
function windowed(input: Series, period: number, operation: (window: number[]) => number): Value[] {
  return input.map((_, i) => {
    if (i < period - 1) return null;
    const window = input.slice(i - period + 1, i + 1);
    return window.some(value => value == null) ? null : finite(operation(window as number[]));
  });
}
const sum = (window: number[]) => window.reduce((a, b) => a + b, 0);
const ma = (input: Series, n: number) => windowed(input, n, window => sum(window) / n);
const total = (input: Series, n: number) => windowed(input, n, sum);
const lag = (input: Series, n = 1): Value[] => input.map((_, i) => i < n ? null : input[i - n]);
const difference = (a: Series, b: Series) => combine([a, b], (x, y) => x - y);
const ratio = (a: Series, b: Series, zero: Value = null) => combine([a, b], (x, y) => y === 0 ? zero ?? NaN : 100 * x / y);
function smooth(input: Series, alpha: number, seedPeriod = 1, initial?: number): Value[] {
  let previous: Value = initial ?? null;
  let seed: number[] = [];
  return input.map(value => {
    if (value == null) { previous = initial ?? null; seed = []; return null; }
    if (previous == null) {
      seed.push(value);
      if (seed.length < seedPeriod) return null;
      previous = sum(seed) / seedPeriod;
    } else previous += alpha * (value - previous);
    previous = finite(previous);
    return previous;
  });
}
const ema = (input: Series, n: number) => smooth(input, 2 / (n + 1));
const wilder = (input: Series, n: number) => smooth(input, 1 / n, n);
const positive = (input: Series) => combine([input], value => Math.max(value, 0));

/** Input must be chronological raw OHLCV bars, before any display transformation. */
export function calculateIndicator(id: IndicatorId, candles: readonly Candle[]): IndicatorResult {
  const c = candles.map(bar => finite(bar.close)), h = candles.map(bar => finite(bar.high));
  const l = candles.map(bar => finite(bar.low)), o = candles.map(bar => finite(bar.open));
  const v = candles.map(bar => finite(bar.volume));
  const previous = lag(c), change = difference(c, previous);
  const lines: IndicatorLine[] = [];
  let label: string = id, parameters = '', description = '';
  let unit: IndicatorResult['unit'] = 'number';
  let range: [number, number] | undefined, zero = false;
  const add = (key: string, values: Series, detail: string, kind: IndicatorLine['kind'] = 'line') => {
    lines.push({ key, label: key, description: detail, values: values.map(value => value == null ? null : finite(value)), kind, colorIndex: lines.length });
  };
  const trueRange = () => h.map((high, i): Value => {
    const low = l[i];
    if (high == null || low == null || (i > 0 && previous[i] == null)) return null;
    return i === 0 ? high - low : Math.max(high - low, Math.abs(high - previous[i]!), Math.abs(low - previous[i]!));
  });
  const stochastic = (period: number, reverse = false) => {
    const high = windowed(h, period, window => Math.max(...window));
    const low = windowed(l, period, window => Math.min(...window));
    return combine([c, high, low], (close, top, bottom) => top === bottom ? 50 : 100 * (reverse ? top - close : close - bottom) / (top - bottom));
  };

  switch (id) {
    case 'VOL': {
      label = '成交量'; parameters = '5,10'; description = '原始成交量及简单均量线'; unit = 'volume';
      add('VOL', v, '原始成交量', 'bar');
      for (const n of [5, 10]) add(`MA${n}`, ma(v, n), `${n}周期简单均量`);
      break;
    }
    case 'MACD': {
      label = '平滑异同均线'; parameters = '12,26,9'; description = '快慢指数均线差及信号线，柱值为差值的两倍'; unit = 'price'; zero = true;
      const dif = difference(ema(c, 12), ema(c, 26)), dea = ema(dif, 9);
      add('DIF', dif, 'EMA12 − EMA26'); add('DEA', dea, 'DIF的9周期EMA');
      add('MACD', combine([dif, dea], (a, b) => 2 * (a - b)), '2 × (DIF − DEA)', 'bar');
      break;
    }
    case 'KDJ': {
      label = '随机指标'; parameters = '9,3,3'; description = '9周期价格位置经递归平滑，K、D初始中性值50；J可超出0–100'; unit = 'percent';
      const k = smooth(stochastic(9), 1 / 3, 1, 50), d = smooth(k, 1 / 3, 1, 50);
      add('K', k, 'RSV的权重1/3递归平滑'); add('D', d, 'K的权重1/3递归平滑'); add('J', combine([k, d], (a, b) => 3 * a - 2 * b), '3K − 2D');
      break;
    }
    case 'RSI': {
      label = '相对强弱指标'; parameters = '6,12,24'; description = 'Wilder平均涨跌幅计算强弱；需完整变化周期'; unit = 'percent'; range = [0, 100];
      for (const n of [6, 12, 24]) {
        const gain = wilder(positive(change), n), loss = wilder(positive(combine([change], x => -x)), n);
        add(`RSI${n}`, combine([gain, loss], (a, b) => b === 0 ? a === 0 ? 50 : 100 : 100 * a / (a + b)), `${n}周期Wilder RSI`);
      }
      break;
    }
    case 'BIAS': {
      label = '乖离率'; parameters = '6,12,24'; description = '收盘价偏离简单均线的百分比'; unit = 'percent'; zero = true;
      for (const n of [6, 12, 24]) { const average = ma(c, n); add(`BIAS${n}`, ratio(difference(c, average), average), `${n}周期乖离率`); }
      break;
    }
    case 'CCI': {
      label = '顺势指标'; parameters = '14'; description = '典型价相对均值的偏离，以平均绝对偏差归一化'; zero = true;
      const typical = combine([h, l, c], (a, b, d) => (a + b + d) / 3);
      add('CCI', windowed(typical, 14, window => {
        const mean = sum(window) / 14, deviation = sum(window.map(x => Math.abs(x - mean))) / 14;
        return deviation === 0 ? 0 : (window[13] - mean) / (0.015 * deviation);
      }), '(典型价 − MA14) / (0.015 × 平均绝对偏差)');
      break;
    }
    case 'WR': {
      label = '威廉指标'; parameters = '10,6'; description = '0–100正值表示法；高值表示收盘更靠近区间低点'; unit = 'percent'; range = [0, 100];
      for (const n of [10, 6]) add(`WR${n}`, stochastic(n, true), `${n}周期正值威廉指标`);
      break;
    }
    case 'ATR': {
      label = '平均真实波幅'; parameters = '14'; description = '真实波幅的14周期Wilder均值；首柱使用高低差'; unit = 'price';
      add('ATR', wilder(trueRange(), 14), '真实波幅的14周期Wilder平滑');
      break;
    }
    case 'BOLL': {
      label = '布林线'; parameters = '20,2'; description = '20周期均值及上下两倍总体标准差'; unit = 'price';
      const middle = ma(c, 20), deviation = windowed(c, 20, window => {
        const mean = sum(window) / 20;
        return Math.sqrt(sum(window.map(x => (x - mean) ** 2)) / 20);
      });
      add('MID', middle, '20周期收盘价均值'); add('UPPER', combine([middle, deviation], (a, b) => a + 2 * b), '中轨 + 2倍总体标准差'); add('LOWER', combine([middle, deviation], (a, b) => a - 2 * b), '中轨 − 2倍总体标准差');
      break;
    }
    case 'EXPMA': {
      label = '指数平均线'; parameters = '12,50'; description = '首个收盘价种子的指数均线'; unit = 'price';
      for (const n of [12, 50]) add(`EMA${n}`, ema(c, n), `${n}周期EMA，权重2/(${n}+1)`);
      break;
    }
    case 'ENE': {
      label = '轨道线'; parameters = '10,11,9'; description = '10周期简单均线上浮11%、下浮9%及轨道中点'; unit = 'price';
      const base = ma(c, 10), upper = combine([base], x => x * 1.11), lower = combine([base], x => x * 0.91);
      add('UPPER', upper, 'MA10 × 1.11'); add('LOWER', lower, 'MA10 × 0.91'); add('ENE', combine([upper, lower], (a, b) => (a + b) / 2), '上下轨道中点');
      break;
    }
    case 'OBV': {
      label = '能量潮'; parameters = '30'; description = '按收盘涨跌累积原始成交量；首柱基线为0'; unit = 'volume'; zero = true;
      let accumulated = 0;
      const obv = c.map((close, i): Value => {
        if (close == null || v[i] == null || (i > 0 && previous[i] == null)) return null;
        if (i > 0) accumulated += Math.sign(close - previous[i]!) * v[i]!;
        return finite(accumulated);
      });
      add('OBV', obv, '累计符号成交量，平盘不计'); add('MAOBV', ma(obv, 30), 'OBV的30周期简单均线');
      break;
    }
    case 'BBI': {
      label = '多空指数'; parameters = '3,6,12,24'; description = '四条不同周期简单均线的等权均值'; unit = 'price';
      add('BBI', combine([ma(c, 3), ma(c, 6), ma(c, 12), ma(c, 24)], (...averages) => sum(averages) / 4), '(MA3 + MA6 + MA12 + MA24) / 4');
      break;
    }
    case 'BRAR': {
      label = '人气意愿指标'; parameters = '26'; description = 'AR比较高低价与开盘，BR比较高低价与前收盘'; unit = 'ratio';
      add('AR', ratio(total(difference(h, o), 26), total(difference(o, l), 26)), '100 × SUM(H−O,26) / SUM(O−L,26)');
      add('BR', ratio(total(positive(difference(h, previous)), 26), total(positive(difference(previous, l)), 26)), '100 × SUM(MAX(H−前收,0),26) / SUM(MAX(前收−L,0),26)');
      break;
    }
    case 'TRIX': {
      label = '三重指数平均'; parameters = '12,20'; description = '三重12周期EMA的单周期变化率及20周期简单信号线'; unit = 'percent'; zero = true;
      const triple = ema(ema(ema(c, 12), 12), 12), trix = ratio(difference(triple, lag(triple)), lag(triple));
      add('TRIX', trix, '三重EMA的百分比变化'); add('MATRIX', ma(trix, 20), 'TRIX的20周期简单均线');
      break;
    }
    case 'VR': {
      label = '成交量变异率'; parameters = '26,6'; description = '上涨量与下跌量之比，平盘量各分一半'; unit = 'ratio';
      const up = combine([change, v], (d, volume) => d > 0 ? volume : d === 0 ? volume / 2 : 0);
      const down = combine([change, v], (d, volume) => d < 0 ? volume : d === 0 ? volume / 2 : 0);
      const vr = ratio(total(up, 26), total(down, 26));
      add('VR', vr, '100 × (上涨量 + 平盘量/2) / (下跌量 + 平盘量/2)'); add('MAVR', ma(vr, 6), 'VR的6周期简单均线');
      break;
    }
    case 'DMI': {
      label = '趋向指标'; parameters = '14,6'; description = '14周期滚动方向运动及6周期ADX均值；采用公开滚动和变体'; unit = 'percent'; range = [0, 100];
      const up = difference(h, lag(h)), down = difference(lag(l), l);
      const plus = combine([up, down], (a, b) => a > 0 && a > b ? a : 0);
      const minus = combine([up, down], (a, b) => b > 0 && b > a ? b : 0);
      const tr = trueRange().map((value, i) => i === 0 ? null : value), denominator = total(tr, 14);
      const pdi = ratio(total(plus, 14), denominator, 0), mdi = ratio(total(minus, 14), denominator, 0);
      const dx = combine([pdi, mdi], (a, b) => a + b === 0 ? 0 : 100 * Math.abs(a - b) / (a + b));
      const adx = ma(dx, 6);
      add('PDI', pdi, '100 × 14周期正方向运动和 / 真实波幅和'); add('MDI', mdi, '100 × 14周期负方向运动和 / 真实波幅和'); add('ADX', adx, 'DX的6周期简单均线'); add('ADXR', combine([adx, lag(adx, 6)], (a, b) => (a + b) / 2), '当前ADX与6周期前ADX的均值');
      break;
    }
    case 'DPO': {
      label = '区间震荡线'; parameters = '21,11,6'; description = '当前收盘价减去11周期前的21周期均值；无未来数据'; unit = 'price'; zero = true;
      const dpo = difference(c, lag(ma(c, 21), 11));
      add('DPO', dpo, 'C − REF(MA(C,21),11)'); add('MADPO', ma(dpo, 6), 'DPO的6周期简单均线');
      break;
    }
    case 'DMA': {
      label = '平均线差'; parameters = '10,50,10'; description = '10与50周期简单均线差及10周期信号均线'; unit = 'price'; zero = true;
      const dif = difference(ma(c, 10), ma(c, 50));
      add('DIF', dif, 'MA10 − MA50'); add('AMA', ma(dif, 10), 'DIF的10周期简单均线');
      break;
    }
    case 'CR': {
      label = '能量指标'; parameters = '26,5,10,20'; description = '以前一柱高低均价为中价的26周期强弱比及简单均线'; unit = 'ratio';
      const mid = lag(combine([h, l], (a, b) => (a + b) / 2));
      const cr = ratio(total(positive(difference(h, mid)), 26), total(positive(difference(mid, l)), 26));
      add('CR', cr, '100 × SUM(MAX(H−前中价,0),26) / SUM(MAX(前中价−L,0),26)');
      for (const n of [5, 10, 20]) add(`MA${n}`, ma(cr, n), `CR的${n}周期简单均线，未位移`);
      break;
    }
    case 'LWR': {
      label = '慢速威廉指标'; parameters = '9,3,3'; description = '正值威廉位置的两级权重1/3递归平滑；初始中性值50'; unit = 'percent'; range = [0, 100];
      const first = smooth(stochastic(9, true), 1 / 3, 1, 50);
      add('LWR1', first, 'WR9的递归平滑'); add('LWR2', smooth(first, 1 / 3, 1, 50), 'LWR1的递归平滑');
      break;
    }
    case 'SKDJ': {
      label = '慢速随机指标'; parameters = '9,3'; description = '9周期RSV经两次EMA3及MA3；首个有效RSV种子'; unit = 'percent'; range = [0, 100];
      const k = ema(ema(stochastic(9), 3), 3);
      add('K', k, 'EMA(EMA(RSV9,3),3)'); add('D', ma(k, 3), 'K的3周期简单均线');
      break;
    }
    case 'DKX': {
      label = '多空线'; parameters = '20,10'; description = 'OHLC基准价的连续20周期线性加权均值及10周期信号线'; unit = 'price';
      const mid = combine([c, l, o, h], (close, low, open, high) => (3 * close + low + open + high) / 6);
      const dkx = windowed(mid, 20, window => sum(window.map((x, i) => x * (i + 1))) / 210);
      add('DKX', dkx, '连续20柱MID线性加权，最近权重20'); add('MADKX', ma(dkx, 10), 'DKX的10周期简单均线');
      break;
    }
    case 'MTM': {
      label = '动量指标'; parameters = '12,6'; description = '当前收盘与12周期前收盘的价格差及6周期信号线'; unit = 'price'; zero = true;
      const mtm = difference(c, lag(c, 12));
      add('MTM', mtm, 'C − REF(C,12)'); add('MAMTM', ma(mtm, 6), 'MTM的6周期简单均线');
      break;
    }
    case 'LON': {
      label = '钱龙长线'; parameters = '10,20,10'; description = '公开双柱成交量变体：累计量价能量的权重1/10与1/20平滑差；非厂商逐值复刻'; zero = true;
      const pairRange = difference(windowed(h, 2, window => Math.max(...window)), windowed(l, 2, window => Math.min(...window)));
      const increment = combine([total(v, 2), pairRange, change], (volume, width, delta) => width === 0 ? 0 : volume * delta / (100 * width));
      let cumulative = 0;
      const energy = increment.map(value => value == null ? null : finite(cumulative += value));
      const lon = difference(smooth(energy, 1 / 10), smooth(energy, 1 / 20));
      add('LON', lon, 'SMA(LONG,10,1) − SMA(LONG,20,1)'); add('LONMA', ma(lon, 10), 'LON的10周期简单均线');
      break;
    }
  }
  return { id, label, parameters, description, unit, lines, ...(range ? { range } : {}), ...(zero ? { zero } : {}) };
}
