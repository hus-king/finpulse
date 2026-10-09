import { useCallback, useState } from 'react';
import { ArrowRight, Bell, ChevronRight, LoaderCircle, Plus, RefreshCw, ShieldCheck, Sparkles, Trash2 } from 'lucide-react';
import QuotePanel from './QuotePanel';
import NewsFeed from './NewsFeed';
import ResearchOverview from './ResearchOverview';
import CommunityCard from './CommunityCard';
import { dateTime, number, percent, tone } from './format';
import type { Dashboard, Job, News, Stock } from './types';

interface Props {
  data: Dashboard | null; loading: boolean; catalog: Stock[]; snapshots: Record<string, Dashboard>; watchlist: string[];
  savingWatch: boolean; code: string; setCode: (code: string) => void; userSignedIn: boolean; add: () => void; remove: (code: string) => void;
  running: boolean; job: Job | null; collect: (days: number, community: boolean, communityOnly?: boolean, analysisOnly?: boolean) => void; audit: () => void; auditLoading: boolean;
  openAssistant: (news?: News) => void; briefing: () => void;
  onMarket: (data: Dashboard) => void;
}

export default function ResearchDashboard(props: Props) {
  const { data, loading, catalog, snapshots, watchlist, savingWatch, code, setCode, userSignedIn, add, remove, running, job, collect, audit, auditLoading, openAssistant, briefing } = props;
  const [includeCommunity, setIncludeCommunity] = useState(false);
  const [days, setDays] = useState(30);
  const stages = data?.pipeline?.stages;
  const openEvent = useCallback((id: string) => { const news = data?.news.find(row => row.id === id); if (news) openAssistant(news); }, [data, openAssistant]);
  return <div className="finance-layout" role="region" aria-label="个股行情与新闻">
    <aside className="portfolio-rail"><section className="panel portfolio-panel"><header><h2>我的自选</h2><button aria-label="添加自选股" className="icon-button" onClick={add}><Plus size={17} /></button></header><p className="rail-caption">{userSignedIn ? '保存在你的账号中' : '登录后保存自选股'}</p>{watchlist.map(stockCode => { const stock = catalog.find(row => row.code === stockCode); const snapshot = snapshots[stockCode]; return stock && <div key={stockCode} className={`portfolio-row ${code === stockCode ? 'active' : ''}`}><button onClick={() => setCode(stockCode)}><span><strong>{stock.name}</strong><small>{stock.exchange} {stock.code}</small></span><span><strong className="mono">{number(snapshot?.stock.price)}</strong><small className={tone(snapshot?.stock.change ?? null)}>{percent(snapshot?.stock.change)}</small></span></button><button className="remove-stock" aria-label={`移除${stock.name}`} disabled={savingWatch} onClick={() => remove(stockCode)}><Trash2 size={12} /></button></div>; })}{watchlist.length === 0 && <p className="rail-empty">还没有自选股，点击加号添加。</p>}<button className="add-watch" onClick={add}><Plus size={14} />管理关注标的</button></section><section className="rail-note"><ShieldCheck size={17} /><h3>研究有据可查</h3><p>新闻保留原文和清洗记录。行情展示数据日期，模型研判保留推断局限。</p><button onClick={briefing}>查看自选股早报<ArrowRight size={14} /></button></section></aside>
    <div className="research-main-column">
      {loading && <div className="panel empty-card"><LoaderCircle className="spin" size={25} /><p>正在读取已保存的数据…</p></div>}
      {data && <>
        <QuotePanel data={data} add={add} running={running} onEvent={openEvent} onMarket={props.onMarket} />
        <section className="pipeline-bar"><div><span className={`status-dot ${running ? 'pending' : ''}`} /><strong>{running ? job?.stage ?? '正在提交任务…' : job?.status === 'failed' ? '本次任务失败' : '真实新闻研究'}</strong><small>{running ? '结果分阶段更新，可先看行情和新闻' : `最近保存：${dateTime(data.as_of)}`}</small></div><div className="pipeline-options"><select aria-label="新闻检索区间" value={days} onChange={e => setDays(+e.target.value)} disabled={running}><option value={7}>近 7 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option></select><label><input type="checkbox" checked={includeCommunity} onChange={e => setIncludeCommunity(e.target.checked)} disabled={running} />含社区样本</label><button className="primary-button" disabled={running} onClick={() => collect(days, includeCommunity)}>{running ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}{running ? '正在处理' : '采集并分析'}</button></div></section>
        {stages && <div className="collection-stages" role="status" aria-live="polite">{[{ key: 'market', label: 'K 线行情' }, { key: 'news', label: '清洗新闻' }, { key: 'industry', label: '行业检索' }, { key: 'analysis', label: 'AI 研判' }].filter(section => stages[section.key]).map(section => <span key={section.key} className={`collection-stage ${stages[section.key]}`}><span className="status-dot" />{section.label}<strong>{stages[section.key] === 'ready' || stages[section.key] === 'completed' ? '已就绪' : stages[section.key] === 'failed' ? '更新失败' : stages[section.key] === 'running' ? '生成中' : '获取中'}</strong>{section.key === 'analysis' && !!data.pipeline?.counts.analysis_total && <small>{data.pipeline.counts.analysis_finished ?? 0}/{data.pipeline.counts.analysis_total}</small>}</span>)}</div>}
        {job?.warnings.length ? <div className="pipeline-warning" role="status">本次任务：{job.warnings.join('；')}</div> : null}
        {data.pipeline && <div className="pipeline-summary"><span>检索 {data.pipeline.counts.input}</span><ChevronRight size={12} /><span>保留 {data.pipeline.counts.retained}</span><ChevronRight size={12} /><span>合并 {data.pipeline.counts.merged}</span><ChevronRight size={12} /><span>本轮研判 {data.pipeline.counts.analyzed}</span><button disabled={auditLoading || !data.pipeline.collection_id} onClick={audit}>{auditLoading ? '读取中…' : '查看清洗记录'}</button></div>}
        {!!data.pipeline?.warnings.length && !job && <details className="pipeline-warning"><summary>数据采集存在 {data.pipeline.warnings.length} 项限制</summary>{data.pipeline.warnings.map(item => <p key={item}>{item}</p>)}</details>}
        <NewsFeed data={data} openAssistant={openAssistant} analyzeAll={() => collect(days, false, false, true)} running={running} stage={job?.stage} />
        <section className="panel returns-panel"><header><h2>新闻后的历史走势</h2><span>事后观察</span></header>{data.backtest.items.length ? <div className="returns-table"><table><thead><tr><th>新闻</th><th>基准日</th><th>3 交易日</th><th>5 交易日</th></tr></thead><tbody>{data.backtest.items.slice(0, 8).map(item => <tr key={item.news_id}><td>{item.title}</td><td>{item.base_date ?? '待有日线'}</td><td className={tone(item.return_3d)}>{percent(item.return_3d)}</td><td className={tone(item.return_5d)}>{percent(item.return_5d)}</td></tr>)}</tbody></table></div> : <p className="returns-note">保存新闻与日线后，这里会展示可计算的后续价格变化。</p>}<p className="returns-note">{data.backtest.note}</p></section>
      </>}
    </div>
    <aside className="research-right-rail"><ResearchOverview data={data} openAssistant={openAssistant} /><CommunityCard data={data?.sentiment} running={running} refresh={() => collect(days, true, true)} /><section className="research-assistant-card"><Sparkles size={22} /><span className="eyebrow">ASK A BETTER QUESTION</span><h2>把消息，放回上下文。</h2><p>让 AI 解释事实、推断和待核实条件，继续追问新闻可能的影响。</p><button disabled={!data} onClick={() => openAssistant()}>打开研究助手<ArrowRight size={15} /></button></section><button className="briefing-rail-link" onClick={briefing}><Bell size={18} /><span><strong>自选股早报</strong><small>查看汇总与订阅设置</small></span><ChevronRight size={15} /></button></aside>
  </div>;
}
