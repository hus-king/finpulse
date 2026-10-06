import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Dashboard, MarketBundle } from './types';

export default function useMarketBundle(code: string, onSnapshot: (data: Dashboard) => void) {
  const [data, setData] = useState<MarketBundle | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const cache = useRef(new Map<string, MarketBundle>());
  const force = useRef(false);
  const refresh = useCallback(() => { force.current = true; setRevision(value => value + 1); }, []);

  useEffect(() => {
    setData(cache.current.get(code) ?? null); setError('');
    let stopped = false, generation = 0, timer: number | undefined;
    let abort: AbortController | null = null;
    let forceOnce = force.current;
    force.current = false;
    const poll = async () => {
      if (stopped || document.hidden) return;
      const current = ++generation;
      const request = new AbortController();
      abort = request;
      setLoading(true);
      let delay = 30;
      try {
        const next = await api<MarketBundle>(`/api/market/${code}/bundle${forceOnce ? '?refresh=true' : ''}`, undefined, request.signal);
        forceOnce = false;
        if (stopped || request.signal.aborted || current !== generation) return;
        cache.current.set(code, next);
        if (cache.current.size > 15) cache.current.delete(cache.current.keys().next().value!);
        setData(next); onSnapshot(next.dashboard); setError('');
        delay = Math.max(30, next.next_poll_seconds);
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
      if (document.hidden) setLoading(false);
      else void poll();
    };
    document.addEventListener('visibilitychange', visibility);
    void poll();
    return () => { stopped = true; abort?.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [code, revision, onSnapshot]);
  return { data: data?.dashboard.stock.code === code ? data : null, error, loading, refresh };
}
