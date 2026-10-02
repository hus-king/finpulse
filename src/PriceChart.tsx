import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { Candle, News } from './types';

echarts.use([CandlestickChart, LineChart, BarChart, GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent, CanvasRenderer]);

const average = (items: Candle[], period: number) => items.map((_, i) => i < period - 1 ? null : +(items.slice(i - period + 1, i + 1).reduce((sum, row) => sum + row.close, 0) / period).toFixed(2));

function ema(values: number[], period: number) {
  const result: number[] = [];
  const alpha = 2 / (period + 1);
  values.forEach((value, i) => result.push(i === 0 ? value : alpha * value + (1 - alpha) * result[i - 1]));
  return result;
}

function weekly(candles: Candle[]) {
  const groups = new Map<string, Candle>();
  for (const candle of candles) {
    const day = new Date(candle.date + 'T12:00:00Z');
    day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
    const key = day.toISOString().slice(0, 10);
    const previous = groups.get(key);
    groups.set(key, previous ? { ...previous, close: candle.close, high: Math.max(previous.high, candle.high), low: Math.min(previous.low, candle.low), volume: previous.volume + candle.volume } : { ...candle, date: key });
  }
  return [...groups.values()];
}

function rsi(values: number[], period = 14) {
  let gain = 0, loss = 0;
  return values.map((value, index) => {
    if (index === 0) return null;
    const diff = value - values[index - 1];
    if (index <= period) { gain += Math.max(diff, 0) / period; loss += Math.max(-diff, 0) / period; }
    else { gain = (gain * (period - 1) + Math.max(diff, 0)) / period; loss = (loss * (period - 1) + Math.max(-diff, 0)) / period; }
    return index < period ? null : loss === 0 ? gain === 0 ? 50 : 100 : 100 - 100 / (1 + gain / loss);
  });
}

export default function PriceChart({ candles: daily, news, mode, period, indicator, onEvent }: { candles: Candle[]; news: News[]; mode: string; period: string; indicator: string; onEvent: (id: string) => void }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!root.current) return;
    const candles = period === '周 K' ? weekly(daily) : daily;
    if (!candles.length) return;
    const chart = echarts.init(root.current);
    const close = candles.map(row => row.close);
    const fast = ema(close, 12), slow = ema(close, 26);
    const dif = close.map((_, i) => fast[i] - slow[i]);
    const dea = ema(dif, 9);
    const macd = dif.map((value, i) => (value - dea[i]) * 2);
    const rows = candles.map(row => [row.open, row.close, row.low, row.high]);
    const dates = candles.map(row => row.date);
    const markers = news.flatMap(article => {
      let index = candles.findIndex(row => row.date >= article.time);
      if (period === '周 K') index = candles.findIndex((row, i) => row.date <= article.time && (i === candles.length - 1 ? daily.at(-1)!.date >= article.time : candles[i + 1].date > article.time));
      if (index < 0) return [];
      return [{ name: article.title, newsId: article.id, coord: [dates[index], candles[index].high * 1.007], itemStyle: { color: article.score == null ? '#8896aa' : article.score > 0 ? '#d64a61' : article.score < 0 ? '#188475' : '#6575c5' } }];
    });
    const cutoff = new Date(candles.at(-1)!.date + 'T00:00:00Z');
    cutoff.setUTCMonth(cutoff.getUTCMonth() - (mode === '近 1 月' ? 1 : 3));
    const firstVisible = mode === '全部' ? 0 : Math.max(0, candles.findIndex(row => row.date >= cutoff.toISOString().slice(0, 10)));
    chart.setOption({
      backgroundColor: 'transparent', animation: false,
      textStyle: { fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif' },
      tooltip: { trigger: 'axis', renderMode: 'richText', axisPointer: { type: 'cross', label: { backgroundColor: '#536276' } }, backgroundColor: '#ffffff', borderColor: '#dde3eb', textStyle: { color: '#273247', fontSize: 12 } },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [{ left: 58, right: 22, top: 22, bottom: 112 }, { left: 58, right: 22, height: 58, bottom: 30 }],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#dce4ed' } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: 'category', gridIndex: 1, data: dates, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: '#6d7c90', fontSize: 10, formatter: (value: string) => value.slice(5) }, splitLine: { show: false } },
      ],
      yAxis: [
        { scale: true, splitNumber: 4, axisLabel: { color: '#748399', fontSize: 10, formatter: (v: number) => v.toFixed(v > 100 ? 0 : 2) }, splitLine: { lineStyle: { color: '#e8edf3', type: 'dashed' } }, axisLine: { show: false } },
        { scale: indicator !== 'RSI', min: indicator === 'RSI' ? 0 : undefined, max: indicator === 'RSI' ? 100 : undefined, gridIndex: 1, splitNumber: 1, axisLabel: { color: '#6d7c90', fontSize: 9, formatter: (v: number) => indicator === 'MACD' || indicator === 'RSI' ? v.toFixed(1) : `${(v / 10000).toFixed(1)}万` }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], startValue: firstVisible, endValue: candles.length - 1, zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false, preventDefaultMouseMove: false }],
      series: [
        { name: period, type: 'candlestick', data: rows, itemStyle: { color: '#d8596d', color0: '#23a18a', borderColor: '#d8596d', borderColor0: '#23a18a' }, markPoint: { symbol: 'circle', symbolSize: 20, label: { formatter: 'N', color: '#ffffff', fontSize: 8, fontWeight: 'bold' }, data: markers } },
        { name: 'MA5', type: 'line', data: average(candles, 5), showSymbol: false, lineStyle: { width: 1.4, color: '#e8c681' } },
        { name: 'MA10', type: 'line', data: average(candles, 10), showSymbol: false, lineStyle: { width: 1.4, color: '#7b9cf3' } },
        { name: 'MA20', type: 'line', data: average(candles, 20), showSymbol: false, lineStyle: { width: 1.4, color: '#b488dd' } },
        ...(indicator === 'RSI' ? [{ name: 'RSI14', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: rsi(close), showSymbol: false, lineStyle: { width: 1.5, color: '#8a69c9' } }] : [{ name: indicator, type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: (indicator === 'MACD' ? macd : candles.map(row => row.volume)).map((value, i) => ({ value, itemStyle: { color: (indicator === 'MACD' ? value >= 0 : candles[i].close >= candles[i].open) ? '#d56676' : '#3a9b83', opacity: 0.7 } })) }]),
        ...(indicator === 'MACD' ? [{ name: 'DIF', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: dif, showSymbol: false, lineStyle: { width: 1, color: '#e8c681' } }, { name: 'DEA', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: dea, showSymbol: false, lineStyle: { width: 1, color: '#7b9cf3' } }] : []),
      ],
    });
    chart.on('click', params => { if (params.componentType === 'markPoint') { const point = params.data as { newsId?: string }; if (point.newsId) onEvent(point.newsId); } });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [daily, news, mode, period, indicator, onEvent]);
  return <div className="price-chart" ref={root} role="img" aria-label="真实历史K线、移动均线与技术指标，Ctrl 加滚轮缩放，普通滚轮滚动页面" />;
}
