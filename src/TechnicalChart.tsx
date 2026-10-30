import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, Maximize2, Minimize2, Minus, Plus, RotateCcw } from 'lucide-react';
import { AreaSeries, CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, PriceScaleMode, createChart } from 'lightweight-charts';
import type { AutoscaleInfo, IChartApi, IPriceLine, ISeriesApi, SeriesType, Time, UTCTimestamp } from 'lightweight-charts';
import { useTheme } from './ThemeContext';
import type { Candle } from './types';
import { bollingerBands, exponentialMovingAverage, heikinAshi, movingAverage } from './chartMath';
import type { ChartOverlay, ChartStyle } from './chartMath';
import type { buildIntraday } from './intradayMath';
import IndicatorPicker from './IndicatorPicker';
import { displayedIndicators } from './indicatorCatalog';
import type { IndicatorSelection } from './indicatorCatalog';
import { calculateIndicator } from './technicalIndicators';
import type { IndicatorLine, IndicatorResult } from './technicalIndicators';
import { chartLayout, formatIndicatorValue, preparePlotData, projectValues } from './chartPresentation';
import { chartTimestamp, initialChartRange } from './tradingChartData';
import './technical-chart.css';

type Session = ReturnType<typeof buildIntraday>;
type ChartSeries = ISeriesApi<SeriesType>;
type Props = {
  instrument?: string; volumeUnit?: string; candles: Candle[]; mode: string; period: string; chartStyle?: ChartStyle; overlay?: ChartOverlay;
  selection: IndicatorSelection; onSelectionChange: (value: IndicatorSelection) => void;
  session?: Session;
};
function overlays(candles: Candle[], overlay: ChartOverlay): IndicatorLine[] {
  const close = candles.map(row => row.close);
  if (overlay === 'MA' || overlay === 'EMA') return [5, 10, 20].map((length, index) => ({
    key: `${overlay}${length}`, label: `${overlay}${length}`, description: `${length}期${overlay === 'MA' ? '均价' : '指数均价'}`,
    values: overlay === 'MA' ? movingAverage(close, length) : exponentialMovingAverage(close, length), kind: 'line', colorIndex: index,
  }));
  if (overlay !== 'BOLL') return [];
  const bands = bollingerBands(close);
  return [
    { key: 'MID', label: 'BOLL中轨', description: '20期均价', values: bands.middle },
    { key: 'UPPER', label: 'BOLL上轨', description: '均价＋2倍标准差', values: bands.upper },
    { key: 'LOWER', label: 'BOLL下轨', description: '均价－2倍标准差', values: bands.lower },
  ].map((line, index) => ({ ...line, kind: 'line', colorIndex: index }));
}

export default function TechnicalChart({ instrument, candles, mode, period, chartStyle = 'candles', overlay = 'MA', selection, onSelectionChange, session, volumeUnit = '股' }: Props) {
  const { theme } = useTheme();
  const canvas = useRef<HTMLDivElement>(null);
  const instance = useRef<IChartApi | null>(null);
  const series = useRef(new Map<string, ChartSeries>());
  const previousCloseLine = useRef<IPriceLine | null>(null);
  const defaultRange = useRef<{ from: number; to: number } | null>(null);
  const view = useRef('');
  const fullScreenButton = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [compact, setCompact] = useState(false);
  const [percentage, setPercentage] = useState(false);
  const [point, setPoint] = useState<number | null>(null);
  const [paneTops, setPaneTops] = useState<number[]>([]);
  const plot = useMemo(() => preparePlotData(candles, period, session), [candles, period, session]);
  const times = useMemo(() => plot.dates.map(date => chartTimestamp(date) as UTCTimestamp), [plot]);
  const idKey = displayedIndicators(selection).join(',');
  const indicators = useMemo(() => displayedIndicators(selection).map(id => {
    const result = calculateIndicator(id, plot.source);
    return { ...result, lines: result.lines.map(line => ({ ...line, values: projectValues(plot, line.values) })) };
  }), [plot, idKey]);
  const mainLines = useMemo(() => overlays(plot.source, session ? 'none' : overlay).map(line => ({ ...line, values: projectValues(plot, line.values) })), [plot, overlay, !!session]);
  const plotted = useMemo(() => !session && chartStyle === 'heikin' ? heikinAshi(plot.source) : plot.source, [plot, chartStyle, !!session]);
  const layout = chartLayout(indicators.length, compact);
  const active = point ?? plot.latestIndex;
  const latest = active >= 0 ? plot.display[active] : null;
  const shown = !session && chartStyle === 'heikin' ? plotted[active] : latest;
  const activeDate = active >= 0 ? plot.dates[active] : session?.day ?? '暂无数据';
  const palette = theme === 'dark'
    ? ['#d6b66d', '#5995eb', '#bb92eb', '#3ab89c', '#e19564', '#d1d8e3']
    : ['#97711d', '#2962ff', '#9254bb', '#16836f', '#b96b37', '#52606e'];
  const eventDates = useRef(plot.dates);
  eventDates.current = plot.dates;
  const intraday = !!session;
  const isLine = intraday || chartStyle === 'line' || chartStyle === 'area';

  useEffect(() => {
    if (!canvas.current) return;
    const chart = createChart(canvas.current, {
      autoSize: true,
      layout: { attributionLogo: true, fontFamily: 'Inter, Segoe UI, Microsoft YaHei, sans-serif', fontSize: 11, panes: { enableResize: true } },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      kineticScroll: { mouse: true, touch: true },
      rightPriceScale: { minimumWidth: 66, borderVisible: true },
      timeScale: { minBarSpacing: .5, rightOffset: 0, lockVisibleTimeRangeOnResize: true, ignoreWhitespaceIndices: false },
      localization: { locale: 'zh-CN' },
    });
    instance.current = chart;
    chart.subscribeCrosshairMove(event => {
      const i = event.point && event.logical !== undefined ? Math.round(event.logical) : -1;
      setPoint(i >= 0 && i < eventDates.current.length ? i : null);
    });
    const observer = new ResizeObserver(() => setCompact((canvas.current?.clientWidth ?? 800) < 560));
    observer.observe(canvas.current);
    return () => { observer.disconnect(); chart.remove(); instance.current = null; series.current.clear(); };
  }, []);

  // Rebuild only when the types of series/panes change. Polls and theme switches
  // update the existing series, preserving the user's time and price scale.
  const structure = `${intraday ? 'intraday' : chartStyle}:${intraday ? 'none' : overlay}:${idKey}`;
  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const range = chart.timeScale().getVisibleLogicalRange();

    previousCloseLine.current = null;
    // Remove auxiliary panes from bottom to top; the main pane stays at index 0.
    [...series.current.values()].reverse().forEach(item => chart.removeSeries(item));
    series.current.clear();
    const common = { priceLineVisible: false, lastValueVisible: false };
    const main = isLine
      ? intraday || chartStyle === 'area'
        ? chart.addSeries(AreaSeries, { lineWidth: 2, priceLineVisible: true })
        : chart.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: true })
      : chart.addSeries(CandlestickSeries, { priceLineVisible: true });
    series.current.set('main', main);
    mainLines.forEach(line => series.current.set(`overlay-${line.key}`, chart.addSeries(LineSeries, { ...common, lineWidth: 1, crosshairMarkerVisible: false })));
    indicators.forEach((indicator, pane) => indicator.lines.forEach(line => {
      series.current.set(`${indicator.id}-${line.key}`, line.kind === 'bar'
        ? chart.addSeries(HistogramSeries, common, pane + 1)
        : chart.addSeries(LineSeries, { ...common, lineWidth: 1, crosshairMarkerRadius: 3 }, pane + 1));
    }));
    chart.panes().forEach((pane, index) => {
      pane.priceScale('right').applyOptions({ scaleMargins: { top: index ? .37 : .2, bottom: .1 }, minimumWidth: 66 });
    });
    if (range) chart.timeScale().setVisibleLogicalRange(range);
    const syncHeaders = () => {
      let top = 0;
      const tops = chart.panes().map(pane => { const value = top; top += pane.getHeight() + 1; return value; });
      setPaneTops(previous => previous.join(',') === tops.join(',') ? previous : tops);
    };
    const observer = new ResizeObserver(syncHeaders);
    chart.panes().forEach(pane => { const element = pane.getHTMLElement(); if (element) observer.observe(element); });
    syncHeaders();
    return () => observer.disconnect();
  }, [structure]);

  useEffect(() => {
    instance.current?.panes().forEach((pane, index) => pane.setStretchFactor(index ? 180 : layout.priceTop + layout.priceHeight));
  }, [structure, compact]);

  useEffect(() => { setPoint(null); }, [plot]);
  useEffect(() => {
    const chart = instance.current;
    const main = series.current.get('main');
    if (!chart || !main) return;
    const styles = getComputedStyle(document.documentElement);
    const color = (name: string) => styles.getPropertyValue(`--chart-${name}`).trim();
    const reference = session?.previousClose;
    const center = reference ?? session?.quote.price ?? 1;
    const span = Math.max(center * .003, ...plot.display.flatMap(row => row ? [Math.abs(row.close - center)] : [])) * 1.12;
    const viewport = chart.timeScale().getVisibleLogicalRange();
    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: styles.getPropertyValue('--theme-panel').trim() || (theme === 'dark' ? '#171b24' : '#ffffff') }, textColor: color('text'), panes: { separatorColor: color('grid'), separatorHoverColor: color('border') } },
      grid: { vertLines: { color: color('grid'), style: LineStyle.Dotted }, horzLines: { color: color('grid'), style: LineStyle.Dotted } },
      crosshair: { vertLine: { color: color('crosshair'), style: LineStyle.Dashed, labelBackgroundColor: color('crosshair') }, horzLine: { color: color('crosshair'), style: LineStyle.Dashed, labelBackgroundColor: color('crosshair') } },
      rightPriceScale: { borderColor: color('border'), mode: PriceScaleMode.Normal },
      timeScale: { borderColor: color('border'), fixLeftEdge: intraday, fixRightEdge: !intraday, timeVisible: intraday || period.endsWith('分钟'), secondsVisible: false,
        tickMarkFormatter: (time: Time) => {
          const date = new Date(Number(time) * 1000).toISOString();
          if (intraday) return date.slice(11, 16) === '11:30' ? '11:30/13:00' : date.slice(11, 16);
          return period.endsWith('分钟') ? date.slice(11, 16) : date.slice(5, 10);
        } },
      localization: { timeFormatter: (time: Time) => new Date(Number(time) * 1000).toISOString().slice(0, intraday || period.endsWith('分钟') ? 16 : 10).replace('T', ' ') },
    });
    const priceFormat = intraday && percentage && reference
      ? { type: 'custom' as const, minMove: .01, formatter: (price: number) => `${(100 * (price / reference - 1)).toFixed(2)}%` }
      : { type: 'price' as const, precision: 2, minMove: .01 };
    main.applyOptions({ priceFormat, autoscaleInfoProvider: intraday ? () => ({ priceRange: { minValue: Math.max(.0001, center - span), maxValue: center + span } }) : undefined });
    if (isLine) {
      main.applyOptions(intraday || chartStyle === 'area'
        ? { lineColor: color('marker-neutral'), topColor: color('marker-neutral') + '28', bottomColor: color('marker-neutral') + '00' }
        : { color: color('marker-neutral') });
      main.setData(plot.display.map((row, i) => row ? { time: times[i], value: row.close } : { time: times[i] }));
    } else {
      main.applyOptions({ upColor: chartStyle === 'hollow' ? 'transparent' : color('up'), downColor: chartStyle === 'hollow' ? 'transparent' : color('down'), borderUpColor: color('up'), borderDownColor: color('down'), wickUpColor: color('up'), wickDownColor: color('down') });
      main.setData(plotted.map((row, i) => ({ time: times[i], open: row.open, high: row.high, low: row.low, close: row.close })));
    }
    mainLines.forEach(line => {
      const item = series.current.get(`overlay-${line.key}`)!;
      item.applyOptions({ color: palette[line.colorIndex], lineStyle: overlay === 'BOLL' && line.colorIndex > 0 ? LineStyle.Dashed : LineStyle.Solid });
      item.setData(line.values.map((value, i) => value == null ? { time: times[i] } : { time: times[i], value }));
    });
    indicators.forEach((indicator, pane) => {
      indicator.lines.forEach(line => {
        const item = series.current.get(`${indicator.id}-${line.key}`)!;
        item.applyOptions({ color: palette[line.colorIndex % palette.length],
          priceFormat: indicator.unit === 'volume' ? { type: 'volume' } : { type: 'price', precision: 2, minMove: .01 },
          autoscaleInfoProvider: indicator.range ? (original: () => AutoscaleInfo | null) => { const value = original(); return value?.priceRange ? { ...value, priceRange: { minValue: Math.min(value.priceRange.minValue, indicator.range![0]), maxValue: Math.max(value.priceRange.maxValue, indicator.range![1]) } } : null; } : undefined,
        });
        item.setData(line.values.map((value, i) => value == null ? { time: times[i] } : { time: times[i], value,
          ...(line.kind === 'bar' ? { color: (indicator.id === 'MACD' ? value >= 0 : (plot.display[i]?.close ?? 0) >= (plot.display[i]?.open ?? 0)) ? color('volume-up') : color('volume-down') } : {}),
        }));
      });
      chart.priceScale('right', pane + 1).applyOptions({ borderColor: color('border'), mode: PriceScaleMode.Normal });
    });
    if (previousCloseLine.current) main.removePriceLine(previousCloseLine.current);
    previousCloseLine.current = intraday && reference ? main.createPriceLine({ price: reference, color: color('crosshair'), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '昨收' }) : null;
    const nextView = `${period}:${mode}:${session?.day ?? ''}`;
    defaultRange.current = initialChartRange(plot.dates, period, mode);
    if (view.current !== nextView || !viewport) {
      view.current = nextView;
      if (defaultRange.current) chart.timeScale().setVisibleLogicalRange(defaultRange.current);
    } else chart.timeScale().setVisibleLogicalRange(viewport);
  }, [plot, plotted, times, mainLines, indicators, structure, mode, period, overlay, chartStyle, theme, percentage]);

  useEffect(() => {
    if (!expanded) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const keyboard = (event: KeyboardEvent) => { if (event.key === 'Escape' && !document.querySelector('.indicator-picker-panel')) { setExpanded(false); fullScreenButton.current?.focus(); } };
    document.addEventListener('keydown', keyboard);
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', keyboard); };
  }, [expanded]);

  function zoom(factor: number) {
    const scale = instance.current?.timeScale(), range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const center = (range.from + range.to) / 2;
    const half = Math.max(2, (range.to - range.from) * factor / 2);
    scale.setVisibleLogicalRange({ from: center - half, to: center + half });
  }
  function reset() {
    const chart = instance.current;
    if (!chart) return;
    if (defaultRange.current) chart.timeScale().setVisibleLogicalRange(defaultRange.current);
    chart.panes().forEach(pane => pane.priceScale('right').applyOptions({ autoScale: true }));
    setPoint(null);
  }
  function latestView() {
    const scale = instance.current?.timeScale(), range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    if (intraday && plot.latestIndex < 0) { reset(); return; }
    const end = intraday ? Math.min(plot.dates.length - .5, plot.latestIndex + 4) : plot.dates.length - 1;
    scale.setVisibleLogicalRange({ from: end - (range.to - range.from), to: end });
  }
  function values(lines: IndicatorLine[], unit: IndicatorResult['unit']) {
    return lines.map(line => <span className="chart-readout-value" key={line.key} style={{ color: palette[line.colorIndex % palette.length] }}>
      <i aria-hidden="true" /><span>{line.label}<small>{line.description}</small></span><b className="mono">{formatIndicatorValue(line.values[active], unit, volumeUnit)}</b>
    </span>);
  }
  const empty = !latest;
  return <div className={`technical-chart${expanded ? ' is-expanded' : ''}`}>
    <div className="chart-navigation" role="group" aria-label="图表交互工具">
      <span className="chart-navigation-title">{instrument ?? (session ? '分时走势' : 'K 线图表')}<small>{period}</small></span>
      <button type="button" onClick={() => zoom(.75)} aria-label="放大图表" title="放大"><Plus size={15} /></button>
      <button type="button" onClick={() => zoom(1.33)} aria-label="缩小图表" title="缩小"><Minus size={15} /></button>
      <button type="button" onClick={reset} aria-label="复位图表" title="恢复默认范围及自动价格刻度"><RotateCcw size={14} /></button>
      <button type="button" onClick={latestView} aria-label="回到最新" title="回到最新行情"><ChevronRight size={16} /></button>
      {intraday && <button type="button" className="chart-percentage-toggle" aria-label="显示百分比刻度" aria-pressed={percentage} disabled={intraday && !session.previousClose} onClick={() => setPercentage(value => !value)} title="相对昨收的涨跌幅">%</button>}
      <button type="button" ref={fullScreenButton} onClick={() => setExpanded(value => !value)} aria-label={expanded ? '退出全屏图表' : '全屏图表'} title={expanded ? '退出全屏 · Esc' : '全屏图表'}>{expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
    </div>
    <div className="technical-chart-stage" style={{ height: layout.height }}>
      <div ref={canvas} className="technical-chart-canvas" role="img" aria-label={`${period}与${indicators.map(item => item.label).join('、')}，滚轮缩放、拖动平移、拖动坐标轴调整刻度，双击坐标轴复位${session ? '；完整交易日横轴，午休折叠' : ''}`} />
      <div className="chart-readout chart-main-readout" data-testid="chart-main-readout" style={{ top: (paneTops[0] ?? 0) + 4 }}>
        <div className="chart-readout-heading"><strong>{session ? '分时' : chartStyle === 'heikin' ? '平滑 K 线 · 计算价格' : period}</strong><time>{activeDate}{point === null ? ' · 最新' : ''}</time></div>
        <div className="chart-readout-values">
          {shown && <span className={`chart-ohlc ${shown.close >= shown.open ? 'up' : 'down'}`}><span>开 {shown.open.toFixed(2)}</span><span>高 {shown.high.toFixed(2)}</span><span>低 {shown.low.toFixed(2)}</span><span>收 {shown.close.toFixed(2)}</span></span>}
          {values(mainLines, 'price')}{empty && <span className="chart-data-gap">此时点暂无行情</span>}
        </div>
      </div>
      {indicators.map((indicator, index) => <div key={indicator.id} className="chart-readout chart-indicator-readout" data-indicator-pane={indicator.id} style={{ top: (paneTops[index + 1] ?? layout.panes[index].headerTop) + 4 }}>
        <div className="chart-readout-heading">{index === 0 ? <>{selection.mode === 'multiple' && <strong>{indicator.id === 'VOL' ? '成交量' : indicator.id}</strong>}<IndicatorPicker selection={selection} onChange={onSelectionChange} /></> : <strong>{indicator.id === 'VOL' ? '成交量' : indicator.id}</strong>}
          <span className="chart-indicator-parameters">{indicator.parameters}</span><time>{activeDate}</time></div>
        <div className="chart-readout-values">{values(indicator.lines, indicator.unit)}{indicator.lines.every(line => line.values[active] == null) && <span className="chart-data-gap">{empty ? '此时点无数据' : '历史数据不足'}</span>}</div>
      </div>)}
    </div>
    <div className="chart-interaction-hint"><span>滚轮缩放 · 拖动平移 · 双击坐标轴复位</span><span><a href="/licenses/lightweight-charts.txt" target="_blank" rel="noreferrer" title="TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc.">图表版权</a> · 北京时间 UTC+8</span></div>
  </div>;
}
