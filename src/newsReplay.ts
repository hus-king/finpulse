import type { Candle, Dashboard, News } from './types';

export type ReplayHorizon = 1 | 3 | 5;
export type ReplayStatus = 'ready' | 'observing' | 'missing' | 'verify';
export interface ReplayContext {
  server_time: string;
  completed_through: string | null;
  calendar: { date: string; is_trading: boolean | null }[];
  benchmark: { candles: Candle[]; refreshing: boolean; error: string | null };
}
export interface ReplayPoint { date: string; day: number; stock: number | null; benchmark: number | null }
export interface ReplayEvent {
  id: string; news: News; members: News[]; status: ReplayStatus; reason: string;
  baseDate: string | null; basePrice: number | null; elapsedDays: number;
  stockReturn: number | null; benchmarkReturn: number | null; excessReturn: number | null;
  latestReturn: number | null; points: ReplayPoint[];
}

const validDay = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const normalizedTitle = (title: string) => title.normalize('NFKC').toLowerCase().replace(/[\p{P}\p{Z}\s]/gu, '');
function canonicalUrl(value: string) {
  try {
    const url = new URL(value);
    url.hash = '';
    for (const key of [...url.searchParams.keys()]) if (/^utm_|^(spm|from)$/i.test(key)) url.searchParams.delete(key);
    url.searchParams.sort();
    return url.toString();
  } catch { return ''; }
}

function groupNews(news: News[]) {
  const groups: News[][] = [];
  const keys = new Map<string, News[]>();
  for (const item of news) {
    const url = canonicalUrl(item.url), title = normalizedTitle(item.title);
    const itemKeys = [url && `${item.time}:url:${url}`, title && `${item.time}:title:${title}`].filter(Boolean) as string[];
    const matching = [...new Set(itemKeys.map(key => keys.get(key)).filter(Boolean))] as News[][];
    const group = matching[0] ?? [];
    if (!matching.length) groups.push(group);
    for (const other of matching.slice(1)) {
      group.push(...other);
      groups.splice(groups.indexOf(other), 1);
      for (const [key, value] of keys) if (value === other) keys.set(key, group);
    }
    group.push(item);
    for (const key of itemKeys) keys.set(key, group);
  }
  return groups;
}

export function analysisTiming(news: News, baseDate: string | null): 'before' | 'after' | 'unknown' {
  if (!baseDate || !news.analyzed_at || !news.analyzed_at.includes('T')) return 'unknown';
  const analyzed = Date.parse(news.analyzed_at);
  if (!Number.isFinite(analyzed)) return 'unknown';
  return analyzed > Date.parse(`${baseDate}T15:00:00+08:00`) ? 'after' : 'before';
}

export function buildReplayEvents(data: Pick<Dashboard, 'news' | 'candles'>, context: ReplayContext, horizon: ReplayHorizon): ReplayEvent[] {
  const calendar = context.calendar;
  const completed = context.completed_through;
  const prices = (bars: Candle[]) => new Map(bars.filter(bar => !bar.partial && completed && bar.date <= completed && Number.isFinite(bar.close) && bar.close > 0).map(bar => [bar.date, bar.close]));
  const stock = prices(data.candles), benchmark = prices(context.benchmark.candles);
  const change = (base: number | null, price: number | undefined) => base != null && price != null ? (price / base - 1) * 100 : null;
  return groupNews(data.news).map(members => {
    // Keep the most recently saved assessment; never average scores across copies.
    const news = [...members].sort((a, b) => (b.analyzed_at ?? '').localeCompare(a.analyzed_at ?? ''))[0];
    const row: ReplayEvent = { id: members[0].id, news, members, status: 'verify', reason: '', baseDate: null, basePrice: null, elapsedDays: 0, stockReturn: null, benchmarkReturn: null, excessReturn: null, latestReturn: null, points: [] };
    if (!validDay(news.time) || news.date_status === 'missing') { row.reason = '缺少可核验的发布日期'; return row; }
    if (news.date_status === 'conflict') { row.reason = '正文与来源日期冲突，需核验发布日期'; return row; }
    if (news.time > context.server_time.slice(0, 10)) { row.reason = '标注的发布日期晚于当前时间'; return row; }
    if (/股票是什么|代码对应标的|查询方法|质量好不好|使用教程|软件下载/.test(news.title)) { row.reason = '标题更像教程或推广材料，需核验事件相关性'; return row; }
    if (/交易型开放式|证券投资基金.*(?:季度|报告)/.test(news.title) && !/增持|减持|买入|卖出|新进|退出|重仓/.test(news.title)) { row.reason = '基金产品或定期报告，需核验是否包含公司新事件'; return row; }
    if (/\d+\.\d+(?:\.\d+)?.*(?:安装|下载)|^研报资讯频道|^股票[_-].*个股公告/.test(news.title)) { row.reason = '软件页面或资讯索引，需核验独立新闻事件'; return row; }
    const start = calendar.findIndex(day => day.date > news.time && day.is_trading === true);
    const days = start < 0 ? [] : calendar.slice(start).filter(day => day.is_trading === true).slice(0, horizon + 1);
    const lastDay = days.at(-1)?.date;
    if (!completed || news.time < calendar[0]?.date || days.length < horizon + 1 || calendar.some(day => day.date > news.time && day.date <= (lastDay ?? calendar.at(-1)?.date ?? '') && day.is_trading == null)) {
      row.reason = '该观察区间的交易日历尚未核验'; return row;
    }
    row.baseDate = days[0].date;
    row.basePrice = stock.get(row.baseDate) ?? null;
    row.elapsedDays = Math.max(0, days.filter(day => day.date <= completed).length - 1);
    const benchmarkBase = benchmark.get(row.baseDate) ?? null;
    row.points = days.map((day, index) => ({ date: day.date, day: index, stock: change(row.basePrice, stock.get(day.date)), benchmark: change(benchmarkBase, benchmark.get(day.date)) }));
    const elapsed = days.filter(day => day.date <= completed);
    const missing = elapsed.filter(day => !stock.has(day.date));
    const latest = row.points.filter(point => point.date <= completed).at(-1);
    row.latestReturn = latest?.stock ?? null;
    if (missing.length) {
      row.status = 'missing'; row.reason = `${missing[0].date.slice(5)} 等 ${missing.length} 日收盘价缺失，可能停牌或行情未补齐`;
    } else if (days[horizon].date > completed) {
      row.status = 'observing'; row.reason = !elapsed.length ? `等待 ${row.baseDate.slice(5)} 基准日收盘` : `已观察 ${row.elapsedDays} / ${horizon} 个交易日`;
    } else {
      row.status = 'ready'; row.reason = `${horizon} 个交易日观察已完成`;
      row.stockReturn = row.points[horizon].stock;
      row.benchmarkReturn = row.points[horizon].benchmark;
      row.excessReturn = row.stockReturn != null && row.benchmarkReturn != null ? row.stockReturn - row.benchmarkReturn : null;
    }
    return row;
  }).sort((a, b) => b.news.time.localeCompare(a.news.time));
}
