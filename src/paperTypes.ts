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
  valuation_refreshing?: boolean; valuation_errors?: Record<string, string>; next_poll_seconds?: number;
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
  actor?: 'manual' | 'algorithm'; strategy_id?: string; model_version?: string;
  request_id: string; sequence: number; code: string; name: string; side: 'buy' | 'sell';
  quantity: number; price_fen: number; gross_fen: number; cash_after_fen: number;
  realized_pnl_fen: number; executed_at: string; quote_as_of: string; quote_fetched_at: string; source: string;
}
export interface PaperHistory { items: PaperTrade[]; next_before_seq: number | null }
export interface PaperIntent { request_id: string; code: string; side: 'buy' | 'sell'; quantity: number }

export interface QuantMetrics { total_return: number; annualized_return: number; max_drawdown: number; trades: number; start: string; end: string }
export interface PaperStrategy { id: string; name: string; available: boolean; kind?: 'peg'; origin?: 'official' | 'user'; revision?: number; digest?: string; description?: string; reason?: string; version?: string; universe?: string[]; rules_summary?: { top_n: number; per_stock_weight: number; max_total_weight: number }; report?: { limits?: string[]; test?: { model: QuantMetrics; momentum: QuantMetrics; equal_weight: QuantMetrics } } }
export interface UserStrategyRecord { id: string; revision: number; digest: string; strategy: Record<string, unknown> }
export interface StrategySubmissionSchema { schema_version: string; json_schema: Record<string, unknown>; example: Record<string, unknown>; features: Record<string, string>; limits: { max_package_bytes: number; max_strategies: number; max_nodes: number; max_depth: number; writes_per_minute: number } }
export interface QuantDecision {
  key: string; date: string; as_of: string; status: string; model_version: string; reason?: string;
  target_weights: Record<string, number>;
  skipped_targets?: Record<string, string>;
  peg_snapshot?: { snapshot_id: string; method_version: string; market_date: string; published_at: string };
  peg_selection?: { code: string; name: string; group: string; peg: number }[];
  allocation_outcome?: { positions: Record<string, { quantity: number; weight: number }>; differences: Record<string, { target_weight: number; actual_weight: number }>; note: string };
  results?: { request_id: string; code: string; status: string; sequence?: number; reason?: string }[];
}
export interface PaperAutomationState {
  mode: 'manual' | 'algorithm'; revision: number; strategy_id: string | null; scheduler_enabled: boolean;
  strategy_config?: { top_n?: number; group?: string };
  reason: string | null; next_check: string; last_decision: QuantDecision | null;
  runtime: { status?: string; checked_at?: string; reason?: string };
}
