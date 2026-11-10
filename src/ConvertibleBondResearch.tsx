import { Fragment, useEffect, useState } from 'react';
import { ArrowRight, ChevronDown, ExternalLink, LoaderCircle, RefreshCw } from 'lucide-react';
import { api } from './api';
import './convertible-bond-research.css';

type Metric = number | null;
type Peer = { code: string; name: string; conversion_value: Metric; premium: Metric };
type Entry = {
  code: string; name: string; stock_code: string; stock_name: string; industry: string | null;
  price: Metric; conversion_value: Metric; stock_price: Metric; conversion_price: Metric;
  rating: string | null; maturity_date: string | null; turnover_20d: Metric;
  stock_return_20d: Metric; industry_return_20d: Metric;
  risk: 'clear' | 'call' | 'credit' | 'exit' | 'unknown'; risk_reason: string | null; tradable: boolean;
  last_trade_date?: string | null; last_conversion_date?: string | null; source_url?: string | null; quote_at?: string | null;
  reference_price: Metric; discount: Metric; relative_return: Metric; upside: Metric; downside: Metric;
  peer_count: number | null; peer_premium: Metric; peers: Peer[];
  up_reference: Metric; down_reference: Metric; rank: number | null; reasons: string[]; eligible: boolean;
};
type Decision = {
  action: 'hold' | 'enter' | 'switch' | 'exit' | 'cash' | 'unavailable'; reason: string;
  candidate_code: string | null; position_code: string | null; thesis_failures: number;
  upside_edge: Metric; downside_deterioration: Metric; switch_cost: Metric;
  switch_threshold: Metric; downside_limit: Metric;
};
type Execution = {
  status: 'pending' | 'filled' | 'blocked'; date: string | null; reason: string | null;
  sell_price: Metric; buy_price: Metric; cost: Metric;
};
type History = {
  as_of: string; decision: Decision; execution: Execution | null; position: Entry | null; candidate: Entry | null;
};
type Research = {
  status: 'ready' | 'no_data' | 'data_error'; as_of: string | null; generated_at: string | null;
  next_review?: string | null; preview?: boolean; close_confirmed?: boolean | null; collected_at?: string | null; sources: string[]; warnings: string[];
  position_code: string | null; entries: Entry[]; decision: Decision | null; execution: Execution | null; history: History[];
};

const decisionLabels: Record<Decision['action'], string> = {
  hold: '继续持有', enter: '拟建仓', switch: '拟换仓', exit: '风险退出', cash: '空仓观察', unavailable: '暂无法评估',
};
const executionLabels: Record<Execution['status'], string> = {
  pending: '待模拟成交', filled: '已模拟成交', blocked: '模拟成交受阻',
};
const riskLabels: Record<Entry['risk'], string> = {
  clear: '正常', call: '强赎', credit: '信用风险', exit: '临近退出', unknown: '条款待核实',
};

const numeric = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const number = (value: Metric | undefined, digits = 2) => numeric(value) ? value.toFixed(digits) : '—';
const change = (value: Metric | undefined, unit = '%') => numeric(value) ? `${value > 0 ? '+' : ''}${(value * 100).toFixed(2)}${unit}` : '—';
const points = (value: number) => `${(value * 100).toFixed(2)} 个百分点`;
const money = (value: Metric | undefined) => numeric(value) ? `${number(value)} 元` : '—';
const sign = (value: Metric | undefined) => !numeric(value) || value === 0 ? undefined : value > 0 ? 'cb-up' : 'cb-down';
const formatTurnover = (value: Metric) => numeric(value) ? `${(value / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} 万元` : '—';
const labels = (entry: Entry | null | undefined, code: string | null | undefined, fallback: string) => entry?.name || code || fallback;

function formatTime(value?: string | null) {
  if (!value) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function sourceLink(value: string | null | undefined) {
  try {
    const url = new URL(value ?? '');
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

type BondExpansion = {
  openId: string | null;
  toggle: (id: string) => void;
  close: () => void;
  openStock: (code: string) => Promise<void>;
};

function bondDetailId(slot: string, entry?: Entry | null) {
  return `cb-detail-${slot}-${entry?.code ?? 'empty'}`;
}

function EntryButton({ entry, code, empty, detailId, expansion }: {
  entry?: Entry | null; code?: string | null; empty: string; detailId: string; expansion: BondExpansion;
}) {
  if (!entry) return <span>{code || empty}</span>;
  return <button className="cb-entry-button" type="button" id={`${detailId}-trigger`}
    aria-expanded={expansion.openId === detailId} aria-controls={expansion.openId === detailId ? detailId : undefined}
    onClick={() => expansion.toggle(detailId)}>{entry.name}<ChevronDown size={14} aria-hidden="true" /></button>;
}

function Status({ entry }: { entry: Entry }) {
  return <span className={`cb-state ${entry.risk !== 'clear' || !entry.tradable ? 'cb-warning' : ''}`}>
    {entry.risk === 'clear' && !entry.tradable ? '暂不可成交' : riskLabels[entry.risk]}
  </span>;
}

function Comparison({ decision, position, candidate, asOf, slot, expansion }: {
  decision: Decision; position: Entry | null; candidate: Entry | null; asOf?: string | null; slot: string; expansion: BondExpansion;
}) {
  const positionId = bondDetailId(`${slot}-position`, position);
  const candidateId = bondDetailId(`${slot}-candidate`, candidate);
  const { upside_edge: edge, downside_deterioration: deterioration, switch_threshold: threshold, switch_cost: cost, downside_limit: downsideLimit } = decision;
  const comparesTwo = decision.position_code && decision.candidate_code && decision.position_code !== decision.candidate_code;
  const rows: [string, Metric | undefined, Metric | undefined][] = [
    ['相对低估', position?.discount, candidate?.discount],
    ['正股 20 日相对行业收益', position?.relative_return, candidate?.relative_return],
    ['正股涨 10% 时的债价情景', position?.upside, candidate?.upside],
    ['正股跌 10% 时的债价情景', position?.downside, candidate?.downside],
  ];
  return <div className="cb-compare">
    <div className="cb-table-scroll">
      <table className="cb-table cb-compare-table">
        <thead><tr><th scope="col">决策时比较</th><th scope="col"><small>原持仓</small><EntryButton entry={position} code={decision.position_code} empty="空仓" detailId={positionId} expansion={expansion} /></th>
          <th scope="col"><small>首选候选</small><EntryButton entry={candidate} code={decision.candidate_code} empty="暂无" detailId={candidateId} expansion={expansion} /></th></tr></thead>
        <tbody>{rows.map(([name, previous, next]) => <tr key={name}><th scope="row">{name}</th>
          <td className={sign(previous)}>{change(previous)}</td><td className={sign(next)}>{change(next)}</td>
        </tr>)}
          <tr><th scope="row">交易与条款</th><td>{position ? <Status entry={position} /> : '—'}</td>
            <td>{candidate ? <Status entry={candidate} /> : '—'}</td></tr>
        </tbody>
      </table>
    </div>
    <BondDetails entry={position} asOf={asOf} detailId={positionId} expansion={expansion} />
    <BondDetails entry={candidate} asOf={asOf} detailId={candidateId} expansion={expansion} />
    {comparesTwo && (numeric(edge) && numeric(threshold) || numeric(deterioration) && numeric(downsideLimit)) &&
      <section className="cb-criteria" aria-label="常规换仓门槛"><h3>常规换仓门槛</h3>
        {decision.action === 'switch' && decision.thesis_failures >= 2 && <p className="cb-empty">本次换仓由原持仓逻辑失效触发。</p>}
        <div className="cb-checks">
        {numeric(edge) && numeric(threshold) && <div><strong>上行优势 {edge - threshold > 1e-12 ? '通过' : '未通过'}</strong>
          <p>{change(edge, ' 个百分点')}，要求超过 {points(threshold)}{numeric(cost) ? `（含估计换仓成本 ${points(cost)}）` : ''}</p></div>}
        {numeric(deterioration) && numeric(downsideLimit) && <div><strong>下行门槛 {deterioration - downsideLimit <= 1e-12 ? '通过' : '未通过'}</strong>
          <p>损失变化 {change(deterioration, ' 个百分点')}，增加上限 {points(downsideLimit)}</p></div>}
      </div></section>}
  </div>;
}

function BondDetails({ entry, asOf, detailId, expansion }: {
  entry?: Entry | null; asOf?: string | null; detailId: string; expansion: BondExpansion;
}) {
  if (!entry || expansion.openId !== detailId) return null;
  return <ExpandedBond key={detailId} entry={entry} asOf={asOf} detailId={detailId} expansion={expansion} />;
}

function ExpandedBond({ entry, asOf, detailId, expansion }: {
  entry: Entry; asOf?: string | null; detailId: string; expansion: BondExpansion;
}) {
  const [stockError, setStockError] = useState('');
  const [opening, setOpening] = useState(false);
  const officialLink = sourceLink(entry.source_url);
  const facts = [
    ['债价', money(entry.price)], ['债券行情时间', formatTime(entry.quote_at)], ['正股价', money(entry.stock_price)], ['当日转股价', money(entry.conversion_price)],
    ['转股价值', money(entry.conversion_value)], ['同类券参考价', money(entry.reference_price)],
    ['同类溢价率中位数', change(entry.peer_premium)], ['相对低估', change(entry.discount)],
    ['近 20 日成交额中位数', formatTurnover(entry.turnover_20d)], ['债项评级', entry.rating || '—'], ['到期日', entry.maturity_date || '—'],
    ['所属行业', entry.industry || '—'], ['正股近 20 日收益', change(entry.stock_return_20d)],
    ['行业近 20 日收益', change(entry.industry_return_20d)], ['正股相对行业收益', change(entry.relative_return)],
  ];
  async function openStock() {
    setOpening(true);
    setStockError('');
    try {
      await expansion.openStock(entry.stock_code);
    } catch (error) {
      setStockError((error as Error).message);
    } finally {
      setOpening(false);
    }
  }
  return <section className="cb-inline-detail" id={detailId} aria-labelledby={`${detailId}-heading`}>
    <header className="cb-detail-header"><div><h3 id={`${detailId}-heading`}>{entry.name}明细</h3><p>{entry.code} · {entry.stock_name || entry.stock_code}{asOf ? ` · ${asOf} 快照` : ''}</p></div>
      <button className="cb-button" type="button" onClick={expansion.close}>收起明细 <ChevronDown size={14} className="cb-chevron-up" aria-hidden="true" /></button></header>
    <div className="cb-detail-content">
      <div className="cb-section-header"><Status entry={entry} />{/^\d{6}$/.test(entry.stock_code) &&
        <button className="cb-button" type="button" disabled={opening} onClick={() => { void openStock(); }}>查看正股行情 <ArrowRight size={14} /></button>}</div>
      {stockError && <p className="cb-notice" role="alert">{stockError}</p>}
      <dl className="cb-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <section className="cb-detail-section"><h4>债价情景测算</h4>
        <dl className="cb-facts cb-scenarios">
          <div><dt>正股上涨 10%</dt><dd className={sign(entry.upside)}>{change(entry.upside)}</dd><small>参考价 {money(entry.up_reference)}</small></div>
          <div><dt>正股下跌 10%</dt><dd className={sign(entry.downside)}>{change(entry.downside)}</dd><small>参考价 {money(entry.down_reference)}</small></div>
        </dl>
        <p>情景按转股价值变化重估同类券参考价。表中涨跌幅以参考价的变化除以当前债价，当前折价单独列示。</p>
      </section>
      <section className="cb-detail-section"><h4>可比券{numeric(entry.peer_count) ? ` · ${entry.peer_count} 只` : ''}</h4>
        {entry.peers.length ? <div className="cb-table-scroll"><table className="cb-table"><thead><tr>
          <th scope="col">转债</th><th scope="col">转股价值</th><th scope="col">转股溢价率</th></tr></thead><tbody>
          {entry.peers.map(peer => <tr key={peer.code}><th scope="row">{peer.name || peer.code} <small>{peer.code}</small></th>
            <td>{money(peer.conversion_value)}</td><td>{change(peer.premium)}</td></tr>)}
        </tbody></table></div> : <p>暂无可展示的可比券。</p>}
      </section>
      <section className="cb-detail-section"><h4>交易与条款</h4>
        {entry.risk_reason && <p>{entry.risk_reason}</p>}
        <dl className="cb-facts cb-scenarios">
          <div><dt>已知最后交易日</dt><dd>{entry.last_trade_date || '—'}</dd></div>
          <div><dt>已知最后转股日</dt><dd>{entry.last_conversion_date || '—'}</dd></div>
        </dl>
        {entry.reasons.length > 0 && <ul className="cb-reasons">{entry.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
        {officialLink && <a className="cb-source" href={officialLink} target="_blank" rel="noopener noreferrer">查看来源 <ExternalLink size={13} /></a>}
      </section>
    </div>
  </section>;
}

function CandidateTable({ entries, blocked, asOf, expansion }: {
  entries: Entry[]; blocked?: boolean; asOf?: string | null; expansion: BondExpansion;
}) {
  return <div className="cb-table-scroll cb-candidate-scroll"><table className="cb-table cb-candidate-table">
    <thead><tr><th scope="col">转债 / 正股</th><th scope="col">债价</th><th scope="col">相对低估</th><th scope="col">正股 20 日相对行业</th>
      <th scope="col">上涨情景</th><th scope="col">下跌情景</th><th scope="col">{blocked ? '未入围原因' : '交易状态'}</th></tr></thead>
    <tbody>{entries.map(entry => {
      const detailId = bondDetailId(blocked ? 'excluded' : 'candidate', entry);
      const expanded = expansion.openId === detailId;
      return <Fragment key={entry.code}>
        <tr className={expanded ? 'cb-expanded-row' : undefined}>
          <th scope="row"><span className="cb-row-name">{entry.rank !== null && !blocked && <small className="cb-rank">{entry.rank}</small>}
            <EntryButton entry={entry} empty="—" detailId={detailId} expansion={expansion} /></span>
            <small className="cb-row-code">{entry.code} · {entry.stock_name || entry.stock_code || '—'}</small></th>
          <td>{money(entry.price)}</td>
          <td className={sign(entry.discount)}>{change(entry.discount)}</td>
          <td className={sign(entry.relative_return)}>{change(entry.relative_return)}</td>
          <td className={sign(entry.upside)}>{change(entry.upside)}</td>
          <td className={sign(entry.downside)}>{change(entry.downside)}</td>
          <td>{blocked ? <span className="cb-block-reason">{entry.reasons.join(' · ') || entry.risk_reason || '暂无说明'}</span> : <Status entry={entry} />}</td>
        </tr>
        {expanded && <tr className="cb-detail-row"><td colSpan={7}>
          <BondDetails entry={entry} asOf={asOf} detailId={detailId} expansion={expansion} />
        </td></tr>}
      </Fragment>;
    })}</tbody>
  </table></div>;
}

function ExecutionDetails({ execution }: { execution: Execution }) {
  return <div className="cb-execution">
    <strong>{executionLabels[execution.status]}</strong>
    {execution.date && <span>{formatTime(execution.date)}</span>}
    {numeric(execution.sell_price) && <span>卖出价 {money(execution.sell_price)}</span>}
    {numeric(execution.buy_price) && <span>买入价 {money(execution.buy_price)}</span>}
    {numeric(execution.cost) && <span>成交成本 {change(execution.cost).replace(/^\+/, '')}</span>}
    {execution.reason && <p>{execution.reason}</p>}
  </div>;
}

export default function ConvertibleBondResearch({ onOpenStock }: { onOpenStock: (code: string) => Promise<void> }) {
  const [research, setResearch] = useState<Research | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [openDetailId, setOpenDetailId] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void api<Research>('/api/convertible-bonds/research', undefined, controller.signal)
      .then(result => { if (!controller.signal.aborted) setResearch(result); })
      .catch(error => { if (!controller.signal.aborted) setError((error as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload]);

  const entries = research?.entries ?? [];
  const eligible = entries.filter(entry => entry.eligible).sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity));
  const excluded = entries.filter(entry => !entry.eligible);
  const expansion: BondExpansion = {
    openId: openDetailId,
    toggle: id => setOpenDetailId(current => current === id ? null : id),
    close: () => {
      if (openDetailId) document.getElementById(`${openDetailId}-trigger`)?.focus();
      setOpenDetailId(null);
    },
    openStock: onOpenStock,
  };
  const decision = research?.decision;
  const activeHistory = research?.history.find(record => record.as_of === research.as_of);
  const findEntry = (code?: string | null) => code ? entries.find(entry => entry.code === code) ?? null : null;
  const currentPosition = findEntry(research?.position_code);
  const candidate = findEntry(decision?.candidate_code) ?? (decision?.candidate_code ? activeHistory?.candidate : eligible[0]) ?? null;
  const previousPosition = activeHistory?.position ?? findEntry(decision?.position_code);
  const holdingId = bondDetailId('holding', currentPosition);
  const decisionCandidateId = bondDetailId('decision', candidate);
  const previewCandidateId = bondDetailId('preview', candidate);
  const incumbentId = bondDetailId('incumbent', currentPosition);
  const incompletePreview = research?.preview && !eligible.length && entries.some(entry =>
    entry.risk === 'unknown' || !numeric(entry.relative_return) || !numeric(entry.turnover_20d));
  const replay = research?.preview && research.close_confirmed && eligible.length > 0;
  const quoteSession = research?.close_confirmed === true ? '收盘' : research?.close_confirmed === false ? '盘中' : '';

  return <div className="cb-research" aria-busy={loading} onKeyDown={event => {
    if (event.key === 'Escape' && openDetailId) expansion.close();
  }}>
    {decision && <section className="cb-panel cb-decision" aria-labelledby="cb-current-decision">
      <header className="cb-section-header"><div><p className="cb-kicker">本周决策 · 模拟跟踪</p>
        <h2 id="cb-current-decision">{decisionLabels[decision.action]}</h2></div></header>
      <div className="cb-meta"><span>信号日期 <strong>{research?.as_of || '—'}</strong></span>
        <span>下次复核 <strong>{research?.next_review || '每周最后一交易日收盘后'}</strong></span></div>
      <div className="cb-holdings">
        <div><span>当前研究持仓</span><strong><EntryButton entry={currentPosition} code={research?.position_code} empty="空仓" detailId={holdingId} expansion={expansion} /></strong></div>
        <div><span>首选候选</span><strong><EntryButton entry={candidate} code={decision.candidate_code} empty="暂无合格券" detailId={decisionCandidateId} expansion={expansion} /></strong></div>
      </div>
      <BondDetails entry={currentPosition} asOf={research?.as_of} detailId={holdingId} expansion={expansion} />
      <BondDetails entry={candidate} asOf={research?.as_of} detailId={decisionCandidateId} expansion={expansion} />
      <p className="cb-decision-reason">{decision.reason}</p>
      {research?.execution && <ExecutionDetails execution={research.execution} />}
      {(previousPosition || candidate) && <Comparison decision={decision} position={previousPosition} candidate={candidate} asOf={research?.as_of} slot="current" expansion={expansion} />}
    </section>}

    <section className="cb-panel" aria-labelledby="cb-candidate-heading">
      <header className="cb-section-header"><div><p className="cb-kicker">{replay ? '历史复算' : '候选筛选'}</p><h2 id="cb-candidate-heading">候选榜</h2>
        <p>按正股上涨 10% 的转债情景排序，展示前 5 只。点击券名查看估值依据。</p></div>
        <div className="cb-candidate-actions">
          {research && <span className="cb-state">{eligible.length} 只入围</span>}
          <button className="cb-button" type="button" disabled={loading} onClick={() => setReload(value => value + 1)}>
            {loading ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}重新读取
          </button>
        </div>
      </header>
      {research?.as_of && <div className="cb-meta"><span>行情截点 <strong>{research.as_of} {quoteSession}</strong></span>
        {research.collected_at && !research.close_confirmed && <span>行情采集 <strong>{formatTime(research.collected_at)}</strong></span>}
      </div>}
      {error && <p className="cb-notice" role="alert">{error}{research ? '，下方保留上次读取的快照。' : ''}</p>}
      {incompletePreview && <p className="cb-notice" role="status">数据未齐 · 排名暂不可用</p>}
      {research?.preview && research.close_confirmed === false && <p className="cb-empty">当前为盘中行情预览，等待核验收盘数据。</p>}
      {research?.status === 'no_data' && <p className="cb-notice">暂无可用研究快照。待行情和条款数据核验后展示候选。</p>}
      {research?.status === 'data_error' && <p className="cb-notice" role="status">研究数据暂无法核验，请查看下方的数据说明。</p>}
      {!decision && candidate && <><div className="cb-incumbent"><span>{replay ? '复算首选' : '预览首选'}</span>
        <EntryButton entry={candidate} empty="暂无" detailId={previewCandidateId} expansion={expansion} />
        <span>相对低估 {change(candidate.discount)} · 上行情景 {change(candidate.upside)}</span>
      </div><BondDetails entry={candidate} asOf={research?.as_of} detailId={previewCandidateId} expansion={expansion} /></>}
      {currentPosition && <><div className="cb-incumbent"><span>当前持仓</span>
        <EntryButton entry={currentPosition} empty="空仓" detailId={incumbentId} expansion={expansion} />
        <span>低估 {change(currentPosition.discount)} · 上行情景 {change(currentPosition.upside)}</span>
      </div><BondDetails entry={currentPosition} asOf={research?.as_of} detailId={incumbentId} expansion={expansion} /></>}
      {eligible.length ? <CandidateTable entries={eligible.slice(0, 5)} asOf={research?.as_of} expansion={expansion} />
        : <p className="cb-empty">{loading ? '正在读取候选。' : research?.status === 'ready' ? '当前没有合格候选。' : '暂无候选数据。'}</p>}
      {excluded.length > 0 && <details className="cb-disclosure"><summary>查看未入围 · {excluded.length} 只</summary>
        <CandidateTable entries={excluded} blocked asOf={research?.as_of} expansion={expansion} /></details>}
      {(research?.warnings.length || research?.sources.length) ? <details className="cb-disclosure">
        <summary>数据来源与说明{research.warnings.length > 0 ? ` · ${research.warnings.length} 项提示` : ''}</summary>
        <ul className="cb-reasons">{[...research.warnings, ...research.sources].map((item, index) => <li key={index}>{item}</li>)}</ul>
      </details> : null}
    </section>

    {!!research?.history.length && <section className="cb-panel" aria-labelledby="cb-history-heading">
      <header className="cb-section-header"><div><p className="cb-kicker">可追溯记录</p><h2 id="cb-history-heading">决策历史</h2>
        <p>每条记录保留当时的候选、换仓依据和模拟成交状态。</p></div></header>
      <div className="cb-history">{research.history.map(record => <details className="cb-disclosure" key={record.as_of}>
        <summary><time dateTime={record.as_of}>{record.as_of}</time><strong>{decisionLabels[record.decision.action]}</strong>
          <span>{labels(record.position, record.decision.position_code, '空仓')} · 首选 {labels(record.candidate, record.decision.candidate_code, '暂无')}</span>
          {record.execution && <span className="cb-state">{executionLabels[record.execution.status]}</span>}</summary>
        <p className="cb-decision-reason">{record.decision.reason}</p>
        {record.execution && <ExecutionDetails execution={record.execution} />}
        {(record.position || record.candidate) && <Comparison decision={record.decision} position={record.position} candidate={record.candidate}
          asOf={record.as_of} slot={`history-${record.as_of}`} expansion={expansion} />}
      </details>)}</div>
    </section>}
  </div>;
}
