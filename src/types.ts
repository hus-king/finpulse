export interface Stock {
  code: string; name: string; initials: string; exchange: string;
  industry: string; price: number | null; change: number | null;
}
export interface Candle { date: string; open: number; close: number; low: number; high: number; volume: number; partial?: boolean }
export interface MinuteMarket {
  code: string; period: number; candles: Candle[]; as_of: string | null; fetched_at: string | null;
  source: string; status: 'ok' | 'stale' | 'unavailable'; refreshing: boolean; forming: boolean;
  is_realtime: boolean; error: string | null; next_poll_seconds: number; note: string;
  derived_from?: string; partial_bars?: number;
  quote: { price: number | null; change: number | null; open: number | null; high: number | null; low: number | null; volume: number | null };
  market: { state: string; label: string; is_trading: boolean; is_trade_day: boolean | null; expected_data_time: string | null; server_time: string; calendar_year: number | null };
}
export interface MarketSentiment {
  source?: string; method?: string; market_as_of?: string | null;
  status: 'ok' | 'stale' | 'unavailable';
  score: number | null;
  level: 'extreme_greed' | 'greed' | 'neutral' | 'fear' | 'extreme_fear' | null;
  level_name: string;
  summary: string;
  advancing: number;
  declining: number;
  flat: number;
  total: number;
  turnover_cny: number;
  updated_at: string | null;
}
export interface SentimentHistory {
  points: { date: string; score: number; advancing: number; declining: number; flat: number; total: number; market_as_of: string; source: string }[];
  trading_dates: string[]; refreshing: boolean; missing_days: number; status: 'ok' | 'loading' | 'unavailable';
  message: string; from_date: string; to_date: string; source: string;
}
export interface MarketBundle {
  dashboard: Dashboard; minutes: Record<string, MinuteMarket>; status: 'ok' | 'partial';
  errors: Record<string, string>; next_poll_seconds: number; fetched_at: string;
  market_sentiment?: MarketSentiment;
}
export interface Analysis { sentiment_score: number | null; version?: string; assessment?: 'positive'|'negative'|'neutral'|'mixed'|'insufficient'; confidence?: 'high'|'medium'|'low'; horizon?: 'short'|'medium'|'long'|'unclear'; positive_factors?: string[]; negative_factors?: string[]; watch_points?: string[]; summary: string; causal_chain: string[]; uncertainty: string }
export interface News {
  id: string; title: string; source: string; url: string; content: string; score: number | null; tag: string; time: string;
  analysis: Analysis | null; analysis_status: string; analysis_error?: string;
  text_source: string; date_status: string; analyzed_at?: string;
  sources: { title: string; url: string; date: string }[];
  news_scope?: 'company' | 'industry'; industry?: string | null;
  related_factors?: string[]; relevance_reason?: string; stale?: boolean; refresh_pending?: boolean;
}
export interface IndustryProfile {
  code: string; industry: string | null; status: 'ok' | 'stale' | 'unavailable';
  source?: string; fetched_at: string | null; version: string; error?: string | null; note: string;
}
export interface Pipeline { date_range: string[]; statuses: Record<string, string>; counts: Record<string, number>; warnings: string[]; collection_id?: string; job_status?: string; stages?: Record<string, string> }
export interface OverviewEvidence { news_id:string; title:string; url:string; text:string; score:number|null; confidence:string; horizon:string; date?:string }
export interface ResearchOverview { status:string; counts:Record<string,number>; analyzed:number; total:number; net_score:number|null; score_series:Omit<OverviewEvidence,'text'>[]; score_range:[number,number]|null; opportunities:OverviewEvidence[]; risks:OverviewEvidence[]; watch_points:OverviewEvidence[]; note:string }
export interface Dashboard {
  excluded_news?:{id:string;title:string;url:string;reason:string}[];
  research_overview?:ResearchOverview;
  business_profile?:{main_business?:string; source?:string; url?:string; fetched_at?:string; status:string; note?:string; revenue_segments:null};
  industry_profile?: IndustryProfile;
  daily_request?: { refreshing: boolean; error: string | null; next_poll_seconds: number; market: { label: string } };
  stock: Stock; candles: Candle[]; news: News[]; as_of: string | null; revision?: string; pipeline: Pipeline | null;
  quote: { status: string; source?: string; is_realtime: boolean; as_of_date?: string; collected_at?: string; volume_unit?: string };
  market_sentiment?: MarketSentiment;
  sentiment: { status: string; bull: number | null; bear: number | null; neutral: number | null; sample_count: number; keywords: string[]; alert?: string; alert_level?: 'overheated' | 'frozen' | 'normal' | null; weighting_method?: string; note?: string; error?: string; source?: string; collected_at?: string; warnings?: string[]; diagnostics?: { direct?: { status?: string; listed?: number; eligible?: number; retained?: number; detail_failed?: number }; tavily?: { status?: string; listed?: number; retained?: number } }; posts: { id: string; title: string; url: string; date: string; stance?: string; content?: string; text_source?: string; views?: number | null; replies?: number | null; weight?: number }[] };
  backtest: { items: { news_id: string; title: string; score: number | null; publication_date: string; base_date: string | null; return_3d: number | null; return_5d: number | null }[]; note: string };
}
export interface Job { id: string; code: string; status: string; stage: string; warnings: string[]; counts: Record<string, number>; data_revision?: string }
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
