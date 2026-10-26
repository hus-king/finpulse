import { useEffect, useRef, useState } from 'react';
import { ExternalLink, LoaderCircle, Sparkles, X } from 'lucide-react';
import { api } from './api';
import { dateTime } from './format';
import useDialogScroll from './useDialogScroll';
import type { HotImpactSnapshot, HotTopic } from './hotNewsTypes';

const relations = { direct: '直接关联', indirect: '间接关联', none: '未发现关联', uncertain: '关联待核实' };
const directions = { positive: '利好', negative: '利空', mixed: '正反并存', unclear: '影响不明' };
const confidence = { high: '高', medium: '中', low: '低' };
type Filter = 'related' | 'all' | 'positive' | 'negative' | 'uncertain';

export default function HotStockImpactPanel({ topic, watchlist, ready, close, returnFocus, onResult }: {
  topic: HotTopic; watchlist: string[]; ready: boolean; close: () => void; returnFocus: HTMLElement | null;
  onResult: (data: HotImpactSnapshot) => void;
}) {
  useDialogScroll();
  const [data, setData] = useState<HotImpactSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [filter, setFilter] = useState<Filter>('related');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const read = useRef<AbortController | null>(null);
  const work = useRef<AbortController | null>(null);
  const dialog = useRef<HTMLElement | null>(null);
  const closeRef = useRef(close); closeRef.current = close;
  const resultRef = useRef(onResult); resultRef.current = onResult;
  const watchKey = watchlist.join(',');

  useEffect(() => {
    const previous = returnFocus ?? document.activeElement;
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current();
      if (event.key !== 'Tab') return;
      const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, select, summary') ?? []).filter(item => item.getClientRects().length > 0);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener('keydown', listener);
    return () => { window.removeEventListener('keydown', listener); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    work.current?.abort(); setAnalyzing(false); setData(null); setLoading(true); setError('');
    if (!ready) return;
    const controller = new AbortController(); read.current = controller;
    api<HotImpactSnapshot>(`/api/hot-news/${topic.id}/analysis`, undefined, controller.signal).then(result => {
      if (!controller.signal.aborted) { setData(result); resultRef.current(result); }
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [topic.id, watchKey, ready, reload]);
  useEffect(() => () => work.current?.abort(), []);

  async function analyze() {
    read.current?.abort(); setLoading(false);
    const controller = new AbortController(); work.current?.abort(); work.current = controller;
    setAnalyzing(true); setError('');
    try {
      const result = await api<HotImpactSnapshot>(`/api/hot-news/${topic.id}/analyze`, {}, controller.signal);
      if (!controller.signal.aborted) { setData(result); resultRef.current(result); setFilter('related'); }
    } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
    finally { if (!controller.signal.aborted) setAnalyzing(false); }
  }

  const rows = data?.items ?? [];
  const related = rows.filter(row => row.analysis && ['direct', 'indirect'].includes(row.analysis.relation));
  const uncertain = rows.filter(row => !row.analysis || row.analysis.relation === 'uncertain');
  const shown = rows.filter(row => filter === 'all' || (filter === 'related' ? row.analysis && ['direct', 'indirect'].includes(row.analysis.relation)
    : filter === 'uncertain' ? !row.analysis || row.analysis.relation === 'uncertain' : row.analysis?.direction === filter));
  return <div className="modal-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section className="panel hot-impact-dialog" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="hot-impact-heading">
      <header><div><small>热点与我的自选股</small><h2 id="hot-impact-heading">关联与利好利空</h2></div><button autoFocus className="icon-button" aria-label="关闭关联分析" onClick={close}><X size={22} /></button></header>
      <h3 className="hot-impact-title">{data?.title ?? topic.title}</h3>
      <p className="hot-impact-note">{data?.note ?? '读取当前账号的自选股及已保存关联判断。'}</p>
      <div className="hot-impact-action"><div><strong>{data ? `${data.counts.completed}/${data.counts.total} 只已完成 · ${data.counts.related} 只有关联` : '核对我的自选股业务资料'}</strong><p>热点事件 → 公司业务角色 → 可能的正反影响</p></div>
        <button className="primary-button" disabled={loading || analyzing || !ready || !rows.length} onClick={() => void analyze()}>{analyzing ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{analyzing ? '分析中…' : data && data.counts.completed === data.counts.total ? '核对并补全' : '分析关联与影响'}</button>
      </div>
      {error && <div className="error-box" role="alert">{error}<button onClick={() => setReload(previous => previous + 1)}>重新读取</button></div>}
      {loading && <p className="hot-empty" role="status">正在读取自选股关联…</p>}
      {analyzing && <p className="hot-empty" role="status">正在核对业务资料和新闻依据。完成后逐只展示关联及影响方向。</p>}
      {data?.counts.failed ? <p className="pipeline-warning" role="status">{data.counts.failed} 只分析未完成，可再次点击分析重试；已完成结果会复用。</p> : null}
      {data && <>
        {!rows.length && <p className="hot-empty">请先添加自选股，再判断热点与哪些公司有关。</p>}
        {!!rows.length && <nav className="hot-filters" aria-label="筛选关联结果">{([
          ['related', `有关联 ${related.length}`], ['positive', '利好'], ['negative', '利空'],
          ['uncertain', `待核实 / 未完成 ${uncertain.length}`], ['all', `全部自选 ${rows.length}`],
        ] as [Filter, string][]).map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</nav>}
        {shown.map(row => <article className="hot-stock-card" key={row.code}>
          <header><div><strong>{row.name}</strong><small>{row.code}</small></div>{row.analysis && <span className={`hot-business-direction ${row.analysis.direction}`}>{directions[row.analysis.direction]}</span>}</header>
          {row.analysis ? <>
            <div className="hot-stock-meta"><span>{relations[row.analysis.relation]}</span><span>证据可信度：{confidence[row.analysis.confidence]}</span></div>
            <p className="hot-stock-mechanism">{row.analysis.mechanism}</p>
            <details><summary>查看新闻与主营依据</summary>{row.analysis.news_evidence.map((quote, index) => <blockquote key={index}>{quote}</blockquote>)}
              {row.analysis.business_evidence && <div><small>公司主营原文</small><blockquote>{row.analysis.business_evidence}</blockquote>{row.business.url && <a href={row.business.url} target="_blank" rel="noopener noreferrer">{row.business.source || '主营资料来源'}<ExternalLink size={12} /></a>}
                <p>{row.business.status === 'stale' ? '沿用旧资料 · ' : ''}{row.business.fetched_at ? dateTime(new Date(row.business.fetched_at * 1000).toISOString()) : '资料时间未知'}</p></div>}
            </details>
            <p className="hot-stock-uncertainty">成立条件与局限：{row.analysis.uncertainty}</p>
            <small className="hot-stock-saved">{row.model} · {dateTime(row.analyzed_at ?? null)}</small>
          </> : <p className="hot-stock-uncertainty">{row.error ?? '尚未分析这只自选股。'}</p>}
        </article>)}
        {!!rows.length && !shown.length && <div className="hot-empty"><p>{data.counts.completed ? '当前筛选下没有匹配结果，可查看全部自选股的逐只判断。' : '点击“分析关联与影响”开始核对，或查看全部自选股。'}</p><button className="hot-reset" onClick={() => setFilter('all')}>查看全部自选股</button></div>}
      </>}
    </section>
  </div>;
}
