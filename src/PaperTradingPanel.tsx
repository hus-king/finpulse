import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, CheckCircle2, Clock3, History, Info, LoaderCircle, RefreshCw, Search, ShieldCheck, WalletCards, X } from 'lucide-react';
import { api, ApiError } from './api';
import { useAuth } from './AuthContext';
import PaperStockSearch from './PaperStockSearch';
import { estimateFees, formatMoney, maxBuyQuantity, newRequestId } from './paperMath';
import type { PaperAccount, PaperHistory, PaperIntent, PaperQuote, PaperTrade } from './paperTypes';
import type { Stock } from './types';
import './paper.css';

const money = (value: number | null) => `¥ ${formatMoney(value)}`;
const signed = (value: number) => `${value > 0 ? '+' : ''}${formatMoney(value)}`;
const tone = (value: number) => value > 0 ? 'up' : value < 0 ? 'down' : '';
const date = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '—';
// These rejections run after the transaction's successful-request lookup.
// Auth, CSRF, routing and schema failures cannot resolve an earlier uncertain request.
const definitiveRejections = new Set(['UNKNOWN_STOCK', 'UNSUPPORTED_STOCK', 'MARKET_CLOSED', 'CALENDAR_UNKNOWN',
  'QUOTE_UNAVAILABLE', 'QUOTE_STALE', 'INVALID_SIDE', 'INVALID_QUANTITY', 'INSUFFICIENT_POSITION', 'T_PLUS_ONE', 'INSUFFICIENT_CASH']);

function pendingIntent(owner?: string): PaperIntent | null {
  if (!owner) return null;
  try {
    const value = JSON.parse(sessionStorage.getItem(`finpulse-paper-pending:${owner}`) ?? 'null');
    if (value && typeof value.request_id === 'string' && /^[0-9a-f-]{36}$/.test(value.request_id)
        && typeof value.code === 'string' && /^\d{6}$/.test(value.code)
        && (value.side === 'buy' || value.side === 'sell') && Number.isSafeInteger(value.quantity)
        && value.quantity > 0 && value.quantity <= 1_000_000) return value;
  } catch { /* An unavailable browser store does not block a new account. */ }
  return null;
}

export default function PaperTradingPanel({ initialCode }: { initialCode: string }) {
  const { user, loading: authLoading, requestLogin, refreshSession } = useAuth();
  const [restored] = useState(() => pendingIntent(user?.id));
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [history, setHistory] = useState<PaperHistory | null>(null);
  const [reading, setReading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [readError, setReadError] = useState('');
  const [code, setCode] = useState(restored?.code ?? initialCode);
  const [quote, setQuote] = useState<PaperQuote | null>(null);
  const [quoteError, setQuoteError] = useState('');
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [picking, setPicking] = useState(false);
  const [side, setSide] = useState<'buy' | 'sell'>(restored?.side ?? 'buy');
  const [quantity, setQuantity] = useState(restored ? String(restored.quantity) : initialCode.startsWith('688') ? '200' : '100');
  const [submitting, setSubmitting] = useState(false);
  const [unknown, setUnknown] = useState(!!restored);
  const [tradeError, setTradeError] = useState(restored ? '有一笔尚未确认的请求，请重试确认原成交，或刷新查看成交记录。' : '');
  const [notice, setNotice] = useState('');
  const lifecycle = useRef<AbortController | null>(null);
  const intent = useRef<PaperIntent | null>(restored);
  const busy = useRef(false);
  const accountVersion = useRef(0);
  function saveIntent(value: PaperIntent | null) {
    if (!user) return;
    try {
      const key = `finpulse-paper-pending:${user.id}`;
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    } catch { /* The in-memory retry remains available if browser storage is blocked. */ }
  }

  const loadAccount = useCallback(async () => {
    const controller = lifecycle.current;
    if (!controller || controller.signal.aborted) return;
    const version = ++accountVersion.current;
    setReading(true);
    try {
      const [next, trades] = await Promise.all([
        api<PaperAccount>('/api/paper/account', undefined, controller.signal),
        api<PaperHistory>('/api/paper/trades', undefined, controller.signal),
      ]);
      if (!controller.signal.aborted && version === accountVersion.current) { setAccount(next); setHistory(trades); setReadError(''); }
    } catch (error) { if (!controller.signal.aborted && version === accountVersion.current) setReadError((error as Error).message); }
    finally { if (!controller.signal.aborted && version === accountVersion.current) setReading(false); }
  }, []);

  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    lifecycle.current = controller;
    void loadAccount();
    const timer = window.setInterval(() => { if (!busy.current) void loadAccount(); }, 30000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [user?.id, loadAccount]);

  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    let timer: number;
    setQuote(null); setQuoteError('');
    async function poll(force = false) {
      try {
        const value = await api<PaperQuote>(`/api/paper/quote/${code}${force ? '?refresh=true' : ''}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        setQuote(value); setQuoteError('');
        timer = window.setTimeout(() => { void poll(); }, Math.max(2, value.next_poll_seconds) * 1000);
      } catch (error) {
        if (!controller.signal.aborted) {
          setQuoteError((error as Error).message); setQuote(null);
          timer = window.setTimeout(() => { void poll(); }, 30000);
        }
      }
    }
    void poll(refreshNonce > 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [code, user?.id, refreshNonce]);

  const frozen = submitting || unknown;
  const selected = quote?.stock.code === code ? quote : null;
  const position = account?.positions.find(item => item.code === code);
  const amount = /^\d+$/.test(quantity) ? Number(quantity) : 0;
  const gross = selected?.price_fen && Number.isSafeInteger(amount) ? selected.price_fen * amount : null;
  const costs = gross !== null ? estimateFees(gross, side) : null;
  const buyable = account && selected?.price_fen ? maxBuyQuantity(account.cash_fen, selected.price_fen, selected.rules) : 0;
  const available = side === 'buy' ? buyable : position?.sellable ?? 0;
  const rule = selected?.rules;
  let invalid = '';
  if (!account) invalid = '正在读取模拟账户。';
  else if (!selected) invalid = quoteError || '正在获取交易报价。';
  else if (!selected.can_trade) invalid = selected.reason ?? '当前不能模拟成交。';
  else if (!Number.isSafeInteger(amount) || amount <= 0 || amount > selected.rules.max_quantity) invalid = '请输入规则允许的正整数股数。';
  else if (side === 'buy' && (amount < selected.rules.min_quantity || amount % selected.rules.quantity_step !== 0)) invalid = `${selected.rules.board}最小买入 ${selected.rules.min_quantity} 股，按 ${selected.rules.quantity_step} 股递增。`;
  else if (amount > available) invalid = side === 'buy' ? '可用现金不足，需计入股款和交易费用。' : '可卖数量不足，当日买入的股份受 T+1 限制。';
  else if (side === 'sell' && (selected.rules.quantity_step === 1 ? amount < 200 && amount !== available : amount % 100 !== 0 && amount % 100 !== available % 100)) invalid = '零股需要一次卖完，不能拆分零股余额。';

  function chooseStock(stock: Pick<Stock, 'code'>) {
    if (frozen) return;
    setQuote(null); setCode(stock.code); setQuantity(stock.code.startsWith('688') ? '200' : '100');
    setPicking(false); setTradeError(''); setNotice('');
  }

  async function submit() {
    if (busy.current || !user || (!unknown && invalid)) return;
    const controller = lifecycle.current;
    if (!controller || controller.signal.aborted) return;
    const request = intent.current ?? { request_id: newRequestId(), code, side, quantity: amount };
    intent.current = request;
    saveIntent(request);
    busy.current = true; setSubmitting(true); setTradeError(''); setNotice('');
    try {
      const trade = await api<PaperTrade>('/api/paper/trades', request, controller.signal);
      if (controller.signal.aborted) return;
      intent.current = null; setUnknown(false);
      saveIntent(null);
      setNotice(`模拟${trade.side === 'buy' ? '买入' : '卖出'}成功 · ${trade.name} ${trade.quantity} 股 · 成交价 ${money(trade.price_fen)}`);
      await loadAccount();
      setRefreshNonce(value => value + 1);
    } catch (error) {
      if (controller.signal.aborted) return;
      const uncertain = !(error instanceof ApiError) || error.status >= 500 || error.code === 'IDEMPOTENCY_CONFLICT'
        || unknown && !definitiveRejections.has(error.code ?? '');
      if (!uncertain) { intent.current = null; saveIntent(null); }
      setUnknown(uncertain);
      setTradeError(uncertain ? '成交结果尚未确认。请用原请求编号重试确认，服务器不会重复成交；也可刷新查看成交记录。' : (error as Error).message);
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) await refreshSession();
    } finally {
      busy.current = false;
      if (!controller.signal.aborted) setSubmitting(false);
    }
  }

  async function loadMore() {
    if (!history?.next_before_seq || moreLoading) return;
    const controller = lifecycle.current;
    if (!controller || controller.signal.aborted) return;
    const cursor = history.next_before_seq;
    setMoreLoading(true);
    try {
      const older = await api<PaperHistory>(`/api/paper/trades?before_seq=${cursor}`, undefined, controller.signal);
      if (!controller.signal.aborted) setHistory(previous => previous?.next_before_seq === cursor ? { items: [...previous.items, ...older.items], next_before_seq: older.next_before_seq } : previous);
    } catch (error) { if (!controller.signal.aborted) setReadError((error as Error).message); }
    finally { if (!controller.signal.aborted) setMoreLoading(false); }
  }

  if (!user) return <section className="panel paper-welcome"><span className="paper-welcome-icon"><WalletCards size={38} /></span><span className="paper-tag">PAPER TRADING</span><h2>用 20 万虚拟资金，练习你的交易判断。</h2><p>独立模拟账户 · A 股交易时段 · T+1 · 持仓盈亏与成交记录</p><button className="primary-button" disabled={authLoading} onClick={() => requestLogin('登录后拥有独立的 20 万元模拟账户，资金与成交记录保存在你的账号下。')}>登录体验模拟盘<ArrowUpRight size={16} /></button><small>虚拟资金，不连接券商，不产生真实交易。</small></section>;

  return <div className="paper-workspace">
    <div className="paper-topbar"><div><span className="paper-tag"><ShieldCheck size={13} />虚拟资金 · 模拟成交</span><span className={`paper-session ${account?.market.can_trade ? 'trading' : ''}`}><Clock3 size={13} />{account?.market.label ?? '读取交易状态'}<small>北京时间</small></span></div><button className="paper-refresh" aria-label="刷新模拟账户" disabled={reading || submitting} onClick={() => { void loadAccount(); }}><RefreshCw size={15} className={reading ? 'spin' : ''} />刷新账户</button></div>
    {readError && <div className="paper-error" role="alert">{readError}<button onClick={() => { void loadAccount(); }}>重试</button></div>}
    <div className="paper-stats">
      <section className="panel paper-stat paper-equity"><span>模拟账户总资产</span><strong className="mono" data-testid="paper-equity">{money(account?.equity_fen ?? null)}</strong><small>初始本金 ¥ 200,000.00</small></section>
      <section className="panel paper-stat"><span>可用现金</span><strong className="mono" data-testid="paper-cash">{money(account?.cash_fen ?? null)}</strong><small>买入时需预留交易费用</small></section>
      <section className="panel paper-stat"><span>持仓市值</span><strong className="mono">{money(account?.market_value_fen ?? null)}</strong><small>{account?.positions.length ?? 0} 只股票 · 按最新缓存报价估值</small></section>
      <section className="panel paper-stat"><span>累计盈亏</span><strong className={`mono ${tone(account?.total_pnl_fen ?? 0)}`}>{account ? signed(account.total_pnl_fen) : '—'}<small>{account ? `${account.return_percent > 0 ? '+' : ''}${account.return_percent.toFixed(2)}%` : ''}</small></strong><small>已实现 {account ? signed(account.realized_pnl_fen) : '—'} · 浮动 {account ? signed(account.unrealized_pnl_fen) : '—'}</small></section>
    </div>

    <div className="paper-trading-grid">
      <section className="panel paper-ticket"><header><div><span className="paper-eyebrow">PLACE A SIMULATED TRADE</span><h2>买卖股票</h2></div><span className="paper-mini-tag">T+1</span></header>
        <div className="paper-selected-stock"><div><strong>{selected?.stock.name ?? code}</strong><span>{code}{rule && ` · ${rule.board}`}</span></div><button type="button" className="paper-icon-button" aria-label="更换交易股票" disabled={frozen} onClick={() => setPicking(!picking)}>{picking ? <X size={17} /> : <Search size={17} />}</button></div>
        {picking && <PaperStockSearch onSelect={chooseStock} />}
        <div className="paper-quote" data-testid="paper-quote" data-code={selected?.stock.code ?? ''}><div><strong className="mono">{money(selected?.price_fen ?? null)}</strong><small>{selected?.quote_status === 'fresh' ? '有效分钟报价' : selected?.quote_status === 'stale' ? '报价延迟 · 暂停成交' : '等待有效报价'}</small></div><button className="paper-icon-button" type="button" aria-label="刷新交易报价" onClick={() => setRefreshNonce(value => value + 1)}><RefreshCw size={16} className={selected?.refreshing ? 'spin' : ''} /></button><p><Clock3 size={12} />{date(selected?.as_of ?? null)} · 北京时间</p></div>
        <form onSubmit={event => { event.preventDefault(); void submit(); }}>
          <div className="paper-side"><button type="button" aria-pressed={side === 'buy'} disabled={frozen} className={side === 'buy' ? 'buy active' : ''} onClick={() => { setSide('buy'); setTradeError(''); setNotice(''); }}>买入</button><button type="button" aria-pressed={side === 'sell'} disabled={frozen} className={side === 'sell' ? 'sell active' : ''} onClick={() => { setSide('sell'); setTradeError(''); setNotice(''); }}>卖出</button></div>
          <label className="paper-quantity-label">交易股数<div><input aria-label="交易股数" type="number" inputMode="numeric" min="1" max={rule?.max_quantity ?? 1000000} step="1" value={quantity} disabled={frozen} onChange={event => { setQuantity(event.target.value); setTradeError(''); setNotice(''); }} /><span>股</span></div></label>
          <div className="paper-available"><span>{side === 'buy' ? '最多可买' : '当前可卖'} <strong>{available.toLocaleString()} 股</strong></span><button type="button" disabled={frozen || !available} onClick={() => setQuantity(String(available))}>填入全部</button></div>
          <p className="paper-lot-note">{rule ? `${rule.board}：最少 ${rule.min_quantity} 股，按 ${rule.quantity_step} 股递增；卖出零股需一次处理。` : '正在读取板块数量规则。'}</p>
          <dl className="paper-estimate"><div><dt>预估成交金额</dt><dd className="mono">{money(gross)}</dd></div><div><dt>佣金</dt><dd>{money(costs?.commission_fen ?? null)}</dd></div><div><dt>过户费{side === 'sell' && ' + 印花税'}</dt><dd>{money(costs ? costs.transfer_fen + costs.stamp_fen : null)}</dd></div><div className="paper-estimate-total"><dt>{side === 'buy' ? '预计支出' : '预计到账'}</dt><dd className="mono">{money(gross !== null && costs ? side === 'buy' ? gross + costs.fees_fen : gross - costs.fees_fen : null)}</dd></div></dl>
          {tradeError && <p className="paper-error" role="alert">{tradeError}</p>}
          {notice && <p className="paper-success" role="status"><CheckCircle2 size={15} />{notice}</p>}
          {invalid && !unknown && <p className="paper-validation"><Info size={14} />{invalid}</p>}
          <button type="submit" className={`paper-submit ${side}`} disabled={submitting || !unknown && !!invalid}>{submitting ? <LoaderCircle size={17} className="spin" /> : side === 'buy' ? <ArrowUpRight size={17} /> : <ArrowDownLeft size={17} />}{submitting ? '正在确认成交…' : unknown ? '重试确认成交' : `确认模拟${side === 'buy' ? '买入' : '卖出'}`}</button>
          <p className="paper-source">成交以服务器有效报价为准 · {selected?.source ?? '新浪分钟行情'}<br />分钟数据存在延迟，不模拟真实撮合、流动性和滑点。</p>
        </form>
      </section>

      <section className="panel paper-holdings"><header><div><span className="paper-eyebrow">YOUR POSITIONS</span><h2>我的持仓 <small>{account?.positions.length ?? 0}</small></h2></div><span className="paper-mini-tag">当日买入锁定</span></header>
        {account?.positions.length ? <div className="paper-table-scroll"><table><thead><tr><th>股票</th><th>持仓 / 可卖</th><th>成本 / 估值价</th><th>市值</th><th>浮动盈亏</th></tr></thead><tbody>{account.positions.map(item => <tr key={item.code} data-testid={`paper-holding-${item.code}`} data-sellable={item.sellable}><td><button disabled={frozen} onClick={() => chooseStock(item)}><strong>{item.name}</strong><small>{item.code}</small></button></td><td><strong>{item.quantity.toLocaleString()} / {item.sellable.toLocaleString()}</strong><small>锁定 {item.locked.toLocaleString()} 股</small></td><td><strong className="mono">{formatMoney(item.average_cost_fen)} / {formatMoney(item.price_fen)}</strong><small>{item.valuation_status === 'estimated' ? '按成交价估算' : `${item.valuation_status === 'stale' ? '缓存 ' : ''}${date(item.price_as_of)}`}</small></td><td className="mono">{formatMoney(item.market_value_fen)}</td><td className={`mono ${tone(item.unrealized_pnl_fen)}`}>{signed(item.unrealized_pnl_fen)}</td></tr>)}</tbody></table></div> : <div className="paper-empty"><WalletCards size={35} /><h3>{reading ? '正在读取持仓' : '你的第一笔模拟交易，从这里开始。'}</h3><p>选择股票，输入股数，体验资金与持仓的变化。</p><div><span>20 万元虚拟本金</span><span>T+1 交易规则</span><span>账号独立记录</span></div></div>}
        <div className="paper-holdings-note"><Info size={14} /><p>买入费用计入持仓成本，卖出使用平均成本计算已实现盈亏。市值按缓存分钟价格估算，报价时间单独展示。</p></div>
      </section>
    </div>

    <section className="panel paper-history"><header><div><span className="paper-eyebrow">TRADE JOURNAL</span><h2><History size={18} />成交记录</h2></div><span className="paper-mini-tag">仅模拟成交</span></header>
      {history?.items.length ? <div className="paper-table-scroll"><table><thead><tr><th>成交时间 · 北京时间</th><th>股票</th><th>方向</th><th>股数</th><th>成交价</th><th>成交金额</th><th>费用与依据</th><th>成交后现金</th></tr></thead><tbody>{history.items.map(trade => <tr key={trade.request_id}><td>{date(trade.executed_at)}</td><td><strong>{trade.name}</strong><small>{trade.code}</small></td><td><span className={`paper-trade-side ${trade.side}`}>{trade.side === 'buy' ? '买入' : '卖出'}</span></td><td>{trade.quantity.toLocaleString()}</td><td className="mono">{formatMoney(trade.price_fen)}</td><td className="mono">{formatMoney(trade.gross_fen)}</td><td><details><summary>{money(trade.fees_fen)}</summary><div>佣金 {money(trade.commission_fen)}<br />过户费 {money(trade.transfer_fen)}<br />印花税 {money(trade.stamp_fen)}<br />{trade.source}<br />报价 {date(trade.quote_as_of)}<br />已实现 {signed(trade.realized_pnl_fen)}</div></details></td><td className="mono">{formatMoney(trade.cash_after_fen)}</td></tr>)}</tbody></table></div> : <div className="paper-history-empty">{reading ? '正在读取成交记录…' : '暂时没有成交记录，每笔成功交易都会保存在这里。'}</div>}
      {history?.next_before_seq && <button className="paper-load-more" disabled={moreLoading} onClick={() => { void loadMore(); }}>{moreLoading ? '正在加载…' : '加载更早成交'}</button>}
    </section>
    <details className="panel paper-rules"><summary><ShieldCheck size={16} />模拟盘交易规则与费用</summary><div><p>每个账号初始资金 20 万元；仅在核验交易日 09:30–11:30、13:00–14:57 模拟成交。当天买入的股份下一交易日才能卖出。集合竞价、盘后和未经核验的交易日暂不支持。</p><p>主板、创业板买入为 100 股整数倍；科创板至少 200 股，之后可逐股增加。卖出时零股需一次处理；不支持融资、卖空、限价挂单或撤单。</p><p>固定模拟费用：双向佣金 0.03%，每笔最低 5 元；双向过户费 0.001%；卖出印花税 0.05%。费用分别四舍五入到分，不代表实际券商收费。</p><p>成交使用新鲜、有效的未复权 1 分钟价格；延迟或缺失行情暂停成交。估值可使用缓存或最近成交价格。第一版不处理分红、送股和拆股；所有资金均为虚拟资金。</p></div></details>
  </div>;
}
