import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, BookOpen, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, Code2, Command, Cpu, FlaskConical, LayoutDashboard, LoaderCircle, Plus, Radio, RefreshCw, Search, Sparkles, TrendingUp, X, Zap } from 'lucide-react';
import { api } from './api';
import AiDrawer from './AiDrawer';
import AccountMenu from './AccountMenu';
import AdminPanel from './AdminPanel';
import { ShieldCheck } from 'lucide-react';
import { useAuth } from './AuthContext';
import PriceChart from './PriceChart';
import type { Dashboard, Health, ModelReply, News, Stock } from './types';

const price = (value: number) => value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const percent = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
const tone = (value: number) => value >= 0 ? 'up' : 'down';

function Sparkline({ seed, rising }: { seed: number; rising: boolean }) {
  const values = Array.from({ length: 24 }, (_, i) => Math.sin(i * 1.2 + seed) * 5 + Math.cos(i * 0.4) * 4 + (rising ? -i * 0.7 : i * 0.5) + 24);
  const points = values.map((value, i) => `${i * 3.3},${value}`).join(' ');
  return <svg className={`sparkline ${rising ? 'up' : 'down'}`} viewBox="0 0 78 43" aria-hidden="true"><polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /></svg>;
}

export default function App() {
  const { user, loading: authLoading, requireAuth, requestLogin, sessionError, refreshSession } = useAuth();
  const [accountOpen, setAccountOpen] = useState(false);
  const previousUser = useRef<string | null>(null);
  const testController = useRef<AbortController | null>(null);
  const [catalog, setCatalog] = useState<Stock[]>([]);
  const [watchlist, setWatchlist] = useState<string[]>(() => { try { const saved = JSON.parse(localStorage.getItem('finpulse.watchlist') ?? 'null'); return Array.isArray(saved) && saved.length > 0 && saved.every(item => typeof item === 'string') ? saved : ['600519', '300750', '688981']; } catch { return ['600519', '300750', '688981']; } });
  const [code, setCode] = useState('600519');
  const [data, setData] = useState<Dashboard | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [adding, setAdding] = useState(false);
  const [mode, setMode] = useState('近 3 月');
  const [indicator, setIndicator] = useState('成交量');
  const [drawer, setDrawer] = useState<{ stock: Stock; news?: News } | null>(null);
  const [view, setView] = useState('dashboard');
  const [lastReply, setLastReply] = useState<ModelReply | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState('');
  const [info, setInfo] = useState(false);

  useEffect(() => { if (user?.role !== 'admin') setView(previous => previous === 'admin' ? 'dashboard' : previous); }, [user?.role]);

  useEffect(() => {
    if (previousUser.current && previousUser.current !== user?.id) {
      testController.current?.abort(); setTesting(false);
      setDrawer(null); setLastReply(null); setTestError(''); setAccountOpen(false);
    }
    previousUser.current = user?.id ?? null;
  }, [user?.id]);
  useEffect(() => () => testController.current?.abort(), []);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setAdding(true); setSearch(''); }
      if (event.key === 'Escape') { setAdding(false); setInfo(false); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([api<{ items: Stock[] }>('/api/stocks', undefined, controller.signal), api<Health>('/api/health', undefined, controller.signal)]).then(([stocks, status]) => { setCatalog(stocks.items); setHealth(status); }).catch(error => { if (error.name !== 'AbortError') setError(error.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => { localStorage.setItem('finpulse.watchlist', JSON.stringify(watchlist)); }, [watchlist]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    api<Dashboard>(`/api/dashboard/${code}`, undefined, controller.signal).then(setData).catch(error => { if (error.name !== 'AbortError') setError(error.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [code]);
  const openEvent = useCallback(() => { if (data) setDrawer({ stock: data.stock, news: data.news[0] }); }, [data]);

  async function testConnection() {
    if (!requireAuth('登录后即可发送模型连接测试。')) return;
    const controller = new AbortController();
    testController.current = controller;
    setTesting(true); setTestError('');
    try {
      const result = await api<ModelReply>('/api/chat', { messages: [{ role: 'user', content: '这是连接测试，请只回复：FinPulse 连接成功。' }] }, controller.signal);
      if (controller.signal.aborted) return;
      setLastReply(result);
      setHealth(await api<Health>('/api/health', undefined, controller.signal));
    } catch (error) { if (!controller.signal.aborted) setTestError((error as Error).message); }
    finally { if (!controller.signal.aborted) setTesting(false); }
  }
  async function refresh() {
    setLoading(true); setError('');
    try { const [next, status] = await Promise.all([api<Dashboard>(`/api/dashboard/${code}`), api<Health>('/api/health')]); setData(next); setHealth(status); }
    catch (error) { setError((error as Error).message); }
    finally { setLoading(false); }
  }

  const stocks = catalog.filter(stock => watchlist.includes(stock.code));
  const matches = catalog.filter(stock => `${stock.code}${stock.name}${stock.initials}`.toLowerCase().includes(search.trim().toLowerCase()));

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="FinPulse 首页"><span className="brand-icon"><Activity size={22} /></span><span>Fin<span className="brand-light">Pulse</span><small>智能股票舆情</small></span></a>
      <div className="workspace-label">研究工作台 <span>DEMO</span></div>
      <nav className="main-nav" aria-label="主导航"><button className={view === 'dashboard' ? 'active' : ''} onClick={() => setView('dashboard')}><LayoutDashboard size={18} />概览看板<span className="nav-indicator" /></button><button className={view === 'lab' ? 'active' : ''} onClick={() => setView('lab')}><FlaskConical size={18} />AI 请求实验室<span className="tiny-tag">LIVE</span></button>{user?.role === 'admin' && <button className={view === 'admin' ? 'active' : ''} onClick={() => setView('admin')}><ShieldCheck size={18} />管理中心</button>}</nav>
      <div className="sidebar-divider" />
      <div className="section-label">我的自选 <span className="count">{stocks.length}</span><button className="icon-button" onClick={() => { setAdding(true); setSearch(''); }} aria-label="添加自选股"><Plus size={16} /></button></div>
      <div className="watchlist">{stocks.map((stock, i) => <button key={stock.code} className={`stock-item ${code === stock.code ? 'selected' : ''}`} onClick={() => { setCode(stock.code); setView('dashboard'); }}><div className="stock-row"><strong>{stock.name}</strong><span className={tone(stock.change)}>{percent(stock.change)}</span></div><div className="stock-row muted"><span className="mono">{stock.exchange} {stock.code}</span><span className="mono">{price(stock.price)}</span></div><Sparkline seed={i * 3} rising={stock.change >= 0} /></button>)}</div>
      <button className="add-stock" onClick={() => { setAdding(true); setSearch(''); }}><Plus size={15} /> 添加自选股</button>
      <div className="sidebar-bottom"><div className="local-status"><span className="status-dot" /><span>本地工作空间</span><span className="mono">v0.2</span></div><button className="profile" disabled={authLoading} onClick={() => user ? setAccountOpen(!accountOpen) : requestLogin()}><span className="avatar">{user ? user.nickname.slice(0, 1).toUpperCase() : 'FP'}</span><span><strong>{user ? user.nickname : '登录你的工作空间'}</strong><small>{user ? `@${user.username}` : 'LOGIN TO USE AI'}</small></span><ChevronRight size={17} /></button></div>
    </aside>

    <div className="workspace">
      <header className="topbar"><div className="breadcrumbs">工作台 <ChevronRight size={13} /><span>{view === 'admin' ? '管理中心' : view === 'dashboard' ? '概览看板' : 'AI 请求实验室'}</span></div><button className="global-search" onClick={() => { setAdding(true); setSearch(''); }}><Search size={15} /><span>搜索股票名称 / 代码</span><Command size={12} /><span className="mono">K</span></button><div className="top-actions"><span className="demo-pill"><span /> 演示模式</span><button className="icon-button" onClick={() => setInfo(true)} aria-label="Demo 说明"><CircleHelp size={18} /></button><AccountMenu open={accountOpen} setOpen={setAccountOpen} onAdmin={() => setView('admin')} /></div></header>

      <main>
        <div className="page-heading"><div><div className="eyebrow">LESS NOISE. MORE INSIGHT.</div><h1>{view === 'admin' ? '管理账号，维护团队工作空间' : view === 'dashboard' ? '看清市场的每一次脉动' : '让每一次请求都有回声'}</h1><p>{view === 'admin' ? '查看网站账号、管理用户状态与操作记录。' : view === 'dashboard' ? '连接行情、新闻与市场情绪，让研究更有依据。' : '从本地后端到模型服务，验证你的 AI 请求链路。'}</p></div>{view !== 'admin' && <button className="outline-button" onClick={refresh} disabled={loading}><RefreshCw size={14} className={loading ? 'spin' : ''} /> 刷新看板</button>}</div>

        {view === 'admin' && user?.role === 'admin' && <AdminPanel key={user.id} />}

        {error && <div className="error-box" role="alert">{error}<button onClick={refresh}>重新连接</button></div>}
        {sessionError && <div className="error-box" role="alert">登录状态暂时无法确认：{sessionError}<button onClick={() => { void refreshSession(); }}>重试</button></div>}
        {data && view === 'dashboard' && <>
          <div className="market-strip">{data.market.indices.map(index => <div className="market-index" key={index.name}><span>{index.name}</span><strong className="mono">{index.value}</strong><span className={`market-change ${tone(index.change)}`}>{index.change >= 0 ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{percent(index.change)}</span></div>)}<div className="market-strip-note"><span className="status-dot" /> 示例行情 <span className="mono">09.30</span></div></div>

          <div className="dashboard-grid">
            <div className="main-column">
              <section className="panel chart-panel"><header className="stock-header"><div className="stock-title"><span className="stock-emblem">{data.stock.name.slice(0, 1)}</span><div><h2>{data.stock.name}<span className="industry-tag">{data.stock.industry}</span></h2><span className="mono muted">{data.stock.exchange}:{data.stock.code}</span></div></div><button className="subtle-button" onClick={() => { setAdding(true); setSearch(''); }}>切换标的<ChevronDown size={14} /></button></header>
                <div className="quote-row"><div className="current-price"><strong className={`mono ${tone(data.stock.change)}`}>{price(data.stock.price)}</strong><span className={tone(data.stock.change)}>{percent(data.stock.change)} <ArrowUpRight size={15} /></span></div><div className="quote-details"><div><span>今开</span><strong className="mono">{price(data.candles.at(-1)!.open)}</strong></div><div><span>最高</span><strong className="mono up">{price(data.candles.at(-1)!.high)}</strong></div><div><span>最低</span><strong className="mono down">{price(data.candles.at(-1)!.low)}</strong></div><div><span>数据日期</span><strong className="mono">09-30</strong></div></div></div>
                <div className="chart-toolbar"><div className="segmented">{['近 1 月', '近 3 月', '全部'].map(item => <button key={item} className={mode === item ? 'active' : ''} onClick={() => setMode(item)}>{item}</button>)}</div><div className="chart-legend"><span><i style={{ background: '#e8c681' }} />MA5</span><span><i style={{ background: '#7b9cf3' }} />MA10</span><span><i style={{ background: '#b488dd' }} />MA20</span></div><span className="chart-kind">日 K<ChevronDown size={11} /></span></div>
                <PriceChart candles={data.candles} mode={mode} indicator={indicator} onEvent={openEvent} />
                <footer className="chart-footer"><div className="indicator-switch">{['成交量', 'MACD'].map(item => <button key={item} className={indicator === item ? 'active' : ''} onClick={() => setIndicator(item)}>{item}</button>)}</div><span><span className="event-dot">AI</span> 点击图中标记查看示例新闻</span><span className="chart-hint">滚轮缩放</span></footer>
              </section>

              <section className="panel news-panel"><header className="panel-heading"><div><Radio size={17} className="mint-text" /><h2>舆情雷达</h2><span className="count">{data.news.length}</span></div><span className="sample-label">示例新闻 · 示例评分</span></header><div className="news-list">{data.news.map(news => <article className="news-item" key={news.id}><span className={`sentiment-number ${news.score > 0 ? 'positive' : news.score < 0 ? 'negative' : 'neutral'}`}>{news.score > 0 ? '+' : ''}{news.score}</span><div className="news-content"><div className="news-meta"><span>{news.source}</span><span>·</span><time className="mono">{news.time}</time><span className="news-topic">{news.tag}</span></div><h3>{news.title}</h3><p>{news.content}</p></div><button className="analyze-button" onClick={() => setDrawer({ stock: data.stock, news })}><Sparkles size={13} /><span>AI 研判</span><ChevronRight size={13} /></button></article>)}</div><footer className="news-footer"><BookOpen size={13} /> 新闻内容为演示材料。登录后点击 AI 研判可获取真实模型分析。</footer></section>
            </div>

            <div className="insight-column">
              <section className="panel temperature-panel"><header className="panel-heading"><div><Activity size={17} className="mint-text" /><h2>市场情绪温度</h2></div><span className="sample-label">示例</span></header><div className="temperature-value"><strong className="mono">{data.market.temperature}<small>/100</small></strong><span>温和乐观 <TrendingUp size={13} /></span></div><div className="temperature-scale"><div className="temperature-marker" style={{ left: `${data.market.temperature}%` }} /></div><div className="scale-labels"><span>极度恐慌</span><span>中性</span><span>极度贪婪</span></div><p>市场风险偏好有所回暖<br />关注消息面的持续性与成交变化</p></section>
              <section className="panel sentiment-panel"><header className="panel-heading"><div><MessageIcon /><h2>社区情绪洞察</h2></div><span className="sample-label">示例</span></header><div className="sentiment-subtitle">{data.stock.name} <span>· {data.sentiment.sample_count} 条样本</span></div><div className="bull-bear"><div><span className="bull-label">看多</span><strong className="mono up">{data.sentiment.bull}<small>%</small></strong></div><span className="versus">VS</span><div><span className="bear-label">看空</span><strong className="mono down">{data.sentiment.bear}<small>%</small></strong></div></div><div className="ratio-track"><span style={{ width: `${data.sentiment.bull}%` }} /><span style={{ width: `${data.sentiment.bear}%` }} /></div><div className="sentiment-caption"><span>讨论偏乐观</span><span>加权多空比</span></div><div className="inner-divider" /><div className="keyword-heading">讨论热词 <span>WORD PULSE</span></div><div className="word-cloud">{data.sentiment.keywords.map((word, index) => <span key={word} className={`word-${index}`}>{word}</span>)}</div><div className="sentiment-note"><span className="status-dot" /> 示例情绪未达到极端预警阈值</div></section>
              <section className="ai-invitation"><div className="ai-orb"><Sparkles size={23} /></div><span className="eyebrow">POWERED BY AI</span><h2>把消息，变成线索。</h2><p>提炼关键信息，拆解影响逻辑。<br />让你的下一次追问更有方向。</p><button onClick={() => setDrawer({ stock: data.stock })}>与 AI 一起研究<ArrowRight size={16} /></button><span className="ai-connection"><span className={`status-dot ${lastReply ? '' : 'pending'}`} />{lastReply ? '模型请求已验证' : health?.configured ? '模型已配置 · 等待首次请求' : '模型尚未配置'}</span></section>
            </div>
          </div>
        </>}

        {view === 'lab' && <div className="lab-grid"><section className="panel lab-panel"><span className="lab-symbol"><Cpu size={30} /></span><span className="eyebrow">MODEL CONNECTION</span><h2>连接你的研究引擎</h2><p>配置从本地文件读取。发送测试请求，确认模型可以正常响应。</p><dl className="connection-details"><div><dt>服务提供方</dt><dd>{health?.provider ?? '未配置'}</dd></div><div><dt>当前模型</dt><dd className="mono">{health?.model ?? '未配置'}</dd></div><div><dt>密钥状态</dt><dd>{health?.configured ? <><CheckCircle2 size={14} className="mint-text" /> 已在后端配置</> : '请检查 config.local.json'}</dd></div><div><dt>请求路径</dt><dd className="mono">浏览器 → localhost → 模型服务</dd></div></dl><button className="primary-button" disabled={testing || !health?.configured} onClick={testConnection}>{testing ? <LoaderCircle className="spin" size={16} /> : <Zap size={16} />}{testing ? '正在请求模型…' : user ? '发送连接测试' : '登录后测试连接'}<ArrowRight size={15} /></button>{testError && <div className="error-box" role="alert">{testError}</div>}</section><section className="panel response-panel"><header className="panel-heading"><div><Code2 size={18} className="mint-text" /><h2>最近一次真实响应</h2></div><span className={`response-badge ${lastReply ? 'success' : ''}`}>{lastReply ? '200 OK' : '等待请求'}</span></header>{lastReply ? <><div className="response-stats"><div><span>耗时</span><strong className="mono">{(lastReply.elapsed_ms / 1000).toFixed(2)}<small>s</small></strong></div><div><span>Token 用量</span><strong className="mono">{lastReply.usage.total_tokens ?? '—'}</strong></div><div><span>请求编号</span><strong className="mono request-id">{lastReply.request_id}</strong></div></div><div className="live-label"><span className="status-dot" /> 模型原始响应</div><pre className="raw-response">{lastReply.content}</pre></> : <div className="empty-response"><Activity size={42} /><h3>一切就绪，等待第一个信号</h3><p>点击左侧按钮，或在看板中发起 AI 研判。<br />请求结果和用量会显示在这里。</p></div>}</section><section className="lab-note"><Sparkles size={17} /><div><strong>从连接测试到完整研判</strong><p>返回概览看板，选择一条示例新闻，点击「AI 研判」即可体验结构化评分、摘要和因果链。</p></div><button onClick={() => setView('dashboard')}>打开看板<ArrowRight size={14} /></button></section></div>}
        {loading && !data && <div className="loading-state"><LoaderCircle className="spin" size={25} /><span>正在连接本地工作空间…</span></div>}
        <footer className="page-footer"><span><Activity size={12} /> FinPulse <span className="footer-dot">·</span> 每一条信息，都值得更清晰的理解。</span><span>行情 / 新闻 / 社区数据为示例 <span className="footer-dot">·</span> AI 请求真实发送</span></footer>
      </main>
    </div>

    {drawer && <AiDrawer key={`${drawer.stock.code}-${drawer.news?.id ?? 'chat'}`} stock={drawer.stock} news={drawer.news} onClose={() => setDrawer(null)} onResult={setLastReply} />}
    {adding && <div className="modal-overlay" onMouseDown={event => { if (event.target === event.currentTarget) setAdding(false); }}><section className="stock-modal" role="dialog" aria-modal="true" aria-labelledby="stock-modal-title"><header><div><span className="eyebrow">BUILD YOUR WATCHLIST</span><h2 id="stock-modal-title">发现你关注的标的</h2></div><button className="icon-button" aria-label="关闭股票搜索" onClick={() => setAdding(false)}><X size={20} /></button></header><label className="stock-search"><Search size={18} /><input autoFocus aria-label="搜索股票" placeholder="名称、代码或拼音缩写，如 GZMT" value={search} onChange={event => setSearch(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') setAdding(false); }} /></label><span className="sample-label">演示股票库 · {matches.length} 个结果</span><div className="search-results">{matches.map(stock => <button key={stock.code} onClick={() => { if (!watchlist.includes(stock.code)) setWatchlist(previous => [...previous, stock.code]); setCode(stock.code); setView('dashboard'); setAdding(false); }}><span className="search-stock-info"><strong>{stock.name}<small>{stock.industry}</small></strong><span className="mono muted">{stock.exchange} {stock.code}</span></span><span className={tone(stock.change)}>{percent(stock.change)}</span><span className="search-stock-action">{watchlist.includes(stock.code) ? '打开' : '添加'}<Plus size={14} /></span></button>)}{matches.length === 0 && <div className="search-empty">没有匹配的演示股票，请尝试名称或代码。</div>}</div></section></div>}
    {info && <div className="modal-overlay" onMouseDown={event => { if (event.target === event.currentTarget) setInfo(false); }}><section className="info-modal" role="dialog" aria-modal="true" aria-labelledby="info-title"><button className="icon-button" aria-label="关闭说明" onClick={() => setInfo(false)}><X size={20} /></button><span className="brand-icon"><Activity size={26} /></span><h2 id="info-title">FinPulse 本地 Demo</h2><p>当前展示用于验证页面交互与模型请求。行情、新闻、社区情绪均为固定示例，后续可以接入你提供的功能与数据。</p><p>注册或登录后，AI 研判、问答和连接测试会通过本地后端调用已配置的真实模型。密钥保存在后端配置文件中。</p><button className="primary-button" onClick={() => setInfo(false)}>开始探索<ArrowRight size={15} /></button></section></div>}
  </div>;
}

function MessageIcon() { return <Radio size={17} className="mint-text" />; }
