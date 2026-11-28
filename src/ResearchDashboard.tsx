import { displayCode, isIndex } from './instruments';
import { useEffect, useRef, useState } from 'react';
import { ArrowRight, LoaderCircle, Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import QuotePanel from './QuotePanel';
import NewsFeed from './NewsFeed';
import ResearchOverview from './ResearchOverview';
import CommunityCard from './CommunityCard';
import NewsReplayPanel from './NewsReplayPanel';
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
  const { data, loading, catalog, snapshots, watchlist, savingWatch, code, setCode, userSignedIn, add, remove, running, job, jobs = {}, batchAnalyze, collect, audit, auditLoading, openAssistant } = props;
  const [days, setDays] = useState(30);
  const [ticks, setTicks] = useState<Record<string, 'up' | 'down'>>({});
  const prevPrices = useRef<Record<string, number>>({});
  const indexView = !!data && isIndex(data.stock);
  const researchCodes = watchlist.filter(c => !catalog.find(stock => stock.code === c && isIndex(stock)));
  const stages = data?.pipeline?.stages;
  const limitations = [...new Set([...(data?.pipeline?.warnings ?? []), ...(job?.warnings ?? [])])];
  const activeWatchlistJobs = researchCodes.map(c => jobs[c]).filter(j => j && ['queued', 'running'].includes(j.status));
  const isBatchActive = activeWatchlistJobs.length > 0;
  const completedCount = researchCodes.length - activeWatchlistJobs.length;

  useEffect(() => {
    const nextTicks: Record<string, 'up' | 'down'> = {};
    let hasTick = false;
    for (const stockCode of watchlist) {
      const stock = catalog.find(row => row.code === stockCode);
      const snapshot = snapshots[stockCode];
      const isSelected = code === stockCode;
      const currentPrice = (isSelected && data?.stock.price != null ? data.stock.price : snapshot?.stock.price) ?? stock?.price;
      const oldPrice = prevPrices.current[stockCode];
      if (currentPrice != null && oldPrice != null && currentPrice !== oldPrice) {
        nextTicks[stockCode] = currentPrice > oldPrice ? 'up' : 'down';
        hasTick = true;
      }
      if (currentPrice != null) {
        prevPrices.current[stockCode] = currentPrice;
      }
    }
    if (hasTick) {
      setTicks(prev => ({ ...prev, ...nextTicks }));
      const timer = window.setTimeout(() => {
        setTicks(prev => {
          const cleaned = { ...prev };
          for (const k of Object.keys(nextTicks)) delete cleaned[k];
          return cleaned;
        });
      }, 700);
      return () => window.clearTimeout(timer);
    }
  }, [watchlist, snapshots, catalog, data, code]);

  return <div className="finance-layout" role="region" aria-label={indexView ? '指数行情' : '个股行情与新闻'}>
    <aside className="portfolio-rail">
      <section className="panel portfolio-panel">
        <header>
          <div>
            <h2>我的自选</h2>
            <p className="rail-caption">{userSignedIn ? `${watchlist.length} 个关注标的` : '登录后云端同步'}</p>
          </div>
          <div className="portfolio-header-actions">
            <button className={`portfolio-batch-btn ${isBatchActive ? 'running' : ''}`} disabled={isBatchActive || researchCodes.length === 0} onClick={() => batchAnalyze?.(false)} title="一键智能研判所有自选股" aria-label="一键智能研判所有自选股">
              {isBatchActive ? <LoaderCircle className="spin" size={12} /> : <Sparkles size={12} />}
              <span>{isBatchActive ? `${completedCount}/${researchCodes.length}` : '全部研判'}</span>
            </button>
            <button aria-label="添加自选股" title="添加或管理自选股" className="icon-button" onClick={add}><Plus size={16} /></button>
          </div>
        </header>
        <div className="portfolio-list" role="region" aria-label="自选股列表" tabIndex={0}>
          {watchlist.map(stockCode => {
            const stock = catalog.find(row => row.code === stockCode);
            const snapshot = snapshots[stockCode];
            const stockJob = jobs[stockCode];
            const isStockActive = stockJob && ['queued', 'running'].includes(stockJob.status);
            const isSelected = code === stockCode;
            const displayPrice = (isSelected && data?.stock.price != null ? data.stock.price : snapshot?.stock.price) ?? stock?.price;
            const displayChange = (isSelected && data?.stock.change != null ? data.stock.change : snapshot?.stock.change) ?? stock?.change;
            const tick = ticks[stockCode];
            return stock && <div key={stockCode} className={`portfolio-row ${isSelected ? 'active' : ''} ${tick ? `tick-${tick}` : ''}`}>
              <button onClick={() => setCode(stockCode)}>
                <span>
                  <strong>{stock.name}</strong>
                  <small>{stock.exchange} {displayCode(stock)}{isIndex(stock) ? ' · 指数' : ''}</small>
                  {isStockActive && <span className="stock-job-tag" title={stockJob.stage || '研判中'}><LoaderCircle className="spin" size={10} /><span>{stockJob.stage ? (stockJob.stage.length > 6 ? stockJob.stage.slice(0, 6) + '…' : stockJob.stage) : '研判中'}</span></span>}
                </span>
                <span>
                  <strong className={`mono ${tick ? `price-tick-${tick}` : ''}`}>{number(displayPrice)}</strong>
                  <small className={`mono ${tone(displayChange ?? null)}`}>{percent(displayChange)}</small>
                </span>
              </button>
              <button className="remove-stock" aria-label={`移除${stock.name}`} title={`移除${stock.name}`} disabled={savingWatch} onClick={() => remove(stockCode)}><Trash2 size={12} /></button>
            </div>;
          })}
          {watchlist.length === 0 && <p className="rail-empty">还没有自选股，点击右上角加号或按 ⌘K 添加。</p>}
        </div>
      </section>
    </aside>

    <div className="research-main-column">
      {loading && <div className="panel empty-card"><LoaderCircle className="spin" size={24} /><p>正在读取已保存的数据…</p></div>}
      {data && <>
        <QuotePanel data={data} add={add} running={running} onMarket={props.onMarket} />
        {!indexView && <>
          <NewsFeed
            data={data}
            openAssistant={openAssistant}
            analyzeAll={() => collect(days, false, false, true)}
            running={running}
            stage={job?.stage}
            headerControls={<div className="pipeline-options">
              <select aria-label="新闻检索区间" value={days} onChange={e => setDays(+e.target.value)} disabled={running}>
                <option value={7}>近 7 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option>
              </select>
              <button className="research-refresh" disabled={running} onClick={() => collect(days, false)}>
                {running ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}{running ? '更新中' : '更新研究'}
              </button>
            </div>}
            statusBanner={<div className="research-update" aria-label="新闻研究更新">
              {running && stages && <div className="research-running" role="status" aria-live="polite">{[{ key: 'news', label: '新闻' }, { key: 'industry', label: '行业资料' }, { key: 'analysis', label: '研判' }].filter(section => stages[section.key]).map(section => <span key={section.key}>{section.label}<strong>{stages[section.key] === 'ready' || stages[section.key] === 'completed' ? '完成' : stages[section.key] === 'failed' ? '更新失败' : '更新中'}</strong>{section.key === 'analysis' && !!data.pipeline?.counts.analysis_total && <small>{data.pipeline.counts.analysis_finished ?? 0}/{data.pipeline.counts.analysis_total}</small>}</span>)}</div>}
              {job?.status === 'failed' && <p className="research-task-error" role="alert">本次研究更新未完成，可重新更新。</p>}
              <div className="research-metadata">
                <span>{running ? job?.stage ?? '正在更新研究材料' : `更新于 ${dateTime(data.as_of)}`} · {data.pipeline?.counts.retained ?? data.news.length} 条有效新闻</span>
                <details className="research-details">
                  <summary>{limitations.length ? `采集说明 (${limitations.length})` : '采集说明'}</summary>
                  <div>
                    {data.pipeline && <><dl><div><dt>检索材料</dt><dd>{data.pipeline.counts.input}</dd></div><div><dt>有效新闻</dt><dd>{data.pipeline.counts.retained}</dd></div><div><dt>合并重复</dt><dd>{data.pipeline.counts.merged}</dd></div><div><dt>复用研判</dt><dd>{data.pipeline.counts.analysis_reused ?? 0}</dd></div><div><dt>本轮研判</dt><dd>{data.pipeline.counts.analyzed}</dd></div></dl><button disabled={auditLoading || !data.pipeline.collection_id} onClick={audit}>{auditLoading ? '正在读取' : '查看清洗记录'}</button></>}
                    {limitations.map(item => <p key={item}>{item}</p>)}
                    <p>自动巡检时间：08:30、12:00、20:00</p>
                  </div>
                </details>
              </div>
            </div>}
          />
          <NewsReplayPanel key={data.stock.code} data={data} openAssistant={openAssistant} />
        </>}
        {indexView && <section className="panel index-notice"><h2>大盘指数行情</h2><p>指数点位用于观察市场整体或板块走势。交易日优先展示当日分时，休市显示最近交易日；可在图表上方切换日 K、周 K。</p><p>指数成交量保留行情源原始单位，不能按个股股数比较。</p></section>}
      </>}
    </div>

    <aside className="research-right-rail">
      {indexView ? <section className="panel index-notice"><h2>关注市场走势</h2><p>在搜索中切换到「指数」，可查看沪深300、中证500、中证1000、科创50、创业板指等，并加入自选。</p><p>指数跟踪提供行情图表，不生成公司新闻研判或社区个股评分。模拟盘继续使用 A 股选股。</p></section> : <>
        <ResearchOverview data={data} openAssistant={openAssistant} running={running} />
        <CommunityCard data={data?.sentiment} running={running} refresh={() => collect(days, true, true)} />
        <section className="research-assistant-card">
          <div className="assistant-card-head"><Sparkles size={18} /><h2>AI 研究助手</h2></div>
          <p>结合原文解释事实、因果链路与待核实条件，针对当前个股继续追问。</p>
          <button disabled={!data} onClick={() => openAssistant()}>打开对话<ArrowRight size={14} /></button>
        </section>
      </>}
    </aside>
  </div>;
}
