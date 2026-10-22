import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronDown, Download, ExternalLink, LoaderCircle, Plus, RefreshCw, Search } from 'lucide-react';
import { api } from './api';
import type { Stock } from './types';
import './peg-leaderboard.css';

type Row = { code: string; name: string; industry: string; group: string; status: string; price: number | null; peg: number | null; forward_pe: number | null; growth: number | null; pe_ttm: number | null; market_cap: number | null; rank: number | null; previous_rank: number | null; rank_change: number | null; rank_status: string; confidence: string; risk_tags: string[]; institutions: number | null; forecast_start: string | null; forecast_end: string | null; collected_at: string | null; stress_peg: number | null; divergence: number | null };
type Snapshot = { status: 'ready' | 'updating' | 'empty' | 'stale'; snapshot_id: string | null; published_at: string | null; as_of_date: string | null; collection_started_at: string | null; collection_finished_at: string | null; market_date: string | null; summary: { total: number; eligible: number; observing: number; not_applicable: number; missing: number; failed: number }; schedule: { time: string; timezone: string; enabled: boolean; next_run: string | null }; last_attempt: { status: string; started_at: string; finished_at?: string; error?: string; completed?: number; total?: number } | null; rows: Row[] };
type Props = { onOpenStock: (code: string) => Promise<void> | void; onAddStock: (stock: Stock) => Promise<boolean> | void; watchlist: string[]; savingWatch: boolean };
let cached: Snapshot | null = null;
const n = (value: number | null, digits = 2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
const at = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
const statuses = ['合格', '观察', '不适用', '数据缺失', '采集失败'];
const stock = (r: Row): Stock => ({ code: r.code, name: r.name, industry: r.industry, initials: '', exchange: r.code.startsWith('6') ? 'SH' : 'SZ', price: r.price, change: null });

function Change({ row }: { row: Row }) {
  if (row.rank_change != null && row.rank_change !== 0) return <span className={row.rank_change > 0 ? 'peg-rise' : 'peg-fall'}>{row.rank_change > 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}{Math.abs(row.rank_change)}</span>;
  const labels: Record<string, string> = { initial: '首次发布', new: '新增成分', entered: '新入榜', exited: '退出排名', group_changed: '分组变化', universe_changed: '样本变化' };
  return <span className="peg-muted">{labels[row.rank_status] ?? '—'}</span>;
}

export default function PegLeaderboard({ onOpenStock, onAddStock, watchlist, savingWatch }: Props) {
  const [data, setData] = useState<Snapshot | null>(cached);
  const [error, setError] = useState(''); const [loading, setLoading] = useState(!cached);
  const [group, setGroup] = useState('非金融'); const [status, setStatus] = useState('合格');
  const [query, setQuery] = useState(''); const [industry, setIndustry] = useState(''); const [confidence, setConfidence] = useState('');
  const [page, setPage] = useState(1); const [expanded, setExpanded] = useState<string | null>(null); const [busy, setBusy] = useState('');
  const controller = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort; setLoading(true);
    try { const next = await api<Snapshot>('/api/screener/csi300', undefined, abort.signal); if (!abort.signal.aborted) { cached = next; setData(next); setError(''); } }
    catch (e) { if (!abort.signal.aborted) setError((e as Error).message); }
    finally { if (!abort.signal.aborted) setLoading(false); }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 60000); return () => { window.clearInterval(timer); controller.current?.abort(); }; }, [load]);
  useEffect(() => setPage(1), [group, status, query, industry, confidence]);
  const industries = useMemo(() => [...new Set((data?.rows ?? []).map(r => r.industry))].sort((a, b) => a.localeCompare(b, 'zh-CN')), [data?.snapshot_id]);
  const filtered = useMemo(() => (data?.rows ?? []).filter(r => (!group || r.group === group) && (!status || r.status === status) && (!industry || r.industry === industry) && (!confidence || r.confidence === confidence) && (!query.trim() || `${r.code} ${r.name}`.toLowerCase().includes(query.trim().toLowerCase()))), [data, group, status, industry, confidence, query]);
  const pages = Math.max(1, Math.ceil(filtered.length / 25)); const current = Math.min(page, pages); const visible = filtered.slice((current - 1) * 25, current * 25);
  async function open(r: Row) { setBusy(r.code); try { await onOpenStock(r.code); } catch (e) { setError((e as Error).message); } finally { setBusy(''); } }
  async function add(r: Row) { setBusy(r.code); try { await onAddStock(stock(r)); } catch (e) { setError((e as Error).message); } finally { setBusy(''); } }
  const summary = data?.summary;
  return <section className="peg-page" aria-label="沪深300低PEG榜">
    <header className="peg-header"><div><p className="peg-eyebrow">STOCK ANALYZER / DAILY SCREEN</p><h2>沪深300 <span>低 PEG 榜</span></h2><p className="peg-muted">合格样本按前瞻 PEG 升序 · 金融与非金融分别排名</p></div><div className="peg-actions"><a href="/api/screener/csi300/export.csv" className="peg-button" download><Download size={15} />导出表格</a><button className="peg-button" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={15} />刷新榜单</button></div></header>
    <section className="peg-intro" aria-labelledby="peg-intro-title">
      <div className="peg-intro-heading"><div><h3 id="peg-intro-title">先了解 PEG</h3><p>把估值和增长放在一起看，衡量为预期盈利增长支付的价格。</p></div><a className="peg-link" href="https://www.fidelity.com/learning-center/trading-investing/fundamental-analysis/company-valuation-ratios" target="_blank" rel="noopener noreferrer">指标参考 <ExternalLink size={12} /></a></div>
      <div className="peg-intro-grid">
        <div><h4>怎么算</h4><p><strong>PEG = 市盈率（PE）÷ 盈利增长率</strong></p><p>增长率按百分数取值：PE 为 20、年增长率为 20%，PEG 就是 20 ÷ 20 = 1。</p></div>
        <div><h4>怎么看</h4><p>同类公司、相近预测口径下，正 PEG 越低，意味着相对预期增长的估值越低。<strong>1 是常见参考，并非低估或高估的固定分界线。</strong></p></div>
        <div><h4>本榜口径</h4><p>使用前瞻 PE ÷ 预测净利润年复合增长率（%），金融与非金融分别排名。结合可信度、风险标签和预测时间一起看。</p></div>
      </div>
      <p className="peg-intro-note">盈利预测可能偏离实际，低 PEG 不代表股价一定上涨。</p>
    </section>
    <div className="peg-timing"><span><i className={`status-dot ${data?.status === 'updating' ? 'pending' : ''}`} />每日 {data?.schedule.time ?? '21:00'} 更新 · 北京时间{data && !data.schedule.enabled ? ' · 自动更新未启用' : ''}</span><span>上次发布：{at(data?.published_at)}</span><span>下次更新：{at(data?.schedule.next_run)}</span></div>
    <div className="peg-summary">{[['成分股', summary?.total], ['合格入榜', summary?.eligible], ['观察', summary?.observing], ['不适用', summary?.not_applicable], ['缺失 / 采集失败', (summary?.missing ?? 0) + (summary?.failed ?? 0)]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value ?? '—'}</strong></div>)}</div>
    {error && <p className="peg-notice peg-warning" role="alert">{error}{data?.snapshot_id ? ' · 已保留上次读取的榜单。' : ''}</p>}
    {data?.last_attempt?.error && <p className="peg-notice peg-warning" role="status">{data.last_attempt.error} · 尝试时间 {at(data.last_attempt.started_at)}</p>}
    {data?.status === 'updating' && <p className="peg-notice" role="status"><LoaderCircle size={15} className="spin" />正在生成新一期榜单：已采集 {data.last_attempt?.completed ?? 0} / {data.last_attempt?.total ?? 300}。完成后整榜更新。</p>}
    <div className="peg-controls"><div className="peg-tabs" aria-label="比较组">{['非金融', '金融', ''].map(g => <button key={g} className={group === g ? 'active' : ''} onClick={() => setGroup(g)}>{g || '全部'}</button>)}</div><label className="peg-search"><Search size={15} /><input aria-label="搜索榜单股票" placeholder="搜索名称 / 代码" value={query} onChange={e => setQuery(e.target.value)} /></label><select aria-label="筛选状态" value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option>{statuses.map(s => <option key={s}>{s}</option>)}</select><select aria-label="行业筛选" value={industry} onChange={e => setIndustry(e.target.value)}><option value="">全部行业</option>{industries.map(s => <option key={s}>{s}</option>)}</select><select aria-label="可信度筛选" value={confidence} onChange={e => setConfidence(e.target.value)}><option value="">全部可信度</option>{['高', '中', '低'].map(s => <option key={s}>{s}</option>)}</select></div>
    <div className="peg-table-wrap"><table className="peg-table"><thead><tr>{['组内名次', '股票', '最新价', '前瞻 PEG', '较上一期', '行业', '可信度', '风险 / 状态', '操作'].map(t => <th key={t}>{t}</th>)}</tr></thead><tbody>{visible.map(r => <Fragment key={r.code}><tr><td className="peg-rank">{r.rank ?? '—'}</td><td><button className="peg-stock" onClick={() => void open(r)} disabled={busy === r.code}><strong>{r.name}</strong><small>{r.code} · {r.group}</small></button></td><td className="mono">{n(r.price)}</td><td className="peg-value mono">{n(r.peg)}</td><td><Change row={r} /></td><td>{r.industry}</td><td><span className={`peg-confidence ${r.confidence === '高' ? 'high' : ''}`}>{r.confidence || '—'}</span></td><td><span className="peg-risk" title={r.risk_tags.join('；')}>{r.status !== '合格' ? r.status : r.risk_tags[0] || '—'}{r.risk_tags.length > 1 && ` +${r.risk_tags.length - 1}`}</span></td><td><div className="peg-row-actions"><button className="peg-button" disabled={savingWatch || watchlist.includes(r.code) || busy === r.code} onClick={() => void add(r)}><Plus size={13} />{watchlist.includes(r.code) ? '已自选' : '自选'}</button><button className="peg-button" aria-label={`${r.name}指标详情`} aria-expanded={expanded === r.code} onClick={() => setExpanded(expanded === r.code ? null : r.code)}><ChevronDown size={14} /></button></div></td></tr>{expanded === r.code && <tr className="peg-expanded"><td colSpan={9}><div className="peg-details">{[['前瞻 PE', n(r.forward_pe)], ['预测增长率', `${n(r.growth)}%`], ['PE(TTM)', n(r.pe_ttm)], ['总市值', `${n(r.market_cap)} 亿`], ['压力 PEG', n(r.stress_peg)], ['覆盖机构', n(r.institutions, 0)], ['预测分歧度', `${n(r.divergence)}%`], ['行情采集时间', at(r.collected_at)], ['预测报告截止', r.forecast_end?.slice(0, 10) ?? '—']].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><p className="peg-detail-risks">{r.risk_tags.join(' · ') || '未记录风险标签'}</p><button className="peg-link" onClick={() => void open(r)}>查看行情与公司消息 <ExternalLink size={13} /></button></td></tr>}</Fragment>)}{!visible.length && <tr><td colSpan={9} className="peg-empty">{loading && !data ? '正在读取已发布榜单…' : !data?.snapshot_id ? '首期榜单尚未发布。每日 21:00 更新，首次采集完成后在这里展示。' : '没有符合当前筛选条件的股票。'}</td></tr>}</tbody></table></div>
    <div className="peg-pagination"><span>共 {filtered.length} 条 · 每页 25 条</span><div><button className="peg-button" disabled={current <= 1} onClick={() => setPage(current - 1)}>上一页</button><span>{current} / {pages}</span><button className="peg-button" disabled={current >= pages} onClick={() => setPage(current + 1)}>下一页</button></div></div>
    <details className="peg-method"><summary>计算方法与数据说明</summary><p>来自 Stock-Analyzer。仅对有效正前瞻 PEG 的合格样本排名，金融与非金融各自比较；PEG 相同按股票代码排序。可信度描述数据质量，风险标签独立展示。观察、不适用、缺失与采集失败项保留原因并不参与排名。</p><p>行情来自本次采集批次，预测报告有各自截止日期。日历对应最近交易日：{data?.market_date ?? '未知'}；逐股源未提供成交时间，停牌股可能沿用更早价格，请结合采集时间和个股行情核验。排名是估值筛选结果，不是收益预测。</p><a className="peg-link" href="https://github.com/hus-king/Stock-Analyzer" target="_blank" rel="noopener noreferrer">查看项目与完整方法 <ExternalLink size={13} /></a></details>
  </section>;
}
