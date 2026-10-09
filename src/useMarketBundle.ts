import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Dashboard, MarketBundle } from './types';
import { marketCache } from './marketCache';

export default function useMarketBundle(code: string, onSnapshot: (data: Dashboard) => void) {
  const [data, setData] = useState<MarketBundle | null>(() => marketCache.get(code));
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const force = useRef(false);
  const refresh = useCallback(() => { force.current = true; setRevision(value => value + 1); }, []);

  useEffect(() => {
    setData(marketCache.get(code)); setError('');
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
        const next = await api<MarketBundle>(`/api/market/${code}/bundle?wait=false${forceOnce ? '&refresh=true' : ''}`, undefined, request.signal);
        forceOnce = false;
        if (stopped || request.signal.aborted || current !== generation) return;
        marketCache.put(next);
        setData(next); onSnapshot(next.dashboard); setError('');
        delay = Math.max(2, next.next_poll_seconds);
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
