import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Flame, LoaderCircle, RefreshCw, Search } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import { dateTime } from './format';
import HotStockImpactPanel from './HotStockImpactPanel';
import HotTopicImpacts from './HotTopicImpacts';
import HotNewsMatchPanel from './HotNewsMatchPanel';
import type { HotFeed, HotImpactPreview, HotTopic, MarketChannel } from './hotNewsTypes';
import './hot-news.css';

const channels: Record<MarketChannel, string> = {
  trade: '贸易与进出口', supply: '供给与供应链', cost: '成本与商品价格', demand: '消费与行业需求',
  regulation: '政策与监管', funding: '利率与资金', risk: '经营与市场风险',
};

export default function HotNewsPanel({ watchlist, watchReady }: { watchlist: string[]; watchReady: boolean }) {
  const { user, requireAuth } = useAuth();
  const [result, setResult] = useState<{ key: string; data: HotFeed } | null>(null);
  const [source, setSource] = useState('all');
  const [channel, setChannel] = useState('all');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('hot');
  const [limit, setLimit] = useState(50);
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<HotTopic | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [matching, setMatching] = useState(false);
  const [error, setError] = useState('');
  const [impactRead, setImpactRead] = useState<{ scope: string; items: Record<string, HotImpactPreview>; loading: boolean; error: string } | null>(null);
  const work = useRef<AbortController | null>(null);
  const impactOpener = useRef<HTMLButtonElement | null>(null);
  const params = new URLSearchParams({ source, channel, q: query, sort, limit: String(limit) }).toString();
  const feed = result?.data ?? null;
  const current = result?.key === params ? result.data : null;
  const refreshState = feed?.refresh;
  const refreshing = submitting || refreshState?.status === 'running';
  const running = refreshState?.status === 'running';
  const watchKey = watchlist.join(',');
  const impactScope = JSON.stringify([user?.id, watchKey, current?.items]);
  const impacts = impactRead?.scope === impactScope ? impactRead : null;

  useEffect(() => {
    if (!user || !watchReady || !current?.items.length) return;
    const controller = new AbortController();
    setImpactRead({ scope: impactScope, items: {}, loading: true, error: '' });
    const ids = current.items.map(topic => topic.id).join(',');
    api<{ items: HotImpactPreview[] }>(`/api/hot-news/analysis-batch?ids=${encodeURIComponent(ids)}`, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted) setImpactRead(previous => ({ scope: impactScope,
        items: { ...Object.fromEntries(data.items.map(item => [item.topic_id, item])), ...(previous?.scope === impactScope ? previous.items : {}) }, loading: false, error: '' }));
    }).catch(e => { if (!controller.signal.aborted) setImpactRead(previous => ({ scope: impactScope, items: previous?.scope === impactScope ? previous.items : {}, loading: false, error: e.message })); });
    return () => controller.abort();
  }, [impactScope, watchReady]);

  function rememberImpact(data: HotImpactPreview) {
    setImpactRead(previous => ({ scope: impactScope, items: { ...(previous?.scope === impactScope ? previous.items : {}), [data.topic_id]: data }, loading: previous?.scope === impactScope && previous.loading, error: '' }));
  }

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    api<HotFeed>(`/api/hot-news?${params}`, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ key: params, data });
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [params, reload]);

  useEffect(() => () => work.current?.abort(), []);

  useEffect(() => {
    if (!running || loading) return;
    const timer = window.setTimeout(() => setReload(value => value + 1), 2000);
    return () => window.clearTimeout(timer);
  }, [running, result, loading]);

  async function refresh() {
    if (!requireAuth('登录后更新全领域热点榜单并筛选可能影响股市的事件。')) return;
    const controller = new AbortController(); work.current?.abort(); work.current = controller;
    setSubmitting(true); setError('');
    try {
      const data = await api<HotFeed>('/api/hot-news/refresh', {}, controller.signal);
      if (!controller.signal.aborted) {
        setResult(previous => ({ key: params, data: previous?.key === params ? { ...previous.data, refresh: data.refresh } : data }));
        setReload(previous => previous + 1);
      }
    } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
    finally { if (!controller.signal.aborted) setSubmitting(false); }
  }

  function resetFilters() {
    setSource('all'); setChannel('all'); setSearch(''); setQuery(''); setSort('hot'); setLimit(50);
  }
  const topics = current?.items ?? [];
  const filtering = source !== 'all' || channel !== 'all' || !!search.trim() || sort !== 'hot' || limit !== 50;
  return <section className="hot-news-workspace" aria-label="市场相关热点榜单">
    <header className="hot-toolbar panel"><div><Flame size={24} /><div><h2>国内国际 · 重要热点新闻</h2><p>每个平台最多采集20条，按事件指向、重复传播、重要性、舆论和具体事实综合评分。</p></div></div>
      <button className="primary-button" onClick={() => void refresh()} disabled={refreshing || loading}>
        {refreshing ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}{refreshing ? '更新进行中…' : '更新热点'}
      </button>
    </header>
    {error && <div className="error-box" role="alert">{error}<button onClick={() => setReload(previous => previous + 1)}>重新读取</button></div>}
    <div className="hot-source-grid">{feed?.sources.map(item => <section className="panel hot-source-card" key={item.id}>
      <div><strong>{item.name}</strong><span className={`hot-source-status ${item.status}`}>{item.status === 'ok' ? '已更新' : item.status === 'stale' ? '沿用上次快照' : '暂不可用'}</span></div>
      <p>{item.fetched_at ? `采集于 ${dateTime(item.fetched_at)}` : '尚未取得可用快照'}</p>
      {item.error && <small>{item.error}</small>}<a href={item.url} target="_blank" rel="noopener noreferrer">{item.id === 'tushare_major' ? '查看接口说明' : item.id.startsWith('tushare_') ? '查看新闻来源入口' : '查看平台热榜'}<ExternalLink size={12} /></a>
    </section>)}</div>
    {refreshState?.status === 'error' && <div className="error-box" role="alert">{refreshState.message || '上次更新未完成，请重试。'}</div>}
    {!!feed?.warnings.length && <div className="pipeline-warning" role="status">{feed.warnings.map(warning => <p key={warning}>{warning}</p>)}</div>}
    {feed?.status === 'stale' && <p className="pipeline-warning" role="status">热榜快照已过期，请更新后核对。各来源保留自己的采集时间。</p>}
    <HotNewsMatchPanel topics={topics} watchlist={watchlist} ready={watchReady} disabled={loading || refreshing}
      onBusy={setMatching} onResults={rows => rows.forEach(rememberImpact)} />
    <section className="panel hot-board" aria-busy={loading || refreshing}>
      <header className="hot-board-heading"><div><h2>重要热点新闻</h2><p>{feed?.note ?? '读取已保存的热榜快照，不自动发起外部采集。'}</p></div>
        {filtering && <button className="hot-reset" onClick={resetFilters} disabled={refreshing}>重置筛选</button>}
      </header>
      <div className="hot-query-controls">
        <label className="hot-search"><span>关键词</span><div><Search size={15} /><input aria-label="搜索热点新闻" value={search} maxLength={80} placeholder="搜索标题或摘要，如关税、航运" onChange={event => setSearch(event.target.value)} disabled={refreshing} /></div></label>
        <label>来源<select aria-label="筛选热榜来源" value={source} onChange={event => setSource(event.target.value)} disabled={refreshing}><option value="all">全部平台</option>{feed?.sources.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
        <label>影响类型<select aria-label="筛选经济影响类型" value={channel} onChange={event => setChannel(event.target.value)} disabled={refreshing}><option value="all">全部影响类型</option>{Object.entries(channels).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        <label>排序<select aria-label="热点排序方式" value={sort} onChange={event => setSort(event.target.value)} disabled={refreshing}><option value="hot">综合评分优先</option><option value="latest">最近更新</option></select></label>
        <label>展示条数<select aria-label="热点展示条数" value={limit} onChange={event => setLimit(Number(event.target.value))} disabled={refreshing}>{[20, 50, 100].map(value => <option key={value} value={value}>{value} 条</option>)}</select></label>
      </div>
      {current && !loading && <p className="hot-result-count" role="status">当前条件匹配 {current.matched_total} 条重要热点，展示 {topics.length} 条。{channel !== 'all' ? '影响类型仅适用于已识别市场入口的事件。' : ''}</p>}
      {loading && <p className="hot-empty" role="status">正在读取热点榜单…</p>}
      {refreshing && <p className="hot-empty" role="status">{refreshState?.message}{refreshState?.total ? `（${refreshState.completed}/${refreshState.total} 条已处理）` : ''} · 正在后台采集和筛选重要新事件，完成后自动展示；可以离开页面稍后回来。</p>}
      <ol className="hot-topic-list">{topics.map((topic, index) => <li key={topic.id} className="hot-topic-card">
        <div className="hot-topic-rank" aria-label={`当前列表第${index + 1}名`}>{String(index + 1).padStart(2, '0')}</div>
        <article><header><h3>{topic.title}</h3><span className={`hot-decision ${topic.screening.decision}`}>{'重要新事件'}</span></header>
          <div className="hot-topic-sources">{topic.sources.map(entry => <a href={entry.url} key={entry.id} target="_blank" rel="noopener noreferrer">
            {entry.publisher || entry.name}{entry.kind === 'news' ? ` · 报道于 ${dateTime(entry.published_at ?? null)} · ${entry.link_kind === 'provider_doc' ? '接口说明' : '来源入口'}` : ` · 第 ${entry.rank} 名`}{entry.heat ? ` · 热度 ${entry.heat}` : ''}{entry.stale ? ' · 旧快照' : ''}<ExternalLink size={11} />
          </a>)}</div>
          {topic.summary && <p className="hot-summary">{topic.summary}</p>}
          {topic.newsworthiness && <details className="hot-score-details"><summary>
            {topic.newsworthiness.decision === 'core' ? '核心热点' : topic.newsworthiness.major_event_supplement ? '重大事件补充' : '热点'} · 新闻优先级 {topic.newsworthiness.total_score}/100
          </summary><p>指向 {topic.newsworthiness.specificity_score}/15 · 重复 {topic.newsworthiness.repetition_score}/30 · 重要性 {topic.newsworthiness.importance_score}/25 · 舆论 {topic.newsworthiness.opinion_score}/15 · 事件 {topic.newsworthiness.event_score}/15</p>
            <p>同平台有效出现 {topic.newsworthiness.repetition_detail.same_platform_count} 次（{topic.newsworthiness.repetition_detail.same_platform_score}/22），跨 {topic.newsworthiness.repetition_detail.platform_count} 个平台（{topic.newsworthiness.repetition_detail.cross_platform_score}/8）。</p>
            {Object.entries(topic.newsworthiness.rubric.evidence).filter(([, quote]) => quote).map(([key, quote]) => <blockquote key={key}>{quote}</blockquote>)}
            <p>{topic.newsworthiness.method === 'rules' ? '规则评分；模型维度未完成，舆论信息未知。' : topic.newsworthiness.opinion_evidence_status === 'unknown' ? '未取得足够舆论证据，该项暂记0分。' : '模型维度经原文引文检查；重复次数与总分由程序计算。'}{topic.newsworthiness.note}</p>
          </details>}
          {topic.attention && <p className={`hot-attention ${topic.attention.priority ? 'priority' : ''}`}>
            {topic.attention.priority ? '重点关注 · ' : ''}{topic.attention.mentions ? `${topic.attention.mentions} 条热搜 · ${topic.attention.platforms} 个平台 · 近24小时 ${topic.attention.snapshots} 次采集上榜` : '资讯报道 · 未取得热搜名次'}
          </p>}
          {topic.members && topic.members.length > 1 && <details className="hot-event-members"><summary>同一事件的 {topic.members.length} 条热搜 / 报道</summary>
            {topic.members.map(member => <div key={member.id}><strong>{member.title}</strong>{member.sources.map(entry => <a key={entry.id} href={entry.url} target="_blank" rel="noopener noreferrer">{entry.name}{entry.rank ? ` · 第${entry.rank}名` : ' · 资讯'}<ExternalLink size={11} /></a>)}</div>)}
          </details>}
          <div className="hot-market-path"><strong>{'事件与影响'}</strong><p>{topic.screening.reason}</p>
            {!!topic.screening.channels.length && <div className="hot-channel-tags">{topic.screening.channels.map(channel => <span key={channel}>{channels[channel]}</span>)}</div>}
          </div>
          <details><summary>查看筛选依据与采集时间</summary><blockquote>{topic.screening.evidence}</blockquote>
            <p>{topic.screening.method === 'model' ? '模型辅助初筛' : '规则初筛'} · {topic.sources.some(entry => entry.kind === 'news') ? '新闻接口材料' : '平台话题摘要'}，尚未独立核验原始报道</p>
            {topic.sources.map(entry => <p key={entry.id}>{entry.name}：{dateTime(entry.observed_at)}{entry.stale ? '（旧快照）' : ''}</p>)}
          </details>
          <HotTopicImpacts key={`${watchKey}:${JSON.stringify(topic)}`} topic={topic}
            data={impacts?.items[topic.id]?.title === topic.title && impacts.items[topic.id].summary === topic.summary ? impacts.items[topic.id] : undefined}
            loading={!!user && (!watchReady || !impacts || impacts.loading)} readError={impacts?.error ?? ''} ready={watchReady}
            matching={matching} onResult={rememberImpact} openDetails={button => { impactOpener.current = button; setSelected(topic); }} />
        </article>
      </li>)}</ol>
      {current && !loading && !topics.length && <div className="hot-empty"><p>{current.candidate_total ? '没有符合当前条件的新闻，可调整关键词或影响类型。' : '暂时没有符合标准的重要新事件。可以更新热点；不会用泛话题或消费小事填满榜单。'}</p>{filtering && <button className="hot-reset" onClick={resetFilters}>重置筛选</button>}</div>}
      {current && current.matched_total > topics.length && topics.length < 100 && <button className="hot-show-all" onClick={() => setLimit(100)}>显示更多匹配热点（最多100条）</button>}
      <footer className="hot-note"><p>{feed?.ranking_note ?? '榜单保留原平台名次、热度和采集时间。'}</p><p>选择热点可核对其与自选股的业务关联、利好利空及原文依据。</p></footer>
    </section>
    {selected && <HotStockImpactPanel key={`${selected.id}:${watchKey}`} topic={selected} watchlist={watchlist} ready={watchReady} close={() => setSelected(null)} returnFocus={impactOpener.current} onResult={rememberImpact} matching={matching} />}
  </section>;
}
