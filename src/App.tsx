import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ExternalLink, FlaskConical, LoaderCircle, Search, X } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import AccountMenu from './AccountMenu';
import AdminPanel from './AdminPanel';
import AiDrawer from './AiDrawer';
import ResearchDashboard from './ResearchDashboard';
import BriefingPanel from './BriefingPanel';
import useDialogScroll from './useDialogScroll';
import StockSearch from './StockSearch';
import type { Dashboard, Health, Job, ModelReply, Stock } from './types';
import { number, percent, tone } from './format';

const defaults = ['600519', '300750', '688981'];

// A slow initial/cache read must not replace a newer staged result.
const newerSnapshot = (previous: Dashboard | null | undefined, next: Dashboard) => previous?.stock.code === next.stock.code && previous.as_of && (!next.as_of || previous.as_of > next.as_of) ? previous : next;

function Dialog({ title, children, close }: { title: string; children: React.ReactNode; close: () => void }) {
  useDialogScroll();
  useEffect(() => {
    const listener = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [close]);
  return <div className="modal-overlay" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}><section className="research-modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button autoFocus className="icon-button" aria-label="关闭弹窗" onClick={close}><X size={20} /></button></header>{children}</section></div>;
}

export default function App() {
  const { user, loading: authLoading, requireAuth, sessionError } = useAuth();
  const [view, setView] = useState('dashboard');
  const [accountOpen, setAccountOpen] = useState(false);
  const [catalog, setCatalog] = useState<Stock[]>([]);
  const [watchlist, setWatchlist] = useState<string[]>(defaults);
  const [watchReady, setWatchReady] = useState(false);
  const [savingWatch, setSavingWatch] = useState(false);
  const [code, setCode] = useState('600519');
  const [data, setData] = useState<Dashboard | null>(null);
  const [snapshots, setSnapshots] = useState<Record<string, Dashboard>>({});
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [drawer, setDrawer] = useState<{ stock: Stock; news?: Dashboard['news'][number] } | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [collecting, setCollecting] = useState<string | null>(null);
  const [lastReply, setLastReply] = useState<ModelReply | null>(null);
  const [testing, setTesting] = useState(false);
  const [audit, setAudit] = useState<{ summary: Record<string, number>; audit: { id: string; title: string; status: string; reasons: string[]; url: string }[] } | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const userId = useRef(user?.id);
  const controller = useRef<AbortController | null>(null);
  const snapshotsRef = useRef(snapshots);
  snapshotsRef.current = snapshots;
  userId.current = user?.id;
  const mergeCatalog = useCallback((items: Stock[]) => setCatalog(previous => Array.from(new Map([...previous, ...items].map(stock => [stock.code, stock])).values())), []);

  useEffect(() => {
    const abort = new AbortController();
    Promise.all([api<{ items: Stock[] }>('/api/stocks?codes=600519,300750,688981,000001,002594', undefined, abort.signal), api<Health>('/api/health', undefined, abort.signal)])
      .then(([stocks, status]) => { if (!abort.signal.aborted) { mergeCatalog(stocks.items); setHealth(status); } })
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => abort.abort();
  }, [mergeCatalog]);

  useEffect(() => {
    if (authLoading) return;
    const abort = new AbortController();
    controller.current?.abort(); setJobs({}); setCollecting(null); setTesting(false); setDrawer(null); setLastReply(null); setAccountOpen(false); setAudit(null); setWatchReady(false); setWatchlist(defaults);
    if (user?.role !== 'admin') setView(previous => previous === 'admin' ? 'dashboard' : previous);
    if (user) {
      api<{ codes: string[]; items: Stock[]; jobs: Job[] }>('/api/watchlist', undefined, abort.signal).then(result => { mergeCatalog(result.items); setWatchlist(result.codes); setJobs(Object.fromEntries(result.jobs.map(job => [job.code, job]))); setWatchReady(true); }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    } else { setWatchReady(true); }
    return () => abort.abort();
  }, [user?.id, authLoading, mergeCatalog]);

  useEffect(() => {
    const abort = new AbortController();
    Promise.all(watchlist.map(target => api<Dashboard>(`/api/dashboard/${target}`, undefined, abort.signal)))
      .then(results => { if (!abort.signal.aborted) { mergeCatalog(results.map(item => item.stock)); setSnapshots(previous => ({ ...previous, ...Object.fromEntries(results.map(item => [item.stock.code, newerSnapshot(previous[item.stock.code], item)])) })); } })
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => abort.abort();
  }, [watchlist, mergeCatalog]);

  useEffect(() => {
    const abort = new AbortController();
    const cached = snapshotsRef.current[code];
    setLoading(!cached); setData(cached ?? null); setError('');
    api<Dashboard>(`/api/dashboard/${code}`, undefined, abort.signal).then(next => { if (!abort.signal.aborted) setData(previous => newerSnapshot(previous, next)); }).catch(e => { if (e.name !== 'AbortError') setError(e.message); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [code]);

  const loadSnapshot = useCallback(async (target: string, signal?: AbortSignal) => {
    const next = await api<Dashboard>(`/api/dashboard/${target}`, undefined, signal);
    if (!signal?.aborted) { setSnapshots(previous => ({ ...previous, [target]: newerSnapshot(previous[target], next) })); if (code === target) { setData(previous => newerSnapshot(previous, next)); setLoading(false); } }
  }, [code]);

  useEffect(() => {
    const active = Object.values(jobs).filter(job => ['queued', 'running'].includes(job.status));
    if (!active.length || !user) return;
    const abort = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const latest = await Promise.all(active.map(job => api<Job>(`/api/research/jobs/${job.id}`, undefined, abort.signal)));
        if (abort.signal.aborted) return;
        await Promise.all(latest.filter(job => !['queued', 'running'].includes(job.status) || job.data_revision && job.data_revision !== jobs[job.code]?.data_revision).map(job => loadSnapshot(job.code, abort.signal)));
        if (!abort.signal.aborted) setJobs(previous => ({ ...previous, ...Object.fromEntries(latest.map(job => [job.code, job])) }));
      } catch (e) { if (!abort.signal.aborted) { setError((e as Error).message); setJobs(previous => ({ ...previous })); } }
    }, 2000);
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [jobs, user?.id, loadSnapshot]);

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setAdding(true); } };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, []);

  async function collect(days: number, community: boolean, communityOnly = false) {
    if (!requireAuth('登录后可以采集真实新闻并使用模型研判。')) return;
    const abort = new AbortController(); controller.current?.abort(); controller.current = abort;
    setCollecting(code); setError('');
    setJobs(previous => { const next = { ...previous }; delete next[code]; return next; });
    try { const next = await api<Job>(`/api/research/${code}/${communityOnly ? 'community' : 'refresh'}`, communityOnly ? { days } : { days, max_articles: 3, include_community: community }, abort.signal); if (!abort.signal.aborted) setJobs(previous => ({ ...previous, [next.code]: next })); }
    catch (e) { if (!abort.signal.aborted) setError((e as Error).message); }
    finally { if (!abort.signal.aborted) setCollecting(null); }
  }

  async function addStock(stock: Stock) {
    if (!requireAuth('登录后添加自选股，自动采集行情与新闻并生成 AI 研判。') || !watchReady || savingWatch) return false;
    const owner = user?.id; setSavingWatch(true); setError('');
    try {
      const result = await api<{ codes: string[]; stock: Stock; job: Job | null }>('/api/watchlist/add', { code: stock.code });
      if (userId.current !== owner) return false;
      mergeCatalog([result.stock]); setWatchlist(result.codes); setCode(result.stock.code); setView('dashboard'); setAdding(false);
      if (result.job) setJobs(previous => ({ ...previous, [result.stock.code]: result.job! }));
      return true;
    } finally { setSavingWatch(false); }
  }

  async function updateWatchlist(next: string[]) {
    if (!requireAuth('登录后，自选股会保存在你的账号中。') || !watchReady || savingWatch) return;
    const owner = user?.id; setSavingWatch(true);
    try {
      const result = await api<{ codes: string[] }>('/api/watchlist', { codes: next });
      if (userId.current === owner) setWatchlist(result.codes);
    } catch (e) { setError((e as Error).message); }
    finally { setSavingWatch(false); }
  }

  async function openAudit() {
    if (!requireAuth('登录后可查看新闻清洗与去重记录。')) return;
    const owner = user?.id; setAuditLoading(true);
    try { const result = await api<NonNullable<typeof audit>>(`/api/research/${code}/audit`); if (owner === userId.current) setAudit(result); }
    catch (e) { setError((e as Error).message); }
    finally { setAuditLoading(false); }
  }

  async function testConnection() {
    if (!requireAuth('登录后可以发送模型连接测试。')) return;
    const abort = new AbortController(); controller.current?.abort(); controller.current = abort;
    setTesting(true); setError('');
    try { const result = await api<ModelReply>('/api/chat', { messages: [{ role: 'user', content: '连接测试，请只回复：FinPulse 连接成功。' }] }, abort.signal); if (!abort.signal.aborted) setLastReply(result); }
    catch (e) { if (!abort.signal.aborted) setError((e as Error).message); }
    finally { if (!abort.signal.aborted) setTesting(false); }
  }

  const job = jobs[code] ?? null;
  const running = collecting === code || !!job && ['queued', 'running'].includes(job.status);
  const openAssistant = useCallback((news?: Dashboard['news'][number]) => { if (data) setDrawer({ stock: data.stock, news }); }, [data]);
  return <div className="finance-shell">
    <header className="finance-header"><div className="header-inner"><a className="finance-brand" href="/"><Activity size={25} /><span>Fin<strong>Pulse</strong><small>财经研究</small></span></a><button className="finance-search" onClick={() => setAdding(true)}><Search size={17} /><span>搜索股票名称、代码或拼音</span><kbd>Ctrl K</kbd></button><span className="research-badge"><span className="status-dot" />真实数据工作台</span><AccountMenu open={accountOpen} setOpen={setAccountOpen} onAdmin={() => setView('admin')} /></div></header>
    <nav className="finance-nav" aria-label="主导航"><div>{[{ id: 'dashboard', label: '行情与新闻' }, { id: 'briefing', label: '自选股早报' }, { id: 'lab', label: '连接状态' }, ...(user?.role === 'admin' ? [{ id: 'admin', label: '管理中心' }] : [])].map(item => <button key={item.id} className={view === item.id ? 'active' : ''} onClick={() => setView(item.id)}>{item.label}</button>)}<span>沪深 A 股 · 真实股票库</span></div></nav>
    <div className="ticker-bar"><div className="ticker-inner"><div className="ticker-label"><Activity size={16} /><strong>关注市场</strong><span>历史日线收盘</span></div>{catalog.slice(0, 3).map(stock => { const cached = snapshots[stock.code]; return <button className={`ticker-card ${code === stock.code ? 'selected' : ''}`} key={stock.code} onClick={() => { setCode(stock.code); setView('dashboard'); }}><span>{stock.name}<small>{cached?.quote.as_of_date ?? '待采集'}</small></span><strong className="mono">{number(cached?.stock.price)}</strong><span className={tone(cached?.stock.change ?? null)}>{percent(cached?.stock.change)}</span></button>; })}</div></div>
    <main className="finance-main"><div className="finance-heading"><div><p className="eyebrow">YOUR SIGNAL, IN CONTEXT</p><h1>{view === 'dashboard' ? '跟踪行情，读懂消息。' : view === 'briefing' ? '你的自选股早报' : view === 'admin' ? '团队账号管理' : '研究引擎连接状态'}</h1><p>{view === 'dashboard' ? '从新闻原文到 AI 研判，每一个结论都有可追溯的来源。' : view === 'briefing' ? '查看已采集消息的汇总，配置邮件或微信订阅。' : view === 'admin' ? '管理账号状态与操作记录。' : '查看数据源和模型配置，验证真实请求。'}</p></div><span className="workspace-tag">LOCAL WORKSPACE <span>v0.3</span></span></div>
      {error && <div className="error-box" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}{sessionError && <div className="error-box" role="alert">登录状态暂时无法确认：{sessionError}</div>}
      {view === 'admin' && user?.role === 'admin' && <AdminPanel key={user.id} />}
      {view === 'briefing' && <BriefingPanel key={user?.id ?? 'guest'} />}
      {view === 'lab' && <div className="connection-grid"><section className="panel connection-card"><FlaskConical size={28} className="mint-text" /><h2>模型与数据源</h2><dl><div><dt>模型服务</dt><dd>{health?.provider ?? '未配置'}</dd></div><div><dt>当前模型</dt><dd>{health?.model ?? '未配置'}</dd></div><div><dt>Tavily</dt><dd>{health?.tavily_configured ? '密钥已配置' : '未配置'}</dd></div><div><dt>AkShare</dt><dd>东方财富新闻 / 腾讯历史日线</dd></div><div><dt>自动早报</dt><dd>{health?.scheduler_enabled ? '已开启调度' : '默认关闭'}</dd></div></dl><button className="primary-button" disabled={testing || !health?.configured || running} onClick={testConnection}>{testing ? <LoaderCircle size={16} className="spin" /> : <FlaskConical size={16} />}发送连接测试</button><p>已配置不代表源站可用。采集任务会记录各接口的实际结果。</p></section><section className="panel connection-card"><h2>最近一次模型响应</h2>{lastReply ? <><div className="reply-metadata">{lastReply.model} · {(lastReply.elapsed_ms / 1000).toFixed(1)}s · {lastReply.usage.total_tokens ?? '—'} tokens</div><pre>{lastReply.content}</pre></> : <div className="empty-card"><Activity size={32} /><p>发送连接测试或分析新闻后，在这里查看结果。</p></div>}</section></div>}
      {view === 'dashboard' && <ResearchDashboard data={data} loading={loading} catalog={catalog} snapshots={snapshots} watchlist={watchlist} savingWatch={savingWatch || !watchReady} code={code} setCode={setCode} userSignedIn={!!user} add={() => setAdding(true)} remove={target => { void updateWatchlist(watchlist.filter(row => row !== target)); }} running={running} job={job} collect={collect} audit={openAudit} auditLoading={auditLoading} openAssistant={openAssistant} briefing={() => setView('briefing')} />}
      <footer className="finance-footer"><span><Activity size={14} />FinPulse · 来源可查，推断可辨。</span><span>历史日线 ≠ 实时报价 · AI 研判需要结合原文核验</span></footer>
    </main>
    {drawer && <AiDrawer key={`${user?.id}-${drawer.stock.code}-${drawer.news?.id ?? 'chat'}`} stock={drawer.stock} news={drawer.news} onClose={() => setDrawer(null)} onResult={reply => { setLastReply(reply); void loadSnapshot(code).catch(e => setError(e.message)); }} />}
    {adding && <Dialog title="查找与管理自选股" close={() => setAdding(false)}><StockSearch watchlist={watchlist} disabled={!watchReady || savingWatch} add={addStock} remove={target => updateWatchlist(watchlist.filter(row => row !== target))} select={stock => { mergeCatalog([stock]); setCode(stock.code); setView('dashboard'); setAdding(false); }} /></Dialog>}
    {audit && <Dialog title="新闻清洗与去重记录" close={() => setAudit(null)}><p className="supported-caption">保留 {audit.summary.retained ?? 0} · 过滤 {audit.summary.filtered ?? 0} · 合并 {audit.summary.merged ?? 0}</p><div className="audit-records">{audit.audit?.map(row => <article key={row.id}><span className={`audit-status ${row.status}`}>{row.status === 'retained' ? '保留' : row.status === 'merged' ? '合并' : '过滤'}</span><h3>{row.title}</h3><p>{row.reasons.join('；') || '通过主体、日期、内容与来源检查'}</p>{row.url && <a href={row.url} target="_blank" rel="noopener noreferrer">查看原文<ExternalLink size={12} /></a>}</article>)}</div></Dialog>}
  </div>;
}
