import { useEffect, useState } from 'react';
import { ArrowRight, BookOpen, ChevronRight, Download, ExternalLink, LoaderCircle, Sparkles } from 'lucide-react';
import AnalysisEvidence from './AnalysisEvidence';
import { newsLabel, isScored } from './analysisPresentation';
import { dateTime } from './format';
import type { Dashboard, News } from './types';

export default function NewsFeed({ data, openAssistant, analyzeAll, running, stage }: { data: Dashboard; openAssistant: (news: News) => void; analyzeAll: () => void; running: boolean; stage?: string }) {
  const [sentiment, setSentiment] = useState('全部');
  const [scope, setScope] = useState('全部范围');
  useEffect(() => { setSentiment('全部'); setScope('全部范围'); }, [data.stock.code]);
  const visible = data.news.filter(row => {
    const kind = row.news_scope ?? 'company';
    const scopeMatches = scope === '全部范围' || kind === (scope === '公司新闻' ? 'company' : 'industry');
    const ready = isScored(row);
    const sentimentMatches = sentiment === '全部' || (sentiment === '利好' ? ready && (row.score ?? 0) > 0 : sentiment === '利空' ? ready && (row.score ?? 0) < 0 : !ready);
    return scopeMatches && sentimentMatches;
  });
  const profile = data.industry_profile;
  const companyCount = data.news.filter(row => row.news_scope !== 'industry').length;
  const industryCount = data.news.length - companyCount;
  const industryStatus = data.pipeline?.statuses?.industry_news;
  const industryEmpty = scope === '行业新闻' && industryCount === 0;
  const industryPending = data.pipeline?.stages?.industry === 'pending';
  const emptyTitle = industryEmpty ? industryPending ? '正在检索与清洗行业新闻' : industryStatus === 'unavailable' || industryStatus === 'partial' ? '行业新闻更新未完成' : !profile ? '当前快照尚未采集行业新闻' : '本轮暂无通过清洗的行业新闻' : data.news.length ? '该分类暂无新闻' : data.pipeline?.stages?.news === 'pending' ? '正在检索与清洗新闻' : '尚无通过清洗的新闻';
  const emptyDescription = industryEmpty ? industryPending ? '公司新闻已取得时会先展示，行业查询仍在后台处理。' : industryStatus === 'unavailable' || industryStatus === 'partial' ? '行业数据源或处理任务失败，请查看采集警告后重试。' : !profile ? '点击上方「采集并分析」，会同时识别行业并检索行业事件。' : '检索结果须通过日期、独立原文和行业事件关联校验；可在清洗记录中查看过滤原因。' : data.news.length ? '试试其他范围或倾向筛选。' : '采集后展示通过来源、日期和事件关联校验的公司及行业新闻。';
  function exportNews() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `finpulse-${data.stock.code}.json`; link.click(); URL.revokeObjectURL(url);
  }
  return <section className="panel live-news">
    <header><div><p className="eyebrow">LATEST COVERAGE</p><h2>{data.stock.name} · 新闻与研判<span>{data.news.length}</span></h2></div><button className="icon-button" aria-label="导出当前研究数据" onClick={exportNews}><Download size={17} /></button></header>
    <div className="industry-profile" role="status">
      <strong>{profile?.industry ? `所属行业：${profile.industry}` : profile ? '所属行业暂无法核验' : '采集时自动识别所属行业'}</strong>
      <span>{profile?.industry ? `${profile.source ?? '个股资料'} · ${dateTime(profile.fetched_at)}${profile.status === 'stale' ? ' · 沿用上次行业资料' : ''}` : profile ? '本轮仅更新公司新闻，已有行业材料会标明更新时间' : '公司新闻与行业事件一起检索，新增股票同样支持'}</span>
      {profile?.industry && <small>行业事件为间接关联，具体业务影响请结合原文及公司资料核验。</small>}
    </div>
    {data.business_profile?.main_business && <details className="business-profile"><summary>研判依据：主营业务资料</summary><p>{data.business_profile.main_business}</p><small>{data.business_profile.source} · {dateTime(data.business_profile.fetched_at ?? null)}{data.business_profile.status === 'stale' ? ' · 沿用上次资料' : ''}</small><p>{data.business_profile.note}</p><a href={data.business_profile.url} target="_blank" rel="noopener noreferrer">查看业务资料来源</a></details>}
    {!!data.excluded_news?.length && <details className="business-profile"><summary>已排除 {data.excluded_news.length} 条非新闻材料</summary>{data.excluded_news.map(row=><p key={row.id}><a href={row.url} target="_blank" rel="noopener noreferrer">{row.title}</a><small> · {row.reason}</small></p>)}</details>}
    <div className="news-tabs news-scope-tabs" role="group" aria-label="新闻范围">{['全部范围', '公司新闻', '行业新闻'].map(item => <button key={item} aria-pressed={scope === item} className={scope === item ? 'active' : ''} onClick={() => setScope(item)}>{item}{item === '公司新闻' ? ` ${companyCount}` : item === '行业新闻' ? ` ${industryCount}` : ''}</button>)}</div>
    <div className="news-tabs" role="group" aria-label="新闻倾向">{['全部', '利好', '利空', '待研判'].map(item => <button key={item} aria-pressed={sentiment === item} className={sentiment === item ? 'active' : ''} onClick={() => setSentiment(item)}>{item}</button>)}</div>
    {sentiment === '待研判' && <div className="analyze-all-toolbar"><span role="status" aria-live="polite">{running ? stage || '正在研判…' : `当前公司有 ${data.news.filter(row => !isScored(row)).length} 条待研判新闻`}</span><button className="primary-button" disabled={running || !data.news.some(row => !isScored(row))} onClick={analyzeAll}>{running ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}全部研判</button></div>}
    {visible.length ? <div className="live-news-list">{visible.map((news, index) => <article className={index === 0 ? 'feature-news' : ''} key={news.id}>
      <div className="news-kicker"><span>{news.source}</span><time>{news.time}</time><span className="news-scope-badge">{news.news_scope === 'industry' ? '行业新闻' : '公司新闻'}</span><span>{news.tag !== '行业新闻' ? news.tag : news.industry}</span><span className={`news-score ${!isScored(news) ? 'unrated' : (news.score ?? 0) > 0 ? 'up' : (news.score ?? 0) < 0 ? 'down' : ''}`}>{newsLabel(news)}</span></div>
      <h3><a href={news.url} target="_blank" rel="noopener noreferrer">{news.title}<ExternalLink size={13} /></a></h3>
      <p>{isScored(news) ? news.analysis?.summary : news.content.slice(0, 190)}</p>
      {isScored(news) && news.analysis && <AnalysisEvidence analysis={news.analysis} />}
      {news.news_scope === 'industry' && <div className="news-industry-relation"><strong>关联因素：{news.related_factors?.join('、') || news.industry || '行业动态'}</strong><p>{news.relevance_reason || '行业间接关联，尚需核验对目标公司的实际影响。'}</p></div>}
      {(news.stale || news.refresh_pending) && <p className="news-stale" role="status">{news.refresh_pending ? '行业检索进行中，暂展示上次材料' : '本轮更新失败，沿用上次材料'}；发布日期为 {news.time}。</p>}
      {isScored(news) && news.analysis && <div className="news-chain">{news.analysis.causal_chain.map((step, i) => <span key={i}>{i > 0 && <ChevronRight size={12} />}{step}</span>)}</div>}
      <div className="news-actions"><span>{news.text_source === 'extracted_body' ? '已提取正文' : '搜索片段'} · {news.date_status === 'body_verified' ? '正文日期已核对' : '来源元数据日期'}{news.sources.length > 1 ? ` · ${news.sources.length} 个合并来源` : ''}</span><button disabled={news.analysis_status === 'running'} onClick={() => openAssistant(news)}>{news.analysis_status === 'running' ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}{isScored(news) ? '查看研判 / 追问' : news.analysis_status === 'running' ? '自动研判中' : '生成 AI 研判'}<ArrowRight size={13} /></button></div>
      {news.analysis_error && <p className="news-analysis-error">研判失败：{news.analysis_error}</p>}
    </article>)}</div> : <div className="empty-card"><BookOpen size={27} /><h3>{emptyTitle}</h3><p>{emptyDescription}</p></div>}
  </section>;
}
