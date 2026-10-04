import { useEffect, useState } from 'react';
import { LoaderCircle, RefreshCw, ShieldCheck, Users } from 'lucide-react';
import { api } from './api';
import { useAuth } from './AuthContext';
import type { AuditEntry, User } from './types';
import MorningAdmin from './MorningAdmin';

export default function AdminPanel() {
  const { user } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  async function reload() {
    setLoading(true); setError('');
    try { const [accounts, log] = await Promise.all([api<{ items: User[] }>('/api/admin/users'), api<{ items: AuditEntry[] }>('/api/admin/audit')]); setUsers(accounts.items); setAudit(log.items); }
    catch (error) { setError((error as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void reload(); }, []);
  async function toggle(account: User) {
    if (!window.confirm(`${account.is_active ? '停用' : '启用'}普通用户 @${account.username}？${account.is_active ? '停用后，该用户会立即退出登录。' : ''}`)) return;
    setBusy(account.id); setError('');
    try { await api(`/api/admin/users/${account.id}/status`, { is_active: !account.is_active }); await reload(); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(null); }
  }
  if (user?.role !== 'admin') return null;
  return <div className="admin-page">
    <section className="panel admin-intro"><ShieldCheck size={28} className="mint-text" /><div><h2>项目管理中心</h2><p>管理网站账号与访问权限。管理员账号由项目负责人维护。</p></div><button className="outline-button" onClick={() => { void reload(); }} disabled={loading || !!busy}><RefreshCw size={14} className={loading ? 'spin' : ''} />刷新</button></section>
    {error && <div className="error-box" role="alert">{error}</div>}
    <MorningAdmin />
    <div className="admin-stats"><span><strong>{users.length}</strong>网站账号</span><span><strong>{users.filter(u => u.role === 'admin').length}</strong>管理员</span><span><strong>{users.filter(u => !u.is_active).length}</strong>已停用</span></div>
    <section className="panel"><header className="panel-heading"><div><Users size={17} className="mint-text" /><h2>用户与权限</h2></div><span className="sample-label">最多显示 500 个账号</span></header>
      {loading && !users.length ? <div className="loading-state"><LoaderCircle size={20} className="spin" />正在加载账号</div> : <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>账号</th><th>身份</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>{users.map(account => <tr key={account.id}><td><strong>{account.nickname}</strong><small>@{account.username}</small></td><td><span className={`role-badge ${account.role}`}>{account.role === 'admin' ? '管理员' : '普通用户'}</span></td><td>{account.is_active ? '正常' : '已停用'}</td><td>{account.last_login_at ? new Date(account.last_login_at).toLocaleString('zh-CN') : '尚未登录'}</td><td>{account.role === 'admin' ? <span className="muted">管理员保护</span> : <button className="outline-button" disabled={!!busy} onClick={() => { void toggle(account); }}>{busy === account.id ? '处理中…' : account.is_active ? '停用账号' : '启用账号'}</button>}</td></tr>)}</tbody></table></div>}
    </section>
    <section className="panel"><header className="panel-heading"><div><ShieldCheck size={17} className="mint-text" /><h2>管理操作记录</h2></div><span className="sample-label">最近 100 条</span></header><div className="audit-list">{audit.length ? audit.map((entry, i) => <div key={i}><span>@{entry.actor_name} {entry.action === 'disable_user' ? '停用' : '启用'}了 @{entry.target_name}</span><time>{new Date(entry.created_at).toLocaleString('zh-CN')}</time></div>) : <p className="muted">暂无管理操作。启停普通用户后，记录会显示在这里。</p>}</div></section>
  </div>;
}
