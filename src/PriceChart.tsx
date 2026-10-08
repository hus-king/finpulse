import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts';
import type { CandlestickSeriesOption, LineSeriesOption, BarSeriesOption } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { Candle, News } from './types';
import { useTheme } from './ThemeContext';
import { movingAverage, exponentialMovingAverage, bollingerBands, heikinAshi, weeklyCandles, relativeStrengthIndex } from './chartMath';
import type { ChartStyle, ChartOverlay } from './chartMath';

echarts.use([CandlestickChart, LineChart, BarChart, GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent, CanvasRenderer]);

export default function PriceChart({ candles: daily, news, mode, period, indicator, chartStyle = 'candles', overlay = 'MA', onEvent }: {
  candles: Candle[]; news: News[]; mode: string; period: string; indicator: string;
  chartStyle?: ChartStyle; overlay?: ChartOverlay; onEvent: (id: string) => void;
}) {
  const { theme } = useTheme();
  const root = useRef<HTMLDivElement>(null);
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null);
  const lastView = useRef('');
  const eventHandler = useRef(onEvent);
  eventHandler.current = onEvent;
  useEffect(() => {
    if (!root.current) return;
    const chart = echarts.init(root.current);
    instance.current = chart;
    chart.on('click', params => { if (params.componentType === 'markPoint') { const point = params.data as { newsId?: string }; if (point.newsId) eventHandler.current(point.newsId); } });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; lastView.current = ''; };
  }, []);
  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const styles = getComputedStyle(document.documentElement);
    const color = (name: string) => styles.getPropertyValue(`--chart-${name}`).trim();
    const chronological = [...daily].sort((a, b) => a.date.localeCompare(b.date));
    const candles = period === '周 K' ? weeklyCandles(chronological) : chronological;
    const minute = period.endsWith('分钟');
    if (!candles.length) { chart.clear(); lastView.current = ''; return; }
    // Indicators stay tied to traded prices; only the displayed OHLC uses Heikin Ashi.
    const plotted = chartStyle === 'heikin' ? heikinAshi(candles) : candles;
    const isLine = chartStyle === 'line' || chartStyle === 'area';
    const close = candles.map(row => row.close);
    const fast = exponentialMovingAverage(close, 12), slow = exponentialMovingAverage(close, 26);
    const dif = close.map((_, i) => fast[i] - slow[i]);
    const dea = exponentialMovingAverage(dif, 9);
    const macd = dif.map((value, i) => (value - dea[i]) * 2);
    const rows = plotted.map(row => [row.open, row.close, row.low, row.high]);
    const dates = candles.map(row => row.date);
    const markers = (minute ? [] : news).flatMap(article => {
      let index = candles.findIndex(row => row.date >= article.time);
      if (period === '周 K') index = candles.findIndex((row, i) => row.date <= article.time && (i === candles.length - 1 ? chronological.at(-1)!.date >= article.time : candles[i + 1].date > article.time));
      if (index < 0) return [];
      const markerPrice = isLine ? plotted[index].close : plotted[index].high;
      return [{ name: article.title, newsId: article.id, coord: [dates[index], markerPrice * 1.007], itemStyle: { color: article.score == null ? color('text') : article.score > 0 ? color('up') : article.score < 0 ? color('down') : color('marker-neutral') } }];
    });
    const cutoff = new Date(candles.at(-1)!.date.slice(0, 10) + 'T00:00:00Z');
    cutoff.setUTCMonth(cutoff.getUTCMonth() - (mode === '近 1 月' ? 1 : 3));
    const days = [...new Set(candles.map(row => row.date.slice(0, 10)))];
    const from = minute ? mode === '当日' ? days.at(-1)! : days.slice(-5)[0] : cutoff.toISOString().slice(0, 10);
    const firstVisible = mode === '全部' ? 0 : Math.max(0, candles.findIndex(row => row.date >= from));
    const view = `${mode}:${period}`;
    const resetZoom = lastView.current !== view;
    lastView.current = view;
    const overlayColors = theme === 'dark' ? ['#e8c681', '#7b9cf3', '#b488dd'] : ['#a27622', '#4b72c9', '#8b5bad'];
    const markPoint = { symbol: 'circle', symbolSize: 20, label: { formatter: 'N', color: '#ffffff', fontSize: 8, fontWeight: 'bold' as const }, data: markers };
    // A distinct main series id removes area fills when changing between area and line.
    const mainSeries: CandlestickSeriesOption | LineSeriesOption = isLine ? {
      id: `price-${chartStyle}`, name: '收盘价', type: 'line', data: close,
      showSymbol: false, lineStyle: { width: 1.8, color: color('marker-neutral') },
      itemStyle: { color: color('marker-neutral') },
      ...(chartStyle === 'area' ? { areaStyle: { color: color('marker-neutral'), opacity: 0.16 } } : {}),
      markPoint,
    } : {
      id: `price-${chartStyle}`, name: chartStyle === 'heikin' ? '平均 K 线（Heikin Ashi）' : chartStyle === 'hollow' ? `空心${period}` : period,
      type: 'candlestick', data: rows, large: false,
      itemStyle: {
        color: chartStyle === 'hollow' ? 'transparent' : color('up'),
        color0: chartStyle === 'hollow' ? 'transparent' : color('down'),
        borderColor: color('up'), borderColor0: color('down'),
      },
      markPoint,
    };
    const overlaySeries: LineSeriesOption[] = [];
    if (overlay === 'MA' || overlay === 'EMA') {
      [5, 10, 20].forEach((length, index) => overlaySeries.push({
        id: `overlay-${overlay}${length}`, name: `${overlay}${length}`, type: 'line',
        data: overlay === 'MA' ? movingAverage(close, length) : exponentialMovingAverage(close, length),
        showSymbol: false, lineStyle: { width: 1.4, color: overlayColors[index] }, itemStyle: { color: overlayColors[index] },
      }));
    } else if (overlay === 'BOLL') {
      const bands = bollingerBands(close);
      [
        { name: 'BOLL20 中轨', data: bands.middle },
        { name: 'BOLL20 上轨（2σ）', data: bands.upper },
        { name: 'BOLL20 下轨（2σ）', data: bands.lower },
      ].forEach((band, index) => overlaySeries.push({
        id: `overlay-BOLL${index}`, name: band.name, type: 'line', data: band.data,
        showSymbol: false, lineStyle: { width: index === 0 ? 1.4 : 1.1, type: index === 0 ? 'solid' : 'dashed', color: overlayColors[index] },
        itemStyle: { color: overlayColors[index] },
      }));
    }
    const secondarySeries: (LineSeriesOption | BarSeriesOption)[] = indicator === 'RSI' ? [{
      id: 'indicator-RSI', name: 'RSI14', type: 'line', xAxisIndex: 1, yAxisIndex: 1,
      data: relativeStrengthIndex(close), showSymbol: false,
      lineStyle: { width: 1.5, color: overlayColors[2] }, itemStyle: { color: overlayColors[2] },
    }] : [{
      id: `indicator-${indicator}`, name: indicator, type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
      data: (indicator === 'MACD' ? macd : candles.map(row => row.volume)).map((value, i) => ({
        value, itemStyle: { color: (indicator === 'MACD' ? value >= 0 : candles[i].close >= candles[i].open) ? color('volume-up') : color('volume-down'), opacity: 0.7 },
      })),
    }];
    if (indicator === 'MACD') secondarySeries.push(
      { id: 'indicator-DIF', name: 'DIF', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: dif, showSymbol: false, lineStyle: { width: 1, color: overlayColors[0] }, itemStyle: { color: overlayColors[0] } },
      { id: 'indicator-DEA', name: 'DEA', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: dea, showSymbol: false, lineStyle: { width: 1, color: overlayColors[1] }, itemStyle: { color: overlayColors[1] } },
    );
    chart.setOption({
      backgroundColor: 'transparent', animation: false,
      textStyle: { fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif' },
      tooltip: { trigger: 'axis', renderMode: 'richText', axisPointer: { type: 'cross', label: { backgroundColor: color('crosshair') } }, backgroundColor: color('tooltip-bg'), borderColor: color('border'), textStyle: { color: color('tooltip-text'), fontSize: 12 } },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [{ left: 58, right: 22, top: 22, bottom: 112 }, { left: 58, right: 22, height: 58, bottom: 30 }],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: color('border') } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: 'category', gridIndex: 1, data: dates, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: color('text'), fontSize: 10, formatter: (value: string) => minute ? value.slice(5, 16).replace(' ', '\n') : value.slice(5) }, splitLine: { show: false } },
      ],
      yAxis: [
        { id: 'price-axis', scale: true, splitNumber: 4, axisLabel: { color: color('text'), fontSize: 10, formatter: (v: number) => v.toFixed(v > 100 ? 0 : 2) }, splitLine: { lineStyle: { color: color('grid'), type: 'dashed' } }, axisLine: { show: false } },
        { id: `indicator-axis-${indicator}`, scale: indicator !== 'RSI', min: indicator === 'RSI' ? 0 : undefined, max: indicator === 'RSI' ? 100 : undefined, gridIndex: 1, splitNumber: 1, axisLabel: { color: color('text'), fontSize: 9, formatter: (v: number) => indicator === 'MACD' || indicator === 'RSI' ? v.toFixed(1) : `${(v / 10000).toFixed(1)}万` }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], ...(resetZoom ? { startValue: firstVisible, endValue: candles.length - 1 } : {}), zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false, preventDefaultMouseMove: false }],
      series: [mainSeries, ...overlaySeries, ...secondarySeries],
    }, { replaceMerge: ['series', 'yAxis'] });
  }, [daily, news, mode, period, indicator, chartStyle, overlay, theme]);
  const styleLabel = { candles: '标准 K 线', hollow: '空心 K 线', heikin: '平均 K 线（Heikin Ashi，展示转换价格）', line: '收盘折线', area: '收盘面积图' }[chartStyle];
  return <div className="price-chart" ref={root} role="img" aria-label={`${styleLabel}，${overlay === 'none' ? '无主图叠加指标' : overlay}，${indicator}；Ctrl 加滚轮缩放，普通滚轮滚动页面`} />;
}
