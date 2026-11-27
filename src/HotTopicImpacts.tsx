import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Sparkles } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import ImpactScore from './ImpactScore';
import type { HotImpactPreview, HotImpactSnapshot, HotTopic } from './hotNewsTypes';

export default function HotTopicImpacts({ topic, data, loading, readError, ready, matching, onResult, openDetails }: {
  topic: HotTopic; data?: HotImpactPreview; loading: boolean; readError: string; ready: boolean;
  matching: boolean; onResult: (result: HotImpactSnapshot) => void; openDetails: (button: HTMLButtonElement) => void;
}) {
  const { user, requireAuth } = useAuth();
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const work = useRef<AbortController | null>(null);
  const resultRef = useRef(onResult); resultRef.current = onResult;
  useEffect(() => () => work.current?.abort(), []);

  async function analyze() {
    if (!requireAuth('登录后分析热点与自己的自选股是否有关、是利好还是利空。')) return;
    const controller = new AbortController(); work.current = controller;
    setAnalyzing(true); setError('');
    try {
      const result = await api<HotImpactSnapshot>(`/api/hot-news/${topic.id}/analyze`, {}, controller.signal);
      if (result.title !== topic.title || result.summary !== topic.summary) throw new Error('热点内容已更新，请更新榜单后查看新的判断。');
      if (!controller.signal.aborted) resultRef.current(result);
    } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
    finally { if (!controller.signal.aborted) setAnalyzing(false); }
  }
  const rows = data?.items ?? [];
  const related = rows.filter(row => row.analysis && ['direct', 'indirect'].includes(row.analysis.relation));
  const sectors = topic.sector_impacts?.filter(sector => sector.direction === 'positive' || sector.direction === 'negative') ?? [];
  const unrelated = rows.filter(row => row.analysis?.relation === 'none' || row.analysis?.relation === 'uncertain').length;
  const complete = data && data.counts.completed === data.counts.total && data.counts.total > 0;
  return <div className="hot-topic-impacts">
    {!!sectors.length && <section className="hot-sector-section" aria-label="可能受影响的板块">
      <h4>可能受影响的板块<span>业务影响</span></h4>
      <div className="hot-sector-grid">{sectors.map(sector => <div className="hot-sector-card" key={sector.id}>
        <div><strong>{sector.name}</strong><span className={`hot-business-direction ${sector.direction}`}>{sector.direction === 'positive' ? '利好' : '利空'}</span></div>
        <p>{sector.reason}</p><small>{sector.method === 'model' ? '模型推理' : '规则推理'}</small>
        <details><summary>新闻依据</summary><blockquote>{sector.evidence}</blockquote></details>
      </div>)}</div>
    </section>}
    <section className="hot-watch-section" aria-label="我的相关自选股" aria-busy={analyzing || loading}>
      <h4>我的相关自选股{data && <span>{data.counts.related} 只有关联 · {data.counts.completed}/{data.counts.total} 只已核对</span>}</h4>
      {!user ? <p className="hot-inline-note">登录后展示与这条新闻相关的自选股及利好利空。</p>
        : loading ? <p className="hot-inline-note" role="status">正在读取已保存的自选股判断…</p>
        : data && !rows.length ? <p className="hot-inline-note">请先添加自选股，再分析与新闻的关联。</p>
        : <>
          {related.map(row => <div className="hot-inline-stock" key={row.code}>
            <div><strong>{row.name}</strong><small>{row.code}</small><ImpactScore analysis={row.analysis!} /><small>{row.analysis!.relation === 'direct' ? '直接关联' : '间接关联'}</small></div>
            <p>{row.analysis!.mechanism}</p>
          </div>)}
          {unrelated > 0 && related.length > 0 && <p className="hot-inline-note">其余 {unrelated} 只未确认关联。</p>}
          {!!data?.counts.pending && <p className="hot-inline-note">{data.counts.pending} 只尚未分析，点击下方按钮核对。</p>}
          {!!data?.counts.failed && <p className="hot-inline-note" role="status">{data.counts.failed} 只判断未完成，可重试；失败不代表无关。</p>}
          {complete && !related.length && <p className="hot-inline-note">已完成分析，未确认本条新闻对自选股有可评分的影响。</p>}
        </>}
      {(error || readError) && <p className="error-box" role="alert">{error || readError}</p>}
      {analyzing && <p className="hot-inline-note" role="status">正在逐股核对主营资料与新闻依据…</p>}
      <div className="hot-inline-actions">
        {!complete && <button className="hot-analyze-inline" disabled={matching || analyzing || !!user && (!ready || loading || !!data && !rows.length)} onClick={() => void analyze()}>
          {analyzing ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}{analyzing ? '分析中…' : data?.counts.failed ? '重试未完成判断' : data?.counts.completed ? '补全自选股判断' : '分析我的自选股'}
        </button>}
        <button className="hot-open-impact" onClick={event => { if (requireAuth('登录后查看自己的自选股关联与原文依据。')) openDetails(event.currentTarget); }}>全部自选与证据</button>
      </div>
    </section>
  </div>;
}
