import type { Analysis, News } from './types';
export const confidenceNames: Record<string,string> = {high:'高',medium:'中',low:'低'};
export const horizonNames: Record<string,string> = {short:'短期预期',medium:'中期经营',long:'长期影响',unclear:'作用期未知'};
export function isScored(news: News) {
  const a = news.analysis, score = a?.sentiment_score;
  return !!a && news.analysis_status === 'completed' && !news.refresh_pending &&
    ['news-v5-directional','news-v4-evidence'].includes(a.version ?? '') &&
    typeof score === 'number' && Number.isInteger(score) && Math.abs(score) > 0 && Math.abs(score) <= 100 && score % 5 === 0 &&
    news.score === score && a.assessment === (score > 0 ? 'positive' : 'negative');
}
export function analysisLabel(analysis: Analysis) {
  const score = analysis.sentiment_score;
  return typeof score === 'number' && score !== 0 && ['positive','negative'].includes(analysis.assessment ?? '')
    ? `${score > 0 ? '利好 +' : '利空 '}${score}/100` : '待研判';
}
export function newsLabel(news: News) { return isScored(news) && news.analysis ? analysisLabel(news.analysis) : '待研判'; }
