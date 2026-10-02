import { useEffect, useRef, useState } from 'react';
import { KeyRound, LoaderCircle, X } from 'lucide-react';
import { api } from './api';
import useDialogScroll from './useDialogScroll';
import type { AuthSession } from './types';

export default function PasswordDialog({ onClose, onSuccess }: { onClose: () => void; onSuccess: (session: AuthSession) => void }) {
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLElement>(null);
  const controls = useRef({ busy, onClose });
  controls.current = { busy, onClose };
  useDialogScroll();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); if (!controls.current.busy) controls.current.onClose(); }
      if (event.key !== 'Tab') return;
      const items = dialog.current?.querySelectorAll<HTMLElement>('input:not(:disabled),button:not(:disabled)');
      if (!items?.length) return;
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handler, true);
    return () => { document.removeEventListener('keydown', handler, true); if (previous?.isConnected) previous.focus(); };
  }, []);
  async function submit() {
    if (busy) return;
    if (password !== confirmation) { setError('两次输入的新密码不一致。'); return; }
    setBusy(true); setError('');
    try { onSuccess(await api<AuthSession>('/api/auth/password', { current_password: current, new_password: password })); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="modal-overlay auth-overlay"><section ref={dialog} className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="password-title">
    <button className="icon-button auth-close" aria-label="关闭密码窗口" disabled={busy} onClick={onClose}><X size={20} /></button>
    <span className="brand-icon"><KeyRound size={22} /></span><h2 id="password-title">修改网站密码</h2><p>保存后，其他设备上的登录会失效，当前设备保持登录。</p>
    <form className="password-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label className="auth-field">当前密码<input autoFocus autoComplete="current-password" type="password" required maxLength={128} disabled={busy} value={current} onChange={event => setCurrent(event.target.value)} /></label>
      <label className="auth-field">新密码<input autoComplete="new-password" type="password" required minLength={8} maxLength={128} disabled={busy} value={password} onChange={event => setPassword(event.target.value)} /></label>
      <label className="auth-field">确认新密码<input autoComplete="new-password" type="password" required minLength={8} maxLength={128} disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
      {error && <div className="error-box" role="alert">{error}</div>}<button className="primary-button auth-submit" disabled={busy}>{busy && <LoaderCircle size={15} className="spin" />}保存新密码</button>
    </form>
  </section></div>;
}
