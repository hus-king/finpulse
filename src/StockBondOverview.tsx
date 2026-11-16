import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChartNoAxesCombined, LoaderCircle, RefreshCw } from 'lucide-react';
import { ColorType, CrosshairMode, LineSeries, LineStyle, createChart } from 'lightweight-charts';
import type { IChartApi, Time, UTCTimestamp } from 'lightweight-charts';
import { number, percent, tone } from './format';
import { useTheme } from './ThemeContext';
import { chartTimestamp } from './tradingChartData';
import useEtfSnapshot from './useEtfSnapshot';
import './stock-bond-overview.css';

type Point = {
  date: string;
  index_close: number | null;
  cn_gov10_pct: number | null;
  spread_pp: number | null;
  flow_20d_pct: number | null;
};

type Horizon = { days: number; n: number; mean_pct: number | null; median_pct: number | null; positive_pct: number | null };
type Group = { id: string; label: string; horizons: Horizon[] };
type Snapshot = {
  status?: string;
  as_of?: string | null;
  expected_date?: string | null;
  flow_first?: string | null;
  flow_last?: string | null;
  history: Point[];
  groups: Group[];
  funds: { code: string; name: string }[];
  method?: string;
};

const baskets = [
  { id: 'long', label: '长历史 · 1 只', endpoint: '/api/market/stock-bond' },
  { id: 'three', label: '共同样本 · 3 只', endpoint: '/api/market/stock-bond?basket=three' },
] as const;

const windows = [{ years: 0, label: '全部' }, { years: 5, label: '近 5 年' }, { years: 1, label: '近 1 年' }];
const horizons = [20, 60, 120];
const metrics = [
  { key: 'index_close', label: '沪深 300', color: '--chart-marker-neutral', pane: 0, axis: 'right', format: number },
  { key: 'cn_gov10_pct', label: '10 年期国债收益率', color: '--theme-warning', pane: 0, axis: 'left',
    format: (value: number) => `${number(value)}%` },
  { key: 'spread_pp', label: '股债收益差', color: '--ink-violet', pane: 1, axis: 'right',
    format: (value: number) => `${number(value)} 个百分点` },
  { key: 'flow_20d_pct', label: '国债 ETF 近 20 日净申赎率', color: '--theme-success', pane: 2, axis: 'right',
    format: percent },
] as const;

function StockBondTrend({ history }: { history: Point[] }) {
  const canvas = useRef<HTMLDivElement>(null);
  const instance = useRef<IChartApi | null>(null);
  const previousRange = useRef<{ from: Time; to: Time; all: boolean } | null>(null);
  const [hovered, setHovered] = useState<Point>();
  const [selectedRange, setSelectedRange] = useState<number | null>(0);
  const { theme } = useTheme();
  const times = useMemo(() => history.map(point => chartTimestamp(point.date) as UTCTimestamp), [history]);
  const latest = useMemo(() => metrics.map(({ key }) =>
    history.reduce<Point | undefined>((last, point) => point[key] == null ? last : point, undefined)), [history]);
  const timeWindows = useMemo(() => {
    const end = times.at(-1);
    if (end == null) return [];
    return windows.map(window => {
      const cutoff = new Date(end * 1000);
      cutoff.setUTCFullYear(cutoff.getUTCFullYear() - window.years);
      const from = window.years ? times.find(time => time * 1000 >= cutoff.getTime()) ?? times[0] : times[0];
      return { ...window, from, to: end };
    });
  }, [times]);

  useEffect(() => {
    if (!canvas.current || !history.length) return;
    setHovered(undefined);
    const style = getComputedStyle(document.documentElement);
    const color = (property: string) => style.getPropertyValue(property).trim();
    const border = color('--chart-border');
    const axis = { borderColor: border, minimumWidth: 54, scaleMargins: { top: .14, bottom: .12 } };
    const guide = { color: color('--chart-crosshair'), style: LineStyle.Dashed, labelBackgroundColor: color('--chart-crosshair') };
    const grid = { color: color('--chart-grid'), style: LineStyle.Dotted };
    const chart = createChart(canvas.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: color('--theme-panel') },
        textColor: color('--chart-text'), attributionLogo: false, fontSize: 11,
        fontFamily: 'Inter, Segoe UI, Microsoft YaHei, sans-serif',
        panes: { separatorColor: border, enableResize: false },
      },
      grid: { vertLines: grid, horzLines: grid },
      crosshair: { mode: CrosshairMode.Normal, vertLine: guide, horzLine: guide },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      leftPriceScale: { ...axis, visible: true },
      rightPriceScale: { ...axis, visible: true },
      timeScale: { minBarSpacing: .02, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true, borderColor: border },
      localization: { locale: 'zh-CN', timeFormatter: (time: Time) => new Date(Number(time) * 1000).toISOString().slice(0, 10) },
    });
    instance.current = chart;

    for (const metric of metrics) {
      const { key, axis: priceScaleId, pane } = metric;
      const shortPercent = (value: number) => `${value.toFixed(2)}${key === 'spread_pp' ? '' : '%'}`;
      const series = chart.addSeries(LineSeries, {
        color: color(metric.color), priceScaleId, lineWidth: key === 'index_close' ? 1 : 2,
        crosshairMarkerRadius: 3, priceLineVisible: false, lastValueVisible: false,
        priceFormat: { type: 'custom', minMove: .01, formatter: key === 'index_close' ? number : shortPercent },
      }, pane);
      const last = history.reduce((lastIndex, point, index) => point[key] == null ? lastIndex : index, -1);
      series.setData(history.map((point, index) => {
        const time = times[index], value = point[key];
        if (value == null) return { time };
        // Hide the outgoing segment that the line series would draw over a gap.
        const gap = index < last && history[index + 1][key] == null;
        return { time, value, color: gap ? 'transparent' : undefined };
      }));
      if (key === 'flow_20d_pct') series.createPriceLine({
        price: 0, color: color('--chart-crosshair'), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false,
      });
    }
    chart.panes().forEach((pane, index) => pane.setStretchFactor(index ? 1 : 1.3));

    chart.subscribeCrosshairMove(event => {
      const index = event.point && event.logical !== undefined ? Math.round(event.logical) : -1;
      setHovered(history[index]);
    });
    const timeScale = chart.timeScale();
    if (previousRange.current && !previousRange.current.all) timeScale.setVisibleRange(previousRange.current);
    else timeScale.fitContent();
    // Keep the current window when a refreshed snapshot or theme recreates the chart.
    timeScale.subscribeVisibleTimeRangeChange(range => {
      if (!range) return;
      const preset = timeWindows.find(window => window.from === range.from && window.to === range.to);
      previousRange.current = { ...range, all: preset?.years === 0 };
      setSelectedRange(preset?.years ?? null);
    });
    return () => { chart.remove(); instance.current = null; };
  }, [history, times, timeWindows, theme]);

  function showWindow(window: typeof timeWindows[number]) {
    const scale = instance.current?.timeScale();
    if (!scale) return;
    if (window.years) scale.setVisibleRange(window);
    else scale.fitContent();
    setSelectedRange(window.years);
  }

  return <>
    <div className="stock-bond-chart-toolbar">
      <span>{hovered ? <time dateTime={hovered.date}>{hovered.date}</time> : '最近有效读数'}</span>
      <div className="segmented stock-bond-range" role="group" aria-label="股债走势图时间范围">
        {timeWindows.map(window => <button type="button" key={window.years} aria-pressed={selectedRange === window.years}
          className={selectedRange === window.years ? 'active' : ''} onClick={() => showWindow(window)}>{window.label}</button>)}
      </div>
    </div>
    <dl className="stock-bond-readout">
      {metrics.map(({ key, label, color, format }, index) => {
        const point = hovered ?? latest[index], value = point?.[key] ?? null;
        return <div key={key}>
          <dt><span style={{ background: `var(${color})` }} aria-hidden="true" />{label}</dt>
          <dd>
            <strong>{value == null ? '—' : format(value)}</strong>
            {!hovered && point && <time dateTime={point.date}>{point.date}</time>}
          </dd>
        </div>;
      })}
    </dl>
    <div ref={canvas} className="stock-bond-chart" role="img"
      aria-label="同一时间轴上的沪深 300 与国债收益率、股债收益差和国债 ETF 近 20 日净申赎率走势图" />
  </>;
}

function GroupReturns({ groups }: { groups: Group[] }) {
  if (!groups.length) return null;
  return <div className="stock-bond-returns">
    <h3>分组后沪深 300 平均收益</h3>
    <table>
      <thead><tr><th scope="col">入组条件</th>{horizons.map(days => <th key={days} scope="col" aria-label={`${days} 个交易日`} title={`${days} 个交易日`}>{days} 日</th>)}</tr></thead>
      <tbody>{groups.map(group => <tr key={group.id}>
        <th scope="row">{group.label}</th>
        {horizons.map(days => {
          const stat = group.horizons.find(item => item.days === days);
          const details = stat?.n ? `中位数 ${percent(stat.median_pct)} · 上涨比例 ${stat.positive_pct == null ? '—' : `${number(stat.positive_pct)}%`}` : undefined;
          const mean = stat?.mean_pct ?? null;
          return <td key={days} title={details}>
            <strong className={mean === 0 ? '' : tone(mean)}>{percent(mean)}</strong>
            <small>{stat?.n ?? 0} 个样本</small>
          </td>;
        })}
      </tr>)}</tbody>
    </table>
  </div>;
}

export default function StockBondOverview() {
  const title = useId();
  const [basket, setBasket] = useState<typeof baskets[number]>(baskets[0]);
  const { data, loading, error, refresh } = useEtfSnapshot<Snapshot>(basket.endpoint);
  const history = data?.history ?? [];
  return <section id="stock-bond-overview" className="panel stock-bond-overview" aria-labelledby={title} aria-busy={loading}>
    <header className="stock-bond-heading">
      <h2 id={title}><ChartNoAxesCombined size={18} aria-hidden="true" />股债联动</h2>
      <div>
        {data?.as_of && <span>历史快照截至 <time dateTime={data.as_of}>{data.as_of}</time></span>}
        <button className="subtle-button" type="button" aria-label="刷新股债联动" title="刷新" disabled={loading} onClick={refresh}>
          {loading ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
        </button>
      </div>
    </header>
    <div className="segmented stock-bond-baskets" role="group" aria-label="国债 ETF 样本">
      {baskets.map(sample => <button type="button" key={sample.id} aria-pressed={basket === sample}
        className={basket === sample ? 'active' : ''} onClick={() => setBasket(sample)}>{sample.label}</button>)}
    </div>
    {error && <p className="stock-bond-note" role="status">{error}</p>}
    {data?.status === 'stale' && !error && <p className="stock-bond-note" role="status">
      {data.expected_date ? `${data.expected_date} 数据尚未齐备，当前显示最近有效数据。` : '当前显示最近有效数据。'}
    </p>}
    {history.length ? <StockBondTrend history={history} /> : !error && <p className="stock-bond-note">
      {loading ? '正在加载股债走势…' : '股债走势待更新。'}
    </p>}
    {data && <GroupReturns groups={data.groups} />}
    {data && (data.method || data.funds.length > 0) && <details className="stock-bond-source">
      <summary>样本与分组规则</summary>
      {data.funds.length > 0 && <p className="stock-bond-source-links"><span>上交所份额</span>{data.funds.map(fund =>
        <a key={fund.code} href={`https://www.sse.com.cn/assortment/fund/list/etfinfo/basic/index.shtml?FUNDID=${encodeURIComponent(fund.code)}`}
          target="_blank" rel="noreferrer">{fund.name}（{fund.code}）</a>)}</p>}
      {data.flow_first && data.flow_last && <p>近 20 日净申赎率覆盖 {data.flow_first} 至 {data.flow_last}</p>}
      <p>股债收益差为沪深 300 盈利收益率减去 10 年期国债收益率。</p>
      {data.method && <p>{data.method}</p>}
      <p className="stock-bond-source-links">
        <a href="https://yield.chinabond.com.cn/cbweb-cbrc-web/cbrc/showCbrc" target="_blank" rel="noreferrer">中债收益率</a>
        <a href="https://www.csindex.com.cn/#/indices/family/detail?indexCode=000300" target="_blank" rel="noreferrer">沪深 300 指数</a>
      </p>
    </details>}
  </section>;
}
