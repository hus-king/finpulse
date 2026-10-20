import type { IndicatorId } from './technicalIndicators';

export type IndicatorSelection = {
  mode: 'single' | 'multiple';
  active: IndicatorId;
  selected: IndicatorId[];
};

export type IndicatorCatalogEntry = {
  id: IndicatorId | string;
  label: string;
  description: string;
  available: boolean;
  reason?: string;
};

const publicIndicators: Array<[IndicatorId, string, string]> = [
  ['VOL', '成交量', '成交量及均量线，观察交易活跃程度。'],
  ['MACD', 'MACD', '快慢均线差及信号线，观察趋势与动能。'],
  ['KDJ', 'KDJ', '收盘价在近期高低区间的位置及平滑变化。'],
  ['RSI', 'RSI', '上涨与下跌幅度的相对强弱。'],
  ['BIAS', 'BIAS', '价格偏离移动平均线的幅度。'],
  ['CCI', 'CCI', '典型价格偏离近期均值的程度。'],
  ['WR', 'WR', '收盘价相对近期最高最低价的位置。'],
  ['ATR', 'ATR', '真实波幅的平均值，观察价格波动。'],
  ['BOLL', 'BOLL', '均线及标准差通道，观察波动区间。'],
  ['EXPMA', 'EXPMA', '对近期价格赋予更高权重的指数均线。'],
  ['ENE', 'ENE', '移动平均线按比例上下延伸形成的轨道。'],
  ['OBV', 'OBV', '按价格涨跌方向累积成交量。'],
  ['BBI', 'BBI', '多条不同周期均线的综合均值。'],
  ['BRAR', 'BRAR', '比较开盘及前收盘附近的买卖力量。'],
  ['TRIX', 'TRIX', '三重指数平滑价格的变化率。'],
  ['VR', 'VR', '上涨与下跌交易日的成交量比率。'],
  ['DMI', 'DMI', '方向运动及趋势强度。'],
  ['DPO', 'DPO', '移位均线去除价格趋势后的波动。'],
  ['DMA', 'DMA', '长短周期均线差及其平均线。'],
  ['CR', 'CR', '相对前一周期中间价的上涨与下跌力量。'],
  ['LWR', 'LWR', '价格区间相对位置的双重平滑线。'],
  ['SKDJ', 'SKDJ', '随机指标的慢速平滑版本。'],
  ['DKX', '多空线', '加权价格形成的多空趋势线。'],
  ['MTM', 'MTM', '当前价格相对若干周期前的变化。'],
  ['LON', 'LON', '结合价格变化与成交量的长线动能。'],
];

const unavailableIndicators: Array<[string, string]> = [
  ['主买净额', '需要带主动买卖方向的逐笔成交数据，当前行情源未提供。'],
  ['大单净量', '需要逐笔成交的大单划分及买卖方向，当前行情源未提供。'],
  ['大单净额', '需要逐笔成交金额、大单划分及买卖方向，当前行情源未提供。'],
  ['主力意愿', '依赖平台专有资金分类及计算规则，当前无法可靠复现。'],
  ['散户线', '依赖账户持仓或平台专有筹码数据，当前行情源未提供。'],
  ['买卖力道', '需要逐笔买卖方向及盘口委托数据，当前行情源未提供。'],
  ['买卖差', '需要历史买卖委托或主动成交分类，当前行情源未提供。'],
  ['分时DDX', '需要分时逐笔大单买卖分类数据，当前行情源未提供。'],
  ['分时博弈', '依赖平台专有资金分组与分时算法，当前无法可靠复现。'],
  ['暗盘资金', '依赖平台专有资金识别规则及逐笔数据，当前无法可靠复现。'],
  ['机构监控', '需要机构账户识别或专有交易分类，当前行情源未提供。'],
  ['散户监控', '需要账户类型识别或专有交易分类，当前行情源未提供。'],
  ['MACD智能参数', '智能参数规则未公开，当前仅提供公开公式的 MACD。'],
  ['KDJ智能参数', '智能参数规则未公开，当前仅提供公开公式的 KDJ。'],
  ['RSI智能参数', '智能参数规则未公开，当前仅提供公开公式的 RSI。'],
  ['WR智能参数', '智能参数规则未公开，当前仅提供公开公式的 WR。'],
  ['九转研判', '平台研判与信号过滤规则未公开，当前无法可靠复现。'],
  ['换手率', '需要逐日流通股本历史，当前行情源未提供完整字段。'],
  ['量比', '需要对应时点的历史均量基准，当前行情源未提供完整字段。'],
  ['竞昨量比', '需要集合竞价量及昨日对应时点成交量，当前行情源未提供。'],
  ['竞昨成交比', '需要集合竞价成交额及昨日对应成交额，当前行情源未提供。'],
  ['今昨成交比', '需要今昨同一时点的累计成交额历史，当前行情源未提供。'],
  ['成交额', '当前历史K线缺少逐周期真实成交额，无法用成交量准确替代。'],
  ['市盈率', '需要历史股价及对应期每股收益，当前缺少完整财务历史。'],
  ['市净率', '需要历史股价及对应期每股净资产，当前缺少完整财务历史。'],
  ['市销率', '需要历史市值及对应期营业收入，当前缺少完整财务历史。'],
  ['市现率', '需要历史市值及对应期经营现金流，当前缺少完整财务历史。'],
];

export const indicatorCatalog: IndicatorCatalogEntry[] = [
  ...publicIndicators.map(([id, label, description]) => ({ id, label, description, available: true })),
  ...unavailableIndicators.map(([label, reason]) => ({ id: `data:${label}`, label, description: reason, available: false, reason })),
];

const knownIds = new Set<string>(publicIndicators.map(([id]) => id));
const isIndicatorId = (id: unknown): id is IndicatorId => typeof id === 'string' && knownIds.has(id);

export function normalizeIndicatorSelection(value: unknown): IndicatorSelection {
  if (typeof value === 'string') {
    const active = value === '成交量' ? 'VOL' : isIndicatorId(value) ? value : 'VOL';
    return { mode: 'single', active, selected: [active] };
  }
  const input = value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
  const mode = input.mode === 'multiple' ? 'multiple' : 'single';
  let selected = Array.isArray(input.selected) ? [...new Set(input.selected.filter(isIndicatorId))].slice(0, 4) : [];
  const active = isIndicatorId(input.active) ? input.active : selected[0] ?? 'VOL';
  if (!selected.includes(active)) selected = [active, ...selected].slice(0, 4);
  return { mode, active, selected };
}

export function selectIndicator(selection: IndicatorSelection, id: IndicatorId): IndicatorSelection {
  const current = normalizeIndicatorSelection(selection);
  if (!isIndicatorId(id)) return current;
  if (current.mode === 'single') return { mode: 'single', active: id, selected: [id] };
  if (current.selected.includes(id)) {
    if (current.selected.length === 1) return current;
    const selected = current.selected.filter(value => value !== id);
    return { ...current, active: current.active === id ? selected[0] : current.active, selected };
  }
  if (current.selected.length === 4) return current;
  return { ...current, active: id, selected: [...current.selected, id] };
}

export function displayedIndicators(selection: IndicatorSelection): IndicatorId[] {
  const current = normalizeIndicatorSelection(selection);
  return current.mode === 'single' ? [current.active] : current.selected;
}
