import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { BarChart3, ExternalLink } from 'lucide-react';
import { ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi, IPrimitivePaneRenderer, ISeriesApi, ISeriesMarkersPluginApi, ISeriesPrimitive, MouseEventParams, SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts';
import TradingChartToolbar from './TradingChartToolbar';
import { chartTimestamp } from './tradingChartData';
import useEtfSnapshot from './useEtfSnapshot';
import { disclosureEvents, holdingPeriods } from './etfHoldingsMath';
import type { DateBasis, Fund } from './etfHoldingsMath';
import { number, percent, tone } from './format';
import { useTheme } from './ThemeContext';
import './etf-funds.css';
import './technical-chart.css';

type Basket = 'wide' | 'long';
type View = 'overlay' | 'flow' | 'inventory';
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
type Disclosure = ReturnType<typeof disclosureEvents>[number] & { fund: Fund };

const cash = (amount: number | null | undefined, signed = true) =>
  amount == null ? '—' : `${signed && amount > 0 ? '+' : ''}${number(amount / 1e8)} 亿元`;
const shares = (change: number) => `${change > 0 ? '+' : ''}${number(change / 1e8)} 亿份`;

function disclosureMarkers(dates: Map<string, Disclosure[]>, up: string, down: string): SeriesMarker<Time>[] {
  return [...dates].flatMap(([day, events]) => {
    const increasing = events.some(event => event.change > 0);
    const decreasing = events.some(event => event.change < 0);
    const time = chartTimestamp(day) as UTCTimestamp;
    const result: SeriesMarker<Time>[] = [];
    if (increasing) result.push({ time, id: day, position: decreasing ? 'aboveBar' : 'inBar', shape: 'arrowUp', color: up, size: 1.55 });
    if (decreasing) result.push({ time, id: day, position: increasing ? 'belowBar' : 'inBar', shape: 'arrowDown', color: down, size: 1.55 });
    return result;
  });
}

function flowAnnotations(chart: IChartApi, days: string[], color: string): ISeriesPrimitive<Time> {
  const times = days.map(day => chartTimestamp(day) as UTCTimestamp);
  const renderer: IPrimitivePaneRenderer = {
    draw(target) {
      target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        context.save();
        context.strokeStyle = color;
        context.setLineDash([3, 4]);
        context.beginPath();
        for (const time of times) {
          const x = chart.timeScale().timeToCoordinate(time);
          if (x == null || x < 0 || x > mediaSize.width) continue;
          context.moveTo(x, 0);
          context.lineTo(x, mediaSize.height);
        }
        context.stroke();
        context.restore();
      });
    },
  };
  const views = [{ zOrder: () => 'bottom' as const, renderer: () => renderer }];
  return { paneViews: () => views };
}

function FlowChart({ snapshot, view, basis, disclosures }: {
  snapshot: Snapshot; view: View; basis: DateBasis; disclosures: Map<string, Disclosure[]>;
}) {
  const showFlow = view !== 'inventory';
  const showInventory = view !== 'flow';
  const canvas = useRef<HTMLDivElement>(null);
  const instance = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Line'>[]>([]);
  const indexSeries = useRef<ISeriesApi<'Line'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lastIndex = useRef(0);
  const fullScreenButton = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [hoveredDay, setHoveredDay] = useState<string | null>(null);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    if (!canvas.current) return;
    const chart = createChart(canvas.current, {
      autoSize: true,
      layout: { attributionLogo: false, fontFamily: 'Inter, Segoe UI, Microsoft YaHei, sans-serif', fontSize: 11 },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      kineticScroll: { mouse: true, touch: true },
      timeScale: { minBarSpacing: .02, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      localization: { locale: 'zh-CN', timeFormatter: (time: Time) => new Date(Number(time) * 1000).toISOString().slice(0, 10) },
    });
    instance.current = chart;
    return () => {
      chart.remove();
      instance.current = null;
      series.current = [];
      indexSeries.current = null;
      markers.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const styles = getComputedStyle(document.documentElement);
    const color = (name: string) => styles.getPropertyValue(name).trim();
    const border = color('--chart-border');
    const grid = color('--chart-grid');
    const crosshair = color('--chart-crosshair');
    const guide = { color: crosshair, style: LineStyle.Dashed, labelBackgroundColor: crosshair };
    const axis = { borderColor: border, minimumWidth: 48, scaleMargins: { top: .1, bottom: .1 } };
    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: color('--theme-panel') }, textColor: color('--chart-text') },
      grid: { vertLines: { color: grid, style: LineStyle.Dotted }, horzLines: { color: grid, style: LineStyle.Dotted } },
      crosshair: { vertLine: guide, horzLine: guide },
      leftPriceScale: { ...axis, visible: true },
      rightPriceScale: { ...axis, visible: true },
      overlayPriceScales: { scaleMargins: axis.scaleMargins },
      timeScale: { borderColor: border },
    });

    const scale = chart.timeScale();
    const range = scale.getVisibleLogicalRange();
    markers.current?.detach();
    series.current.forEach(item => chart.removeSeries(item));
    series.current = [];
    const history = snapshot.history;
    const first = history[0].date;
    const last = history.at(-1)!.date;
    const marks = new Set(snapshot.announcements.filter(event => first <= event.date && event.date <= last).map(event => event.date));
    const disclosure = snapshot.inventory.disclosed.date;
    const anchorDates = new Set(marks);
    if (first <= disclosure && disclosure <= last) {
      anchorDates.add(disclosure);
      if (showInventory) marks.add(disclosure);
    }
    const plotDates = new Set(history.map(point => point.date));
    const timelineAnchors = [...anchorDates].filter(day => !plotDates.has(day)).map(day => ({ time: chartTimestamp(day) as UTCTimestamp }));

    const addLine = (
      valueOf: (point: Point) => number | null,
      lineColor: string,
      priceScaleId: string,
      formatter: (value: number) => string,
      width: 1 | 2,
      anchors: { time: UTCTimestamp }[] = [],
    ) => {
      const line = chart.addSeries(LineSeries, {
        priceScaleId, color: lineColor, lineWidth: width, crosshairMarkerRadius: 3,
        crosshairMarkerBackgroundColor: lineColor, priceLineVisible: false,
        lastValueVisible: priceScaleId !== 'cumulative', priceFormat: { type: 'custom', minMove: .01, formatter },
      });
      series.current.push(line);
      const values = history.map(valueOf);
      const lastValue = values.reduce<number>((last, value, index) => value == null ? last : index, -1);
      const rows = history.map((point, index) => {
        const time = chartTimestamp(point.date) as UTCTimestamp;
        const value = values[index];
        if (value == null) return { time };
        // Lightweight Charts joins numeric points across whitespace. Hide that
        // outgoing segment so missing ETF observations stay visibly blank.
        return { time, value, color: index < lastValue && values[index + 1] == null ? 'transparent' : undefined };
      });
      line.setData(anchors.length ? [...rows, ...anchors].sort((a, b) => a.time - b.time) : rows);
      return line;
    };
    const ticks = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
    const index = addLine(point => point.index_close, color('--chart-marker-neutral'), 'right', ticks, 1, timelineAnchors);
    index.attachPrimitive(flowAnnotations(chart, [...marks], crosshair));
    indexSeries.current = index;
    markers.current = createSeriesMarkers(index, [], { zOrder: 'top' });
    let flow: ISeriesApi<'Line'> | undefined;
    let inventory: ISeriesApi<'Line'> | undefined;
    if (showFlow) flow = addLine(point => point.score, color('--theme-warning'), 'left', value => ticks(value) + '%', 2);
    if (showInventory) inventory = addLine(
      point => point.cumulative_amount == null ? null : point.cumulative_amount / 1e8,
      color('--ink-violet'), showFlow ? 'cumulative' : 'left', ticks, 2,
    );
    if (view !== 'overlay') (flow ?? inventory)?.createPriceLine({
      price: 0, color: crosshair, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false,
    });
    lastIndex.current = history.length + timelineAnchors.length - 1;
    if (range) scale.setVisibleLogicalRange(range);
    else scale.fitContent();
  }, [snapshot, theme, view, showFlow, showInventory]);

  useEffect(() => {
    const styles = getComputedStyle(document.documentElement);
    markers.current?.setMarkers(disclosureMarkers(
      disclosures, styles.getPropertyValue('--chart-up').trim(), styles.getPropertyValue('--chart-down').trim(),
    ));
  }, [disclosures, snapshot, theme, view]);

  useEffect(() => {
    setHoveredDay(null);
    setSelectedDay(null);
    instance.current?.clearCrosshairPosition();
  }, [basis]);

  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const scale = chart.timeScale();
    const move = ({ point, time, hoveredInfo }: MouseEventParams) => {
      const markerId = hoveredInfo?.objectId;
      const marker = point && hoveredInfo?.objectKind === 'series-marker' && typeof markerId === 'string'
        ? disclosures.get(markerId)?.[0] : undefined;
      if (marker && indexSeries.current) chart.setCrosshairPosition(marker.close, chartTimestamp(marker.date) as UTCTimestamp, indexSeries.current);
      setHoveredDay(marker?.date ?? (point && typeof time === 'number' ? new Date(time * 1000).toISOString().slice(0, 10) : null));
    };
    const click = ({ point }: MouseEventParams) => {
      const line = indexSeries.current;
      if (!point || !line) return;
      // A narrow chart spans several sessions per pixel. Match the marker's
      // plotted coordinates to find its exact date when the user taps it.
      let best = 16;
      let selected: Disclosure | undefined;
      for (const events of disclosures.values()) {
        const event = events[0];
        const x = scale.timeToCoordinate(chartTimestamp(event.date) as UTCTimestamp);
        const y = line.priceToCoordinate(event.close);
        if (x == null || y == null || x < 0 || x > scale.width()) continue;
        const offsets = events.some(event => event.change > 0) && events.some(event => event.change < 0) ? [-16, 16] : [0];
        const distance = Math.min(...offsets.map(offset => Math.hypot(point.x - x, point.y - y - offset)));
        if (distance >= best) continue;
        selected = event;
        best = distance;
      }
      setSelectedDay(selected?.date ?? null);
      if (selected) {
        setHoveredDay(null);
        chart.setCrosshairPosition(selected.close, chartTimestamp(selected.date) as UTCTimestamp, line);
      }
    };
    chart.subscribeCrosshairMove(move);
    chart.subscribeClick(click);
    chart.subscribeDblClick(click);
    return () => {
      chart.unsubscribeCrosshairMove(move);
      chart.unsubscribeClick(click);
      chart.unsubscribeDblClick(click);
    };
  }, [disclosures]);

  useEffect(() => {
    if (!expanded) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setExpanded(false); fullScreenButton.current?.focus(); }
    };
    document.addEventListener('keydown', escape);
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', escape); };
  }, [expanded]);

  function zoom(factor: number) {
    const scale = instance.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const center = (range.from + range.to) / 2;
    const half = Math.max(2, (range.to - range.from) * factor / 2);
    scale.setVisibleLogicalRange({ from: center - half, to: center + half });
  }

  function reset() {
    instance.current?.timeScale().fitContent();
    series.current.forEach(line => line.priceScale().setAutoScale(true));
    setHoveredDay(null);
    setSelectedDay(null);
  }

  function latest() {
    const scale = instance.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (scale && range) scale.setVisibleLogicalRange({ from: lastIndex.current - (range.to - range.from), to: lastIndex.current });
    setHoveredDay(null);
    setSelectedDay(null);
  }

  const activeDay = hoveredDay ?? selectedDay;
  const point = activeDay ? snapshot.history.find(row => row.date === activeDay) : snapshot.history.at(-1);
  const day = activeDay ?? point?.date;
  const events = day ? snapshot.announcements.filter(event => event.date === day) : [];
  const changes = activeDay ? disclosures.get(activeDay) ?? [] : [];
  const disclosure = snapshot.inventory.disclosed;
  return <div className={`technical-chart etf-flow-trading-chart${expanded ? ' is-expanded' : ''}`}>
    <TradingChartToolbar title="资金走势" expanded={expanded} fullScreenButton={fullScreenButton}
      onZoom={zoom} onReset={reset} onLatest={latest} onFullScreen={() => setExpanded(value => !value)} />
    <div className="etf-flow-chart-readout">
      <ul className="etf-overlay-legend" aria-label="曲线与坐标轴">
        {showFlow && <li className="etf-overlay-flow">20 日动向 <b className="mono">{percent(point?.score)}</b></li>}
        {showInventory && <li className="etf-overlay-inventory">累计变化 <b className="mono">{cash(point?.cumulative_amount)}</b></li>}
        <li className="etf-overlay-index">上证指数 <b className="mono">{number(point?.index_close)}{point?.index_close != null && ' 点'}</b></li>
        {disclosures.size > 0 && <>
          <li className="etf-disclosure-marker is-increase">披露增持</li>
          <li className="etf-disclosure-marker is-decrease">披露减持</li>
        </>}
      </ul>
      <time dateTime={day}>{day}{!activeDay && ' · 最新'}</time>
    </div>
    <div className="technical-chart-stage etf-flow-chart-stage">
      <div ref={canvas} className="technical-chart-canvas" role="img" aria-label={view === 'overlay'
        ? '20 日动向、累计变化与上证指数叠加图'
        : showInventory ? '累计异常净申赎与上证指数走势' : '资金动向指标与上证指数走势'} />
    </div>
    {changes.length > 0 ? <div className="etf-flow-chart-details etf-flow-disclosure-details">
      {changes.map(({ fund, period, change }) => <div key={`${fund.code}-${period.report_date}`}>
        <strong className={tone(change)}>{fund.name} · {shares(change)}</strong>
        <span>报告期末 {period.report_date}{period.publication_date && ` · 披露日 ${period.publication_date}`}</span>
      </div>)}
      {changes[0].position != null && <span>上证近一年区间位置 {changes[0].position.toFixed(1)}%</span>}
      {events.map(event => <span key={event.url}>{event.entity} · {event.title}</span>)}
    </div> : activeDay && <div className="etf-flow-chart-details">
      {point?.net != null && <span>样本净申赎 {cash(point.net)}</span>}
      {showFlow && point?.amount != null && <span>窗口内异常净申赎 {cash(point.amount)}</span>}
      {showFlow && point?.agreeing_groups != null && <span>同向异常 {point.agreeing_groups}/{snapshot.group_count} 类宽基</span>}
      {showInventory && !!point?.daily_counted_flow && <span>当日计入 {cash(point.daily_counted_flow)}</span>}
      {showInventory && day === disclosure.date && <span>年报具名持仓 {cash(disclosure.amount, false)}</span>}
      {events.map(event => <span key={event.url}>{event.entity} · {event.title}</span>)}
    </div>}
    <div className="chart-interaction-hint">
      <span>滚轮缩放 · 拖动平移{disclosures.size > 0 && ' · 悬停或点按箭头查看披露'}</span>
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer" title="TradingView Lightweight Charts™">TradingView</a>
    </div>
  </div>;
}

export default function EtfFlowIndicator() {
  const title = useId();
  const [basket, setBasket] = useState<Basket>('wide');
  const [view, setView] = useState<View>('overlay');
  const [basis, setBasis] = useState<DateBasis>('published');
  const { data: snapshot, loading, error } = useEtfSnapshot<Snapshot>(`/api/market/etf-flow-indicator?basket=${basket}`);
  const { data: funds, error: disclosureError } = useEtfSnapshot<Fund[]>('/market-archives/etf-holder-disclosures.json');
  const disclosureFunds = useMemo(() => (funds ?? []).filter(fund => snapshot?.funds.includes(fund.code)), [funds, snapshot]);
  const disclosures = useMemo(() => {
    const days = new Map<string, Disclosure[]>();
    if (!snapshot?.history.length || !disclosureFunds.length) return days;
    const index = snapshot.history.flatMap(point => point.index_close == null ? [] : [{ date: point.date, close: point.index_close }]);
    const events = disclosureFunds.flatMap(fund => disclosureEvents(holdingPeriods(fund, 'all'), index, basis)
      .map(event => ({ ...event, fund }))).sort((a, b) => a.date.localeCompare(b.date));
    for (const event of events) {
      const sameDay = days.get(event.date);
      if (sameDay) sameDay.push(event);
      else days.set(event.date, [event]);
    }
    return days;
  }, [snapshot, disclosureFunds, basis]);
  const showFlow = view !== 'inventory';
  const showInventory = view !== 'flow';
  const inventory = snapshot?.inventory;
  const latest = snapshot?.history.filter(point => point.score != null || point.cumulative_amount != null).at(-1);

  return (
    <section id="etf-flow-indicator" className="panel etf-funds-card etf-holdings-card" aria-labelledby={title} aria-busy={loading}>
      <header className="etf-funds-heading">
        <h2 id={title}><BarChart3 size={17} aria-hidden="true" />国家队资金动向（宽基 ETF 代理）</h2>
        {latest && <div className="etf-funds-status etf-overlay-readings">
          {showFlow && <span>20 日动向 <strong className={latest.score ? tone(latest.score) : undefined}>{percent(latest.score)}</strong></span>}
          {showInventory && <span>累计变化 <strong className={latest.cumulative_amount ? tone(latest.cumulative_amount) : undefined}>{cash(latest.cumulative_amount)}</strong></span>}
          <time dateTime={latest.date}>{latest.date}</time>
        </div>}
      </header>
      <div className="etf-holdings-controls">
        <div className="etf-funds-groups" role="group" aria-label="选择宽基 ETF 样本">
          <button type="button" aria-pressed={basket === 'wide'} onClick={() => setBasket('wide')}>完整样本 · 9 只</button>
          <button type="button" aria-pressed={basket === 'long'} onClick={() => setBasket('long')}>长历史 · 5 只</button>
        </div>
        <div className="etf-funds-groups" role="group" aria-label="选择资金指标">
          <button type="button" aria-pressed={view === 'overlay'} onClick={() => setView('overlay')}>叠加</button>
          <button type="button" aria-pressed={view === 'flow'} onClick={() => setView('flow')}>20 日动向</button>
          <button type="button" aria-pressed={view === 'inventory'} onClick={() => setView('inventory')}>累计变化</button>
        </div>
        {disclosureFunds.length > 0 && <div id="etf-holdings" className="etf-funds-groups" role="group" aria-label="披露箭头时间">
          <button type="button" aria-pressed={basis === 'published'} onClick={() => setBasis('published')}>披露后</button>
          <button type="button" aria-pressed={basis === 'period-end'} onClick={() => setBasis('period-end')}>报告期末</button>
        </div>}
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
      {snapshot?.history.length ? <FlowChart key={basket} snapshot={snapshot} view={view} basis={basis} disclosures={disclosures} /> : (
        <p className="etf-funds-empty" role="status">{error || (snapshot ? '暂无足够的历史份额数据' : '正在读取资金指标…')}</p>
      )}
      {error && snapshot?.history.length ? <p className="etf-funds-notice" role="status">更新失败，显示上次加载的数据。</p> : null}
      {disclosureError && !funds && <p className="etf-funds-notice" role="status">公开持仓披露档案暂不可用。</p>}
      <p className="etf-funds-note">
        {showInventory && '累计变化从样本起点逐日累加。'}
        每个交易日早间更新上一交易日数据，固定竖虚线标记公告日或年报持仓统计期末。
        {disclosureFunds.length > 0 && (basis === 'published'
          ? '箭头对齐披露后的首个交易日。'
          : '箭头对齐报告期末或此前最近交易日，持仓数据当时尚未公开。')}
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
        {disclosureFunds.length > 0 && <p className="etf-funds-note">
          披露箭头覆盖 {disclosureFunds.map(fund => fund.name).join('、')}，按相邻报告中同一批具名账户的份额变化标记。
          近一年区间位置按过去 252 个交易日的收盘价高低区间计算。
        </p>}
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
        {disclosures.size > 0 && <ul className="etf-funds-events" aria-label="增减持披露原文">
          {[...disclosures.values()].reverse().flat().map(({ date, fund, period, change }) => (
            <li key={`${fund.code}-${period.report_date}`}><time dateTime={date}>{date}</time>
              <span className={tone(change)}>{fund.name} · {shares(change)}</span>
              {period.source_url && <a href={period.source_url} target="_blank" rel="noreferrer">报告原文 <ExternalLink size={11} aria-hidden="true" /></a>}
            </li>
          ))}
        </ul>}
      </details>
    </section>
  );
}
