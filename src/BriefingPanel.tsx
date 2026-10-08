import { useCallback, useEffect, useState } from 'react';
import { Bell, CheckCircle2, ExternalLink, LoaderCircle, RefreshCw, Send, Newspaper } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import { dateTime } from './format';

interface Delivery { at: string; kind?: string; status: Record<string, string>; channels?: Record<string, { attempts: number }> }
interface Settings { enabled: boolean; email: string; email_enabled: boolean; wechat_enabled: boolean; interests: string[]; max_items: number; lookback_days: number; smtp_configured: boolean; pushplus_configured: boolean; scheduler_running: boolean; morning_time: string; every_day: boolean; next_run: string | null; delivery?: Delivery }
interface CompanyImpact { stale?: boolean; refresh_pending?: boolean; code: string; name: string; score: number | null; summary: string | null; uncertainty: string | null; relevance_reason?: string | null }
interface Item { news_scope?: 'company' | 'industry'; industry?: string | null; company_impacts?: CompanyImpact[]; id: string; title: string; url: string; time: string; score: number | null; summary: string | null; uncertainty: string | null; priority: number; rank: number; related_stocks: { code: string; name: string }[]; reasons: string[]; components: Record<string, number> }
interface Digest { date: string; generated_at: string; note: string; recommendations: Item[]; warnings: string[]; ranking: { note: string; deduplicated: number; filtered_non_news: number; candidate_count: number }; sections: { code: string; name: string; as_of: string | null }[] }
interface History { date: string; generated_at: string; count: number }
const topics = ['业绩财报', '公司公告', '行业政策', '风险事件'];
const statuses: Record<string, string> = { sent: '已发送', accepted: '渠道已受理', failed: '失败，可自动重试', not_configured: '服务端 SMTP 未配置', unknown: '结果不确定，请核实；不会自动重发', sending: '发送中或上次发送中断，请核实', busy: '另一个任务正在发送' };

export default function BriefingPanel() {
  const { user, requireAuth } = useAuth();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [history, setHistory] = useState<History[]>([]);
  const [selection, setSelection] = useState('latest');
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [clearToken, setClearToken] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [emailEnabled, setEmailEnabled] = useState(true);
  const [wechatEnabled, setWechatEnabled] = useState(true);
  const [interests, setInterests] = useState<string[]>([]);
  const [maximum, setMaximum] = useState(10);
  const [windowDays, setWindowDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const loadHistory = useCallback(async () => setHistory((await api<{ items: History[] }>('/api/briefing/history')).items), []);
  useEffect(() => {
    if (!user) return;
    const abort = new AbortController();
    Promise.all([api<Settings>('/api/subscription', undefined, abort.signal), api<{ digest: Digest | null }>('/api/briefing/latest', undefined, abort.signal), api<{ items: History[] }>('/api/briefing/history', undefined, abort.signal)])
      .then(([next, latest, saved]) => { setSettings(next); setEmail(next.email); setEnabled(next.enabled); setEmailEnabled(next.email_enabled); setWechatEnabled(next.wechat_enabled); setInterests(next.interests); setMaximum(next.max_items); setWindowDays(next.lookback_days); setDigest(latest.digest); setHistory(saved.items); })
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => abort.abort();
  }, [user?.id]);
  useEffect(() => {
    if (!user || selection !== 'latest') return;
    const abort = new AbortController();
    const timer = window.setInterval(() => {
      Promise.all([api<{ digest: Digest | null }>('/api/briefing/latest', undefined, abort.signal), api<Settings>('/api/subscription', undefined, abort.signal)]).then(([latest, next]) => { if (!abort.signal.aborted) { setDigest(latest.digest); setSettings(next); void loadHistory(); } }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    }, 15000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [user?.id, selection, loadHistory]);
  async function save() {
    if (!requireAuth('登录后可配置自己的晨报。')) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api('/api/subscription', { enabled, email, email_enabled: emailEnabled, wechat_enabled: wechatEnabled, interests, max_items: maximum, lookback_days: windowDays, pushplus_token: clearToken ? '' : token.trim() || null });
      setSettings(await api<Settings>('/api/subscription')); setToken(''); setClearToken(false); setDirty(false); setNotice('设置已保存；下一次生成晨报时应用。');
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function show(value: string) {
    if (!requireAuth('登录后可查看自己的每日晨报。')) return;
    setError(''); setBusy(true);
    try {
      const next = value === 'preview' ? await api<Digest>('/api/briefing/preview') : value === 'latest' ? (await api<{ digest: Digest | null }>('/api/briefing/latest')).digest : await api<Digest>(`/api/briefing/history/${value}`);
      setDigest(next); setSelection(value);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function generate() {
    if (!requireAuth('登录后可以生成自己的晨报。')) return;
    setError(''); setBusy(true); setNotice('');
    try { setDigest(await api<Digest>('/api/briefing/generate', {})); setSelection('latest'); await loadHistory(); setNotice('今日晨报已保存。此次使用现有数据，没有发送消息。'); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function testDelivery() {
    if (!requireAuth('登录后可以测试自己的推送渠道。') || !window.confirm('向已保存并开启的邮箱 / 微信渠道发送一份真实测试晨报？')) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const outcome = await api<Delivery>('/api/briefing/test-delivery', {});
      setSettings(await api<Settings>('/api/subscription'));
      setNotice(Object.entries(outcome.status).map(([channel, status]) => `${channel === 'email' ? '邮件' : '微信'}：${statuses[status] ?? status}`).join('；'));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const changed = () => { setDirty(true); setNotice(''); };
  return <div className="briefing-layout">
    <section className="panel digest-panel"><header><div><span className="eyebrow">YOUR DAILY MORNING BRIEF</span><h2>每日晨报 · 为你排序</h2></div><button className="outline-button" disabled={busy} onClick={() => { void show('preview'); }}><RefreshCw size={14} />查看当前预览</button></header>
      <p className="digest-note">晨报根据你的自选股和关注主题推荐新闻。利好与利空都参与排序，优先级表示阅读价值。</p>
      <div className="briefing-actions"><button className="primary-button" disabled={busy || dirty} onClick={generate}><Newspaper size={15} />生成今日晨报</button><select aria-label="晨报日期" value={selection} disabled={busy} onChange={e => { void show(e.target.value); }}><option value="latest">最近一期晨报</option><option value="preview">当前数据预览</option>{history.map(row => <option key={row.date} value={row.date}>{row.date} · {row.count} 条</option>)}</select></div>
      <p className="digest-note">手动生成使用已保存的数据，不额外搜索或推送；每日自动任务会先更新数据。修改设置后请先保存。</p>
      {digest ? <><p className="digest-time">{selection === 'preview' ? '未保存的预览' : `${digest.date} 晨报`} · 生成时间：{dateTime(digest.generated_at)}</p><details className="ranking-explanation"><summary>推荐排序如何计算 · 合并 {digest.ranking.deduplicated} 条重复新闻 · 过滤 {digest.ranking.filtered_non_news ?? 0} 条非新闻</summary><p>{digest.ranking.note}</p></details>
        {digest.warnings.length > 0 && <details className="briefing-warnings"><summary>{digest.warnings.length} 条数据提示</summary>{digest.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</details>}
        {digest.recommendations.map(row => <article className="digest-item ranked-news" key={`${row.id}-${row.rank}`}><div className="recommendation-rank">{row.rank.toString().padStart(2, '0')}</div><div><div className="recommendation-meta"><span>{row.related_stocks.map(stock => stock.name).join(' / ')} · {row.time}</span><span className="priority-score">优先级 {row.priority.toFixed(0)}</span></div><a href={row.url} target="_blank" rel="noopener noreferrer">{row.title}<ExternalLink size={12} /></a><p>{row.summary ?? '尚未完成研判，请结合原文阅读。'}</p>{(row.news_scope === 'industry' || (row.company_impacts?.length ?? 0) > 1) && <div className="company-impacts">{row.company_impacts?.map(impact => <section key={impact.code}><strong>{impact.name} · AI {impact.score ?? '未研判'}</strong>{(impact.stale || impact.refresh_pending) && <p className="news-stale">{impact.refresh_pending ? '行业检索进行中，暂展示上次材料。' : '本轮更新失败，沿用该公司的上次材料。'}</p>}<p>{impact.summary ?? '尚未完成该公司的独立研判。'}</p>{impact.relevance_reason && <p>{impact.relevance_reason}</p>}<small>{impact.uncertainty}</small></section>)}</div>}<div className="recommendation-reasons">{row.reasons.map(reason => <span key={reason}>{reason}</span>)}</div><details><summary>查看评分与研判局限</summary><p>{Object.entries(row.components).map(([name, value]) => `${name} ${value}`).join(' · ')} · AI 情绪 {row.score ?? '未研判'}</p><small>{row.uncertainty ?? '暂无结构化研判；推荐排序不能代替事实核验。'}</small></details></div></article>)}
        {!digest.recommendations.length && <div className="empty-card"><Newspaper size={28} /><p>当前时间窗口没有符合条件的新闻。</p><small>请先添加自选股并采集，或等待每日更新；不会生成模拟新闻。</small></div>}
        <div className="digest-sources"><h3>关注股票的数据时间</h3>{digest.sections.map(section => <p key={section.code}>{section.name}（{section.code}）<span>{dateTime(section.as_of)}</span></p>)}</div></> : <div className="empty-card"><Bell size={28} /><p>{user ? '还没有保存晨报，点击“生成今日晨报”开始。' : '登录后查看属于你的晨报。'}</p>{!user && <button className="primary-button" onClick={() => requireAuth('登录后查看每日晨报。')}>登录查看</button>}</div>}
    </section>
    <section className="panel subscription-panel"><Bell size={24} className="mint-text" /><h2>晨报与推送设置</h2><p>{settings?.every_day === false ? '周一至周五' : '每天'} {settings?.morning_time ?? '08:30'}（上海时间）自动更新。休市日照常整理新闻，股价保留最近交易日日期。</p>
      <label className="subscription-toggle"><input type="checkbox" checked={enabled} onChange={e => { setEnabled(e.target.checked); changed(); }} />每天为我生成站内晨报</label><p className="digest-note">不填写推送渠道也可以只在网站阅读。</p>
      <div className="briefing-preferences"><label className="auth-field">新闻时间窗口<select value={windowDays} onChange={e => { setWindowDays(Number(e.target.value)); changed(); }}>{[1, 3, 7, 14, 30].map(days => <option key={days} value={days}>近 {days} 天</option>)}</select></label><label className="auth-field">每期推荐条数<input type="number" min={3} max={20} value={maximum} onChange={e => { setMaximum(Number(e.target.value)); changed(); }} /></label></div>
      <span className="auth-field">重点关注</span><div className="topic-options">{topics.map(topic => <label key={topic}><input type="checkbox" checked={interests.includes(topic)} onChange={e => { setInterests(previous => e.target.checked ? [...previous, topic] : previous.filter(item => item !== topic)); changed(); }} />{topic}</label>)}</div>
      <label className="subscription-toggle"><input type="checkbox" checked={emailEnabled} onChange={e => { setEmailEnabled(e.target.checked); changed(); }} />邮件推送</label><label className="auth-field">接收邮箱<input type="email" maxLength={254} placeholder="your@email.com" value={email} onChange={e => { setEmail(e.target.value); changed(); }} /></label>{!settings?.smtp_configured && <p className="channel-hint">服务端 SMTP 尚未配置，填写邮箱后仍需管理员配置发信账号。</p>}
      <label className="subscription-toggle"><input type="checkbox" checked={wechatEnabled} onChange={e => { setWechatEnabled(e.target.checked); changed(); }} />微信推送（PushPlus）</label><label className="auth-field">PushPlus token<input type="password" autoComplete="off" placeholder={settings?.pushplus_configured ? '已保存，留空保留原 token' : '填入自己的 PushPlus token'} value={token} onChange={e => { setToken(e.target.value); setClearToken(false); changed(); }} /></label>{settings?.pushplus_configured && <label className="subscription-toggle"><input type="checkbox" checked={clearToken} onChange={e => { setClearToken(e.target.checked); changed(); }} />删除已保存的 token</label>}
      <div className="scheduler-status"><span className={`status-dot ${settings?.scheduler_running ? '' : 'pending'}`} />{settings?.scheduler_running ? '每日调度已开启' : '每日调度未开启'}<p>{settings?.next_run ? `下次执行：${dateTime(settings.next_run)}` : '指定一个后端实例启动调度，管理员可在管理中心设置时间并手动更新。'}</p></div>
      <button className="primary-button" disabled={busy} onClick={save}>{busy ? <LoaderCircle size={14} className="spin" /> : <CheckCircle2 size={14} />}保存设置</button><button className="outline-button test-delivery" disabled={busy || dirty || !settings || !(settings.email && settings.email_enabled || settings.pushplus_configured && settings.wechat_enabled)} onClick={testDelivery}><Send size={14} />发送真实测试推送</button>
      {notice && <p className="saved-notice" role="status">{notice}</p>}{error && <div className="error-box" role="alert">{error}</div>}
      {settings?.delivery && <div className="delivery-result"><h3>最近{settings.delivery.kind === 'test' ? '测试' : '晨报'}推送</h3><p className="digest-time">{dateTime(settings.delivery.at)}</p>{Object.entries(settings.delivery.status).map(([channel, status]) => <p key={channel}>{channel === 'email' ? '邮件' : '微信'}：{statuses[status] ?? status} {settings.delivery?.channels?.[channel] && <small>（尝试 {settings.delivery.channels[channel].attempts} 次）</small>}</p>)}{!Object.keys(settings.delivery.status).length && <p>仅站内阅读，未启用外部渠道。</p>}<small>微信“受理”表示接口接受请求，实际到达以你的微信为准。</small></div>}
    </section>
  </div>;
}
