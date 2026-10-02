export interface Stock {
  code: string; name: string; initials: string; exchange: string;
  industry: string; price: number | null; change: number | null;
}
export interface Candle { date: string; open: number; close: number; low: number; high: number; volume: number }
export interface Analysis { sentiment_score: number; summary: string; causal_chain: string[]; uncertainty: string }
export interface News {
  id: string; title: string; source: string; url: string; content: string; score: number | null; tag: string; time: string;
  analysis: Analysis | null; analysis_status: string; analysis_error?: string;
  text_source: string; date_status: string; analyzed_at?: string;
  sources: { title: string; url: string; date: string }[];
}
export interface Pipeline { date_range: string[]; statuses: Record<string, string>; counts: Record<string, number>; warnings: string[]; collection_id: string }
export interface Dashboard {
  stock: Stock; candles: Candle[]; news: News[]; as_of: string | null; pipeline: Pipeline | null;
  quote: { status: string; source?: string; is_realtime: boolean; as_of_date?: string; collected_at?: string; volume_unit?: string };
  sentiment: { status: string; bull: number | null; bear: number | null; neutral: number | null; sample_count: number; keywords: string[]; alert?: string; note?: string; error?: string; posts: { id: string; title: string; url: string; date: string; stance?: string }[] };
  backtest: { items: { news_id: string; title: string; score: number | null; publication_date: string; base_date: string | null; return_3d: number | null; return_5d: number | null }[]; note: string };
}
export interface Job { id: string; code: string; status: string; stage: string; warnings: string[]; counts: Record<string, number> }
export interface Health { status: string; configured: boolean; model: string | null; provider: string | null; tavily_configured: boolean; scheduler_enabled: boolean }
export interface ModelReply {
  content: string; model: string; elapsed_ms: number; request_id: string;
  usage: { total_tokens?: number }; analysis?: Analysis; cached?: boolean;
}
export interface User {
  id: string; username: string; nickname: string; created_at: string; last_login_at: string | null;
  role: 'user' | 'admin'; is_active: boolean;
}
export interface AuditEntry { actor_name: string; target_name: string; action: string; created_at: string }
export interface AuthSession { user: User; csrf_token: string; expires_at: string }
