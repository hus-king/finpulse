import { useEffect, useRef, useState } from 'react';
import { ExternalLink, LoaderCircle, Sparkles } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import { dateTime } from './format';
import { impactDirections } from './hotNewsTypes';
import type { HotImpactPreview, HotMatchJob, HotTopic } from './hotNewsTypes';

export default function HotNewsMatchPanel({ topics, watchlist, ready, disabled, onResults, onBusy }: {
  topics: HotTopic[]; watchlist: string[]; ready: boolean; disabled: boolean;
  onResults: (rows: HotImpactPreview[]) => void; onBusy: (running: boolean) => void;
}) {
  const { user, requireAuth } = useAuth();
  const [job, setJob] = useState<HotMatchJob | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const read = useRef<AbortController | null>(null), work = useRef<AbortController | null>(null);
  const resultsRef = useRef(onResults); resultsRef.current = onResults;
  const running = job?.status === 'queued' || job?.status === 'running';
  const watchKey = watchlist.join(',');

  useEffect(() => {
    if (!user || !ready) return;
    const controller = new AbortController(); read.current = controller;
    setLoading(true);
    api<{ job: HotMatchJob | null }>('/api/hot-news/matches', undefined, controller.signal).then(result => {
      if (!controller.signal.aborted) { setJob(result.job); setError(''); if (result.job) resultsRef.current(result.job.results); }
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [user?.id, ready, watchKey, reload]);

  useEffect(() => {
    if (!running || loading || submitting) return;
    const timer = window.setTimeout(() => setReload(value => value + 1), 2000);
    return () => window.clearTimeout(timer);
  }, [running, loading, submitting, job]);
  useEffect(() => { onBusy(running); return () => onBusy(false); }, [running, onBusy]);
  useEffect(() => () => work.current?.abort(), []);

  async function start() {
    if (!requireAuth('登录后，一键匹配当前新闻与自己的自选股。')) return;
    const controller = new AbortController(); work.current = controller; read.current?.abort();
    setSubmitting(true); setLoading(false); setError('');
    try {
      const result = await api<{ job: HotMatchJob }>('/api/hot-news/match', { topic_ids: topics.map(topic => topic.id) }, controller.signal);
      if (!controller.signal.aborted) { setJob(result.job); resultsRef.current(result.job.results); }
    } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
    finally { if (!controller.signal.aborted) setSubmitting(false); }
  }
  const groups = job?.stocks.filter(stock => watchlist.includes(stock.code)) ?? [];
  const processed = (job?.counts.completed ?? 0) + (job?.counts.failed ?? 0);
  const progress = job?.counts.total ? Math.min(100, processed / job.counts.total * 100) : 0;
  return <section className="panel hot-match-panel" aria-label="一键匹配新闻与自选股" aria-busy={running || submitting}>
    <header><div><h2>热点新闻 × 我的自选股</h2><p>一键匹配当前列表的 {topics.length} 条新闻与 {watchlist.length} 只自选股，汇总可能有影响的股票及对应消息。</p></div>
      <button className="primary-button hot-match-start" disabled={disabled || running || submitting || !!user && (!ready || loading || !watchlist.length) || !topics.length} onClick={() => void start()}>
        {running || submitting ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{running || submitting ? '一键分析进行中…' : job ? '重新一键分析' : '一键分析'}
      </button>
    </header>
    {error && <div className="error-box" role="alert">{error}<button onClick={() => setReload(value => value + 1)}>重新读取任务</button></div>}
    {!user && <p className="hot-inline-note">登录后匹配自己的股票；已有判断自动复用，未完成部分按需分析。</p>}
    {user && ready && !watchlist.length && <p className="hot-inline-note">请先添加自选股。</p>}
    {job && <>
      <div className="hot-match-progress"><div><strong>{job.message}</strong><span>本次范围：{job.topic_total} 条新闻 × {job.stock_total} 只股票</span></div>
        <progress aria-label="一键分析进度" max={100} value={progress} /><p>已处理 {processed}/{job.counts.total} 对 · {job.counts.related} 对有关联 · 涉及 {groups.length} 只自选股</p>
        <small>开始于 {dateTime(job.started_at)} · {running ? '后台运行，离开页面后继续' : '已保存本次匹配结果'}</small>
      </div>
      {job.watchlist_changed && <p className="pipeline-warning" role="status">自选股已变化，当前只展示仍在自选中的股票；请重新分析以核对新列表。</p>}
      {job.counts.failed > 0 && <p className="hot-inline-note" role="status">{job.counts.failed} 对尚未完成，可重新一键分析重试；失败不表示无关。</p>}
      {job.counts.uncertain > 0 && <p className="hot-inline-note">另有 {job.counts.uncertain} 对关联证据不足，保留待核实。</p>}
      {groups.length > 0 && <div className="hot-match-results">{groups.map(stock => <article className="hot-match-stock" key={stock.code}>
        <header><div><strong>{stock.name}</strong><small>{stock.code}</small></div><span>{stock.news.length} 条相关消息</span></header>
        {stock.news.map(news => <section className="hot-match-news" key={news.topic_id}>
          <div><h3>{news.title}</h3><span className={`hot-business-direction ${news.analysis.direction}`}>{impactDirections[news.analysis.direction]}</span></div>
          <p>{news.analysis.mechanism}</p><small>{news.analysis.relation === 'direct' ? '直接关联' : '间接关联'} · 证据可信度低，实际敞口待核实</small>
          <details><summary>查看判断依据与成立条件</summary>{news.analysis.news_evidence.map((quote, index) => <blockquote key={index}>{quote}</blockquote>)}
            {news.analysis.business_evidence && <><small>主营原文依据</small><blockquote>{news.analysis.business_evidence}</blockquote></>}
            <p>{news.analysis.uncertainty}</p>{news.sources.map(source => <a href={source.url} target="_blank" rel="noopener noreferrer" key={source.id}>{source.publisher || source.name} · {source.link_kind === 'provider_doc' ? '接口说明' : '来源入口'}<ExternalLink size={11} /></a>)}
          </details>
        </section>)}
      </article>)}</div>}
      {!groups.length && <p className="hot-inline-note">{running ? '正在核对资料，有依据的关联会陆续显示在这里。' : job.counts.completed === job.counts.total ? '已核对本次范围，暂未发现有依据的关联。' : '暂未取得有依据的关联，仍有未完成或待核实的判断。'}</p>}
    </>}
  </section>;
}
