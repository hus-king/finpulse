import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';

const CACHE_PREFIX = 'finpulse:etf:v1:';
const FRESH_MS = 5 * 60_000;
const MAX_AGE_MS = 7 * 24 * 60 * 60_000;
type Cached<T> = { savedAt: number; data: T };
type Result<T> = { key: string; data?: T; loading: boolean; error: string };

function readCache<T>(key: string): Cached<T> | undefined {
  try {
    const saved = JSON.parse(localStorage.getItem(CACHE_PREFIX + key) ?? 'null') as Cached<T> | null;
    if (saved?.data == null) return;
    const age = Date.now() - saved.savedAt;
    if (age >= 0 && age < MAX_AGE_MS) return saved;
  } catch { /* Chart data can always be fetched if storage is unavailable. */ }
}

function writeCache<T>(key: string, data: T) {
  try {
    localStorage.setItem(CACHE_PREFIX + key, JSON.stringify({ savedAt: Date.now(), data }));
  } catch { /* A quota or browser setting must not prevent displaying the response. */ }
}

const readApi = <T,>(key: string, signal: AbortSignal) => api<T>(key, undefined, signal);

export default function useEtfSnapshot<T>(key: string, load = readApi<T>) {
  const [result, setResult] = useState<Result<T>>();
  const [request, setRequest] = useState(0);
  const handledRequest = useRef(0);
  const initial = useMemo(() => readCache<T>(key), [key]);

  useEffect(() => {
    const cached = readCache<T>(key);
    const forced = request !== handledRequest.current;
    handledRequest.current = request;
    if (!forced && cached && Date.now() - cached.savedAt < FRESH_MS) {
      setResult({ key, data: cached.data, loading: false, error: '' });
      return;
    }

    const controller = new AbortController();
    setResult({ key, data: cached?.data, loading: true, error: '' });
    load(key, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      writeCache(key, data);
      setResult({ key, data, loading: false, error: '' });
    }).catch((error: Error) => {
      if (!controller.signal.aborted) {
        setResult({ key, data: cached?.data, loading: false, error: error.message });
      }
    });
    return () => controller.abort();
  }, [key, load, request]);

  const current = result?.key === key ? result : { data: initial?.data, loading: true, error: '' };
  return { ...current, refresh: () => setRequest(value => value + 1) };
}
