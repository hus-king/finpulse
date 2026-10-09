import type { MarketBundle } from './types';

const shanghaiDay = (stamp: number) => new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date(stamp));

// Public market snapshots survive quote-panel unmounts; no disk or account data.
export function createMarketCache(limit = 15, maxAgeMs = 5 * 60_000) {
  const entries = new Map<string, { value: MarketBundle; saved: number }>();
  return {
    get(code: string, now = Date.now()): MarketBundle | null {
      const entry = entries.get(code);
      if (!entry) return null;
      const serverTime = Date.parse(entry.value.minutes['1']?.market.server_time ?? '');
      if (now - entry.saved > maxAgeMs || !Number.isFinite(serverTime) || shanghaiDay(serverTime) !== shanghaiDay(now)) {
        entries.delete(code); return null;
      }
      entries.delete(code); entries.set(code, entry);
      return entry.value;
    },
    put(value: MarketBundle, now = Date.now()) {
      const code = value.dashboard.stock.code;
      entries.delete(code); entries.set(code, { value, saved: now });
      if (entries.size > limit) entries.delete(entries.keys().next().value!);
    },
  };
}

export const marketCache = createMarketCache();
