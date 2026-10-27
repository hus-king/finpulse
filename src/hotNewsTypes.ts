export type MarketChannel = 'trade' | 'supply' | 'cost' | 'demand' | 'regulation' | 'funding' | 'risk';
export type ImpactDirection = 'positive' | 'negative' | 'mixed' | 'unclear';
export const impactDirections = { positive: '利好', negative: '利空', mixed: '正反并存', unclear: '影响不明' };
export interface SectorImpact {
  id: string; name: string; direction: ImpactDirection; reason: string; evidence: string; method: 'rules' | 'model';
}
export interface HotSource {
  id: string; name: string; rank: number | null; kind?: 'news' | 'trend'; link_kind?: string; publisher?: string; published_at?: string; heat: string | null; url: string;
  observed_at: string; observed_timestamp: number; stale: boolean;
}
export interface HotTopic {
  id: string; title: string; summary: string; sources: HotSource[]; rank: number; rank_score: number;
  sector_impacts: SectorImpact[];
  attention?: { mentions: number; platforms: number; snapshots: number; best_rank: number | null; priority: boolean; related_reports: number; score: number };
  members?: { id: string; title: string; sources: HotSource[] }[];
  screening: { event?: { kind: string; actor: string; action: string; new_fact: string; novelty: string; importance: string; attribution: string }; id: string; decision: 'keep' | 'review' | 'exclude'; channels: MarketChannel[];
    reason: string; evidence: string; method: 'rules' | 'model'; model?: string };
}
export interface HotFeed {
  items: HotTopic[]; status: 'ok' | 'partial' | 'stale' | 'unavailable'; fetched_at: string | null;
  sources: { id: string; name: string; url: string; status: 'ok' | 'stale' | 'unavailable'; fetched_at: string | null; error: string | null }[];
  screening: 'rules' | 'model' | 'mixed'; warnings: string[];
  refresh?: { status: 'idle' | 'running' | 'completed' | 'error'; stage?: string; message?: string; completed?: number; total?: number };
  counts: { keep: number }; note: string; ranking_note: string;
  matched_total: number; candidate_total: number;
  filters: { decision: 'keep'; source: string; channel: string; q: string; sort: 'hot' | 'latest'; limit: number };
  channel_options: { id: MarketChannel; name: string }[];
}
export interface HotStockImpact {
  code: string; relation: 'direct' | 'indirect' | 'none' | 'uncertain';
  direction: 'positive' | 'negative' | 'mixed' | 'unclear'; confidence: 'high' | 'medium' | 'low';
  news_evidence: string[]; business_evidence: string; mechanism: string; uncertainty: string;
  score: number | null;
  relationship_evidence?: { id: string; quote: string; source: string; url: string }[];
}
export interface HotImpactRow {
  code: string; name: string; status: 'ok' | 'pending' | 'failed'; analysis: HotStockImpact | null;
  model?: string; analyzed_at?: string; error?: string;
  business: { main_business: string; source: string | null; url: string | null; fetched_at: number | null; status: string };
}
export interface HotImpactSnapshot {
  topic_id: string; title: string; summary: string; sources: HotSource[]; items: HotImpactRow[]; version: string; note: string;
  counts: { total: number; completed: number; pending: number; failed: number; related: number };
}
export type HotImpactPreview = Omit<HotImpactSnapshot, 'items'> & { items: Omit<HotImpactRow, 'business'>[] };
export interface HotMatchJob {
  id: string; status: 'queued' | 'running' | 'completed' | 'partial' | 'interrupted' | 'error';
  stage: string; message: string; started_at: string; updated_at: string;
  topic_ids: string[]; topic_total: number; stock_total: number; topics_completed: number; watchlist_changed: boolean;
  counts: { total: number; completed: number; failed: number; uncertain: number; related: number; related_stocks: number };
  results: HotImpactPreview[];
  errors: { topic_id: string; title: string; message: string }[];
  stocks: { code: string; name: string; news: { topic_id: string; title: string; sources: HotSource[]; analysis: HotStockImpact; model?: string; analyzed_at?: string }[] }[];
}
