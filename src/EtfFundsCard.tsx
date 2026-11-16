import { useEffect, useId, useRef, useState } from 'react';
import { BarChart3, ExternalLink, LoaderCircle, RefreshCw } from 'lucide-react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import useEtfSnapshot from './useEtfSnapshot';
import { number, tone } from './format';
import { useTheme } from './ThemeContext';
import './etf-funds.css';

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

type HistoryPoint = { date: string; net_flow: number | null; index_close: number | null };
type FundRow = {
  code: string;
  name: string;
  trade_date: string;
  shares: number;
  share_change: number | null;
  net_flow: number | null;
  flow_5d: number | null;
  flow_20d: number | null;
  turnover: number | null;
  volume_ratio: number | null;
  volume_alert: boolean;
  flow_alert: 'in' | 'out' | null;
};
type FundGroup = { id: string; name: string; history: HistoryPoint[]; rows: FundRow[] };
type EtfFunds = {
  status: 'ok' | 'not_ready' | 'stale';
  latest_date: string | null;
  expected_date: string | null;
  as_of: string | null;
  groups: FundGroup[];
  events?: { date: string; entity: string; title: string; url: string }[];
};

const marketTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

function inHundredMillions(value: number | null, signed = false): string {
  if (value == null) return '—';
  return (signed && value > 0 ? '+' : '') + number(value / 1e8);
}

function periodNetFlow(history: HistoryPoint[], end: string | null | undefined, days: number): number | null {
  const points = history.slice(-days);
  if (points.length < days || points.at(-1)?.date !== end) return null;
  let total = 0;
  for (const point of points) {
    if (point.net_flow == null) return null;
    total += point.net_flow;
  }
  return total;
}

function EtfFundsTrend({ history, name }: { history: HistoryPoint[]; name: string }) {
  const root = useRef<HTMLDivElement>(null);
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null);
  const { theme } = useTheme();
  const hasFlow = history.some(point => point.net_flow != null);
  const hasIndex = history.some(point => point.index_close != null);
  const hasData = hasFlow || hasIndex;

  useEffect(() => {
    if (!root.current) return;
    const chart = echarts.init(root.current);
    instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; };
  }, [hasData]);

  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const style = getComputedStyle(document.documentElement);
    const color = (name: string) => style.getPropertyValue(`--chart-${name}`).trim();
    chart.setOption({
      animation: false,
      grid: { left: 8, right: 8, top: 12, bottom: 6, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      tooltip: {
        trigger: 'axis', renderMode: 'richText', backgroundColor: color('tooltip-bg'), borderColor: color('border'),
        textStyle: { color: color('tooltip-text'), fontSize: 11 },
        formatter: (items: unknown) => {
          const index = (items as { dataIndex: number }[])[0]?.dataIndex;
          const point = history[index];
          if (!point) return '';
          const lines = [point.date, `净申赎 ${point.net_flow == null ? '—' : `${inHundredMillions(point.net_flow, true)} 亿元`}`];
          if (point.index_close != null) lines.push(`指数 ${number(point.index_close)}`);
          return lines.join('\n');
        },
      },
      xAxis: {
        type: 'category', data: history.map(point => point.date),
        axisLine: { lineStyle: { color: color('border') } }, axisTick: { show: false },
        axisLabel: { color: color('text'), fontSize: 10, hideOverlap: true, formatter: (day: string) => day.slice(5) },
      },
      yAxis: [
        { type: 'value', axisLabel: { color: color('text'), fontSize: 10 }, splitLine: { lineStyle: { color: color('grid'), type: 'dashed' } } },
        { type: 'value', scale: true, show: hasIndex, axisLabel: { color: color('text'), fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        {
          type: 'bar', name: '估算净申赎', barMaxWidth: 14,
          data: history.map(({ net_flow }) => net_flow == null ? null : {
            value: net_flow / 1e8,
            itemStyle: { color: color(net_flow < 0 ? 'down' : 'up') },
          }),
        },
        ...(hasIndex ? [{
          type: 'line', name: '指数收盘', yAxisIndex: 1, showSymbol: false, lineStyle: { width: 1.5, color: color('marker-neutral') },
          data: history.map(point => point.index_close),
        }] : []),
      ],
    }, { notMerge: true });
  }, [history, hasIndex, theme]);

  return (
    <div className="etf-funds-trend">
      <div className="etf-funds-trend-heading">
        <h3>净申赎走势</h3>
        <div className="etf-funds-legend">
          <span>估算净申赎（亿元）</span>
          {hasIndex && <span className="etf-funds-index-legend">指数收盘</span>}
        </div>
      </div>
      {hasData ? <div ref={root} className="etf-funds-chart" role="img" aria-label={`${name}净申赎走势${hasIndex ? '及指数收盘走势' : ''}`} />
        : <p className="etf-funds-empty">暂无可计算的历史净申赎</p>}
    </div>
  );
}

function EtfFundsRows({ rows, date }: { rows: FundRow[]; date: string | null | undefined }) {
  if (!rows.length) return null;
  return (
    <details className="etf-funds-details">
      <summary>ETF 明细 <span>{rows.length} 只</span></summary>
      <div className="etf-funds-table" tabIndex={0} role="region" aria-label="宽基 ETF 明细">
        <table>
          <thead><tr>
            {['基金', '份额（亿份）', '份额变化（亿份）', '净申赎（亿元）', '近 5 日（亿元）', '近 20 日（亿元）', '成交额（亿元）', '异常提示'].map(label => (
              <th key={label} scope="col">{label}</th>
            ))}
          </tr></thead>
          <tbody>{rows.map(row => (
            <tr key={row.code}>
              <th scope="row">{row.name}<small>{row.code}{row.trade_date !== date ? ` · ${row.trade_date}` : ''}</small></th>
              <td>{inHundredMillions(row.shares)}</td>
              <td className={tone(row.share_change)}>{inHundredMillions(row.share_change, true)}</td>
              <td className={tone(row.net_flow)}>{inHundredMillions(row.net_flow, true)}</td>
              <td className={tone(row.flow_5d)}>{inHundredMillions(row.flow_5d, true)}</td>
              <td className={tone(row.flow_20d)}>{inHundredMillions(row.flow_20d, true)}</td>
              <td>{inHundredMillions(row.turnover)}</td>
              <td><div className="etf-funds-alerts">
                {row.flow_alert && <span className={row.flow_alert === 'in' ? 'up' : 'down'}>{row.flow_alert === 'in' ? '异常净申购' : '异常净赎回'}</span>}
                {row.volume_alert && <span className="etf-funds-volume">成交放量{row.volume_ratio != null && ` ${number(row.volume_ratio)} 倍`}</span>}
                {!row.flow_alert && !row.volume_alert && '—'}
              </div></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </details>
  );
}

export default function EtfFundsCard() {
  const title = useId();
  const { data, loading, error, refresh } = useEtfSnapshot<EtfFunds>('/api/market/etf-funds');
  const [groupId, setGroupId] = useState('');

  const groups = data?.groups ?? [];
  const group = groups.find(item => item.id === groupId) ?? groups[0];
  const date = data?.latest_date;
  const collectedAt = data?.as_of ? new Date(data.as_of) : null;
  const timestamp = collectedAt && !Number.isNaN(collectedAt.getTime()) ? marketTime.format(collectedAt) : null;
  const hasData = group && date;

  return (
    <section className="panel etf-funds-card" aria-labelledby={title} aria-busy={loading}>
      <header className="etf-funds-heading">
        <h2 id={title}><BarChart3 size={17} aria-hidden="true" />宽基 ETF 资金监控</h2>
        <div className="etf-funds-status">
          {date && <span>交易日 <time dateTime={date}>{date}</time>{timestamp && ` · 采集于 ${timestamp}`}</span>}
          <button type="button" className="subtle-button" title="刷新 ETF 资金数据" aria-label="刷新 ETF 资金数据" disabled={loading}
            onClick={refresh}>
            {loading ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
          </button>
        </div>
      </header>
      {error && <p className="etf-funds-notice" role="status">{error}</p>}
      {data?.status === 'stale' && <p className="etf-funds-notice" role="status">
        {data.expected_date && `${data.expected_date} 数据尚未齐备，`}当前显示最近有效数据。
      </p>}
      {hasData ? (
        <>
          <div className="etf-funds-body">
            <div className="etf-funds-summary">
              <div className="etf-funds-groups" aria-label="选择跟踪指数" role="group">
                {groups.map(item => <button key={item.id} type="button" aria-pressed={item.id === group.id} onClick={() => setGroupId(item.id)}>{item.name}</button>)}
              </div>
              <dl className="etf-funds-metrics">
                {([['当日净申赎', 1], ['近 5 日', 5], ['近 20 日', 20]] as const).map(([label, days]) => {
                  const value = periodNetFlow(group.history, date, days);
                  return <div key={days}><dt>{label}</dt><dd className={tone(value)}>{inHundredMillions(value, true)}{value != null && <small> 亿</small>}</dd></div>;
                })}
              </dl>
              <div className="etf-funds-summary-alerts">
                <span>异常净申赎 <strong>{group.rows.filter(row => row.flow_alert).length}</strong></span>
                <span>成交放量 <strong>{group.rows.filter(row => row.volume_alert).length}</strong></span>
              </div>
            </div>
            <EtfFundsTrend key={group.id} name={group.name} history={group.history} />
          </div>
          <EtfFundsRows key={group.id} rows={group.rows} date={date} />
        </>
      ) : !error && <p className="etf-funds-empty" role="status">{loading ? '正在读取 ETF 资金数据…' : '暂无已采集的 ETF 资金数据'}</p>}
      {!!data?.events?.length && <details className="etf-funds-details">
        <summary>官方增减持公告</summary>
        <ul className="etf-funds-events">{data.events.map(event => (
          <li key={event.url}><time dateTime={event.date}>{event.date}</time><span>{event.entity}</span>
            <a href={event.url} target="_blank" rel="noreferrer">{event.title} <ExternalLink size={11} aria-hidden="true" /></a></li>
        ))}</ul>
      </details>}
      <p className="etf-funds-note">沪深交易所份额 · 金额按份额变化估算，反映基金整体申赎 · 交易日 09:00 更新</p>
    </section>
  );
}
