import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { MinuteMarket } from './types';

export default function useMinuteMarket(code: string, period: number | null) {
  const [data, setData] = useState<MinuteMarket | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const cache = useRef(new Map<string, MinuteMarket>());
  const force = useRef(false);
  const refresh = useCallback(() => { force.current = true; setRevision(value => value + 1); }, []);

  useEffect(() => {
    if (!period) { setLoading(false); return; }
    const key = `${code}:${period}`;
    setData(cache.current.get(key) ?? null); setError('');
    let stopped = false, timer: number | undefined;
    let generation = 0;
    let abort: AbortController | null = null;
    let forceOnce = force.current;
    force.current = false;
    const poll = async () => {
      if (stopped || document.hidden) return;
      const requestGeneration = ++generation;
      const requestAbort = new AbortController();
      abort = requestAbort;
      setLoading(true);
      let delay = 30;
      try {
        const result = await api<MinuteMarket>(`/api/market/${code}/minutes?period=${period}${forceOnce ? '&refresh=true' : ''}`, undefined, requestAbort.signal);
        forceOnce = false;
        if (stopped || requestAbort.signal.aborted || requestGeneration !== generation) return;
        cache.current.set(key, result);
        if (cache.current.size > 30) cache.current.delete(cache.current.keys().next().value!);
        setData(result); setError('');
        delay = Math.max(2, result.next_poll_seconds);
      } catch (e) {
        if (!stopped && !requestAbort.signal.aborted && requestGeneration === generation) setError((e as Error).message);
      } finally {
        if (!stopped && requestGeneration === generation) {
          setLoading(false);
          if (!document.hidden) timer = window.setTimeout(() => { void poll(); }, delay * 1000);
        }
      }
    };
    const visibility = () => {
      window.clearTimeout(timer);
      generation++;
      if (document.hidden) abort?.abort();
      else { abort?.abort(); void poll(); }
    };
    document.addEventListener('visibilitychange', visibility);
    void poll();
    return () => { stopped = true; abort?.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [code, period, revision]);

  const current = data?.code === code && data.period === period ? data : null;
  return { data: current, error, loading, refresh };
}
