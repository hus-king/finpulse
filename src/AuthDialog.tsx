import { useEffect, useRef, useState } from 'react';
import { Activity, ArrowRight, Eye, EyeOff, LoaderCircle, LockKeyhole, X } from 'lucide-react';
import { api } from './api';
import useDialogScroll from './useDialogScroll';
import type { AuthSession } from './types';

export default function AuthDialog({ initialAdmin, reason, onClose, onSuccess }: { initialAdmin: boolean; reason: string; onClose: () => void; onSuccess: (session: AuthSession) => void }) {
  const [admin, setAdmin] = useState(initialAdmin);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [nickname, setNickname] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const controls = useRef({ busy, onClose });
  controls.current = { busy, onClose };
  useDialogScroll();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    nameInput.current?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); if (!controls.current.busy) controls.current.onClose(); }
      if (event.key !== 'Tab') return;
      const elements = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]');
      if (!elements?.length) return;
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handler, true);
    return () => { document.removeEventListener('keydown', handler, true); if (previous?.isConnected) previous.focus(); };
  }, []);

  const changeMode = (next: 'login' | 'register') => { setMode(next); setError(''); setPassword(''); setConfirmation(''); setVisible(false); nameInput.current?.focus(); };
  async function submit() {
    if (busy) return;
    if (mode === 'register' && password !== confirmation) { setError('两次输入的密码不一致。'); return; }
    setBusy(true); setError('');
    try {
      const session = await api<AuthSession>(`/api/auth/${admin ? 'admin/login' : mode}`, { username: username.trim(), password, ...(!admin && mode === 'register' ? { nickname: nickname.trim() } : {}) });
      onSuccess(session);
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="modal-overlay auth-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section ref={dialog} className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title" aria-describedby="auth-reason">
      <button className="icon-button auth-close" aria-label="关闭登录窗口" disabled={busy} onClick={onClose}><X size={20} /></button>
      <span className="brand-icon"><Activity size={24} /></span>
      <div className="eyebrow">YOUR FINPULSE WORKSPACE</div>
      <h2 id="auth-title">{admin ? '管理员登录' : mode === 'login' ? '用户登录' : '创建你的研究账号'}</h2>
      <p id="auth-reason">{admin ? '使用分配的网站管理员账号，登录后可管理普通用户。' : initialAdmin ? '登录后即可使用 AI 问答、新闻研判和模型连接测试。' : reason}</p>
      <div className="auth-tabs" aria-label="登录身份"><button type="button" disabled={busy} aria-pressed={!admin} className={!admin ? 'active' : ''} onClick={() => { setAdmin(false); changeMode('login'); }}>用户登录</button><button type="button" disabled={busy} aria-pressed={admin} className={admin ? 'active' : ''} onClick={() => { setAdmin(true); changeMode('login'); }}>管理员登录</button></div>
      {!admin && <div className="auth-mode-switch"><button type="button" disabled={busy} onClick={() => changeMode(mode === 'login' ? 'register' : 'login')}>{mode === 'login' ? '没有账号？注册普通用户' : '已有账号？返回用户登录'}</button></div>}
      <form onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label className="auth-field">用户名<input ref={nameInput} name="username" autoComplete="username" required minLength={3} maxLength={32} pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,31}" title="3–32 位字母、数字、下划线、点或短横线，以字母或数字开头" placeholder="例如 finpulse_01" value={username} disabled={busy} onChange={event => setUsername(event.target.value)} /></label>
        {mode === 'register' && <label className="auth-field">昵称<input name="nickname" autoComplete="nickname" required maxLength={40} placeholder="页面中显示的名字" value={nickname} disabled={busy} onChange={event => setNickname(event.target.value)} /></label>}
        <label className="auth-field">密码<span className="auth-password"><input name="password" type={visible ? 'text' : 'password'} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} required minLength={mode === 'register' ? 8 : 1} maxLength={128} placeholder={mode === 'register' ? '至少 8 位，最多 128 位' : '输入你的密码'} value={password} disabled={busy} onChange={event => setPassword(event.target.value)} /><button type="button" disabled={busy} aria-label={visible ? '隐藏密码' : '显示密码'} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={16} /> : <Eye size={16} />}</button></span></label>
        {mode === 'register' && <label className="auth-field">确认密码<input name="confirmation" type={visible ? 'text' : 'password'} autoComplete="new-password" required minLength={8} maxLength={128} placeholder="再输入一次密码" value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} /></label>}
        {error && <div className="error-box auth-error" role="alert">{error}</div>}
        <button type="submit" className="primary-button auth-submit" disabled={busy}>{busy ? <LoaderCircle size={16} className="spin" /> : <LockKeyhole size={15} />}{busy ? '正在验证…' : mode === 'login' ? '登录并继续' : '创建账号并登录'}{!busy && <ArrowRight size={15} />}</button>
      </form>
      <p className="auth-footnote">{admin ? '管理员账号由负责人创建，不开放管理员注册。' : mode === 'register' ? '注册成功后会自动登录。用户名不区分大小写。' : '普通用户账号只能通过用户入口登录。'} 登录状态保留 7 天，可随时退出。</p>
    </section>
  </div>;
}
