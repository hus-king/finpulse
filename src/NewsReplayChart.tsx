import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useTheme } from './ThemeContext';
import { percent } from './format';
import type { ReplayPoint } from './newsReplay';

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

export default function NewsReplayChart({ points, name }: { points: ReplayPoint[]; name: string }) {
  const root = useRef<HTMLDivElement>(null);
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null);
  const { theme } = useTheme();
  useEffect(() => {
    if (!root.current) return;
    const chart = echarts.init(root.current);
    instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; };
  }, []);
  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const css = getComputedStyle(document.documentElement);
    const color = (name: string) => css.getPropertyValue(`--chart-${name}`).trim();
    const latest = points.filter(point => point.stock != null).at(-1)?.stock ?? 0;
    const stockColor = color(latest < 0 ? 'down' : 'up');
    chart.setOption({ animation: false, backgroundColor: 'transparent',
      grid: { left: 53, right: 18, top: 22, bottom: 46 },
      tooltip: { trigger: 'axis', renderMode: 'richText', backgroundColor: color('tooltip-bg'), borderColor: color('border'),
        textStyle: { color: color('tooltip-text'), fontSize: 11 },
        formatter: (params: unknown) => {
          const index = (params as { dataIndex: number }[])[0]?.dataIndex;
          const point = points[index];
          return point ? `${point.date} · ${point.day ? `D+${point.day}` : 'D0 基准'}\n${name}  ${percent(point.stock)}\n沪深300  ${percent(point.benchmark)}` : '';
        } },
      xAxis: { type: 'category', data: points.map(point => point.date), boundaryGap: false,
        axisTick: { show: false }, axisLine: { lineStyle: { color: color('border') } },
        axisLabel: { color: color('text'), fontSize: 10, lineHeight: 16, hideOverlap: true,
          formatter: (_: string, index: number) => `${index ? `D+${index}` : 'D0'}\n${points[index].date.slice(5)}` } },
      yAxis: { type: 'value', scale: true, axisLabel: { color: color('text'), fontSize: 10, formatter: (value: number) => `${value > 0 ? '+' : ''}${Number(value.toFixed(1))}%` },
        splitLine: { lineStyle: { color: color('grid'), type: 'dashed' } } },
      series: [
        { name, type: 'line', data: points.map(point => point.stock), connectNulls: false, symbol: 'circle', symbolSize: 5,
          lineStyle: { width: 2, color: stockColor }, itemStyle: { color: stockColor },
          markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: color('crosshair'), width: 1, type: 'dashed', opacity: .65 }, data: [{ yAxis: 0 }] } },
        { name: '沪深300', type: 'line', data: points.map(point => point.benchmark), connectNulls: false, symbol: 'circle', symbolSize: 4,
          lineStyle: { width: 1.5, color: color('marker-neutral') }, itemStyle: { color: color('marker-neutral') } },
      ],
    }, { notMerge: true });
  }, [points, name, theme]);
  return <div ref={root} className="news-replay-chart" role="img" aria-label={`${name}与沪深300在同一基准收盘后${Math.max(0, points.length - 1)}个交易日的累计涨跌幅，缺失价格留空`} />;
}
