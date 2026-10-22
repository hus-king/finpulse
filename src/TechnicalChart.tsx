import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts';
import type { LineSeriesOption, BarSeriesOption, CandlestickSeriesOption } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useTheme } from './ThemeContext';
import type { Candle, News } from './types';
import { bollingerBands, exponentialMovingAverage, heikinAshi, movingAverage } from './chartMath';
import type { ChartOverlay, ChartStyle } from './chartMath';
import type { buildIntraday } from './intradayMath';
import IndicatorPicker from './IndicatorPicker';
import { displayedIndicators } from './indicatorCatalog';
import type { IndicatorSelection } from './indicatorCatalog';
import { calculateIndicator } from './technicalIndicators';
import type { IndicatorLine, IndicatorResult } from './technicalIndicators';
import { chartLayout, formatIndicatorValue, preparePlotData, projectValues, readoutIndex } from './chartPresentation';
import './technical-chart.css';

echarts.use([CandlestickChart, LineChart, BarChart, GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent, MarkLineComponent, CanvasRenderer]);
type Series = LineSeriesOption | BarSeriesOption | CandlestickSeriesOption;
type Session = ReturnType<typeof buildIntraday>;
const noNews: News[] = [];
type Props = {
  volumeUnit?: string; candles: Candle[]; news?: News[]; mode: string; period: string; chartStyle?: ChartStyle; overlay?: ChartOverlay;
  selection: IndicatorSelection; onSelectionChange: (value: IndicatorSelection) => void;
  onEvent?: (id: string) => void; session?: Session;
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

export default function TechnicalChart({ candles, news = noNews, mode, period, chartStyle = 'candles', overlay = 'MA', selection, onSelectionChange, onEvent, session, volumeUnit = '股' }: Props) {
  const { theme } = useTheme();
  const canvas = useRef<HTMLDivElement>(null);
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null);
  const lastView = useRef('');
  const [compact, setCompact] = useState(false);
  const [point, setPoint] = useState<string | null>(null);
  const plot = useMemo(() => preparePlotData(candles, period, session), [candles, period, session]);
  const ids = displayedIndicators(selection);
  const idKey = ids.join(',');
  const indicators = useMemo(() => ids.map(id => {
    const result = calculateIndicator(id, plot.source);
    return { ...result, lines: result.lines.map(line => ({ ...line, values: projectValues(plot, line.values) })) };
  }), [plot, idKey]);
  const mainLines = useMemo(() => overlays(plot.source, session ? 'none' : overlay).map(line => ({ ...line, values: projectValues(plot, line.values) })), [plot, overlay, !!session]);
  const plotted = useMemo(() => !session && chartStyle === 'heikin' ? heikinAshi(plot.source) : plot.source, [plot, chartStyle, !!session]);
  const layout = chartLayout(indicators.length, compact);
  const active = point === null ? plot.latestIndex : readoutIndex(point, plot.dates) ?? plot.latestIndex;
  const latest = active >= 0 ? plot.display[active] : null;
  const shown = !session && chartStyle === 'heikin' ? plotted[active] : latest;
  const activeDate = active >= 0 ? plot.dates[active] : session?.day ?? '暂无数据';
  const palette = theme === 'dark'
    ? ['#e8c681', '#7b9cf3', '#b488dd', '#48b8ad', '#e29d68', '#d7dce5']
    : ['#926718', '#416ab8', '#8b4fac', '#168c7d', '#ae5c28', '#526276'];
  const eventData = useRef({ dates: plot.dates, onEvent });
  eventData.current = { dates: plot.dates, onEvent };

  useEffect(() => {
    if (!canvas.current) return;
    const chart = echarts.init(canvas.current);
    instance.current = chart;
    const observer = new ResizeObserver(() => {
      setCompact((canvas.current?.clientWidth ?? 800) < 560);
      chart.resize();
    });
    observer.observe(canvas.current);
    chart.on('updateAxisPointer', event => {
      const axes = (event as unknown as { axesInfo?: { axisDim: string; value: unknown }[] }).axesInfo;
      const value = axes?.find(axis => axis.axisDim === 'x')?.value;
      const index = readoutIndex(value, eventData.current.dates);
      if (index !== null) setPoint(eventData.current.dates[index]);
    });
    chart.on('click', params => {
      if (params.componentType === 'markPoint') {
        const data = params.data as { newsId?: string };
        if (data.newsId) eventData.current.onEvent?.(data.newsId);
      }
    });
    chart.getZr().on('globalout', () => setPoint(null));
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; };
  }, []);

  useEffect(() => { setPoint(null); }, [plot]);
  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const styles = getComputedStyle(document.documentElement);
    const color = (name: string) => styles.getPropertyValue(`--chart-${name}`).trim();
    const { dates, display } = plot;
    const intraday = !!session;
    const isLine = intraday || chartStyle === 'line' || chartStyle === 'area';
    const markers = (intraday || period.endsWith('分钟') ? [] : news).flatMap(article => {
      const index = period === '周 K'
        ? plot.source.findIndex((row, i) => row.date <= article.time && (i === plot.source.length - 1 ? candles.at(-1)!.date >= article.time : plot.source[i + 1].date > article.time))
        : dates.findIndex(date => date >= article.time);
      const bar = plotted[index];
      if (!bar) return [];
      return [{ name: article.title, newsId: article.id, coord: [dates[index], (isLine ? bar.close : bar.high) * 1.007], itemStyle: { color: article.score == null ? color('text') : article.score > 0 ? color('up') : article.score < 0 ? color('down') : color('marker-neutral') } }];
    });
    const main: Series = isLine ? {
      id: `main-${intraday ? 'intraday' : chartStyle}`, type: 'line', name: intraday ? '分钟价格' : '收盘价',
      data: display.map(row => row?.close ?? null), connectNulls: false, showSymbol: false,
      lineStyle: { color: color('marker-neutral'), width: 1.6 }, itemStyle: { color: color('marker-neutral') },
      ...(intraday || chartStyle === 'area' ? { areaStyle: { color: color('marker-neutral'), opacity: 0.09 } } : {}),
      ...(intraday && session.previousClose ? { markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: color('text'), type: 'dashed', opacity: 0.6 }, data: [{ yAxis: session.previousClose }] } } : {}),
      markPoint: { symbol: 'circle', symbolSize: 18, label: { formatter: 'N', color: '#fff', fontSize: 8 }, data: markers },
    } : {
      id: `main-${intraday ? 'intraday' : chartStyle}`, type: 'candlestick', name: chartStyle === 'heikin' ? '平滑 K 线（计算价格）' : period,
      data: plotted.map(row => [row.open, row.close, row.low, row.high]),
      itemStyle: { color: chartStyle === 'hollow' ? 'transparent' : color('up'), color0: chartStyle === 'hollow' ? 'transparent' : color('down'), borderColor: color('up'), borderColor0: color('down') },
      markPoint: { symbol: 'circle', symbolSize: 18, label: { formatter: 'N', color: '#fff', fontSize: 8 }, data: markers },
    };
    const series: Series[] = [main, ...mainLines.map(line => ({
      id: `overlay-${line.key}`, name: line.label, type: 'line' as const, data: line.values, showSymbol: false, connectNulls: false,
      lineStyle: { color: palette[line.colorIndex], width: 1.2, type: overlay === 'BOLL' && line.colorIndex > 0 ? 'dashed' as const : 'solid' as const }, itemStyle: { color: palette[line.colorIndex] },
    }))];
    indicators.forEach((indicator, pane) => indicator.lines.forEach(line => {
      const index = pane + 1;
      const shade = palette[line.colorIndex % palette.length];
      series.push(line.kind === 'bar' ? {
        id: `${indicator.id}-${line.key}`, name: line.label, type: 'bar', xAxisIndex: index, yAxisIndex: index,
        barMaxWidth: intraday ? 5 : 10,
        data: line.values.map((value, i) => ({ value, itemStyle: { color: (indicator.id === 'MACD' ? (value ?? 0) >= 0 : (display[i]?.close ?? 0) >= (display[i]?.open ?? 0)) ? color('volume-up') : color('volume-down'), opacity: 0.75 } })),
      } : {
        id: `${indicator.id}-${line.key}`, name: line.label, type: 'line', xAxisIndex: index, yAxisIndex: index,
        data: line.values, showSymbol: false, connectNulls: false, lineStyle: { width: 1.25, color: shade }, itemStyle: { color: shade },
      });
    }));
    const grids = [{ id: 'plot-0', left: 58, right: intraday ? 56 : 22, top: layout.priceTop, height: layout.priceHeight }, ...layout.panes.map((pane, index) => ({ id: `plot-${index + 1}`, left: 58, right: intraday ? 56 : 22, top: pane.gridTop, height: pane.height }))];
    const xAxis = grids.map((_, index) => ({
      id: `time-${index}`, type: 'category' as const, gridIndex: index, data: dates, boundaryGap: !intraday,
      axisLine: { lineStyle: { color: color('border') } }, axisTick: { show: false }, splitLine: { show: false },
      axisLabel: { show: index === grids.length - 1, color: color('text'), fontSize: 10, hideOverlap: true,
        ...(intraday ? { interval: (index: number) => [0, 30, 60, 90, 120, 151, 181, 211, 241].includes(index) } : {}),
        formatter: (value: string) => intraday ? value.slice(11, 16) === '11:30' ? '11:30/13:00' : value.slice(11, 16) : period.endsWith('分钟') ? value.slice(5, 16).replace(' ', '\n') : value.slice(5) },
      axisPointer: { label: { show: index === grids.length - 1, formatter: (params: { value: unknown }) => {
        const i = readoutIndex(params.value, dates); return i === null ? '' : intraday ? dates[i].slice(11, 16) : dates[i];
      } } },
    }));
    const reference = session?.previousClose;
    const center = reference ?? session?.quote.price ?? 1;
    const span = Math.max(center * .003, ...display.flatMap(row => row ? [Math.abs(row.close - center)] : [])) * 1.12;
    const minimum = Math.max(.0001, center - span), maximum = center + span;
    const yAxis = [{
      id: 'y-axis-0', type: 'value' as const, scale: true, gridIndex: 0, splitNumber: 4,
      ...(intraday ? { min: minimum, max: maximum } : {}),
      axisLabel: { color: color('text'), fontSize: 10, formatter: (value: number) => value.toFixed(value > 100 && !intraday ? 0 : 2) },
      axisLine: { show: false }, splitLine: { lineStyle: { color: color('grid'), type: 'dashed' as const } },
    }, ...indicators.map((indicator, index) => ({
      id: `y-axis-${index + 1}`, type: 'value' as const, gridIndex: index + 1, scale: true, splitNumber: 2,
      ...(indicator.range ? { min: indicator.range[0], max: indicator.range[1] } : {}),
      ...(indicator.id === 'VOL' ? { min: 0 } : {}),
      axisLabel: { color: color('text'), fontSize: 9, formatter: (value: number) => indicator.unit === 'volume' ? Math.abs(value) >= 10000 ? `${(value / 10000).toFixed(1)}万` : value.toFixed(0) : value.toFixed(1) },
      axisLine: { show: false }, splitLine: { lineStyle: { color: color('grid'), opacity: .5, type: 'dashed' as const } },
    }))];
    const yWithPercent: unknown[] = [...yAxis];
    if (intraday && reference) yWithPercent.push({ id: `y-axis-${yAxis.length}`, type: 'value', gridIndex: 0, position: 'right', min: (minimum / reference - 1) * 100, max: (maximum / reference - 1) * 100, splitNumber: 4, axisLabel: { color: color('text'), fontSize: 10, formatter: (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%` }, axisLine: { show: false }, splitLine: { show: false } });
    const lastDay = dates.at(-1)?.slice(0, 10);
    const cutoff = new Date((lastDay && /^\d{4}-\d{2}-\d{2}$/.test(lastDay) ? lastDay : '2026-01-01') + 'T00:00:00Z');
    cutoff.setUTCMonth(cutoff.getUTCMonth() - (mode === '近 1 月' ? 1 : 3));
    const days = [...new Set(dates.map(date => date.slice(0, 10)))];
    const from = period.endsWith('分钟') ? mode === '当日' ? days.at(-1)! : days.slice(-5)[0] : cutoff.toISOString().slice(0, 10);
    const view = `${period}:${mode}`;
    const resetZoom = lastView.current !== view;
    lastView.current = view;
    const first = mode === '全部' ? 0 : Math.max(0, dates.findIndex(date => date >= from));
    chart.setOption({
      backgroundColor: 'transparent', animation: false, textStyle: { fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif' },
      tooltip: { trigger: 'axis', showContent: false, axisPointer: { type: 'cross', label: { backgroundColor: color('crosshair'), precision: 2 } } },
      axisPointer: { link: [{ xAxisIndex: 'all' }] }, grid: grids, xAxis, yAxis: yWithPercent,
      dataZoom: intraday ? [] : [{ id: 'timeline-zoom', type: 'inside', xAxisIndex: grids.map((_, i) => i),
        ...(resetZoom ? { startValue: first, endValue: dates.length - 1 } : {}), zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false, preventDefaultMouseMove: false }],
      series,
    }, { replaceMerge: ['series', 'grid', 'xAxis', 'yAxis', 'dataZoom'] });
  }, [plot, plotted, mainLines, indicators, layout.height, compact, mode, period, chartStyle, overlay, theme, news]);

  function values(lines: IndicatorLine[], unit: IndicatorResult['unit']) {
    return lines.map(line => <span className="chart-readout-value" key={line.key} style={{ color: palette[line.colorIndex % palette.length] }}>
      <i aria-hidden="true" /><span>{line.label}<small>{line.description}</small></span><b className="mono">{formatIndicatorValue(line.values[active], unit, volumeUnit)}</b>
    </span>);
  }
  const empty = !latest;
  return <div className="technical-chart" style={{ height: layout.height }}>
    <div ref={canvas} className="technical-chart-canvas" role="img" aria-label={`${period}与${indicators.map(item => item.label).join('、')}，时间轴和光标联动；${session ? '完整交易日横轴，缺失分钟留空' : 'Ctrl 加滚轮缩放'}`} />
    <div className="chart-readout chart-main-readout" data-testid="chart-main-readout" style={{ top: 0 }}>
      <div className="chart-readout-heading"><strong>{session ? '分时' : chartStyle === 'heikin' ? '平滑 K 线 · 计算价格' : period}</strong><time>{activeDate}{point === null ? ' · 最新' : ''}</time></div>
      <div className="chart-readout-values">
        {shown && <span className="chart-ohlc"><span>开 {shown.open.toFixed(2)}</span><span>收 {shown.close.toFixed(2)}</span><span>高 {shown.high.toFixed(2)}</span><span>低 {shown.low.toFixed(2)}</span></span>}
        {values(mainLines, 'price')}{empty && <span className="chart-data-gap">此时点暂无行情</span>}
      </div>
    </div>
    {indicators.map((indicator, index) => <div key={indicator.id} className="chart-readout chart-indicator-readout" data-indicator-pane={indicator.id} style={{ top: layout.panes[index].headerTop }}>
      <div className="chart-readout-heading">{index === 0 ? <>{selection.mode === 'multiple' && <strong>{indicator.id === 'VOL' ? '成交量' : indicator.id}</strong>}<IndicatorPicker selection={selection} onChange={onSelectionChange} /></> : <strong>{indicator.id === 'VOL' ? '成交量' : indicator.id}</strong>}
        <span className="chart-indicator-parameters">{indicator.parameters}</span><time>{activeDate}</time></div>
      <div className="chart-readout-values">{values(indicator.lines, indicator.unit)}{indicator.lines.every(line => line.values[active] == null) && <span className="chart-data-gap">{empty ? '此时点无数据' : '历史数据不足'}</span>}</div>
    </div>)}
  </div>;
}
