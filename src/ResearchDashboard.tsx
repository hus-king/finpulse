import { displayCode, isIndex } from './instruments';
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
  running: boolean; job: Job | null; jobs?: Record<string, Job>; batchAnalyze?: (analysisOnly?: boolean) => void;
  collect: (days: number, community: boolean, communityOnly?: boolean, analysisOnly?: boolean) => void; audit: () => void; auditLoading: boolean;
  openAssistant: (news?: News) => void; briefing: () => void;
  onMarket: (data: Dashboard) => void;
}

export default function ResearchDashboard(props: Props) {
  const { data, loading, catalog, snapshots, watchlist, savingWatch, code, setCode, userSignedIn, add, remove, running, job, jobs = {}, batchAnalyze, collect, audit, auditLoading, openAssistant, briefing } = props;
  const [days, setDays] = useState(30);
  const indexView = !!data && isIndex(data.stock);
  const researchCodes = watchlist.filter(c => !catalog.find(stock => stock.code === c && isIndex(stock)));
  const stages = data?.pipeline?.stages;
  const limitations = [...new Set([...(data?.pipeline?.warnings ?? []), ...(job?.warnings ?? [])])];
  const activeWatchlistJobs = researchCodes.map(c => jobs[c]).filter(j => j && ['queued', 'running'].includes(j.status));
  const isBatchActive = activeWatchlistJobs.length > 0;
  const completedCount = researchCodes.length - activeWatchlistJobs.length;
  const openEvent = useCallback((id: string) => { const news = data?.news.find(row => row.id === id); if (news) openAssistant(news); }, [data, openAssistant]);
  return <div className="finance-layout" role="region" aria-label={indexView ? '指数行情' : '个股行情与新闻'}>
    <aside className="portfolio-rail"><section className="panel portfolio-panel"><header><h2>我的自选</h2><div className="portfolio-header-actions"><button className={`portfolio-batch-btn ${isBatchActive ? 'running' : ''}`} disabled={isBatchActive || researchCodes.length === 0} onClick={() => batchAnalyze?.(false)} title="一键智能研判所有自选股" aria-label="一键智能研判所有自选股">{isBatchActive ? <LoaderCircle className="spin" size={12} /> : <Sparkles size={12} />}<span>{isBatchActive ? `${completedCount}/${researchCodes.length}` : '全部研判'}</span></button><button aria-label="添加自选股" className="icon-button" onClick={add}><Plus size={17} /></button></div></header><p className="rail-caption">{userSignedIn ? '保存在你的账号中' : '登录后保存自选股'}</p>{watchlist.map(stockCode => { const stock = catalog.find(row => row.code === stockCode); const snapshot = snapshots[stockCode]; const stockJob = jobs[stockCode]; const isStockActive = stockJob && ['queued', 'running'].includes(stockJob.status); const isSelected = code === stockCode; const displayPrice = (isSelected && data?.stock.price != null ? data.stock.price : snapshot?.stock.price) ?? stock?.price; const displayChange = (isSelected && data?.stock.change != null ? data.stock.change : snapshot?.stock.change) ?? stock?.change; return stock && <div key={stockCode} className={`portfolio-row ${isSelected ? 'active' : ''}`}><button onClick={() => setCode(stockCode)}><span><strong>{stock.name}</strong><small>{stock.exchange} {displayCode(stock)}{isIndex(stock) ? ' · 指数' : ''}</small>{isStockActive && <span className="stock-job-tag" title={stockJob.stage || '研判中'}><LoaderCircle className="spin" size={10} /><span>{stockJob.stage ? (stockJob.stage.length > 6 ? stockJob.stage.slice(0, 6) + '…' : stockJob.stage) : '研判中'}</span></span>}</span><span><strong className="mono">{number(displayPrice)}</strong><small className={tone(displayChange ?? null)}>{percent(displayChange)}</small></span></button><button className="remove-stock" aria-label={`移除${stock.name}`} disabled={savingWatch} onClick={() => remove(stockCode)}><Trash2 size={12} /></button></div>; })}{watchlist.length === 0 && <p className="rail-empty">还没有自选股，点击加号添加。</p>}<button className="add-watch" onClick={add}><Plus size={14} />管理关注标的</button></section><section className="rail-note"><ShieldCheck size={17} /><h3>研究有据可查</h3><p>新闻保留原文和清洗记录。行情展示数据日期，模型研判保留推断局限。</p><button onClick={briefing}>查看自选股早报<ArrowRight size={14} /></button></section></aside>
    <div className="research-main-column">
      {loading && <div className="panel empty-card"><LoaderCircle className="spin" size={25} /><p>正在读取已保存的数据…</p></div>}
      {data && <>
        <QuotePanel data={data} add={add} running={running} onEvent={openEvent} onMarket={props.onMarket} />
        {!indexView && <>
        <section className="research-update" aria-label="新闻研究更新">
          <div className="research-update-heading"><div><h2>新闻研究</h2><p>{running ? job?.stage ?? '正在更新研究材料' : `更新于 ${dateTime(data.as_of)}`}</p></div><div className="pipeline-options"><select aria-label="新闻检索区间" value={days} onChange={e => setDays(+e.target.value)} disabled={running}><option value={7}>近 7 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option></select><button className="research-refresh" disabled={running} onClick={() => collect(days, false)}>{running ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}{running ? '更新中' : '更新研究'}</button></div></div>
          {running && stages && <div className="research-running" role="status" aria-live="polite">{[{ key: 'news', label: '新闻' }, { key: 'industry', label: '行业资料' }, { key: 'analysis', label: '研判' }].filter(section => stages[section.key]).map(section => <span key={section.key}>{section.label}<strong>{stages[section.key] === 'ready' || stages[section.key] === 'completed' ? '完成' : stages[section.key] === 'failed' ? '更新失败' : '更新中'}</strong>{section.key === 'analysis' && !!data.pipeline?.counts.analysis_total && <small>{data.pipeline.counts.analysis_finished ?? 0}/{data.pipeline.counts.analysis_total}</small>}</span>)}</div>}
          {job?.status === 'failed' && <p className="research-task-error" role="alert">本次研究更新未完成，可重新更新。</p>}
          <div className="research-metadata"><span>{data.pipeline?.counts.retained ?? data.news.length} 条有效新闻</span><details className="research-details"><summary>{limitations.length ? `采集说明 (${limitations.length})` : '采集说明'}</summary><div>{data.pipeline && <><dl><div><dt>检索材料</dt><dd>{data.pipeline.counts.input}</dd></div><div><dt>有效新闻</dt><dd>{data.pipeline.counts.retained}</dd></div><div><dt>合并重复</dt><dd>{data.pipeline.counts.merged}</dd></div><div><dt>复用研判</dt><dd>{data.pipeline.counts.analysis_reused ?? 0}</dd></div><div><dt>本轮研判</dt><dd>{data.pipeline.counts.analyzed}</dd></div></dl><button disabled={auditLoading || !data.pipeline.collection_id} onClick={audit}>{auditLoading ? '正在读取' : '查看清洗记录'}</button></>}{limitations.map(item => <p key={item}>{item}</p>)}<p>自动巡检时间：08:30、12:00、20:00</p></div></details></div>
        </section>
        <NewsFeed data={data} openAssistant={openAssistant} analyzeAll={() => collect(days, false, false, true)} running={running} stage={job?.stage} />
        <section className="panel returns-panel"><header><h2>新闻后的历史走势</h2><span>事后观察</span></header>{data.backtest.items.length ? <div className="returns-table"><table><thead><tr><th>新闻</th><th>基准日</th><th>3 交易日</th><th>5 交易日</th></tr></thead><tbody>{data.backtest.items.slice(0, 8).map(item => <tr key={item.news_id}><td>{item.title}</td><td>{item.base_date ?? '待有日线'}</td><td className={tone(item.return_3d)}>{percent(item.return_3d)}</td><td className={tone(item.return_5d)}>{percent(item.return_5d)}</td></tr>)}</tbody></table></div> : <p className="returns-note">保存新闻与日线后，这里会展示可计算的后续价格变化。</p>}<p className="returns-note">{data.backtest.note}</p></section>
        </>}
        {indexView && <section className="panel index-notice"><h2>大盘指数行情</h2><p>指数点位用于观察市场整体或板块走势。交易日优先展示当日分时，休市显示最近交易日；可在图表上方切换日 K、周 K。</p><p>指数成交量保留行情源原始单位，不能按个股股数比较。</p></section>}
      </>}
    </div>
    <aside className="research-right-rail">{indexView ? <section className="panel index-notice"><span className="eyebrow">MARKET INDEX</span><h2>关注市场走势</h2><p>在搜索中切换到「指数」，可查看沪深300、中证500、中证1000、科创50、创业板指等，并加入自选。</p><p>指数跟踪提供行情图表，不生成公司新闻研判或社区个股评分。模拟盘继续使用 A 股选股。</p></section> : <><ResearchOverview data={data} openAssistant={openAssistant} running={running} /><CommunityCard data={data?.sentiment} running={running} refresh={() => collect(days, true, true)} /><section className="research-assistant-card"><Sparkles size={22} /><span className="eyebrow">ASK A BETTER QUESTION</span><h2>把消息，放回上下文。</h2><p>让 AI 解释事实、推断和待核实条件，继续追问新闻可能的影响。</p><button disabled={!data} onClick={() => openAssistant()}>打开研究助手<ArrowRight size={15} /></button></section><button className="briefing-rail-link" onClick={briefing}><Bell size={18} /><span><strong>自选股早报</strong><small>查看汇总与订阅设置</small></span><ChevronRight size={15} /></button></>}</aside>
  </div>;
}
