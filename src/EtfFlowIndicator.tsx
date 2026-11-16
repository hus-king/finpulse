import { useEffect, useId, useRef, useState } from 'react';
import { BarChart3, ExternalLink } from 'lucide-react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { DataZoomComponent, GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { api } from './api';
import { number, percent, tone } from './format';
import { useTheme } from './ThemeContext';
import './etf-funds.css';

echarts.use([LineChart, DataZoomComponent, GridComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

type Basket = 'wide' | 'long';
type View = 'flow' | 'inventory';
type Point = {
  date: string;
  score: number | null;
  amount: number | null;
  net: number | null;
  agreeing_groups: number | null;
  daily_counted_flow: number | null;
  cumulative_amount: number | null;
  index_close: number | null;
};
type Announcement = { date: string; entity: string; title: string; url: string };
type HoldingAmount = { date: string; amount: number };
type Inventory = {
  disclosed: HoldingAmount & { sources: { code: string; publication_date: string; source_url: string }[] };
  sample_assets: HoldingAmount | null;
  since_disclosure: number | null;
  omitted_days: number;
};
type Snapshot = {
  funds: string[]; group_count: number; expected_date: string; history: Point[];
  announcements: Announcement[]; inventory: Inventory;
};

const cash = (amount: number | null | undefined, signed = true) =>
  amount == null ? '—' : `${signed && amount > 0 ? '+' : ''}${number(amount / 1e8)} 亿元`;

function FlowChart({ snapshot, view }: { snapshot: Snapshot; view: View }) {
  const showInventory = view === 'inventory';
  const metricLabel = showInventory ? '累计异常净申赎' : '资金动向';
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
    const indicatorColor = color('--theme-warning');
    const indexColor = color('--chart-marker-neutral');
    const border = color('--chart-border');
    const axisLabel = { fontSize: 10, hideOverlap: true };
    const points = new Map(snapshot.history.map(point => [point.date, point]));
    const first = snapshot.history[0].date;
    const last = snapshot.history.at(-1)!.date;
    const events = snapshot.announcements.filter(event => first <= event.date && event.date <= last);
    const disclosure = snapshot.inventory.disclosed;
    const markerDates = new Set(events.map(event => event.date));
    if (showInventory && first <= disclosure.date && disclosure.date <= last) markerDates.add(disclosure.date);

    chart.current?.setOption({
      useUTC: true,
      animation: false,
      grid: { left: 9, right: 9, top: 34, bottom: 52, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      tooltip: {
        trigger: 'axis', renderMode: 'richText', confine: true, borderColor: border,
        backgroundColor: color('--chart-tooltip-bg'), textStyle: { color: color('--chart-tooltip-text'), fontSize: 11 },
        formatter: (items: unknown) => {
          const timestamp = Number((items as { axisValue: number }[])[0]?.axisValue);
          if (!Number.isFinite(timestamp)) return '';
          const day = new Date(timestamp).toISOString().slice(0, 10);
          const point = points.get(day);
          if (!point) return day;
          const lines = [day, `${metricLabel} ${showInventory ? cash(point.cumulative_amount) : percent(point.score)}`];
          if (showInventory) {
            if (point.daily_counted_flow) lines.push(`当日计入 ${cash(point.daily_counted_flow)}`);
            if (day === disclosure.date) lines.push(`年报具名持仓 ${cash(disclosure.amount, false)}`);
          } else if (point.amount != null) {
            lines.push(`窗口内异常日净申赎 ${cash(point.amount)}`);
          }
          if (point.net != null) lines.push(`当日样本净申赎 ${cash(point.net)}`);
          if (point.agreeing_groups != null) lines.push(`同向异常 ${point.agreeing_groups}/${snapshot.group_count} 类宽基`);
          if (point.index_close != null) lines.push(`上证指数 ${number(point.index_close)} 点`);
          for (const event of events) if (event.date === day) lines.push(`${event.entity} · ${event.title}`);
          return lines.join('\n');
        },
      },
      xAxis: { type: 'time', min: Date.parse(first), max: Date.parse(last), boundaryGap: false,
        axisLine: { lineStyle: { color: border } }, axisTick: { show: false },
        axisLabel: { ...axisLabel, color: color('--chart-text') } },
      yAxis: [
        { type: 'value', name: showInventory ? '累计净申赎（亿元）' : '资金动向（%）', nameTextStyle: { color: indicatorColor, align: 'left', fontSize: 11 },
          axisLabel: { ...axisLabel, color: indicatorColor }, splitLine: { lineStyle: { color: color('--chart-grid'), type: 'dashed' } } },
        { type: 'value', name: '上证指数（点）', scale: true, nameTextStyle: { color: indexColor, align: 'right', fontSize: 11 },
          axisLabel: { ...axisLabel, color: indexColor }, splitLine: { show: false } },
      ],
      dataZoom: [{
        type: 'slider', xAxisIndex: 0, bottom: 4, height: 18, filterMode: 'none', showDetail: false,
        showDataShadow: false, borderColor: border, backgroundColor: color('--theme-panel'),
        fillerColor: color('--theme-accent-soft'), handleStyle: { color: indexColor }, brushSelect: false,
      }],
      series: [
        {
          type: 'line', name: '上证指数', yAxisIndex: 1, showSymbol: false, sampling: 'lttb',
          lineStyle: { color: indexColor, width: 1.5 }, data: snapshot.history.map(point => [Date.parse(point.date), point.index_close]),
        },
        {
          type: 'line', name: metricLabel, showSymbol: false, connectNulls: false,
          lineStyle: { color: indicatorColor, width: 2 },
          data: snapshot.history.map(point => [Date.parse(point.date),
            showInventory ? (point.cumulative_amount == null ? null : point.cumulative_amount / 1e8) : point.score]),
          markLine: {
            silent: true, symbol: 'none', label: { show: false },
            lineStyle: { color: color('--chart-crosshair'), type: 'dashed', opacity: .65 },
            data: [{ yAxis: 0 }, ...Array.from(markerDates, day => ({ xAxis: Date.parse(day) }))],
          },
        },
      ],
    }, { notMerge: true });
  }, [snapshot, theme, showInventory, metricLabel]);

  return <div ref={root} className="etf-holdings-chart" role="img" aria-label={showInventory ? "累计异常净申赎与上证指数走势" : "资金动向指标与上证指数走势"} />;
}

export default function EtfFlowIndicator() {
  const title = useId();
  const [basket, setBasket] = useState<Basket>('wide');
  const [view, setView] = useState<View>('flow');
  const [result, setResult] = useState<{ basket: Basket; snapshot: Snapshot }>();
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api<Snapshot>(`/api/market/etf-flow-indicator?basket=${basket}`, undefined, controller.signal)
      .then(snapshot => { if (!controller.signal.aborted) setResult({ basket, snapshot }); })
      .catch((err: Error) => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [basket]);

  const snapshot = result?.basket === basket ? result.snapshot : undefined;
  const showInventory = view === 'inventory';
  const inventory = snapshot?.inventory;
  const latest = snapshot?.history.filter(point => point[showInventory ? 'cumulative_amount' : 'score'] != null).at(-1);
  const reading = showInventory ? latest?.cumulative_amount : latest?.score;

  return (
    <section id="etf-flow-indicator" className="panel etf-funds-card etf-holdings-card" aria-labelledby={title}>
      <header className="etf-funds-heading">
        <h2 id={title}><BarChart3 size={17} aria-hidden="true" />国家队资金动向（宽基 ETF 代理）</h2>
        {latest && <span className="etf-funds-status">
          <strong className={reading ? tone(reading) : undefined}>{showInventory ? cash(reading) : percent(reading)}</strong> · {latest.date}
        </span>}
      </header>
      <div className="etf-holdings-controls">
        <div className="etf-funds-groups" role="group" aria-label="选择宽基 ETF 样本">
          <button type="button" aria-pressed={basket === 'wide'} onClick={() => setBasket('wide')}>完整样本 · 9 只</button>
          <button type="button" aria-pressed={basket === 'long'} onClick={() => setBasket('long')}>长历史 · 5 只</button>
        </div>
        <div className="etf-funds-groups" role="group" aria-label="选择资金指标">
          <button type="button" aria-pressed={view === 'flow'} onClick={() => setView('flow')}>20 日动向</button>
          <button type="button" aria-pressed={showInventory} onClick={() => setView('inventory')}>累计变化</button>
        </div>
        {snapshot?.history.length ? <span className="etf-funds-status">
          {snapshot.history[0].date} 至 {snapshot.history.at(-1)?.date}
        </span> : null}
      </div>
      {showInventory && inventory && (
        <dl className="etf-funds-metrics etf-inventory-metrics">
          <div>
            <dt>最近具名持仓</dt>
            <dd>{cash(inventory.disclosed.amount, false)}</dd>
            <small>{inventory.disclosed.date} · 年报披露</small>
          </div>
          <div>
            <dt>披露期末以来累计变化</dt>
            <dd className={inventory.since_disclosure ? tone(inventory.since_disclosure) : undefined}>{cash(inventory.since_disclosure)}</dd>
            <small>{latest?.date ?? '等待份额数据'}{inventory.omitted_days > 0 && ` · ${inventory.omitted_days} 日缺值`}</small>
          </div>
          <div>
            <dt>当前样本场内总规模</dt>
            <dd>{cash(inventory.sample_assets?.amount, false)}</dd>
            <small>{inventory.sample_assets ? `${inventory.sample_assets.date} · 样本持仓上限` : '等待份额数据'}</small>
          </div>
        </dl>
      )}
      {snapshot?.history.length ? <FlowChart snapshot={snapshot} view={view} /> : (
        <p className="etf-funds-empty" role="status">{error || (snapshot ? '暂无足够的历史份额数据' : '正在读取资金指标…')}</p>
      )}
      <p className="etf-funds-note">
        {showInventory
          ? '金线从样本起点累加，符合条件的净申赎每天计一次。机构持仓以具名披露为准。'
          : '正值偏流入，负值偏流出。每个交易日早间更新上一交易日数据，竖虚线为已核对的公告或披露日期。'}
      </p>
      <details className="etf-funds-details">
        <summary>计算口径与官方公告</summary>
        <p className="etf-funds-note">
          日净申赎按相邻交易日的交易所份额差乘当日单位净值估算。
          至少两类宽基的净申赎占前日规模比例同时高于各自此前 60 个有效日的 95% 分位，或低于 5% 分位，
          且方向与篮子净额一致，该日篮子净额即计入指标。
          取近 20 个有效交易日的累计额除以窗口期初规模。份额折算与缺失日期留空。
        </p>
        <p className="etf-funds-note">
          指标覆盖样本内全部参与者，机构身份依赖具名披露。大单和超大单反映场内成交，可用作独立佐证。
          {snapshot && ` 样本代码 ${snapshot.funds.join('、')}。`}
        </p>
        {showInventory && inventory && (
          <p className="etf-funds-note">
            年报具名持仓按报告期末份额乘单位净值估算，当前样本规模按当日场内总份额乘单位净值估算。
            累计变化按各日净申赎额加总。年报原文
            {inventory.disclosed.sources.map(report => (
              <a key={report.code} className="etf-inventory-source" href={report.source_url} target="_blank" rel="noreferrer"
                title={`公告日 ${report.publication_date}`}>{report.code}</a>
            ))}
          </p>
        )}
        <ul className="etf-funds-events">{snapshot?.announcements.map(event => (
          <li key={event.url}><time dateTime={event.date}>{event.date}</time><span>{event.entity}</span>
            <a href={event.url} target="_blank" rel="noreferrer">{event.title} <ExternalLink size={11} aria-hidden="true" /></a></li>
        ))}</ul>
      </details>
    </section>
  );
}
