import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Sparkles } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import { impactDirections } from './hotNewsTypes';
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
  const uncertain = rows.filter(row => row.analysis?.relation === 'uncertain');
  const unrelated = rows.filter(row => row.analysis?.relation === 'none').length;
  const complete = data && data.counts.completed === data.counts.total && data.counts.total > 0;
  return <div className="hot-topic-impacts">
    <section className="hot-sector-section" aria-label="可能受影响的板块">
      <h4>可能受影响的板块<span>业务影响</span></h4>
      {!!topic.sector_impacts?.length ? <div className="hot-sector-grid">{topic.sector_impacts.map(sector => <div className="hot-sector-card" key={sector.id}>
        <div><strong>{sector.name}</strong><span className={`hot-business-direction ${sector.direction}`}>{impactDirections[sector.direction]}</span></div>
        <p>{sector.reason}</p><small>{sector.method === 'model' ? '模型推理' : '规则推理'} · 待核实实际敞口</small>
        <details><summary>新闻依据</summary><blockquote>{sector.evidence}</blockquote></details>
      </div>)}</div> : <p className="hot-inline-note">暂未识别有充分依据的板块影响，需补充报道内容。</p>}
    </section>
    <section className="hot-watch-section" aria-label="我的相关自选股" aria-busy={analyzing || loading}>
      <h4>我的相关自选股{data && <span>{data.counts.related} 只有关联 · {data.counts.completed}/{data.counts.total} 只已核对</span>}</h4>
      {!user ? <p className="hot-inline-note">登录后展示与这条新闻相关的自选股及利好利空。</p>
        : loading ? <p className="hot-inline-note" role="status">正在读取已保存的自选股判断…</p>
        : data && !rows.length ? <p className="hot-inline-note">请先添加自选股，再分析与新闻的关联。</p>
        : <>
          {related.map(row => <div className="hot-inline-stock" key={row.code}>
            <div><strong>{row.name}</strong><small>{row.code}</small><span className={`hot-business-direction ${row.analysis!.direction}`}>{impactDirections[row.analysis!.direction]}</span><small>{row.analysis!.relation === 'direct' ? '直接关联' : '间接关联'}</small></div>
            <p>{row.analysis!.mechanism}</p>
          </div>)}
          {!!uncertain.length && <p className="hot-inline-note">关联待核实：{uncertain.map(row => row.name).join('、')}（暂不判定利好利空）</p>}
          {unrelated > 0 && <p className="hot-inline-note">另有 {unrelated} 只自选股暂未发现有依据的关联。</p>}
          {!!data?.counts.pending && <p className="hot-inline-note">{data.counts.pending} 只尚未分析，点击下方按钮核对。</p>}
          {!!data?.counts.failed && <p className="hot-inline-note" role="status">{data.counts.failed} 只判断未完成，可重试；失败不代表无关。</p>}
          {complete && !related.length && !uncertain.length && <p className="hot-inline-note">已核对当前自选股，暂未发现有依据的关联。</p>}
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
