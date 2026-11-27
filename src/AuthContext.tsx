import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { setPaperSnapshotOwner } from './paperSnapshots';
import type { ReactNode } from 'react';
import { api, ApiError, setCsrfToken } from './api';
import AuthDialog from './AuthDialog';
import PasswordDialog from './PasswordDialog';
import type { AuthSession, User } from './types';

const USER_CACHE_KEY = 'finpulse-user-cache';

interface CachedSession {
  user: User;
  csrf_token?: string;
}

function getInitialSession(): CachedSession | null {
  try {
    if (typeof window === 'undefined' || typeof localStorage === 'undefined') return null;
    const raw = localStorage.getItem(USER_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      if (parsed.user && typeof parsed.user === 'object' && parsed.user.id) {
        return parsed as CachedSession;
      }
      if (parsed.id && parsed.username) {
        return { user: parsed as User };
      }
    }
  } catch {
    // Local storage unavailable or malformed
  }
  return null;
}

const initialSession = getInitialSession();
if (initialSession) {
  setPaperSnapshotOwner(initialSession.user.id);
  if (initialSession.csrf_token) setCsrfToken(initialSession.csrf_token);
}

interface AuthState {
  user: User | null; loading: boolean; sessionError: string;
  requestLogin: (reason?: string) => void;
  requestAdminLogin: () => void;
  requestPasswordChange: () => void;
  requireAuth: (reason: string) => boolean;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
}
const Context = createContext<AuthState | null>(null);
export function useAuth() {
  const context = useContext(Context);
  if (!context) throw new Error('AuthProvider is missing');
  return context;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => initialSession?.user ?? null);
  const [loading, setLoading] = useState(() => !initialSession);
  const [sessionError, setSessionError] = useState('');
  const [dialog, setDialog] = useState<{ reason: string; admin?: boolean } | null>(null);
  const [changingPassword, setChangingPassword] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const channel = useRef<BroadcastChannel | null>(null);
  const update = useCallback((session: AuthSession | null) => {
    setPaperSnapshotOwner(session?.user.id ?? null);
    setCsrfToken(session?.csrf_token ?? null);
    setUser(session?.user ?? null);
    try {
      if (typeof localStorage !== 'undefined') {
        if (session?.user) {
          localStorage.setItem(USER_CACHE_KEY, JSON.stringify({ user: session.user, csrf_token: session.csrf_token }));
        } else {
          localStorage.removeItem(USER_CACHE_KEY);
        }
      }
    } catch {
      // Local storage unavailable
    }
    if (!session) setChangingPassword(false);
    setLoading(false);
    setSessionError('');
  }, []);
  const refreshSession = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    try {
      const session = await api<AuthSession>('/api/auth/me', undefined, controller.signal);
      if (!controller.signal.aborted) update(session);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && (error.status === 401 || error.code === 'ACCOUNT_DISABLED')) update(null);
      else { setSessionError((error as Error).message); setLoading(false); }
    }
  }, [update]);
  useEffect(() => {
    void refreshSession();
    const onFocus = () => { void refreshSession(); };
    const onExpired = (event: Event) => {
      pending.current?.abort(); update(null);
      setDialog({ reason: (event as CustomEvent<string>).detail });
    };
    window.addEventListener('focus', onFocus);
    window.addEventListener('finpulse:auth-required', onExpired);
    if ('BroadcastChannel' in window) {
      channel.current = new BroadcastChannel('finpulse-auth');
      channel.current.onmessage = () => { void refreshSession(); };
    }
    return () => {
      pending.current?.abort(); channel.current?.close(); channel.current = null;
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('finpulse:auth-required', onExpired);
    };
  }, [refreshSession, update]);

  const requestLogin = (reason = '登录后即可使用 AI 问答、新闻研判和模型连接测试。') => {
    if (loading) return;
    if (sessionError) { void refreshSession(); return; }
    setDialog({ reason });
  };
  const requestAdminLogin = () => {
    if (loading) return;
    if (sessionError) { void refreshSession(); return; }
    setDialog({ reason: '使用项目负责人分配的管理员账号登录。', admin: true });
  };
  const requireAuth = (reason: string) => {
    if (loading) return false;
    if (user) return true;
    if (sessionError) { void refreshSession(); return false; }
    requestLogin(reason); return false;
  };
  const acceptSession = (session: AuthSession) => {
    pending.current?.abort(); update(session); setDialog(null);
    channel.current?.postMessage('changed');
  };
  const logout = async () => {
    try { await api('/api/auth/logout', {}); }
    catch (error) { if (!(error instanceof ApiError && (error.status === 401 || error.code === 'ACCOUNT_DISABLED'))) throw error; }
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.removeItem(USER_CACHE_KEY);
      }
    } catch {
      // Local storage unavailable
    }
    pending.current?.abort(); update(null); setDialog(null);
    setChangingPassword(false);
    channel.current?.postMessage('changed');
  };
  return <Context.Provider value={{ user, loading, sessionError, requestLogin, requestAdminLogin, requestPasswordChange: () => setChangingPassword(true), requireAuth, logout, refreshSession }}>
    {children}
    {dialog && <AuthDialog initialAdmin={dialog.admin ?? false} reason={dialog.reason} onClose={() => setDialog(null)} onSuccess={acceptSession} />}
    {changingPassword && user && <PasswordDialog onClose={() => setChangingPassword(false)} onSuccess={session => { acceptSession(session); setChangingPassword(false); }} />}
  </Context.Provider>;
}
