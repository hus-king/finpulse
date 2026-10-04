import { useEffect, useState } from 'react';
import { Clock3, LoaderCircle, Play, Save } from 'lucide-react';
import { api } from './api';
import { dateTime } from './format';

interface Run { id: string; date: string; status: string; stage: string; users: number; stocks: Record<string, { status: string; reused?: boolean; warnings: string[] }>; warnings: string[]; finished_at?: string }
interface Schedule { enabled: boolean; morning_time: string; every_day: boolean; lookback_days: number; max_articles: number; instance_enabled: boolean; running: boolean; next_run: string | null; last_run: Run | null; active_research_jobs: number; research_parallelism: number; queue_limit: number }
const labels: Record<string, string> = { queued: '等待中', running: '执行中', completed: '完成', partial: '部分来源不可用', failed: '失败', interrupted: '已中断' };

export default function MorningAdmin() {
  const [status, setStatus] = useState<Schedule | null>(null);
  const [form, setForm] = useState({ enabled: true, morning_time: '08:30', every_day: true, lookback_days: 7, max_articles: 3 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    const load = async (initial: boolean) => {
      try { const next = await api<Schedule>('/api/admin/briefing/status', undefined, abort.signal); if (!abort.signal.aborted) { setStatus(next); if (initial) setForm({ enabled: next.enabled, morning_time: next.morning_time, every_day: next.every_day, lookback_days: next.lookback_days, max_articles: next.max_articles }); } }
      catch (e) { if ((e as Error).name !== 'AbortError') setError((e as Error).message); }
    };
    void load(true);
    const timer = window.setInterval(() => { void load(false); }, 3000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, []);
  async function save() {
    setBusy(true); setError(''); setNotice('');
    try { await api('/api/admin/briefing/settings', form); setStatus(await api<Schedule>('/api/admin/briefing/status')); setNotice('每日更新设置已保存。'); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function run() {
    if (!window.confirm('更新所有启用晨报的账号所关注的股票，并生成个人晨报、发送已订阅渠道。会调用真实搜索与模型服务；今天已更新的股票会复用。是否开始？')) return;
    setBusy(true); setError(''); setNotice('');
    try { await api('/api/admin/briefing/run', {}); setStatus(await api<Schedule>('/api/admin/briefing/status')); setNotice('每日更新已提交，进度会自动刷新。'); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const running = !!status?.last_run && ['queued', 'running'].includes(status.last_run.status);
  return <section className="panel morning-admin"><header className="panel-heading"><div><Clock3 size={18} className="mint-text" /><h2>每日更新与晨报调度</h2></div><span className="sample-label">上海时间</span></header>
    <p>合并所有用户的自选股，每只股票每日采集一次，分别排序生成个人晨报。相同参数的并发采集共享执行，用户任务和订阅相互隔离。</p>
    <div className="morning-admin-fields"><label><input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />启用每日任务</label><label>执行时间<input type="time" value={form.morning_time} onChange={e => setForm({ ...form, morning_time: e.target.value })} /></label><label>执行日期<select value={form.every_day ? 'daily' : 'weekday'} onChange={e => setForm({ ...form, every_day: e.target.value === 'daily' })}><option value="daily">每天（含周末）</option><option value="weekday">周一至周五</option></select></label><label>检索天数<input type="number" min={1} max={30} value={form.lookback_days} onChange={e => setForm({ ...form, lookback_days: Number(e.target.value) })} /></label><label>每只 AI 研判上限<input type="number" min={1} max={8} value={form.max_articles} onChange={e => setForm({ ...form, max_articles: Number(e.target.value) })} /></label></div>
    <div className="briefing-actions"><button className="primary-button" disabled={busy || !status} onClick={save}><Save size={14} />保存调度设置</button><button className="outline-button" disabled={busy || running || !status} onClick={run}>{running ? <LoaderCircle size={14} className="spin" /> : <Play size={14} />}{running ? '每日更新执行中' : '立即执行每日更新'}</button></div>
    <div className="scheduler-status"><span className={`status-dot ${status?.running && status.enabled ? '' : 'pending'}`} />{status?.running && status.enabled ? `下次执行：${dateTime(status.next_run)}` : '自动调度尚未运行'}{!status?.instance_enabled && <p>当前实例没有启动调度。使用 start.ps1 -Scheduler 启动指定后端；其他开发实例保持关闭。</p>}<p>重启后补做今日到期的任务，不补发往日晨报。确定失败的渠道每 15 分钟最多尝试 3 次；结果不确定的请求不会自动重发。</p></div>
    {status && <p className="digest-note">用户采集任务总数 {status.active_research_jobs}/{status.queue_limit} · 同时采集 {status.research_parallelism} 只股票 · 模型同时请求上限 4</p>}
    {status?.last_run && <div className="daily-run-result"><h3>{status.last_run.date} · {labels[status.last_run.status] ?? status.last_run.status}</h3><p>{status.last_run.stage} · 已处理 {Object.keys(status.last_run.stocks).length} 只股票 / {status.last_run.users} 份个人晨报</p><div className="daily-stock-results">{Object.entries(status.last_run.stocks).map(([code, result]) => <span key={code} title={result.warnings.join('；')}>{code}：{labels[result.status] ?? result.status}{result.reused ? '（复用）' : ''}</span>)}</div>{status.last_run.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</div>}
    {notice && <p className="saved-notice" role="status">{notice}</p>}{error && <div className="error-box" role="alert">{error}</div>}
  </section>;
}
