export type HolderGroup = 'huijin' | 'social-security' | 'csf';
type Holder = { name: string; shares: number; group: HolderGroup };
type Report = {
  report_date: string;
  publication_date: string | null;
  source_url: string | null;
  total_shares?: number;
  status: 'disclosed' | 'anonymous' | 'missing';
  holders: Holder[];
};
type ShareConversion = { date: string; ratio: string; source_url: string };
export type Fund = { code: string; name: string; reports: Report[]; conversions?: ShareConversion[] };
export type Close = { date: string; close: number };
export type DateBasis = 'published' | 'period-end';
export type Period = Report & {
  matches: Holder[];
  shares: number | null;
  ownershipPercent: number | null;
  change: number | null;
  changeNote: string;
};

function holderNames(holders: Holder[]) {
  return holders.map(({ name }) => name.normalize('NFKC').replace(/\s/g, '')).sort().join('\n');
}

function conversionFactor(ratio: string) {
  const match = /^(\d+)\s*份[拆合]为\s*(\d+)\s*份$/.exec(ratio);
  return match && Number(match[1]) > 0 && Number(match[2]) > 0 ? Number(match[2]) / Number(match[1]) : null;
}

export function holdingPeriods(fund: Fund, group: HolderGroup | 'all'): Period[] {
  const reports = [...fund.reports].sort((a, b) => a.report_date.localeCompare(b.report_date));
  const selected = reports.map(report => {
    const matches = report.status === 'disclosed' ? report.holders.filter(holder => group === 'all' || holder.group === group) : [];
    const shares = matches.length ? matches.reduce((sum, holder) => sum + holder.shares, 0) : null;
    const ownershipPercent = shares != null && report.total_shares && report.total_shares > 0
      ? 100 * shares / report.total_shares
      : null;
    return { ...report, matches, shares, ownershipPercent };
  });

  return selected.map((report, index) => {
    const previous = selected[index - 1];
    if (!previous || previous.shares == null || report.shares == null) {
      return { ...report, change: null, changeNote: '缺少相邻具名披露' };
    }
    if (holderNames(previous.matches) !== holderNames(report.matches)) {
      return { ...report, change: null, changeNote: '具名账户名单变化' };
    }
    const factors = (fund.conversions ?? [])
      .filter(({ date }) => previous.report_date < date && date <= report.report_date)
      .map(({ ratio }) => conversionFactor(ratio));
    if (factors.some(factor => factor == null)) return { ...report, change: null, changeNote: '缺少份额折算比例' };
    const adjusted = Math.round(factors.reduce<number>((shares, factor) => shares * (factor ?? 1), previous.shares));
    return { ...report, change: report.shares - adjusted, changeNote: factors.length ? '已折算拆分或合并' : '' };
  });
}

export function indexRangePosition(index: Close[], at: number): number | null {
  if (at < 251) return null;
  const values = index.slice(at - 251, at + 1).map(point => point.close);
  const low = Math.min(...values);
  const span = Math.max(...values) - low;
  return span > 0 ? (index[at].close - low) / span * 100 : null;
}

export function disclosureEvents(periods: Period[], index: Close[], basis: DateBasis) {
  return periods.flatMap(period => {
    if (period.change == null || period.change === 0) return [];
    const cutoff = basis === 'published' ? period.publication_date : period.report_date;
    if (!cutoff) return [];
    // Publication dates have no time of day. Wait until the next session
    // so an announcement after the close cannot use that same day's quote.
    const firstAfter = index.findIndex(({ date }) => date > cutoff);
    if (basis === 'published' && firstAfter < 0) return [];
    if (basis === 'period-end' && period.report_date < index[0]?.date) return [];
    const at = basis === 'published' ? firstAfter : (firstAfter < 0 ? index.length - 1 : firstAfter - 1);
    if (at < 0) return [];
    return [{ period, change: period.change, ...index[at], position: indexRangePosition(index, at) }];
  });
}
