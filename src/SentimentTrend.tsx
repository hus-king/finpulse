import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useTheme } from './ThemeContext';
import type { SentimentHistory } from './types';

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

export default function SentimentTrend({ history, error }: { history: SentimentHistory | null; error: string }) {
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
    const points = history?.points ?? [];
    const byDay = new Map(points.map(point => [point.date, point]));
    const dates = (history?.trading_dates ?? points.map(point => point.date)).filter(date => date !== history?.to_date || byDay.has(date));
    chart.setOption({ animation: false, backgroundColor: 'transparent',
      grid: { left: 30, right: 12, top: 12, bottom: 25 },
      tooltip: { trigger: 'axis', renderMode: 'richText', backgroundColor: color('tooltip-bg'), borderColor: color('border'), textStyle: { color: color('tooltip-text'), fontSize: 11 },
        formatter: (params: unknown) => {
          const item = (params as { dataIndex: number }[])[0];
          const point = item && byDay.get(dates[item.dataIndex]);
          if (!point) return '';
          const provisional = point.market_as_of.slice(11, 16) < '15:00';
          return `${point.date}${provisional ? ' · 盘中' : ' · 收盘'}\n情绪 ${point.score} / 100\n上涨 ${point.advancing.toLocaleString()}  下跌 ${point.declining.toLocaleString()}\n平盘 ${point.flat.toLocaleString()}`;
        } },
      xAxis: { type: 'category', boundaryGap: false, data: dates, axisTick: { show: false }, axisLine: { lineStyle: { color: color('border') } }, axisLabel: { color: color('text'), fontSize: 9, hideOverlap: true, formatter: (date: string) => date.slice(5) } },
      yAxis: { type: 'value', min: 0, max: 100, interval: 50, axisLabel: { color: color('text'), fontSize: 9 }, splitLine: { lineStyle: { color: color('grid'), type: 'dashed' } } },
      series: [{ type: 'line', data: dates.map(date => byDay.get(date)?.score ?? null), connectNulls: false, smooth: false, showSymbol: points.length < 3, symbolSize: 5,
        lineStyle: { width: 2, color: '#e26d80' }, itemStyle: { color: '#e26d80' }, areaStyle: { color: '#e26d80', opacity: .08 },
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: color('text'), opacity: .5, type: 'dashed' }, data: [{ yAxis: 50 }] } }],
    }, { notMerge: true });
  }, [history, theme]);
  const points = history?.points ?? [];
  const last = points.at(-1);
  return <div className="market-overview-trend">
    <div className="sentiment-trend-heading"><h3>近 30 天情绪走势</h3><span>{last ? `${last.date.slice(5)} · ${last.score} 分` : '0—100'}</span></div>
    <div className="sentiment-trend-canvas">
      <div ref={root} className="sentiment-trend-chart" role="img" aria-label={`近30天沪深全市场情绪走势，已记录${points.length}个交易日，50分为中性参考线`} />
      {!points.length && <p className="sentiment-trend-empty" role="status">{error || history?.message || (history?.refreshing || !history ? '正在读取历史情绪…' : '暂无历史记录')}</p>}
    </div>
    <p className="sentiment-trend-note" title={`历史来源：${history?.source ?? ''}。按上涨、下跌、平盘家数计算，与当前情绪使用同一公式；当日点随行情更新。`}>
      {history?.from_date.slice(5)}{history ? ` — ${history.to_date.slice(5)} · ` : ''}{points.length} 个交易日
      {history?.refreshing ? ' · 正在补齐' : history?.missing_days ? ` · ${history.missing_days} 日缺失` : ' · 50 分中性'}
      {error && points.length > 0 ? ' · 更新失败' : ''}
    </p>
  </div>;
}
