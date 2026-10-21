import { useCallback, useEffect, useState } from 'react';
import { Bot, Pause, Play, RefreshCw } from 'lucide-react';
import { api } from './api';
import UserStrategyEditor from './UserStrategyEditor';
import type { PaperAutomationState, PaperStrategy, QuantDecision } from './paperTypes';

const percent = (value?: number) => value === undefined ? '—' : `${(value * 100).toFixed(2)}%`;
const stamp = (value?: string) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '尚无';

export default function PaperAutomation({ disabled, onModeChange }: { disabled: boolean; onModeChange: (mode: 'manual' | 'algorithm' | null) => void }) {
  const [state, setState] = useState<PaperAutomationState | null>(null);
  const [strategies, setStrategies] = useState<PaperStrategy[]>([]);
  const [selected, setSelected] = useState('manual');
  const [decisions, setDecisions] = useState<QuantDecision[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [moreBusy, setMoreBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const strategy = strategies.find(item => item.id === (selected === 'manual' ? state?.strategy_id : selected));

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [next, catalogue, history] = await Promise.all([
        api<PaperAutomationState>('/api/paper/automation', undefined, signal),
        api<{ items: PaperStrategy[] }>('/api/paper/strategies', undefined, signal),
        api<{ items: QuantDecision[]; next_before: string | null }>('/api/paper/automation/decisions', undefined, signal),
      ]);
      if (signal?.aborted) return;
      setState(next); setStrategies(catalogue.items); setDecisions(history.items); setCursor(history.next_before);
      onModeChange(next.mode); setError('');
    } catch (err) { if (!signal?.aborted) { setError((err as Error).message); onModeChange(null); } }
  }, [onModeChange]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => { void load(controller.signal); }, 30000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [load]);
  useEffect(() => { if (state && !dirty) setSelected(state.mode === 'manual' ? 'manual' : state.strategy_id ?? 'manual'); }, [state, dirty]);

  async function change(mode: 'manual' | 'algorithm', id?: string) {
    if (!state || busy) return;
    setBusy(true); setError('');
    try {
      const target = strategies.find(item => item.id === id);
      const next = await api<PaperAutomationState>('/api/paper/automation', { mode, strategy_id: id ?? null, expected_revision: state.revision, ...(target?.origin === 'user' ? { strategy_revision: target.revision } : {}) }, undefined, 'PUT');
      setState(next); setDirty(false); onModeChange(next.mode);
    } catch (err) { await load(); setDirty(false); setError((err as Error).message); }
    finally { setBusy(false); }
  }

  async function more() {
    if (!cursor || moreBusy) return;
    setMoreBusy(true);
    try {
      const data = await api<{ items: QuantDecision[]; next_before: string | null }>(`/api/paper/automation/decisions?before=${encodeURIComponent(cursor)}`);
      setDecisions(items => [...items, ...data.items]); setCursor(data.next_before);
    } catch (err) { setError((err as Error).message); }
    finally { setMoreBusy(false); }
  }

  const unchanged = state && selected === (state.mode === 'manual' ? 'manual' : state.strategy_id);
  const displayed = strategy ?? strategies[0];
  const model = displayed?.report?.test?.model;
  return <section className="panel paper-automation" aria-label="模拟盘操作方式">
    <header><div><span className="paper-eyebrow">ACCOUNT PILOT</span><h2><Bot size={19} />自己操作，或选择算法托管</h2></div><span className={`paper-pilot-status ${state?.mode === 'algorithm' ? 'active' : ''}`}>{state ? state.mode === 'algorithm' ? '算法托管中' : '手动操作' : '读取操作方式'}</span></header>
    <div className="paper-pilot-controls"><label>操作方式<select aria-label="操作方式" value={selected} disabled={busy || disabled || !state} onChange={event => { setSelected(event.target.value); setDirty(true); }}><option value="manual">自己操作 · 手动交易</option><optgroup label="官方算法">{strategies.filter(item => item.origin !== 'user').map(item => <option key={item.id} value={item.id} disabled={!item.available}>{item.name}{item.available ? ' · 官方' : ' · 暂不可用'}</option>)}</optgroup><optgroup label="我的策略">{strategies.filter(item => item.origin === 'user').map(item => <option key={item.id} value={item.id} disabled={!item.available}>{item.name} · 我的策略 · r{item.revision}{item.available ? '' : ' · 暂不可用'}</option>)}</optgroup></select></label>
      <button className="primary-button" disabled={busy || disabled || !state || !!unchanged || selected !== 'manual' && !strategy?.available} onClick={() => { void change(selected === 'manual' ? 'manual' : 'algorithm', selected === 'manual' ? undefined : selected); }}>{busy ? <RefreshCw className="spin" size={15} /> : <Play size={15} />}{selected === 'manual' ? '切回手动' : strategy?.origin === 'user' ? '启用我的策略' : '启用官方算法'}</button>
      {state?.mode === 'algorithm' && <button className="paper-pause" disabled={busy} onClick={() => { void change('manual'); }}><Pause size={15} />暂停并手动接管</button>}
    </div>
    <p className="paper-pilot-note">启用后，算法会在交易日北京时间 10:00 后自动管理此模拟账户，关闭网页后继续运行。切换保留持仓；股票池外持仓不自动清仓。托管期间手动新单暂停。</p>
    {disabled && <p className="paper-pilot-note">请先确认当前未完成的交易请求，再切换操作方式。</p>}
    {error && <div className="paper-error" role="alert">{error}<button onClick={() => { void load(); }}>重新读取</button></div>}
    {state && !state.scheduler_enabled && <p className="paper-pilot-warning">当前实例未开启自动执行，托管配置会保存；由已开启调度的服务器处理。</p>}
    {state?.reason && <p className="paper-pilot-warning">{state.reason}</p>}
    {state?.mode === 'algorithm' && <div className="paper-pilot-runtime"><span>最近检查 {stamp(state.runtime.checked_at)}</span><span>{state.runtime.reason ?? (state.last_decision?.status === 'completed' ? '今日决策已完成' : '等待交易时段或首轮检查')}</span><span>{state.next_check}</span></div>}
    {displayed?.available && displayed.origin !== 'user' && <details className="paper-model-report"><summary>官方策略 · 训练与样本外表现</summary><div>
      <p>日频量价多因子神经网络 · {displayed.version} · 最多 5 只股票 · 单票 20% · 总仓 80% · 每日成交额 30% 上限 · 回撤 15% 暂停。</p>
      <div className="paper-model-metrics"><span>样本外收益<strong className={model && model.total_return > 0 ? 'up' : 'down'}>{percent(model?.total_return)}</strong></span><span>最大回撤<strong>{percent(model?.max_drawdown)}</strong></span><span>年化收益<strong>{percent(model?.annualized_return)}</strong></span><span>卖出成交数<strong>{model?.trades ?? '—'}</strong></span></div>
      {model && model.total_return < 0 && <p className="paper-pilot-warning">研究版：这段样本外测试亏损 {percent(-model.total_return)}。请结合基准比较评估，仅供模拟盘实验。</p>}
      <p>测试区间 {model?.start ?? '—'} 至 {model?.end ?? '—'}。训练、验证与测试按日期隔离；收益已计固定模拟费用及双边 10bp 滑点。</p>
      {displayed?.report?.test && <div className="paper-table-scroll"><table><thead><tr><th>同条件比较</th><th>收益</th><th>最大回撤</th></tr></thead><tbody>{([['model','官方模型'],['momentum','简单动量'],['equal_weight','等权配置']] as const).map(([key, label]) => <tr key={key}><td>{label}</td><td>{percent(displayed.report?.test?.[key]?.total_return)}</td><td>{percent(displayed.report?.test?.[key]?.max_drawdown)}</td></tr>)}</tbody></table></div>}
      {displayed.report?.limits?.map(text => <p className="paper-pilot-note" key={text}>{text}</p>)}
    </div></details>}
    {displayed?.origin === 'user' && <div className="paper-rule-summary"><strong>我的规则策略 · 尚未回测</strong><p>{displayed.description}</p><p>{displayed.version} · 股票池 {displayed.universe?.length ?? 0} 只 · 最多 {displayed.rules_summary?.top_n ?? 5} 只 · 单票 {percent(displayed.rules_summary?.per_stock_weight)} · 总仓 {percent(displayed.rules_summary?.max_total_weight)}</p><p>继续受共同风控限制：每日成交额 30%、最多 5 只、总仓 80%、单票 20%、回撤 15% 暂停。</p></div>}
    <UserStrategyEditor strategies={strategies} activeId={state?.mode === 'algorithm' ? state.strategy_id : null} onChanged={load} />
    {strategies.filter(item => !item.available).map(item => <p className="paper-pilot-warning" key={item.id}>{item.name}：{item.reason}</p>)}
    <details className="paper-decisions"><summary>算法决策与执行记录 {decisions.length ? `· ${decisions.length}` : ''}</summary>
      {decisions.length ? decisions.map(decision => <article key={decision.key}><strong>{decision.date} · {decision.status === 'completed' ? '已完成' : '执行中，可恢复'}</strong><small>{decision.model_version} · 信号日 {decision.as_of}</small><p>{decision.reason ?? '根据历史量价信号生成目标组合'}</p><p>目标：{Object.entries(decision.target_weights).map(([code, weight]) => `${code} ${percent(weight)}`).join(' / ') || '现金'}</p>{decision.results?.map(result => <p key={result.request_id}>{result.code} · {result.status === 'filled' ? `已成交 #${result.sequence}` : result.reason}</p>)}</article>) : <p className="paper-pilot-note">尚无算法决策。启用后在下一个有效交易时段检查。</p>}
      {cursor && <button disabled={moreBusy} onClick={() => { void more(); }}>{moreBusy ? '读取中…' : '加载更早决策'}</button>}
    </details>
  </section>;
}
