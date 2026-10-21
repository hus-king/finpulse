import { useEffect, useState } from 'react';
import { api } from './api';
import type { PaperStrategy, StrategySubmissionSchema, UserStrategyRecord } from './paperTypes';

function download(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function UserStrategyEditor({ strategies, activeId, onChanged }: { strategies: PaperStrategy[]; activeId: string | null; onChanged: () => Promise<void> }) {
  const [schema, setSchema] = useState<StrategySubmissionSchema | null>(null);
  const [draft, setDraft] = useState('');
  const [record, setRecord] = useState<UserStrategyRecord | null>(null);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const locked = !!record && record.id === activeId;
  const mine = strategies.filter(item => item.origin === 'user');
  useEffect(() => {
    const controller = new AbortController();
    api<StrategySubmissionSchema>('/api/paper/strategy-schema', undefined, controller.signal).then(setSchema).catch(err => { if (!controller.signal.aborted) setError((err as Error).message); });
    return () => controller.abort();
  }, []);

  function clearFeedback() { setError(''); setMessage(''); setConfirmDelete(false); }
  function packageValue() {
    if (new TextEncoder().encode(draft).length > (schema?.limits.max_package_bytes ?? 65536)) throw new Error('策略 JSON 不能超过64KiB。');
    let value: unknown;
    try { value = JSON.parse(draft); } catch { throw new Error('JSON 格式无效，请检查括号、引号和逗号。'); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('策略 JSON 必须是一个对象。');
    return value;
  }
  async function action(run: () => Promise<void>) {
    if (busy) return;
    clearFeedback(); setBusy(true);
    try { await run(); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  function useExample() {
    if (!schema) return;
    clearFeedback(); setRecord(null); setSelected(''); setDraft(JSON.stringify(schema.example, null, 2));
  }
  async function save(asNew = false) {
    await action(async () => {
      const strategy = packageValue();
      const next = record && !asNew
        ? await api<UserStrategyRecord>(`/api/paper/strategies/${encodeURIComponent(record.id)}`, { strategy, expected_revision: record.revision }, undefined, 'PUT')
        : await api<UserStrategyRecord>('/api/paper/strategies', { strategy });
      setRecord(next); setSelected(next.id); setDraft(JSON.stringify(next.strategy, null, 2));
      await onChanged(); setMessage(`已保存 · r${next.revision} · 可在操作方式中选择启用。`);
    });
  }

  return <details className="paper-user-strategies"><summary>我的策略 · 导入与管理</summary><section aria-label="我的 JSON 策略" className="paper-rule-editor">
    <p>上传 JSON 规则策略，由 FinPulse 每日自动执行。策略仅你可见；保存后需在上方选择并启用。每个账号最多 10 份。</p>
    <div className="paper-rule-toolbar"><label>已保存的策略<select aria-label="已保存的策略" value={selected} disabled={busy} onChange={event => setSelected(event.target.value)}><option value="">选择我的策略</option>{mine.map(item => <option key={item.id} value={item.id}>{item.name} · r{item.revision}</option>)}</select></label>
      <button disabled={busy || !selected} onClick={() => { void action(async () => { const next = await api<UserStrategyRecord>(`/api/paper/strategies/${encodeURIComponent(selected)}`); setRecord(next); setDraft(JSON.stringify(next.strategy, null, 2)); setMessage(`已读取 · r${next.revision}`); }); }}>读取策略</button>
      <button disabled={busy || !schema} onClick={useExample}>使用规则示例</button>
    </div>
    <div className="paper-rule-toolbar"><label className="paper-rule-file">导入 JSON 文件<input type="file" aria-label="导入 JSON 文件" accept=".json,application/json" disabled={busy || locked} onChange={event => {
      const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
      void action(async () => { if (file.size > (schema?.limits.max_package_bytes ?? 65536)) throw new Error('文件不能超过64KiB。'); const text = await file.text(); setDraft(text); setMessage('已导入草稿，请校验后保存。'); });
    }} /></label>
      <button disabled={!schema || busy} onClick={() => download(schema?.example, 'finpulse-rules-example.json')}>下载示例</button>
      <button disabled={!schema || busy} onClick={() => download(schema?.json_schema, 'finpulse-rules-v1.schema.json')}>下载 JSON Schema</button>
    </div>
    {record && <p className="paper-rule-version">正在编辑：{String(record.strategy.name)} · r{record.revision} · {record.digest.slice(0, 12)}{locked ? ' · 托管中，请先暂停再修改或删除' : ''}</p>}
    <label className="paper-rule-json">策略 JSON<textarea aria-label="策略 JSON" spellCheck={false} value={draft} readOnly={busy || locked} onChange={event => { setDraft(event.target.value); clearFeedback(); }} placeholder="使用规则示例，或导入符合 finpulse.rules.v1 的 JSON 文件" /></label>
    <div className="paper-rule-toolbar">
      <button disabled={busy || !draft} onClick={() => { void action(async () => { const result = await api<{ digest: string }>('/api/paper/strategies/validate', { strategy: packageValue() }); setMessage(`校验通过 · ${result.digest.slice(0, 12)}。仅检查格式与限额，尚未回测收益。`); }); }}>校验策略</button>
      {record && <button className="primary-button" disabled={busy || locked || !draft} onClick={() => { void save(); }}>保存修改</button>}
      <button className={record ? '' : 'primary-button'} disabled={busy || !draft || mine.length >= 10} onClick={() => { void save(true); }}>保存为新策略</button>
      <button disabled={busy || !draft} onClick={() => { clearFeedback(); try { download(packageValue(), 'finpulse-my-rules.json'); } catch (err) { setError((err as Error).message); } }}>导出 JSON</button>
      {record && <button className="paper-rule-delete" disabled={busy || locked} onClick={() => setConfirmDelete(value => !value)}>删除策略</button>}
    </div>
    {confirmDelete && record && <div className="paper-rule-confirm"><span>删除“{String(record.strategy.name)}”？已有成交和决策记录保留。</span><button disabled={busy || locked} onClick={() => { void action(async () => { await api(`/api/paper/strategies/${encodeURIComponent(record.id)}`, { expected_revision: record.revision }, undefined, 'DELETE'); setRecord(null); setSelected(''); await onChanged(); setMessage('策略已删除。'); }); }}>确认删除此策略</button><button onClick={() => setConfirmDelete(false)}>取消</button></div>}
    {busy && <p role="status">正在处理…</p>}
    {error && <p className="paper-error" role="alert">{error}</p>}
    {message && <p className="paper-rule-success" role="status">{message}</p>}
    <p className="paper-pilot-note">规则示例尚未回测。校验与保存不会下单，也不调用分析模型。仅启用后由模拟盘执行；规则报错时跳过本轮并保留持仓。</p>
    <details className="paper-rule-reference"><summary>统一提交规范与可用指标</summary><p>版本 finpulse.rules.v1 · 股票池 1–30 只 · 评分按降序选前 1–5 只 · 全部筛选条件需满足 · 表达式最多 128 节点 / 6 层。支持 add、sub、mul、div、min、max、abs；比较 gt、gte、lt、lte、eq。</p><p>权重为小数（0.2 表示 20%）。使用上一完整交易日的历史量价，按北京时间交易日 10:00 后执行；不支持上传 Python 或 JavaScript。</p><dl>{Object.entries(schema?.features ?? {}).map(([name, description]) => <div key={name}><dt>{name}</dt><dd>{description}</dd></div>)}</dl><a href="/api/paper/strategy-schema" target="_blank" rel="noreferrer">查看完整接口规范与示例</a></details>
  </section></details>;
}
