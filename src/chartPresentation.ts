import type { Candle } from './types';
import type { buildIntraday } from './intradayMath';
import type { IndicatorResult } from './technicalIndicators';
import { weeklyCandles } from './chartMath.ts';

type Session = ReturnType<typeof buildIntraday>;
export function preparePlotData(candles: readonly Candle[], period: string, session?: Pick<Session, 'day' | 'times' | 'rows' | 'previousClose'>) {
  const chronological = [...candles].sort((a, b) => a.date.localeCompare(b.date));
  if (period === '分时' && session) {
    const end = session.rows.at(-1)?.date;
    // Calculate on earlier sessions too; only project onto the current day's fixed axis.
    const source = chronological.filter(row => !session.day || row.date.slice(0, 10) < session.day || !!end && row.date <= end);
    const dates = session.times.map(time => `${session.day ?? ''} ${time}:00`);
    const byTime = new Map(session.rows.map(row => [row.date.slice(11, 16), row]));
    const display = session.times.map(time => byTime.get(time) ?? null);
    return { source, dates, display, latestIndex: display.map(row => row !== null).lastIndexOf(true) };
  }
  const source = period === '周 K' ? weeklyCandles(chronological) : chronological;
  return { source, dates: source.map(row => row.date), display: source as (Candle | null)[], latestIndex: source.length - 1 };
}
export type PlotData = ReturnType<typeof preparePlotData>;
export function projectValues(plot: PlotData, values: readonly (number | null)[]): (number | null)[] {
  const indices = new Map(plot.source.map((row, i) => [row.date, i]));
  return plot.dates.map(date => {
    const index = indices.get(date);
    const value = index === undefined ? null : values[index];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  });
}
export function readoutIndex(value: unknown, dates: readonly string[]): number | null {
  if (typeof value === 'number') return Number.isInteger(value) && value >= 0 && value < dates.length ? value : null;
  if (typeof value === 'string') { const index = dates.indexOf(value); return index >= 0 ? index : null; }
  return null;
}
export function formatIndicatorValue(value: number | null | undefined, unit: IndicatorResult['unit']): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (unit === 'volume') return Math.abs(value) >= 10000 ? `${(value / 10000).toFixed(2)} 万股` : `${value.toLocaleString('en-US', { maximumFractionDigits: 2 })} 股`;
  return (Math.abs(value) < 0.005 ? 0 : value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
export function chartLayout(count: number, compact: boolean) {
  const priceTop = 58, priceHeight = compact ? 230 : 260;
  const panes = Array.from({ length: count }, (_, index) => {
    const headerTop = priceTop + priceHeight + 12 + index * 180;
    return { headerTop, gridTop: headerTop + 54, height: 110 };
  });
  return { priceTop, priceHeight, panes, height: count ? panes.at(-1)!.gridTop + 110 + 32 : priceTop + priceHeight + 32 };
}
