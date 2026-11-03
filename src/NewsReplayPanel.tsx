import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, ExternalLink, Info, LoaderCircle, RefreshCw } from 'lucide-react';
import { api } from './api';
import { isScored } from './analysisPresentation';
import { number, percent, tone } from './format';
import { analysisTiming, buildReplayEvents } from './newsReplay';
import type { ReplayContext, ReplayEvent, ReplayHorizon } from './newsReplay';
import type { Dashboard, News } from './types';
import NewsReplayChart from './NewsReplayChart';
import './news-replay.css';

type Filter = 'ready' | 'observing' | 'attention';
const filters: { key: Filter; label: string }[] = [{ key: 'ready', label: '可复盘' }, { key: 'observing', label: '观察中' }, { key: 'attention', label: '待补 / 核验' }];
const matches = (event: ReplayEvent, filter: Filter) => filter === 'attention' ? event.status === 'missing' || event.status === 'verify' : event.status === filter;
const timestamp = (value?: string) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未记录研判时间';

export default function NewsReplayPanel({ data, openAssistant }: { data: Dashboard; openAssistant: (news: News) => void }) {
  const [context, setContext] = useState<ReplayContext | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [horizon, setHorizon] = useState<ReplayHorizon>(3);
  const [filter, setFilter] = useState<Filter | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    let stopped = false, timer: number | undefined, request: AbortController | undefined;
    const poll = async () => {
      if (stopped || document.hidden) return;
      request = new AbortController();
      const current = request;
      setLoading(true);
      let delay = 60;
      try {
        const next = await api<ReplayContext>('/api/market/replay-context', undefined, current.signal);
        if (stopped || current.signal.aborted) return;
        setContext(next); setError('');
        if (next.benchmark.refreshing) delay = 3;
      } catch (error) {
        if (!stopped && !current.signal.aborted) setError((error as Error).message);
      } finally {
        if (!stopped && !current.signal.aborted) {
          setLoading(false);
          timer = window.setTimeout(() => { void poll(); }, delay * 1000);
        }
      }
    };
    const visibility = () => { window.clearTimeout(timer); request?.abort(); if (!document.hidden) void poll(); else setLoading(false); };
    document.addEventListener('visibilitychange', visibility);
    void poll();
    return () => { stopped = true; request?.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [revision]);
  const events = useMemo(() => context ? buildReplayEvents(data, context, horizon) : [], [data, context, horizon]);
  const activeFilter = filter ?? filters.find(item => events.some(event => matches(event, item.key)))?.key ?? 'ready';
  const visible = events.filter(event => matches(event, activeFilter));
  const selected = visible.find(event => event.id === selectedId) ?? visible[0];
  const updatingScores = data.pipeline?.score_display === 'updating';
  const scored = (news: News) => !updatingScores && isScored(news);
  const finalPoint = selected?.points[Math.min(selected.elapsedDays, horizon)];
  const currentReturn = selected?.status === 'ready' ? selected.stockReturn : selected?.status === 'observing' ? selected.latestReturn : null;
  const benchmarkReturn = selected?.status === 'ready' ? selected.benchmarkReturn : selected?.status === 'observing' ? finalPoint?.benchmark ?? null : null;
  const excessReturn = currentReturn != null && benchmarkReturn != null ? currentReturn - benchmarkReturn : null;
  const hasPrices = selected?.points.some(point => point.stock != null || point.benchmark != null);
  const plottedReturn = selected?.points.filter(point => point.stock != null).at(-1)?.stock ?? 0;
  const missingBenchmark = selected?.points.some(point => context?.completed_through && point.date <= context.completed_through && point.benchmark == null);
  const timing = selected ? analysisTiming(selected.news, selected.baseDate) : 'unknown';

  return <section className="panel returns-panel news-replay" aria-labelledby="news-replay-title">
    <header className="news-replay-heading"><div><h2 id="news-replay-title">消息影响复盘</h2><p>把消息放回走势，观察个股与大盘的同期表现</p></div><span>事后观察</span></header>
    <div className="news-replay-toolbar">
      <div className="news-replay-filters" role="group" aria-label="复盘状态">{filters.map(item => <button type="button" key={item.key} aria-pressed={activeFilter === item.key} onClick={() => { setFilter(item.key); setSelectedId(null); }}>{item.label}<span>{events.filter(event => matches(event, item.key)).length}</span></button>)}</div>
      <div className="news-replay-horizons" role="group" aria-label="复盘观察期">{([1, 3, 5] as const).map(days => <button type="button" key={days} aria-pressed={days === horizon} onClick={() => setHorizon(days)}>{days} 日</button>)}</div>
    </div>
    {error && <div className="news-replay-notice" role="alert"><span>{context ? '复盘行情更新失败，暂展示上次数据。' : error}</span><button disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={12} />重试</button></div>}
    {!context ? <div className="news-replay-empty" role="status">{loading && <LoaderCircle size={18} className="spin" />}<p>{error ? '交易日历与对照行情暂不可用' : '正在读取交易日历与沪深300行情…'}</p></div> : !events.length ? <div className="news-replay-empty"><Info size={21} /><p>采集公司或行业新闻后，这里会自动开始跟踪后续走势。</p></div> : <div className="news-replay-workspace">
      <div className="news-replay-events" role="group" aria-label="复盘事件列表">
        <div className="news-replay-list-heading"><span>事件 · {visible.length}</span><span>AI 分 / {horizon} 日表现</span></div>
        <div className="news-replay-event-scroll">{visible.length ? visible.map(event => <button type="button" className="news-replay-event" key={event.id} aria-pressed={selected?.id === event.id} onClick={() => setSelectedId(event.id)}>
          <span className="news-replay-event-meta"><time>{event.news.time || '日期待核验'}</time><span>{event.news.news_scope === 'industry' ? '行业' : '公司'}{event.members.length > 1 ? ` · 合并 ${event.members.length} 条` : ''}</span></span>
          <strong title={event.news.title}>{event.news.title}</strong>
          <span className="news-replay-event-bottom"><span className={scored(event.news) ? tone(event.news.score) : ''}>{scored(event.news) ? `AI ${(event.news.score ?? 0) > 0 ? '+' : ''}${event.news.score}` : '待研判'}</span><span className={tone(event.stockReturn)}>{event.status === 'ready' ? percent(event.stockReturn) : event.status === 'observing' ? event.basePrice != null ? `观察 ${event.elapsedDays}/${horizon} 日` : '待基准收盘' : event.status === 'verify' ? '待核验' : '收盘价待补'}</span></span>
        </button>) : <div className="news-replay-list-empty">{activeFilter === 'ready' ? `还没有走完 ${horizon} 个交易日的事件。` : activeFilter === 'observing' ? '当前没有正在观察的事件。' : '当前没有待补或待核验的事件。'}</div>}</div>
      </div>
      <div className="news-replay-detail" aria-label="所选事件复盘详情" aria-live="polite">
        {selected ? <>
          <div className="news-replay-detail-heading"><span>{selected.news.time} · {selected.news.source}</span><h3>{selected.news.title}</h3><p className={`news-replay-progress ${selected.status}`}>{selected.reason}</p></div>
          <div className="news-replay-metrics"><div><span>{selected.status === 'observing' ? `已观察 D+${selected.elapsedDays}` : `${horizon} 日个股涨跌`}</span><strong className={tone(currentReturn)}>{percent(currentReturn)}</strong></div><div><span>沪深300</span><strong className={tone(benchmarkReturn)}>{percent(benchmarkReturn)}</strong></div><div><span>相对大盘</span><strong className={tone(excessReturn)}>{excessReturn == null ? '—' : `${excessReturn > 0 ? '+' : ''}${excessReturn.toFixed(2)}`}<small>{excessReturn == null ? '' : '百分点'}</small></strong></div></div>
          <div className="news-replay-plot">
            {hasPrices ? <NewsReplayChart points={selected.points} name={data.stock.name} /> : <div className="news-replay-empty"><Info size={21} /><p>{selected.status === 'verify' ? '核验通过后再展示价格观察' : selected.reason}</p><small>收盘价未取得时，走势图保持留空</small></div>}
          </div>
          <div className="news-replay-legend"><span><i style={{ background: plottedReturn < 0 ? 'var(--down)' : 'var(--up)' }} />{data.stock.name}</span><span><i className="benchmark" />沪深300</span><small>D0 同日收盘归零</small></div>
          {missingBenchmark && <p className="news-replay-data-note">{context.benchmark.refreshing ? '沪深300日线正在补齐，缺失处暂留空。' : '沪深300部分收盘数据待补，缺失处留空。'}</p>}
          {!missingBenchmark && context.benchmark.error && <p className="news-replay-data-note">沪深300更新失败，沿用已保存的收盘数据。</p>}
          <dl className="news-replay-facts"><div><dt>基准收盘</dt><dd>{selected.baseDate ?? '待核验'}<span>{selected.basePrice != null ? `¥ ${number(selected.basePrice)}` : ''}</span></dd></div><div><dt>日期依据</dt><dd>{selected.news.date_status === 'body_verified' ? '正文日期已核验' : selected.news.date_status === 'metadata_only' ? '来源标注日期' : '日期待核验'}</dd></div></dl>
          <div className="news-replay-assessment"><div><span className={scored(selected.news) ? tone(selected.news.score) : ''}>{scored(selected.news) ? `AI ${(selected.news.score ?? 0) > 0 ? '+' : ''}${selected.news.score} 分` : updatingScores ? '研判更新中' : '待研判'}</span>{scored(selected.news) && <small>{timestamp(selected.news.analyzed_at)} · {timing === 'after' ? '事后研判' : timing === 'before' ? '基准收盘前研判' : '时点待核验'}</small>}</div><p>{scored(selected.news) ? selected.news.analysis?.summary || '查看已保存的研判依据。' : '尚无可展示的已保存研判，可在研究助手中分析这条消息。'}</p>{scored(selected.news) && timing === 'after' && <small className="news-replay-timing-note">研判晚于基准收盘，不能作为事前预测记录。</small>}</div>
          <div className="news-replay-actions"><a href={selected.news.url} target="_blank" rel="noopener noreferrer">新闻原文<ExternalLink size={12} /></a><button type="button" onClick={() => openAssistant(selected.news)}>查看研判依据<ArrowRight size={13} /></button>{selected.members.length > 1 && <details><summary>{selected.members.length} 条合并报道</summary>{selected.members.map(news => <a key={news.id} href={news.url} target="_blank" rel="noopener noreferrer">{news.source} · {news.title}</a>)}</details>}</div>
        </> : <div className="news-replay-empty"><Info size={21} /><p>切换复盘状态，查看其他事件。</p></div>}
      </div>
    </div>}
    <footer className="news-replay-footnote">以发布日后的首个交易日收盘为 D0，观察后续 1 / 3 / 5 个交易日。未复权、不含费用；同期表现不代表新闻造成的涨跌。{context && events.length < data.news.length && <span>同日同链接或同标题的重复报道已合并。</span>}</footer>
  </section>;
}
