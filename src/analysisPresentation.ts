import type { Analysis, News } from './types';
export const assessmentNames: Record<string,string> = { positive:'偏利好', negative:'偏利空', neutral:'中性', mixed:'正负影响并存', insufficient:'待补证' };
export const confidenceNames: Record<string,string> = {high:'高',medium:'中',low:'低'};
export const horizonNames: Record<string,string> = {short:'短期预期',medium:'中期经营',long:'长期影响',unclear:'作用期未知'};
export function analysisLabel(analysis: Analysis) {
  if (analysis.assessment === 'insufficient' || analysis.assessment === 'mixed') return assessmentNames[analysis.assessment];
  const score = analysis.sentiment_score;
  return `${analysis.assessment ? '影响' : '旧版'} ${score != null && score > 0 ? '+' : ''}${score ?? '—'}/${analysis.assessment ? '100' : '2'}`;
}
export function newsLabel(news: News) {
  return news.analysis ? analysisLabel(news.analysis) : news.analysis_status === 'running' ? 'AI 研判中' : news.analysis_status === 'failed' ? '研判失败' : '待研判';
}
