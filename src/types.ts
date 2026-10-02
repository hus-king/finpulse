export interface Stock {
  code: string; name: string; initials: string; exchange: string;
  industry: string; price: number; change: number; bull_ratio: number;
}
export interface Candle { date: string; open: number; close: number; low: number; high: number; volume: number }
export interface News { id: string; title: string; source: string; content: string; score: number; tag: string; time: string }
export interface Dashboard {
  stock: Stock; candles: Candle[]; news: News[]; as_of: string;
  market: { temperature: number; indices: { name: string; value: string; change: number }[] };
  sentiment: { bull: number; bear: number; sample_count: number; keywords: string[] };
}
export interface Health { status: string; configured: boolean; model: string | null; provider: string | null }
export interface ModelReply {
  content: string; model: string; elapsed_ms: number; request_id: string;
  usage: { total_tokens?: number };
  analysis?: { sentiment_score: number; summary: string; causal_chain: string[]; uncertainty: string };
}
export interface User {
  id: string; username: string; nickname: string; created_at: string; last_login_at: string | null;
  role: 'user' | 'admin'; is_active: boolean;
}
export interface AuditEntry { actor_name: string; target_name: string; action: string; created_at: string }
export interface AuthSession { user: User; csrf_token: string; expires_at: string }
