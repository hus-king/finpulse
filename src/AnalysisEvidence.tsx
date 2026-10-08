import type { Analysis } from './types';
import { confidenceNames, horizonNames } from './analysisPresentation';
export default function AnalysisEvidence({analysis}:{analysis:Analysis}) {
  if (!analysis.assessment) return <small className="evidence-meta">旧版评分将中性与证据不足合并，可重新采集更新。</small>;
  return <div className="analysis-evidence"><p className="evidence-meta">证据可信度：{confidenceNames[analysis.confidence ?? 'low']} · {horizonNames[analysis.horizon ?? 'unclear']}</p><details><summary>查看利好、风险与验证条件</summary>{[['利好因素',analysis.positive_factors],['风险因素',analysis.negative_factors],['后续验证',analysis.watch_points]].map(([label,items]) => Array.isArray(items) && items.length > 0 && <section key={String(label)}><strong>{String(label)}</strong><ul>{items.map(text => <li key={text}>{text}</li>)}</ul></section>)}<p>{analysis.uncertainty}</p></details></div>;
}
