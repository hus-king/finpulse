import { useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { api } from './api';
import { readPaperSnapshot, writePaperSnapshot } from './paperSnapshots';
import { useTheme } from './ThemeContext';
import './paper-performance.css';

echarts.use([LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

type Metric = 'return' | 'profit';
interface Returns {
  strategy_profit_fen: number; strategy_return_percent: number;
  benchmark_profit_fen: number; benchmark_return_percent: number;
  excess_profit_fen: number; excess_return_percent: number;
}
interface PerformancePoint extends Returns { date: string; trading_date: string; phase: 'open' | 'close' }
interface Performance {
  status: 'ready' | 'loading' | 'waiting' | 'empty'; reason?: string | null;
  benchmark: { code: string; name: string }; benchmark_options: { code: string; name: string }[];
  initial_cash_fen: number | null; start_date: string | null; as_of: string | null;
  summary: Returns | null; points: PerformancePoint[];
  provenance?: { replay_note?: string | null };
}

const indices = [
  ['sh000300', '沪深300'], ['sh000001', '上证指数'], ['sz399001', '深证成指'],
  ['sz399006', '创业板指'], ['sh000688', '科创50'], ['sh000905', '中证500'],
  ['sh000852', '中证1000'], ['sh000016', '上证50'],
].map(([code, name]) => ({ code, name }));
const money = (fen: number) => `${fen > 0 ? '+' : fen < 0 ? '−' : ''}¥${(Math.abs(fen) / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const percent = (value: number, suffix = '%') => `${value > 0 ? '+' : ''}${value.toFixed(2)}${suffix}`;
const tone = (value: number) => value > 0 ? 'up' : value < 0 ? 'down' : '';

function ComparisonChart({ data, metric }: { data: Performance; metric: Metric }) {
  const root = useRef<HTMLDivElement>(null);
  const chart = useRef<ReturnType<typeof echarts.init> | null>(null);
  const { theme } = useTheme();
  useEffect(() => {
    if (!root.current) return;
    const instance = echarts.init(root.current);
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); instance.dispose(); chart.current = null; };
  }, []);
  useEffect(() => {
    if (!chart.current) return;
    const css = getComputedStyle(document.documentElement);
    const color = (name: string) => css.getPropertyValue(name).trim();
    const latest = data.summary?.strategy_profit_fen ?? 0;
    const strategyColor = color(latest < 0 ? '--down' : '--up');
    const strategyValue = (point: PerformancePoint) => metric === 'return' ? point.strategy_return_percent : point.strategy_profit_fen / 100;
    const benchmarkValue = (point: PerformancePoint) => metric === 'return' ? point.benchmark_return_percent : point.benchmark_profit_fen / 100;
    const axisValue = (value: number) => metric === 'return' ? `${Number(value.toFixed(1))}%` : Math.abs(value) >= 10_000 ? `${Number((value / 10_000).toFixed(1))}万` : value.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
    chart.current.setOption({ animation: false, backgroundColor: 'transparent',
      grid: { left: 8, right: 16, top: 40, bottom: 8, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      legend: { selectedMode: false, top: 3, left: 10, icon: 'roundRect', itemWidth: 17, itemHeight: 3, textStyle: { color: color('--chart-text'), fontSize: 12 } },
      tooltip: { trigger: 'axis', renderMode: 'richText', backgroundColor: color('--chart-tooltip-bg'), borderColor: color('--chart-border'),
        textStyle: { color: color('--chart-tooltip-text'), fontSize: 12, lineHeight: 21 },
        formatter: (params: unknown) => {
          const index = (params as { dataIndex: number }[])[0]?.dataIndex;
          const point = data.points[index];
          if (!point) return '';
          const value = (profit: number, rate: number) => metric === 'return' ? percent(rate) : money(profit);
          const excess = metric === 'return' ? `收益率差  ${percent(point.excess_return_percent, ' 个百分点')}` : `收益差额  ${money(point.excess_profit_fen)}`;
          return `${point.trading_date} ${point.phase === 'open' ? '开盘基准' : '收盘'}\n模拟盘  ${value(point.strategy_profit_fen, point.strategy_return_percent)}\n${data.benchmark.name}  ${value(point.benchmark_profit_fen, point.benchmark_return_percent)}\n${excess}`;
        } },
      xAxis: { type: 'category', boundaryGap: false, data: data.points.map(p => p.date),
        axisLine: { lineStyle: { color: color('--chart-border') } }, axisTick: { show: false },
        axisLabel: { color: color('--chart-text'), fontSize: 11, hideOverlap: true,
          formatter: (_: string, index: number) => `${data.points[index].trading_date.slice(5)}${data.points[index].phase === 'open' ? '\n开盘' : index < 2 ? '\n收盘' : ''}` } },
      yAxis: { type: 'value', scale: true, axisLabel: { color: color('--chart-text'), fontSize: 11, formatter: axisValue },
        splitLine: { lineStyle: { color: color('--chart-grid'), type: 'dashed' } } },
      series: [
        { name: '我的模拟盘', type: 'line', data: data.points.map(strategyValue), symbol: 'circle', showSymbol: data.points.length === 1, symbolSize: 6,
          lineStyle: { color: strategyColor, width: 2.6 }, itemStyle: { color: strategyColor },
          markLine: { silent: true, symbol: 'none', label: { show: false }, data: [{ yAxis: 0 }],
            lineStyle: { color: color('--chart-crosshair'), width: 1, type: 'dashed', opacity: .6 } } },
        { name: data.benchmark.name, type: 'line', data: data.points.map(benchmarkValue), symbol: 'circle', showSymbol: data.points.length === 1, symbolSize: 6,
          lineStyle: { color: '#4a8cff', width: 1.8 }, itemStyle: { color: '#4a8cff' } },
      ],
    }, { notMerge: true });
  }, [data, metric, theme]);
  return <div ref={root} className="paper-performance-chart" role="img" aria-label={`模拟盘与${data.benchmark.name}截至${data.as_of ?? ''}的累计${metric === 'return' ? '收益率' : '收益金额'}对比`} />;
}

export default function PaperPerformance({ ownerKey, revision = 0 }: { ownerKey: string; revision?: number }) {
  const [benchmark, setBenchmark] = useState('sh000300');
  const [metric, setMetric] = useState<Metric>('return');
  const [state, setState] = useState<{ owner: string; benchmark: string; revision: number; data: Performance } | null>(null);
  const [errorState, setError] = useState<{ owner: string; benchmark: string; revision: number; message: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const cacheKey = `performance:${benchmark}`;
  const saved = useMemo(() => readPaperSnapshot<Performance>(ownerKey, cacheKey)?.value ?? null, [ownerKey, cacheKey, revision]);
  const data = state?.owner === ownerKey && state.benchmark === benchmark && state.revision === revision ? state.data : saved;
  const error = errorState?.owner === ownerKey && errorState.benchmark === benchmark && errorState.revision === revision ? errorState.message : '';
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    setError(null);
    async function load() {
      setLoading(true);
      let delay = 60_000;
      try {
        const next = await api<Performance>(`/api/paper/performance?benchmark=${encodeURIComponent(benchmark)}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        if (next.status === 'loading') {
          delay = 3000;
          setState(previous => {
            const last = previous?.owner === ownerKey && previous.benchmark === benchmark && previous.revision === revision ? previous.data : saved;
            return { owner: ownerKey, benchmark, revision, data: last?.points.length ? { ...last, reason: next.reason } : next };
          });
        } else {
          writePaperSnapshot(ownerKey, cacheKey, next);
          setState({ owner: ownerKey, benchmark, revision, data: next });
        }
        setError(null);
      } catch (reason) { if (!controller.signal.aborted) setError({ owner: ownerKey, benchmark, revision, message: (reason as Error).message }); }
      finally {
        if (!controller.signal.aborted) { setLoading(false); timer = window.setTimeout(() => { void load(); }, delay); }
      }
    }
    if (ownerKey) void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [ownerKey, benchmark, cacheKey, saved, revision, refresh]);
  const summary = data?.summary;
  const metrics = summary ? [
    { label: '模拟盘收益', amount: summary.strategy_profit_fen, rate: summary.strategy_return_percent },
    { label: `${data.benchmark.name}等额持有`, amount: summary.benchmark_profit_fen, rate: summary.benchmark_return_percent },
    { label: metric === 'return' ? '超额收益率' : '收益差额', amount: summary.excess_profit_fen, rate: summary.excess_return_percent, excess: true },
  ] : [];
  return <section className="panel paper-performance" aria-labelledby="paper-performance-title">
    <div className="paper-performance-heading"><h2 id="paper-performance-title">收益与收益率对比</h2>
      <div className="paper-performance-controls"><label><span>对比指数</span><select aria-label="对比指数" value={benchmark} onChange={event => setBenchmark(event.target.value)}>{(data?.benchmark_options ?? indices).map(item => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
        <div className="paper-performance-switch" role="group" aria-label="收益显示方式"><button type="button" aria-pressed={metric === 'return'} onClick={() => setMetric('return')}>收益率</button><button type="button" aria-pressed={metric === 'profit'} onClick={() => setMetric('profit')}>收益金额</button></div>
        <button type="button" className="paper-performance-refresh" aria-label="刷新收益对比" title="刷新收益对比" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} className={loading ? 'spin' : ''} /></button>
      </div></div>
    {summary && <div className="paper-performance-metrics">{metrics.map(item => <div key={item.label}>
      <span>{item.label}{metric === 'return' && item.excess && '（百分点）'}</span>
      <strong className={tone(item.amount)}>{metric === 'profit' ? money(item.amount) : percent(item.rate, item.excess ? '' : '%')}</strong>
    </div>)}</div>}
    {data?.points.length ? <ComparisonChart data={data} metric={metric} /> : <p className="paper-performance-empty" role="status">{error || data?.reason || '正在读取收益对比…'}</p>}
    {error && data?.points.length ? <p className="paper-performance-notice" role="status">{error}</p> : null}
    {data?.points.length ? <div className="paper-performance-footer"><span>{data.start_date} 开始 · 对比至 {data.as_of} 收盘</span><span>初始资金 ¥{((data.initial_cash_fen ?? 0) / 100).toLocaleString('zh-CN')}</span></div> : null}
    {data?.reason && data.points.length ? <p className="paper-performance-notice" role="status">{data.reason}</p> : null}
    {!!data?.points.length && <p className="paper-performance-basis">以相同本金和起点比较收盘收益。模拟盘已扣交易费用，指数不含股息再投资和交易费用。</p>}
    {data?.provenance?.replay_note && <p className="paper-performance-basis">{data.provenance.replay_note}</p>}
  </section>;
}
