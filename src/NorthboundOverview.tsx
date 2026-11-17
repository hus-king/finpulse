import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowLeftRight, LoaderCircle, RefreshCw } from 'lucide-react';
import { ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart } from 'lightweight-charts';
import type { IChartApi, Time, UTCTimestamp } from 'lightweight-charts';
import { number, tone } from './format';
import { useTheme } from './ThemeContext';
import { chartTimestamp, initialChartRange } from './tradingChartData';
import useEtfSnapshot from './useEtfSnapshot';
import './stock-bond-overview.css';
import './northbound-overview.css';

type Point = {
  date: string;
  net: number | null;
  net_5d: number | null;
  turnover: number;
  turnover_5d: number | null;
  sse_close: number;
  csi300_close: number | null;
};
type Snapshot = { as_of: string; disclosure_change: string; history: Point[] };

const modes = [
  { day: 'turnover', window: 'turnover_5d', label: '成交活跃度', metric: '当日成交总额', smooth: '近 5 日平均成交额' },
  { day: 'net', window: 'net_5d', label: '历史净买入', metric: '当日净买入额', smooth: '近 5 日累计净买入' },
] as const;
const indices = [
  { key: 'sse_close', label: '上证指数' },
  { key: 'csi300_close', label: '沪深 300' },
] as const;
const ranges = ['近 3 月', '近 1 年', '全部'] as const;
const empty: Point[] = [];

function NorthboundTrend({ history, mode, index }: {
  history: Point[];
  mode: typeof modes[number];
  index: typeof indices[number];
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const range = useRef<typeof ranges[number]>('近 1 年');
  const [selected, setSelected] = useState<typeof ranges[number]>('近 1 年');
  const [hovered, setHovered] = useState<Point>();
  const { theme } = useTheme();
  const dates = useMemo(() => history.map(point => point.date), [history]);
  const point = hovered ?? history.at(-1);
  const signed = mode.day === 'net';
  const amount = (value: number | null | undefined) =>
    value == null ? '—' : (signed && value > 0 ? '+' : '') + number(value) + ' 亿元';

  useEffect(() => {
    if (!canvas.current || !dates.length) return;
    setHovered(undefined);
    const style = getComputedStyle(document.documentElement);
    const color = (name: string) => style.getPropertyValue(name).trim();
    const borderColor = color('--chart-border');
    const scale = { visible: true, borderColor, scaleMargins: { top: .13, bottom: .13 } };
    const grid = { color: color('--chart-grid'), style: LineStyle.Dotted };
    const instance = createChart(canvas.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: color('--theme-panel') },
        textColor: color('--chart-text'), attributionLogo: false, fontSize: 11,
        fontFamily: 'Inter, Segoe UI, Microsoft YaHei, sans-serif' },
      grid: { vertLines: grid, horzLines: grid },
      crosshair: { mode: CrosshairMode.Normal },
      leftPriceScale: scale,
      rightPriceScale: scale,
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      timeScale: { minBarSpacing: .03, fixLeftEdge: true, fixRightEdge: true, borderColor },
      localization: { locale: 'zh-CN', timeFormatter: (time: Time) => new Date(Number(time) * 1000).toISOString().slice(0, 10) },
    });
    chart.current = instance;
    const amounts = instance.addSeries(HistogramSeries, { priceScaleId: 'left',
      color: color('--theme-muted'), priceLineVisible: false, lastValueVisible: false,
      priceFormat: { type: 'custom', minMove: .01, formatter: number } });
    const average = instance.addSeries(LineSeries, { priceScaleId: 'left',
      color: color('--theme-warning'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const quotes = instance.addSeries(LineSeries, { priceScaleId: 'right',
      color: color('--chart-marker-neutral'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const times = dates.map(date => chartTimestamp(date) as UTCTimestamp);
    const up = color('--chart-up'), down = color('--chart-down');
    amounts.setData(history.map((row, i) => {
      const value = row[mode.day], time = times[i];
      return value == null ? { time } : { time, value, color: signed ? (value >= 0 ? up : down) : undefined };
    }));
    average.setData(history.map((row, i) => {
      const value = row[mode.window], time = times[i];
      return value == null ? { time } : { time, value, color: history[i + 1]?.[mode.window] === null ? 'transparent' : undefined };
    }));
    quotes.setData(history.map((row, i) => {
      const value = row[index.key], time = times[i];
      return value == null ? { time } : { time, value, color: history[i + 1]?.[index.key] === null ? 'transparent' : undefined };
    }));
    if (signed) amounts.createPriceLine({ price: 0, color: color('--chart-crosshair'), lineWidth: 1,
      lineStyle: LineStyle.Dashed, axisLabelVisible: false });
    instance.subscribeCrosshairMove(event => {
      const logical = event.point && event.logical !== undefined ? Math.round(event.logical) : -1;
      setHovered(history[logical]);
    });
    const initial = initialChartRange(dates, '日K', range.current);
    if (initial) instance.timeScale().setVisibleLogicalRange(initial);
    return () => { instance.remove(); chart.current = null; };
  }, [dates, history, index, mode, signed, theme]);

  return <>
    <div className="stock-bond-chart-toolbar">
      <span><time dateTime={point?.date}>{point?.date}</time></span>
      <div className="segmented" role="group" aria-label="北向资金图表时间范围">
        {ranges.map(item => <button key={item} type="button" className={selected === item ? 'active' : ''}
          aria-pressed={selected === item} onClick={() => {
            range.current = item;
            setSelected(item);
            const next = initialChartRange(dates, '日K', item);
            if (next) chart.current?.timeScale().setVisibleLogicalRange(next);
          }}>{item}</button>)}
      </div>
    </div>
    <dl className="stock-bond-readout northbound-readout">
      <div><dt>{mode.metric} · 左轴</dt><dd><strong className={signed ? tone(point?.net ?? null) : ''}>
        {amount(point?.[mode.day])}
      </strong></dd></div>
      <div><dt><span className="northbound-smooth" aria-hidden="true" />{mode.smooth} · 左轴</dt>
        <dd><strong>{amount(point?.[mode.window])}</strong></dd></div>
      <div><dt><span className="northbound-index" aria-hidden="true" />{index.label} · 右轴</dt>
        <dd><strong>{number(point?.[index.key])}</strong></dd></div>
    </dl>
    <div ref={canvas} className="northbound-chart" role="img" aria-label={mode.metric + '与' + index.label + '走势'} />
  </>;
}

export default function NorthboundOverview() {
  const title = useId();
  const [mode, setMode] = useState<typeof modes[number]>(modes[0]);
  const [index, setIndex] = useState<typeof indices[number]>(indices[0]);
  const { data, loading, error, refresh } = useEtfSnapshot<Snapshot>('/api/market/northbound');
  const history = useMemo(() => {
    if (!data) return empty;
    return mode.day === 'net' ? data.history.filter(row => row.date < data.disclosure_change) : data.history;
  }, [data, mode]);
  const last = history.at(-1)?.date;

  return <section id="northbound-overview" className="panel stock-bond-overview northbound-overview"
    aria-labelledby={title} aria-busy={loading}>
    <header className="stock-bond-heading">
      <h2 id={title}><ArrowLeftRight size={18} aria-hidden="true" />北向资金</h2>
      <div>
        {last && <span>数据截至 <time dateTime={last}>{last}</time></span>}
        <button className="subtle-button" type="button" aria-label="刷新北向资金" title="刷新" disabled={loading} onClick={refresh}>
          {loading ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
        </button>
      </div>
    </header>
    <div className="northbound-controls">
      <div className="segmented" role="group" aria-label="北向资金指标">
        {modes.map(choice => <button key={choice.day} type="button" aria-pressed={mode === choice}
          className={mode === choice ? 'active' : ''} onClick={() => setMode(choice)}>{choice.label}</button>)}
      </div>
      <div className="segmented" role="group" aria-label="北向资金对照指数">
        {indices.map(choice => <button key={choice.key} type="button" aria-pressed={index === choice}
          className={index === choice ? 'active' : ''} onClick={() => setIndex(choice)}>{choice.label}</button>)}
      </div>
    </div>
    {error && <p className="stock-bond-note" role="status">{error}</p>}
    {history.length ? <NorthboundTrend history={history} mode={mode} index={index} /> : !error &&
      <p className="stock-bond-note" role="status">{loading ? '正在加载北向资金…' : '北向资金数据待更新。'}</p>}
    <p className="stock-bond-note">
      2024-08-19 起，交易所公开北向成交总额。净买入视图截至 2024-08-16，正值为净买入，负值为净卖出。
    </p>
    <p className="stock-bond-note stock-bond-source-links">
      <a href="https://data.eastmoney.com/hsgt/index.html" target="_blank" rel="noreferrer">东方财富历史数据</a>
      <a href="https://www.hkex.com.hk/Mutual-Market/Stock-Connect/Statistics/Historical-Daily" target="_blank" rel="noreferrer">港交所每日统计</a>
      <a href="https://www3.hkexnews.hk/sdw/mutualmarket/SH.htm" target="_blank" rel="noreferrer">季度持股披露</a>
    </p>
  </section>;
}
