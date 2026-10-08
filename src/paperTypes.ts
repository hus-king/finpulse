import type { Stock } from './types';

export interface PaperRules { board: string; min_quantity: number; quantity_step: number; max_quantity: number }
export interface PaperMarket { can_trade: boolean; label: string; server_time: string; reason: string | null; reason_code: string | null }
export interface PaperFees { commission_fen: number; transfer_fen: number; stamp_fen: number; fees_fen: number }
export interface PaperPosition {
  code: string; name: string; quantity: number; sellable: number; locked: number;
  cost_fen: number; average_cost_fen: number; price_fen: number; price_as_of: string;
  valuation_status: 'fresh' | 'stale' | 'estimated'; market_value_fen: number; unrealized_pnl_fen: number;
}
export interface PaperAccount {
  initial_cash_fen: number; cash_fen: number; market_value_fen: number; equity_fen: number;
  realized_pnl_fen: number; unrealized_pnl_fen: number; total_pnl_fen: number;
  return_percent: number; total_fees_fen: number; positions: PaperPosition[]; market: PaperMarket;
}
export interface PaperQuote {
  stock: Stock; rules: PaperRules; price_fen: number | null; as_of: string | null;
  fetched_at: string | null; source: string; market: PaperMarket; can_trade: boolean;
  reason: string | null; reason_code: string | null; refreshing: boolean; next_poll_seconds: number;
  quote_status: 'fresh' | 'stale' | 'unavailable';
}
export interface PaperTrade extends PaperFees {
  request_id: string; sequence: number; code: string; name: string; side: 'buy' | 'sell';
  quantity: number; price_fen: number; gross_fen: number; cash_after_fen: number;
  realized_pnl_fen: number; executed_at: string; quote_as_of: string; quote_fetched_at: string; source: string;
}
export interface PaperHistory { items: PaperTrade[]; next_before_seq: number | null }
export interface PaperIntent { request_id: string; code: string; side: 'buy' | 'sell'; quantity: number }
