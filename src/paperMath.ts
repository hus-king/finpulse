import type { PaperFees, PaperRules } from './paperTypes';

export function newRequestId(): string {
  // getRandomValues is also available on the project's plain HTTP deployment.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function estimateFees(grossFen: number, side: 'buy' | 'sell'): PaperFees {
  const commission_fen = Math.max(500, Math.floor((grossFen * 3 + 5000) / 10000));
  const transfer_fen = Math.floor((grossFen + 50000) / 100000);
  const stamp_fen = side === 'sell' ? Math.floor((grossFen * 5 + 5000) / 10000) : 0;
  return { commission_fen, transfer_fen, stamp_fen, fees_fen: commission_fen + transfer_fen + stamp_fen };
}

export function maxBuyQuantity(cashFen: number, priceFen: number, rules: PaperRules): number {
  if (!Number.isSafeInteger(cashFen) || !Number.isSafeInteger(priceFen) || cashFen <= 0 || priceFen <= 0) return 0;
  const step = rules.quantity_step;
  let low = Math.ceil(rules.min_quantity / step), high = Math.floor(Math.min(rules.max_quantity, cashFen / priceFen) / step);
  let affordable = 0;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2), quantity = middle * step, gross = quantity * priceFen;
    if (gross + estimateFees(gross, 'buy').fees_fen <= cashFen) { affordable = quantity; low = middle + 1; }
    else high = middle - 1;
  }
  return affordable;
}

export function formatMoney(fen: number | null): string {
  return fen === null || !Number.isFinite(fen) ? '—' : (fen / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
