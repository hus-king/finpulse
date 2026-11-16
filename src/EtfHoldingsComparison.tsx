import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { BarChart3, ExternalLink } from 'lucide-react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { DataZoomComponent, GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { api } from './api';
import useEtfSnapshot from './useEtfSnapshot';
import { number } from './format';
import { useTheme } from './ThemeContext';
import './etf-funds.css';

echarts.use([LineChart, DataZoomComponent, GridComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

type HolderGroup = 'huijin' | 'social-security' | 'csf';
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
type Fund = { code: string; name: string; reports: Report[]; conversions?: ShareConversion[] };
type Close = { date: string; close: number };
type Period = Report & { matches: Holder[]; shares: number | null; ownershipPercent: number | null };
type AxisPoint = { axisValue: number };

const groups = [
  { id: 'all', label: '已披露合计' },
  { id: 'huijin', label: '汇金' },
  { id: 'social-security', label: '社保' },
  { id: 'csf', label: '证金' },
] as const;
const units = (shares: number) => number(shares / 1e8);
const SOURCE_RULE_URL = 'https://www.amac.org.cn/xwfb/xhyw/202603/t20260313_27472.html';

function reportStatus(period: Period): string {
  if (period.status === 'missing') return '暂缺报告原文';
  if (period.status === 'anonymous') return '未披露具名持有人';
  return '名单未列该机构';
}

async function indexArchive(signal: AbortSignal): Promise<Close[]> {
  const response = await fetch('/market-archives/sse-composite-close.csv', { signal });
  if (!response.ok) throw new Error('上证指数历史暂不可用');
  return (await response.text()).trim().split('\n').slice(1).map(line => {
    const [date, close] = line.split(',');
    return { date, close: Number(close) };
  }).filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && Number.isFinite(row.close) && row.close > 0);
}

async function loadHoldingsArchive(_key: string, signal: AbortSignal) {
  const [funds, index] = await Promise.all([
    api<Fund[]>('/market-archives/etf-holder-disclosures.json', undefined, signal),
    indexArchive(signal),
  ]);
  if (!funds.length || !index.length) throw new Error('持仓或行情档案为空');
  return { funds, index };
}

async function loadIndexCloses(key: string, signal: AbortSignal): Promise<Close[]> {
  const { candles } = await api<{ candles: Close[] }>(key, undefined, signal);
  return candles.filter(point => Number.isFinite(point.close) && point.close > 0).map(({ date, close }) => ({ date, close }));
}

function HoldingsChart({ index, periods, fund, first }: {
  index: Close[]; periods: Period[]; fund: Fund; first: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const chart = useRef<ReturnType<typeof echarts.init> | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    if (!root.current) return;
    const instance = echarts.init(root.current);
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); instance.dispose(); chart.current = null; };
  }, []);

  useEffect(() => {
    const style = getComputedStyle(document.documentElement);
    const color = (name: string) => style.getPropertyValue(name).trim();
    const holdingColor = color('--theme-warning');
    const indexColor = color('--chart-marker-neutral');
    const border = color('--chart-border');
    const axisLabel = { fontSize: 10, hideOverlap: true };
    const dates = new Map(index.map(point => [point.date, point.close]));
    const reports = new Map(periods.map(period => [period.report_date, period]));
    const last = index.at(-1)?.date ?? first;
    const conversions = fund.conversions ?? [];
    const holdingPoints = periods.map(({ report_date, ownershipPercent }) =>
      [Date.parse(report_date), ownershipPercent]);

    chart.current?.setOption({
      useUTC: true,
      animation: false,
      grid: { left: 9, right: 9, top: 32, bottom: 52, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      tooltip: {
        trigger: 'axis', renderMode: 'richText', confine: true, textStyle: { color: color('--chart-tooltip-text'), fontSize: 11 },
        backgroundColor: color('--chart-tooltip-bg'), borderColor: border,
        formatter: (items: unknown) => {
          const timestamp = Number((items as AxisPoint[])[0]?.axisValue);
          if (!Number.isFinite(timestamp)) return '';
          const day = new Date(timestamp).toISOString().slice(0, 10);
          const close = dates.get(day);
          const report = reports.get(day);
          const lines = [day];
          if (close != null) lines.push(`上证指数 ${number(close)} 点`);
          if (report) {
            const { shares, ownershipPercent, publication_date: published } = report;
            if (shares == null) {
              lines.push(reportStatus(report));
            } else {
              if (ownershipPercent != null) lines.push(`持仓占比 ${number(ownershipPercent)}%`);
              lines.push(`披露份额 ${shares.toLocaleString('zh-CN')} 份`);
            }
            if (published) lines.push(`公告 ${published}`);
          }
          return lines.join('\n');
        },
      },
      xAxis: {
        type: 'time', min: Date.parse(first), max: Date.parse(last), boundaryGap: false,
        axisLine: { lineStyle: { color: border } }, axisTick: { show: false },
        axisLabel: { ...axisLabel, color: color('--chart-text') },
      },
      yAxis: [
        { type: 'value', min: 0, name: '持仓占比（%）', nameTextStyle: { color: holdingColor, fontSize: 11, align: 'left' },
          axisLabel: { ...axisLabel, color: holdingColor }, splitLine: { lineStyle: { color: color('--chart-grid'), type: 'dashed' } } },
        { type: 'value', scale: true, name: '上证指数（点）', nameTextStyle: { color: indexColor, fontSize: 11, align: 'right' },
          axisLabel: { ...axisLabel, color: indexColor }, splitLine: { show: false } },
      ],
      dataZoom: [{
        type: 'slider', xAxisIndex: 0, bottom: 4, height: 18, filterMode: 'none', showDetail: false,
        showDataShadow: false, borderColor: border, backgroundColor: color('--theme-panel'),
        fillerColor: color('--theme-accent-soft'), handleStyle: { color: indexColor },
        brushSelect: false,
      }],
      series: [
        {
          name: '上证指数', type: 'line', yAxisIndex: 1, showSymbol: false, sampling: 'lttb',
          lineStyle: { color: indexColor, width: 1.5 }, itemStyle: { color: indexColor },
          data: index.map(point => [Date.parse(point.date), point.close]),
        },
        {
          name: '持仓占比', type: 'line', yAxisIndex: 0, connectNulls: false, symbol: 'circle', symbolSize: 6,
          lineStyle: { color: holdingColor, width: 2 }, itemStyle: { color: holdingColor },
          data: holdingPoints,
          markLine: {
            symbol: 'none',
            lineStyle: { color: holdingColor, type: 'dashed', opacity: .65 },
            label: { position: 'insideEndTop', formatter: '{b}', fontSize: 10, color: holdingColor },
            data: conversions.map(({ date, ratio }) => ({ xAxis: Date.parse(date), name: ratio })),
          },
          z: 3,
        },
      ],
    }, { notMerge: true });
  }, [first, fund, index, periods, theme]);

  return <div className="etf-holdings-chart" ref={root} role="img" aria-label={`${fund.name}公开持仓占比与上证指数`} />;
}

export default function EtfHoldingsComparison() {
  const title = useId();
  const { data: archive, loading, error } = useEtfSnapshot('holdings-archive', loadHoldingsArchive);
  const { data: daily } = useEtfSnapshot('/api/market/sh000001/daily', loadIndexCloses);
  const [fundCode, setFundCode] = useState('');
  const [group, setGroup] = useState<(typeof groups)[number]['id']>('all');
  const funds = archive?.funds ?? [];
  const fund = funds.find(item => item.code === fundCode) ?? funds[0];
  const first = fund?.reports.find(report => report.holders.length)?.report_date;
  const periods = useMemo<Period[]>(() => (fund?.reports ?? []).map(report => {
    const matches = group === 'all' ? report.holders : report.holders.filter(holder => holder.group === group);
    const shares = matches.length ? matches.reduce((sum, holder) => sum + holder.shares, 0) : null;
    const ownershipPercent = shares != null && report.total_shares && report.total_shares > 0
      ? 100 * (shares / report.total_shares)
      : null;
    return { ...report, matches, shares, ownershipPercent };
  }), [fund, group]);
  const indexWindow = useMemo(() => {
    if (!archive || !first) return [];
    const last = archive.index.at(-1)!.date;
    return [...archive.index.filter(point => point.date >= first), ...(daily ?? []).filter(point => point.date > last)];
  }, [first, archive, daily]);
  const lastReport = periods.filter(period => period.shares != null).at(-1)?.report_date;

  return (
    <section id="etf-holdings" className="panel etf-funds-card etf-holdings-card" aria-labelledby={title} aria-busy={loading}>
      <header className="etf-funds-heading">
        <h2 id={title}><BarChart3 size={17} aria-hidden="true" />国家队公开持仓与上证指数</h2>
        {first && indexWindow.length > 0 && <span className="etf-funds-status">{first} 至 {indexWindow.at(-1)?.date}</span>}
      </header>
      {error && archive && <p className="etf-funds-notice" role="status">更新失败，显示上次加载的数据。</p>}
      {fund && first && indexWindow.length ? (
        <>
          <div className="etf-holdings-controls">
            <div className="etf-funds-groups" role="group" aria-label="查看持仓的 ETF">
              {funds.map(item => <button key={item.code} type="button" aria-pressed={item.code === fund.code}
                onClick={() => setFundCode(item.code)}>{item.name} <small>{item.code}</small></button>)}
            </div>
            <div className="etf-funds-groups" role="group" aria-label="查看持仓的机构">
              {groups.map(item => <button key={item.id} type="button" aria-pressed={item.id === group}
                onClick={() => setGroup(item.id)}>{item.label}</button>)}
            </div>
          </div>
          <HoldingsChart index={indexWindow} periods={periods} fund={fund} first={first} />
          <p className="etf-funds-note">
            圆点为报告期末的持仓占比，蓝线为上证指数日收盘。
            {lastReport ? `最近具名持仓 ${lastReport}。` : '当前机构暂无具名持仓记录。'}
            拖动滑块缩放。
          </p>
          {!!fund.conversions?.length && (
            <div className="etf-holdings-conversions etf-funds-note">
              <span>份额折算</span>
              {fund.conversions.map(({ date, ratio, source_url }) => (
                <a key={date} href={source_url} target="_blank" rel="noreferrer">{date} {ratio}</a>
              ))}
            </div>
          )}
          <details className="etf-funds-details">
            <summary>持仓明细与披露原文</summary>
            <div className="etf-funds-table etf-holdings-table" tabIndex={0} role="region" aria-label="公开持仓明细">
              <table>
                <thead><tr>
                  <th scope="col">报告期末</th><th scope="col">披露日</th>
                  <th scope="col">占比 / 份额</th><th scope="col">持有人及来源</th>
                </tr></thead>
                <tbody>{periods.slice().reverse().map(period => (
                  <tr key={period.report_date}>
                    <th scope="row">{period.report_date}</th><td>{period.publication_date ?? '—'}</td>
                    <td title={period.shares == null ? undefined : `${period.shares.toLocaleString('zh-CN')} 份`}>
                      {period.ownershipPercent == null ? '—' : `${number(period.ownershipPercent)}%`}
                      {period.shares != null && <small>{units(period.shares)} 亿份</small>}
                    </td>
                    <td>
                      {period.matches.length
                        ? period.matches.map(holder => <small key={holder.name}>{holder.name} · {units(holder.shares)} 亿份</small>)
                        : <small>{reportStatus(period)}</small>}
                      {period.source_url && <a href={period.source_url} target="_blank" rel="noreferrer">
                        报告原文 <ExternalLink size={11} aria-hidden="true" />
                      </a>}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </details>
          <p className="etf-funds-note">
            持仓占比为具名披露份额除以同一期基金总份额。占比也会随基金总份额变化，缺少披露的期次留空。
            <a href={SOURCE_RULE_URL} target="_blank" rel="noreferrer">2026 年新版披露模板</a>
          </p>
        </>
      ) : <p className="etf-funds-empty" role="status">{error ? '持仓或行情档案暂不可用，请稍后刷新。' : '正在读取持仓和指数历史…'}</p>}
    </section>
  );
}
