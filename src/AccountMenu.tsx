import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, LoaderCircle, LogIn, LogOut, UserRound, ShieldCheck, KeyRound } from 'lucide-react';
import { useAuth } from './AuthContext';

export default function AccountMenu({ open, setOpen, onAdmin }: { open: boolean; setOpen: (open: boolean) => void; onAdmin: () => void }) {
  const { user, loading, sessionError, refreshSession, requestLogin, requestPasswordChange, logout } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!container.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open, setOpen]);
  useEffect(() => { setError(''); }, [open]);
  async function signOut() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await logout();
      if (container.current?.contains(document.activeElement)) trigger.current?.focus();
      setOpen(false);
    }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="account-control" ref={container}
    onBlur={event => { if (open && !event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
    onKeyDown={event => {
      if (open && event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        trigger.current?.focus();
      }
    }}>
    <button ref={trigger} className={`account-button ${user ? 'signed-in' : ''}`} disabled={loading} aria-label={loading ? '正在确认登录状态' : user ? '打开账号菜单' : sessionError ? '重新确认登录状态' : '用户登录'} aria-expanded={user ? open : undefined} aria-controls={open && user ? panelId : undefined} onClick={() => {
      trigger.current?.focus();
      if (user) setOpen(!open);
      else if (sessionError) void refreshSession();
      else requestLogin();
    }}>
      {loading ? <LoaderCircle size={15} className="spin" /> : user ? <span className="account-avatar">{user.nickname.slice(0, 1).toUpperCase()}</span> : <LogIn size={15} />}
      <span>{loading ? '确认中' : user ? user.nickname : sessionError ? '重试登录状态' : '用户登录'}</span>{user && <ChevronDown size={12} />}
    </button>
    {open && user && <section id={panelId} className="account-menu" aria-label="当前账号">
      <div className="account-menu-heading"><span className="icon-square mint"><UserRound size={20} /></span><div><strong>{user.nickname}</strong><span>@{user.username}</span></div></div>
      <div className="account-created">注册于 {new Date(user.created_at).toLocaleDateString('zh-CN')}</div>
      <span className="account-ready"><span className="status-dot" /> {user.role === 'admin' ? '项目管理员' : '普通用户'} · 已登录</span>
      {user.role === 'admin' && <button className="account-logout" onClick={() => { setOpen(false); onAdmin(); }}><ShieldCheck size={14} />打开管理中心</button>}
      <button className="account-logout" onClick={() => { trigger.current?.focus(); setOpen(false); requestPasswordChange(); }}><KeyRound size={14} />修改网站密码</button>
      {error && <div className="error-box" role="alert">{error}</div>}
      <button className="account-logout" aria-disabled={busy} onClick={() => { void signOut(); }}>{busy ? <LoaderCircle size={14} className="spin" /> : <LogOut size={14} />}{busy ? '正在退出…' : '退出登录'}</button>
    </section>}
  </div>;
}
