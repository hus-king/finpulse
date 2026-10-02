import { useEffect, useState } from 'react';
import { Bell, CheckCircle2, ExternalLink, LoaderCircle, RefreshCw } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import { dateTime } from './format';

interface Settings { enabled: boolean; email: string; pushplus_configured: boolean; scheduler_running: boolean; delivery?: { at: string; status: Record<string, string> } }
interface Digest { generated_at: string; note: string; sections: { code: string; name: string; as_of: string | null; news: { id: string; title: string; url: string; time: string; score: number | null; summary: string | null; uncertainty: string | null }[] }[] }

export default function BriefingPanel() {
  const { user, requireAuth } = useAuth();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [clearToken, setClearToken] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!user) return;
    const abort = new AbortController();
    Promise.all([api<Settings>('/api/subscription', undefined, abort.signal), api<Digest>('/api/briefing/preview', undefined, abort.signal)]).then(([next, preview]) => { setSettings(next); setEmail(next.email); setEnabled(next.enabled); setDigest(preview); }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => abort.abort();
  }, [user?.id]);
  async function save() {
    if (!requireAuth('登录后可配置自己的早报订阅。')) return;
    setBusy(true); setError(''); setSaved(false);
    try {
      await api('/api/subscription', { enabled, email, pushplus_token: clearToken ? '' : token.trim() || null });
      setSettings(await api<Settings>('/api/subscription')); setToken(''); setClearToken(false); setSaved(true);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function refresh() {
    if (!requireAuth('登录后可查看自己的自选股早报。')) return;
    setError(''); setBusy(true);
    try { setDigest(await api<Digest>('/api/briefing/preview')); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="briefing-layout"><section className="panel digest-panel"><header><div><span className="eyebrow">YOUR MORNING BRIEF</span><h2>自选股消息汇总</h2></div><button className="outline-button" disabled={busy} onClick={refresh}><RefreshCw size={14} />刷新预览</button></header><p className="digest-note">预览读取已保存的数据，不额外搜索、不调用模型、不发送消息。</p>{digest ? <><p className="digest-time">生成时间：{dateTime(digest.generated_at)}</p>{digest.sections.map(section => <article className="digest-section" key={section.code}><h3>{section.name}<span>{section.code}</span></h3><p>数据更新：{dateTime(section.as_of)}</p>{section.news.length ? section.news.map(row => <div className="digest-item" key={row.id}><a href={row.url} target="_blank" rel="noopener noreferrer">{row.title}<ExternalLink size={12} /></a><span>{row.time} · {row.score == null ? '尚未研判' : `AI ${row.score > 0 ? '+' : ''}${row.score}`}</span><p>{row.summary ?? '暂无结构化摘要'}</p>{row.uncertainty && <small>{row.uncertainty}</small>}</div>) : <p className="rail-empty">尚无通过清洗的新闻，请先在看板发起采集。</p>}</article>)}{!digest.sections.length && <p className="rail-empty">请先添加自选股。</p>}</> : <div className="empty-card"><Bell size={28} /><p>{user ? '正在读取汇总…' : '登录后即可查看你的自选股早报。'}</p><button className="primary-button" onClick={refresh}>查看早报</button></div>}</section><section className="panel subscription-panel"><Bell size={24} className="mint-text" /><h2>早报订阅设置</h2><p>调度服务运行时，工作日 08:30（上海时间）采集自选股新闻、生成早报并向已订阅渠道发送。</p><label className="subscription-toggle"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />启用我的订阅</label><label className="auth-field">接收邮箱<input type="email" maxLength={254} placeholder="your@email.com" value={email} onChange={e => setEmail(e.target.value)} /></label><label className="auth-field">PushPlus token<input type="password" autoComplete="off" placeholder={settings?.pushplus_configured ? '已保存，留空保留原 token' : '填入自己的 PushPlus token'} value={token} onChange={e => { setToken(e.target.value); setClearToken(false); }} /></label>{settings?.pushplus_configured && <label className="subscription-toggle"><input type="checkbox" checked={clearToken} onChange={e => setClearToken(e.target.checked)} />删除已保存的 PushPlus token</label>}<div className="scheduler-status"><span className={`status-dot ${settings?.scheduler_running ? '' : 'pending'}`} />{settings?.scheduler_running ? '后端调度器正在运行' : '后端调度器未开启'}<p>管理员需在运行调度的后端配置中开启 scheduler；邮件还需配置 SMTP。只在一个后端实例启用调度。</p></div><button className="primary-button" disabled={busy} onClick={save}>{busy ? <LoaderCircle size={14} className="spin" /> : <CheckCircle2 size={14} />}保存订阅设置</button>{saved && <p className="saved-notice">设置已保存。</p>}{error && <div className="error-box" role="alert">{error}</div>}{settings?.delivery && <p className="digest-time">最近推送：{dateTime(settings.delivery.at)}<br />{Object.entries(settings.delivery.status).map(([channel, status]) => `${channel}: ${status}`).join(' · ')}</p>}</section></div>;
}
