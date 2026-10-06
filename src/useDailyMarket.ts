import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Dashboard } from './types';

export default function useDailyMarket(code: string, onSnapshot: (data: Dashboard) => void) {
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const force = useRef(false);
  const refresh = useCallback(() => { force.current = true; setRevision(value => value + 1); }, []);

  useEffect(() => {
    let stopped = false, generation = 0, timer: number | undefined;
    let abort: AbortController | null = null;
    let forceOnce = force.current;
    force.current = false;
    setError('');
    const poll = async () => {
      if (stopped || document.hidden) return;
      const current = ++generation;
      const request = new AbortController();
      abort = request;
      setLoading(true);
      let delay = 30;
      try {
        const next = await api<Dashboard>(`/api/market/${code}/daily${forceOnce ? '?refresh=true' : ''}`, undefined, request.signal);
        forceOnce = false;
        if (stopped || request.signal.aborted || current !== generation) return;
        onSnapshot(next); setError('');
        delay = Math.max(2, next.daily_request?.next_poll_seconds ?? 30);
      } catch (e) {
        if (!stopped && !request.signal.aborted && current === generation) setError((e as Error).message);
      } finally {
        if (!stopped && current === generation) {
          setLoading(false);
          if (!document.hidden) timer = window.setTimeout(() => { void poll(); }, delay * 1000);
        }
      }
    };
    const visibility = () => {
      window.clearTimeout(timer); generation++; abort?.abort();
      if (!document.hidden) void poll();
    };
    document.addEventListener('visibilitychange', visibility);
    void poll();
    return () => { stopped = true; abort?.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [code, revision, onSnapshot]);
  return { error, loading, refresh };
}
