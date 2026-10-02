import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { Candle } from './types';

echarts.use([CandlestickChart, LineChart, BarChart, GridComponent, TooltipComponent, DataZoomComponent, MarkPointComponent, CanvasRenderer]);

const average = (items: Candle[], period: number) => items.map((_, i) => i < period - 1 ? null : +(items.slice(i - period + 1, i + 1).reduce((sum, row) => sum + row.close, 0) / period).toFixed(2));

function ema(values: number[], period: number) {
  const result: number[] = [];
  const alpha = 2 / (period + 1);
  values.forEach((value, i) => result.push(i === 0 ? value : alpha * value + (1 - alpha) * result[i - 1]));
  return result;
}

export default function PriceChart({ candles, mode, indicator, onEvent }: { candles: Candle[]; mode: string; indicator: string; onEvent: () => void }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!root.current) return;
    const chart = echarts.init(root.current);
    const close = candles.map(row => row.close);
    const fast = ema(close, 12), slow = ema(close, 26);
    const dif = close.map((_, i) => fast[i] - slow[i]);
    const dea = ema(dif, 9);
    const macd = dif.map((value, i) => (value - dea[i]) * 2);
    const rows = candles.map(row => [row.open, row.close, row.low, row.high]);
    const dates = candles.map(row => row.date.slice(5));
    const eventIndex = candles.length - 18;
    chart.setOption({
      backgroundColor: 'transparent', animation: false,
      textStyle: { fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif' },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#273243' } }, backgroundColor: '#17212e', borderColor: '#344357', textStyle: { color: '#e4eaf2', fontSize: 12 } },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [{ left: 58, right: 22, top: 22, bottom: 112 }, { left: 58, right: 22, height: 58, bottom: 30 }],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#26313f' } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
        { type: 'category', gridIndex: 1, data: dates, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: '#6d7c90', fontSize: 10, interval: 13 }, splitLine: { show: false } },
      ],
      yAxis: [
        { scale: true, splitNumber: 4, axisLabel: { color: '#748399', fontSize: 10, formatter: (v: number) => v.toFixed(v > 100 ? 0 : 2) }, splitLine: { lineStyle: { color: '#1b2635', type: 'dashed' } }, axisLine: { show: false } },
        { scale: true, gridIndex: 1, splitNumber: 1, axisLabel: { color: '#6d7c90', fontSize: 9, formatter: (v: number) => indicator === 'MACD' ? v.toFixed(1) : `${(v / 1000).toFixed(1)}k` }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: mode === '近 1 月' ? 66 : mode === '近 3 月' ? 22 : 0, end: 100 }],
      series: [
        { name: '日 K', type: 'candlestick', data: rows, itemStyle: { color: '#f27783', color0: '#41caa4', borderColor: '#f27783', borderColor0: '#41caa4' }, markPoint: { symbol: 'circle', symbolSize: 20, label: { formatter: 'AI', color: '#0b1715', fontSize: 8, fontWeight: 'bold' }, itemStyle: { color: '#5ce1b9' }, data: [{ name: '示例新闻事件', coord: [eventIndex, candles[eventIndex].high * 1.004] }] } },
        { name: 'MA5', type: 'line', data: average(candles, 5), showSymbol: false, lineStyle: { width: 1.4, color: '#e8c681' } },
        { name: 'MA10', type: 'line', data: average(candles, 10), showSymbol: false, lineStyle: { width: 1.4, color: '#7b9cf3' } },
        { name: 'MA20', type: 'line', data: average(candles, 20), showSymbol: false, lineStyle: { width: 1.4, color: '#b488dd' } },
        { name: indicator, type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: (indicator === 'MACD' ? macd : candles.map(row => row.volume)).map((value, i) => ({ value, itemStyle: { color: (indicator === 'MACD' ? value >= 0 : candles[i].close >= candles[i].open) ? '#d56676' : '#3a9b83', opacity: 0.7 } })) },
        ...(indicator === 'MACD' ? [{ name: 'DIF', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: dif, showSymbol: false, lineStyle: { width: 1, color: '#e8c681' } }, { name: 'DEA', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: dea, showSymbol: false, lineStyle: { width: 1, color: '#7b9cf3' } }] : []),
      ],
    });
    chart.on('click', params => { if (params.componentType === 'markPoint') onEvent(); });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [candles, mode, indicator, onEvent]);
  return <div className="price-chart" ref={root} role="img" aria-label="示例日K线、移动均线与副图，支持鼠标滚轮缩放" />;
}
