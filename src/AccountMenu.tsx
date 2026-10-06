import { useEffect, useRef, useState } from 'react';
import { ChevronDown, LoaderCircle, LogIn, LogOut, UserRound, ShieldCheck, KeyRound } from 'lucide-react';
import { useAuth } from './AuthContext';

export default function AccountMenu({ open, setOpen, onAdmin }: { open: boolean; setOpen: (open: boolean) => void; onAdmin: () => void }) {
  const { user, loading, sessionError, refreshSession, requestLogin, requestAdminLogin, requestPasswordChange, logout } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!container.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open, setOpen]);
  useEffect(() => { setError(''); }, [open]);
  async function signOut() {
    setBusy(true); setError('');
    try { await logout(); setOpen(false); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="account-control" ref={container}>
    {!user && !sessionError && <button className="admin-login-button" disabled={loading} onClick={requestAdminLogin}><ShieldCheck size={14} /><span>管理员登录</span></button>}
    <button className={`account-button ${user ? 'signed-in' : ''}`} disabled={loading} aria-label={loading ? '正在确认登录状态' : user ? '打开账号菜单' : sessionError ? '重新确认登录状态' : '用户登录'} aria-expanded={user ? open : undefined} onClick={() => user ? setOpen(!open) : sessionError ? void refreshSession() : requestLogin()}>
      {loading ? <LoaderCircle size={15} className="spin" /> : user ? <span className="account-avatar">{user.nickname.slice(0, 1).toUpperCase()}</span> : <LogIn size={15} />}
      <span>{loading ? '确认中' : user ? user.nickname : sessionError ? '重试登录状态' : '用户登录'}</span>{user && <ChevronDown size={12} />}
    </button>
    {open && user && <section className="account-menu" aria-label="当前账号">
      <div className="account-menu-heading"><span className="icon-square mint"><UserRound size={20} /></span><div><strong>{user.nickname}</strong><span>@{user.username}</span></div></div>
      <div className="account-created">注册于 {new Date(user.created_at).toLocaleDateString('zh-CN')}</div>
      <span className="account-ready"><span className="status-dot" /> {user.role === 'admin' ? '项目管理员' : '普通用户'} · 已登录</span>
      {user.role === 'admin' && <button className="account-logout" onClick={() => { setOpen(false); onAdmin(); }}><ShieldCheck size={14} />打开管理中心</button>}
      <button className="account-logout" onClick={() => { setOpen(false); requestPasswordChange(); }}><KeyRound size={14} />修改网站密码</button>
      {error && <div className="error-box" role="alert">{error}</div>}
      <button className="account-logout" disabled={busy} onClick={() => { void signOut(); }}>{busy ? <LoaderCircle size={14} className="spin" /> : <LogOut size={14} />}{busy ? '正在退出…' : '退出登录'}</button>
    </section>}
  </div>;
}
