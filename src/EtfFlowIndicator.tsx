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
type Point = {
  date: string;
  score: number | null;
  amount: number | null;
  net: number | null;
  agreeing_groups: number | null;
  index_close: number | null;
};
type Announcement = { date: string; entity: string; title: string; url: string };
type Snapshot = { funds: string[]; group_count: number; expected_date: string; history: Point[]; announcements: Announcement[] };

const cash = (amount: number) => `${amount > 0 ? '+' : ''}${number(amount / 1e8)} 亿元`;

function FlowChart({ snapshot }: { snapshot: Snapshot }) {
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
          const lines = [day, `资金动向 ${percent(point.score)}`];
          if (point.amount != null) lines.push(`窗口内异常日净申赎 ${cash(point.amount)}`);
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
        { type: 'value', name: '资金动向（%）', nameTextStyle: { color: indicatorColor, align: 'left', fontSize: 11 },
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
          type: 'line', name: '资金动向', showSymbol: false, connectNulls: false,
          lineStyle: { color: indicatorColor, width: 2 }, data: snapshot.history.map(point => [Date.parse(point.date), point.score]),
          markLine: {
            silent: true, symbol: 'none', label: { show: false },
            lineStyle: { color: color('--chart-crosshair'), type: 'dashed', opacity: .65 },
            data: [{ yAxis: 0 }, ...events.map(event => ({ xAxis: Date.parse(event.date) }))],
          },
        },
      ],
    }, { notMerge: true });
  }, [snapshot, theme]);

  return <div ref={root} className="etf-holdings-chart" role="img" aria-label="资金动向指标与上证指数走势" />;
}

export default function EtfFlowIndicator() {
  const title = useId();
  const [basket, setBasket] = useState<Basket>('wide');
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
  const latest = snapshot?.history.filter(point => point.score != null).at(-1);

  return (
    <section id="etf-flow-indicator" className="panel etf-funds-card etf-holdings-card" aria-labelledby={title}>
      <header className="etf-funds-heading">
        <h2 id={title}><BarChart3 size={17} aria-hidden="true" />国家队资金动向（宽基 ETF 代理）</h2>
        {latest && <span className="etf-funds-status">
          <strong className={latest.score ? tone(latest.score) : undefined}>{percent(latest.score)}</strong> · {latest.date}
        </span>}
      </header>
      <div className="etf-holdings-controls">
        <div className="etf-funds-groups" role="group" aria-label="选择宽基 ETF 样本">
          <button type="button" aria-pressed={basket === 'wide'} onClick={() => setBasket('wide')}>完整样本 · 9 只</button>
          <button type="button" aria-pressed={basket === 'long'} onClick={() => setBasket('long')}>长历史 · 5 只</button>
        </div>
        {snapshot?.history.length ? <span className="etf-funds-status">
          {snapshot.history[0].date} 至 {snapshot.history.at(-1)?.date}
        </span> : null}
      </div>
      {snapshot?.history.length ? <FlowChart snapshot={snapshot} /> : (
        <p className="etf-funds-empty" role="status">{error || (snapshot ? '暂无足够的历史份额数据' : '正在读取资金指标…')}</p>
      )}
      <p className="etf-funds-note">
        正值偏流入，负值偏流出。每个交易日早间更新上一交易日数据，竖虚线为已核对的公告或披露日期。
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
        <ul className="etf-funds-events">{snapshot?.announcements.map(event => (
          <li key={event.url}><time dateTime={event.date}>{event.date}</time><span>{event.entity}</span>
            <a href={event.url} target="_blank" rel="noreferrer">{event.title} <ExternalLink size={11} aria-hidden="true" /></a></li>
        ))}</ul>
      </details>
    </section>
  );
}
