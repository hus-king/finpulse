import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import type { AdjustedMarketHistory, MarketAdjustment } from './types';

interface HistoryState { key: string; data: AdjustedMarketHistory | null; error: string; loading: boolean }

/** Adjusted daily bars are chart-only: never replace quotes or the raw bundle. */
export default function useAdjustedHistory(code: string, adjustment: MarketAdjustment, period: string, rawFingerprint: string, enabled: boolean) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<HistoryState | null>(null);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const active = enabled && adjustment !== 'none';
  const key = JSON.stringify([code, adjustment, period, rawFingerprint, revision]);

  useEffect(() => {
    if (!active) { setState(null); return; }
    let stopped = false;
    let timer: number | undefined;
    let request: AbortController | undefined;
    setState({ key, data: null, error: '', loading: true });
    const poll = async () => {
      request = new AbortController();
      try {
        const data = await api<AdjustedMarketHistory>(`/api/market/${encodeURIComponent(code)}/history?adjustment=${adjustment}`, undefined, request.signal);
        if (stopped || request.signal.aborted) return;
        if (data.code !== code || data.adjustment !== adjustment || !['ready', 'loading', 'unavailable'].includes(data.status)
          || data.status === 'ready' && (!Array.isArray(data.candles) || !data.candles.length)) {
          throw new Error('复权数据暂不可用，请重试。');
        }
        setState({ key, data, error: '', loading: data.status === 'loading' });
        if (data.status === 'loading') timer = window.setTimeout(() => { void poll(); }, 2000);
      } catch (error) {
        if (!stopped && !request.signal.aborted) {
          setState({ key, data: null, error: (error as Error).message, loading: false });
        }
      }
    };
    void poll();
    return () => { stopped = true; request?.abort(); window.clearTimeout(timer); };
  }, [active, adjustment, code, key]);

  const current = active && state?.key === key ? state : null;
  return { data: current?.data ?? null, error: current?.error ?? '', loading: active && (current?.loading ?? true), refresh };
}
