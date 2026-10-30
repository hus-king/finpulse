// Source strings are exchange-local wall times. Encode as UTC solely for the
// chart's ordering, and format with UTC too: browser timezone must not shift a bar.
export function chartTimestamp(date: string): number {
  return Date.parse(`${date.length === 10 ? date + 'T00:00:00' : date.slice(0, 19).replace(' ', 'T')}Z`) / 1000;
}

export function initialChartRange(dates: readonly string[], period: string, mode: string) {
  if (!dates.length) return null;
  // Leave a small gutter so the closing-time label is not clipped by the price axis.
  if (period === '分时') return { from: -.5, to: dates.length + 7.5 };
  const last = dates.at(-1)!;
  const cutoff = new Date(chartTimestamp(last.slice(0, 10)) * 1000);
  cutoff.setUTCMonth(cutoff.getUTCMonth() - (mode === '近 1 月' ? 1 : 3));
  const days = [...new Set(dates.map(date => date.slice(0, 10)))];
  const from = period.endsWith('分钟') ? mode === '当日' ? days.at(-1)! : days.slice(-5)[0] : cutoff.toISOString().slice(0, 10);
  const first = mode === '全部' ? 0 : Math.max(0, dates.findIndex(date => date >= from));
  return { from: first - .5, to: dates.length - 1 };
}
