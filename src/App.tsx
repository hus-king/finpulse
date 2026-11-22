import { headlineIndices } from './instruments';
import { lazy, Suspense, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Activity, ExternalLink, FlaskConical, Github, LoaderCircle, Search, X } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import { ThemeToggle } from './ThemeContext';
import AccountMenu from './AccountMenu';
import AdminPanel from './AdminPanel';
import AiDrawer from './AiDrawer';
import ResearchDashboard from './ResearchDashboard';
import MarketSentimentCard from './MarketSentimentCard';
import BriefingPanel from './BriefingPanel';
import HotNewsPanel from './HotNewsPanel';
import PaperTradingPanel from './PaperTradingPanel';
import PegLeaderboard from './PegLeaderboard';
import ConvertibleBondResearch from './ConvertibleBondResearch';
import useDialogScroll from './useDialogScroll';
import StockSearch from './StockSearch';
import type { BatchAnalyzeResponse, BatchQuotesResponse, Dashboard, Health, Job, ModelReply, Stock } from './types';
import { number, percent, tone } from './format';
import ReadingRail from './ReadingRail';
import BrandWordmark from './BrandWordmark';

const defaults = ['600519', '300750', '688981'];
const EtfFundsCard = lazy(() => import('./EtfFundsCard'));
const EtfFlowIndicator = lazy(() => import('./EtfFlowIndicator'));
const StockBondOverview = lazy(() => import('./StockBondOverview'));
const NorthboundOverview = lazy(() => import('./NorthboundOverview'));
const navigation = [
  { id: 'dashboard', label: '行情与新闻' },
  { id: 'etf-funds', label: '资金与宏观' },
  { id: 'hot-news', label: '热点榜单' },
  { id: 'screener', label: 'PEG榜单' },
  { id: 'convertible-bonds', label: '可转债研究' },
  { id: 'paper', label: '模拟盘' },
  { id: 'briefing', label: '自选股早报' },
  { id: 'lab', label: '连接状态' },
  { id: 'admin', label: '管理中心' },
];

const shareableViews = ['screener', 'hot-news', 'convertible-bonds', 'etf-funds', 'paper'];

function FinanceNav({ view, isAdmin, onSelect }: { view: string; isAdmin: boolean; onSelect: (view: string) => void }) {
  const row = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const strip = row.current;
    const active = strip?.querySelector('[aria-current="page"]');
    if (!strip || !active) return;
    const bounds = strip.getBoundingClientRect();
    const tab = active.getBoundingClientRect();
    if (tab.left < bounds.left) strip.scrollLeft += tab.left - bounds.left;
    else if (tab.right > bounds.right) strip.scrollLeft += tab.right - bounds.right;
  }, [view]);

  return <nav className="finance-nav" aria-label="主导航"><div ref={row}>
    {navigation.filter(item => item.id !== 'admin' || isAdmin).map(item =>
      <button key={item.id} type="button" className={view === item.id ? 'active' : ''} aria-current={view === item.id ? 'page' : undefined} onClick={() => onSelect(item.id)}>
        {item.label}
      </button>
    )}
  </div></nav>;
}

const normalizeTime = (s?: string | null) => {
  if (!s) return 0;
  const iso = s.includes('T') ? s : s.replace(' ', 'T');
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
};

// A slow initial/cache read must not replace a newer staged result or realtime quote.
const newerSnapshot = (previous: Dashboard | null | undefined, next: Dashboard) => {
  if (!previous || previous.stock.code !== next.stock.code) return next;
  const base = previous.as_of && (!next.as_of || previous.as_of > next.as_of) ? previous : next;

  const prevDate = previous.quote.as_of_date ?? '';
  const nextDate = next.quote.as_of_date ?? '';
  const prevTime = normalizeTime(previous.quote.collected_at);
  const nextTime = normalizeTime(next.quote.collected_at);

  const prevIsRealtime = !!previous.quote.is_realtime;
  const nextIsRealtime = !!next.quote.is_realtime;

  let usePreviousQuote = false;
  if (prevIsRealtime && !nextIsRealtime) {
    usePreviousQuote = prevDate >= nextDate || !nextDate;
  } else if (!prevIsRealtime && nextIsRealtime) {
    usePreviousQuote = false;
  } else {
    usePreviousQuote = prevDate > nextDate || (prevDate === nextDate && prevTime > nextTime);
  }

  const quote = usePreviousQuote ? previous.quote : next.quote;
  const candles = next.candles.length ? next.candles : previous.candles;
  const stockPrice = usePreviousQuote ? (previous.stock.price ?? next.stock.price) : (next.stock.price ?? previous.stock.price);
  const stockChange = usePreviousQuote ? (previous.stock.change ?? next.stock.change) : (next.stock.change ?? previous.stock.change);

  return {
    ...base,
    quote,
    candles,
    stock: { ...base.stock, price: stockPrice, change: stockChange },
    backtest: next.candles.length ? next.backtest : (previous.candles.length ? previous.backtest : base.backtest),
    daily_request: next.daily_request ?? previous.daily_request,
  };
};

function Dialog({ title, children, close }: { title: string; children: React.ReactNode; close: () => void }) {
  const dialog = useRef<HTMLElement>(null);
  const titleId = useId();
  useDialogScroll();
  useLayoutEffect(() => {
    const previous = document.activeElement;
    const first = dialog.current?.querySelector<HTMLElement>('input') ?? dialog.current?.querySelector('button');
    first?.focus({ preventScroll: true });
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true }); };
  }, []);

  function handleKey(event: React.KeyboardEvent<HTMLElement>) {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
    if (event.key !== 'Tab') return;
    const controls = event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), summary, a[href]');
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && event.target === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && event.target === last) { event.preventDefault(); first.focus(); }
  }

  return <div className="modal-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialog} className="research-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={handleKey}>
      <header><h2 id={titleId}>{title}</h2><button className="icon-button" aria-label="关闭弹窗" onClick={close}><X size={20} /></button></header>
      {children}
    </section>
  </div>;
}

export default function App() {
  const { user, loading: authLoading, requireAuth, sessionError } = useAuth();
  const [view, setView] = useState(() => { const requested = new URLSearchParams(window.location.search).get('view') ?? ''; return shareableViews.includes(requested) ? requested : 'dashboard'; });
  useEffect(() => {
    const url = new URL(window.location.href);
    if (shareableViews.includes(view)) url.searchParams.set('view', view);
    else if (shareableViews.includes(url.searchParams.get('view') ?? '')) url.searchParams.delete('view');
    if (url.href !== window.location.href) window.history.replaceState(null, '', url);
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [view]);
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
  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;
  userId.current = user?.id;
  const mergeCatalog = useCallback((items: Stock[]) => setCatalog(previous => Array.from(new Map([...previous, ...items].map(stock => [stock.code, stock])).values())), []);
  const marketSnapshot = useCallback((next: Dashboard) => {
    setSnapshots(previous => ({ ...previous, [next.stock.code]: newerSnapshot(previous[next.stock.code], next) }));
    setData(previous => previous?.stock.code === next.stock.code ? newerSnapshot(previous, next) : previous);
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    Promise.all([api<{ items: Stock[] }>('/api/stocks?codes=sh000001,sz399001,sh000300,600519,300750,688981,000001,002594', undefined, abort.signal), api<Health>('/api/health', undefined, abort.signal)])
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
    const quoteCodes = [...new Set([...headlineIndices, ...watchlist])];
    let stopped = false;
    let timer: number | undefined;
    let abort: AbortController | null = null;

    const pollQuotes = async () => {
      if (stopped || document.hidden) return;
      abort?.abort();
      abort = new AbortController();
      let delay = 30;
      try {
        const resp = await api<BatchQuotesResponse>(`/api/market/quotes?codes=${quoteCodes.join(',')}`, undefined, abort.signal);
        if (stopped || abort.signal.aborted) return;
        if (resp && resp.items) {
          setSnapshots(prev => {
            const next = { ...prev };
            for (const [stockCode, quote] of Object.entries(resp.items)) {
              const existing = next[stockCode];
              const asOfDate = quote.as_of ? quote.as_of.slice(0, 10) : undefined;
              const collectedAt = quote.as_of ? (quote.as_of.includes('T') ? quote.as_of : quote.as_of.replace(' ', 'T')) : new Date().toISOString();
              if (existing) {
                next[stockCode] = {
                  ...existing,
                  stock: { ...existing.stock, price: quote.price, change: quote.change },
                  quote: {
                    ...existing.quote,
                    price: quote.price,
                    change: quote.change,
                    as_of_date: asOfDate ?? existing.quote.as_of_date,
                    collected_at: collectedAt,
                    is_realtime: true,
                  },
                };
              } else {
                const stock = catalogRef.current.find(s => s.code === stockCode) || {
                  code: stockCode,
                  name: quote.name,
                  initials: '',
                  exchange: stockCode.startsWith('sh') || stockCode.startsWith('6') ? 'SH' : 'SZ',
                  industry: 'A 股',
                  price: quote.price,
                  change: quote.change,
                };
                next[stockCode] = {
                  stock: { ...stock, price: quote.price, change: quote.change },
                  quote: {
                    status: 'ok',
                    price: quote.price,
                    change: quote.change,
                    as_of_date: asOfDate,
                    collected_at: collectedAt,
                    is_realtime: true,
                  },
                  candles: [],
                  news: [],
                  sentiment: { status: 'not_collected', sample_count: 0, bull: null, bear: null, neutral: null, keywords: [], posts: [] },
                  as_of: quote.as_of,
                  data_source: 'live',
                  pipeline: null,
                  backtest: { items: [], note: '' },
                } as unknown as Dashboard;
              }
            }
            return next;
          });
          setData(prev => {
            if (!prev) return prev;
            const quote = resp.items[prev.stock.code];
            if (!quote) return prev;
            const asOfDate = quote.as_of ? quote.as_of.slice(0, 10) : undefined;
            const collectedAt = quote.as_of ? (quote.as_of.includes('T') ? quote.as_of : quote.as_of.replace(' ', 'T')) : new Date().toISOString();
            return {
              ...prev,
              stock: { ...prev.stock, price: quote.price, change: quote.change },
              quote: {
                ...prev.quote,
                price: quote.price,
                change: quote.change,
                as_of_date: asOfDate ?? prev.quote.as_of_date,
                collected_at: collectedAt,
                is_realtime: true,
              },
            };
          });
          setCatalog(prev => {
            let changed = false;
            const next = prev.map(stock => {
              const quote = resp.items[stock.code];
              if (quote && (stock.price !== quote.price || stock.change !== quote.change)) {
                changed = true;
                return { ...stock, price: quote.price, change: quote.change };
              }
              return stock;
            });
            return changed ? next : prev;
          });
        }
        delay = resp.is_trading ? 4 : 30;
      } catch (e) {
        if (!stopped && !abort?.signal.aborted) {
          delay = 10;
        }
      } finally {
        if (!stopped && !document.hidden) {
          timer = window.setTimeout(() => { void pollQuotes(); }, delay * 1000);
        }
      }
    };

    const handleVisibility = () => {
      window.clearTimeout(timer);
      abort?.abort();
      if (!document.hidden) {
        void pollQuotes();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    void pollQuotes();

    return () => {
      stopped = true;
      abort?.abort();
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [watchlist]);

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
    const listener = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'k') return;
      e.preventDefault();
      if (!document.querySelector('[aria-modal="true"]')) setAdding(true);
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, []);

  async function collect(days: number, community: boolean, communityOnly = false, analysisOnly = false) {
    if (!requireAuth('登录后可以采集真实新闻并使用模型研判。')) return;
    const abort = new AbortController(); controller.current?.abort(); controller.current = abort;
    setCollecting(code); setError('');
    setJobs(previous => { const next = { ...previous }; delete next[code]; return next; });
    try { const next = await api<Job>(`/api/research/${code}/${analysisOnly ? 'analyze-all' : communityOnly ? 'community' : 'refresh'}`, analysisOnly ? {} : communityOnly ? { days } : { days, include_community: community }, abort.signal); if (!abort.signal.aborted) setJobs(previous => ({ ...previous, [next.code]: next })); }
    catch (e) { if (!abort.signal.aborted) setError((e as Error).message); }
    finally { if (!abort.signal.aborted) setCollecting(null); }
  }

  async function batchAnalyzeWatchlist(analysisOnly = false) {
    if (!requireAuth('登录后可以一键研判所有自选股。')) return;
    if (!watchlist.length) return;
    setError('');
    try {
      const resp = await api<BatchAnalyzeResponse>('/api/watchlist/analyze-all', {
        codes: watchlist,
        analysis_only: analysisOnly,
      });
      if (resp && resp.jobs) {
        setJobs(previous => ({
          ...previous,
          ...Object.fromEntries(resp.jobs.map(j => [j.code, j])),
        }));
      }
    } catch (e) {
      setError((e as Error).message);
    }
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
  async function openListedStock(target: string) {
    const result = await api<{ items: Stock[] }>(`/api/stocks?codes=${target}`);
    const selected = result.items.find(item => item.code === target);
    if (!selected) throw new Error('股票列表数据源暂不可用，请稍后重试。');
    mergeCatalog([selected]); setCode(target); setView('dashboard');
  }

  const currentPage = navigation.find(item => item.id === view) ?? navigation[0];
  return <div className="finance-shell">
    <a className="skip-link" href="#workspace-content">跳至主要内容</a>
    <header className="finance-header"><div className="header-inner">
      <a className="finance-brand" href="/" aria-label="FinPulse 财经研究"><BrandWordmark /></a>
      <button type="button" className="finance-search" onClick={() => setAdding(true)}>
        <Search size={17} aria-hidden="true" /><span>搜索股票、指数名称或代码</span>
        <kbd aria-hidden="true">{/Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K'}</kbd>
      </button>
      <ThemeToggle />
      <AccountMenu open={accountOpen} setOpen={setAccountOpen} onAdmin={() => setView('admin')} />
    </div></header>
    <FinanceNav view={view} isAdmin={user?.role === 'admin'} onSelect={setView} />
    {view === 'dashboard' && <div className="ticker-bar"><div className="ticker-inner">
      <div className="ticker-label"><Activity size={16} aria-hidden="true" /><strong>主要指数</strong><span>行情快照</span></div>
      {headlineIndices.flatMap(id => catalog.find(stock => stock.code === id) ?? []).map(stock => {
        const cached = snapshots[stock.code];
        const price = cached?.stock.price ?? stock.price;
        const change = cached?.stock.change ?? stock.change;
        return <button type="button" className={`ticker-card ${code === stock.code ? 'selected' : ''}`} aria-pressed={code === stock.code} key={stock.code} onClick={() => setCode(stock.code)}>
          <span>{stock.name}<small>{cached?.quote.as_of_date ?? '待获取'}</small></span>
          <strong key={price} className="mono quote-update">{number(price)}</strong>
          <span className={tone(change)}>{percent(change)}</span>
        </button>;
      })}
    </div></div>}
    <main className="finance-main" id="workspace-content" tabIndex={-1}>
      <header className="finance-heading"><h1>{currentPage.label}</h1></header>
      {error && <div className="error-box" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}{sessionError && <div className="error-box" role="alert">登录状态暂时无法确认：{sessionError}</div>}
      <div className="workspace-view" key={view}>
      {view === 'admin' && user?.role === 'admin' && <AdminPanel key={user.id} />}
      {view === 'briefing' && <BriefingPanel key={user?.id ?? 'guest'} />}
      {view === 'hot-news' && <HotNewsPanel key={user?.id ?? 'guest'} watchlist={watchlist} watchReady={watchReady} />}
      {view === 'screener' && <PegLeaderboard watchlist={watchlist} savingWatch={savingWatch || !watchReady} onAddStock={addStock} onOpenStock={openListedStock} />}
      {view === 'convertible-bonds' && <ConvertibleBondResearch onOpenStock={openListedStock} />}
      {view === 'paper' && <PaperTradingPanel key={user?.id ?? 'guest'} initialCode={code} onOpenStock={target => { setCode(target); setView('dashboard'); }} />}
      {view === 'lab' && <div className="connection-grid"><section className="panel connection-card"><FlaskConical size={28} className="mint-text" /><h2>模型与数据源</h2><dl><div><dt>模型服务</dt><dd>{health?.provider ?? '未配置'}</dd></div><div><dt>当前模型</dt><dd>{health?.model ?? '未配置'}</dd></div><div><dt>Tavily</dt><dd>{health?.tavily_configured ? '密钥已配置' : '未配置'}</dd></div><div><dt>AkShare</dt><dd>东方财富新闻 / 腾讯日线 / 新浪分钟行情</dd></div><div><dt>自动早报</dt><dd>{health?.scheduler_enabled ? '已开启调度' : '默认关闭'}</dd></div></dl><button className="primary-button" disabled={testing || !health?.configured || running} onClick={testConnection}>{testing ? <LoaderCircle size={16} className="spin" /> : <FlaskConical size={16} />}发送连接测试</button><p>已配置不代表源站可用。采集任务会记录各接口的实际结果。</p></section><section className="panel connection-card"><h2>最近一次模型响应</h2>{lastReply ? <><div className="reply-metadata">{lastReply.model} · {(lastReply.elapsed_ms / 1000).toFixed(1)}s · {lastReply.usage.total_tokens ?? '—'} tokens</div><pre>{lastReply.content}</pre></> : <div className="empty-card"><Activity size={32} /><p>发送连接测试或分析新闻后，在这里查看结果。</p></div>}</section></div>}
      {view === 'dashboard' && <MarketSentimentCard />}
      {view === 'etf-funds' && <>
        <Suspense fallback={<p role="status">正在打开 ETF 资金动向…</p>}><EtfFlowIndicator /></Suspense>
        <Suspense fallback={<p role="status">正在打开 ETF 资金监控…</p>}><EtfFundsCard /></Suspense>
        <Suspense fallback={<p role="status">正在打开股债联动…</p>}><StockBondOverview /></Suspense>
        <Suspense fallback={<p role="status">正在打开北向资金…</p>}><NorthboundOverview /></Suspense>
      </>}
      {view === 'dashboard' && <ResearchDashboard data={data} loading={loading} catalog={catalog} snapshots={snapshots} watchlist={watchlist} savingWatch={savingWatch || !watchReady} code={code} setCode={setCode} userSignedIn={!!user} add={() => setAdding(true)} remove={target => { void updateWatchlist(watchlist.filter(row => row !== target)); }} running={running} job={job} jobs={jobs} batchAnalyze={batchAnalyzeWatchlist} collect={collect} audit={openAudit} auditLoading={auditLoading} openAssistant={openAssistant} briefing={() => setView('briefing')} onMarket={marketSnapshot} />}
      </div>
      <footer className="finance-footer"><span>FinPulse</span><a href="https://github.com/hus-king/finpulse" target="_blank" rel="noopener noreferrer"><Github size={15} aria-hidden="true" />GitHub</a></footer>
    </main>
    <ReadingRail view={view} />
    {drawer && <AiDrawer key={`${user?.id}-${drawer.stock.code}-${drawer.news?.id ?? 'chat'}`} stock={drawer.stock} news={drawer.news} onClose={() => setDrawer(null)} onResult={reply => { setLastReply(reply); void loadSnapshot(code).catch(e => setError(e.message)); }} />}
    {adding && <Dialog title="查找与管理自选股" close={() => setAdding(false)}><StockSearch watchlist={watchlist} disabled={!watchReady || savingWatch} add={addStock} remove={target => updateWatchlist(watchlist.filter(row => row !== target))} select={stock => { mergeCatalog([stock]); setCode(stock.code); setView('dashboard'); setAdding(false); }} /></Dialog>}
    {audit && <Dialog title="新闻清洗与去重记录" close={() => setAudit(null)}><p className="supported-caption">保留 {audit.summary.retained ?? 0} · 过滤 {audit.summary.filtered ?? 0} · 合并 {audit.summary.merged ?? 0}</p><div className="audit-records">{audit.audit?.map(row => <article key={row.id}><span className={`audit-status ${row.status}`}>{row.status === 'retained' ? '保留' : row.status === 'merged' ? '合并' : '过滤'}</span><h3>{row.title}</h3><p>{row.reasons.join('；') || '通过主体、日期、内容与来源检查'}</p>{row.url && <a href={row.url} target="_blank" rel="noopener noreferrer">查看原文<ExternalLink size={12} /></a>}</article>)}</div></Dialog>}
  </div>;
}
