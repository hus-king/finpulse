import type { HotStockImpact } from './hotNewsTypes';

export default function ImpactScore({ analysis }: { analysis: HotStockImpact }) {
  const score = analysis.score;
  const valid = Number.isInteger(score) && score !== null && score !== 0 && Math.abs(score) <= 10;
  const related = analysis.relation === 'direct' || analysis.relation === 'indirect';
  return <span className={`hot-business-direction ${valid && score! > 0 ? 'positive' : valid ? 'negative' : 'unclear'}`}>
    {related ? valid ? `${score! > 0 ? '+' : ''}${score}分 · ${score! > 0 ? '利好' : '利空'}` : '待重新评分'
      : analysis.relation === 'none' ? '未发现关联' : '关联待核实'}
  </span>;
}
