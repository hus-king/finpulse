import { useEffect, useRef, useState } from 'react';
import { Activity, Info, LoaderCircle, RefreshCw } from 'lucide-react';
import { api } from './api';
import type { MarketSentiment, SentimentHistory } from './types';
import SentimentTrend from './SentimentTrend';

function formatTurnover(cny: number): string {
  if (!cny || cny <= 0) return '—';
  if (cny >= 1e12) {
    return `${(cny / 1e12).toFixed(2)} 万亿`;
  }
  return `${Math.round(cny / 1e8).toLocaleString()} 亿`;
}

function formatMarketTime(marketAsOf?: string | null, updatedAt?: string | null): string {
  const raw = marketAsOf || updatedAt;
  if (!raw) return '';
  if (raw.includes('T')) {
    const timePart = raw.split('T')[1];
    return timePart.slice(0, 5);
  }
  const match = raw.match(/\b\d{2}:\d{2}/);
  return match ? match[0] : raw.slice(-5);
}

export default function MarketSentimentCard() {
  const [sentiment, setSentiment] = useState<MarketSentiment | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController | null>(null);
  const historyActive = useRef<AbortController | null>(null);
  const [history, setHistory] = useState<SentimentHistory | null>(null);
  const [historyError, setHistoryError] = useState('');
  const fetchHistory = async () => {
    historyActive.current?.abort();
    const controller = new AbortController(); historyActive.current = controller;
    try {
      const data = await api<SentimentHistory>('/api/market/sentiment/history', undefined, controller.signal);
      if (!controller.signal.aborted) {
        setHistory(previous => data.status === 'unavailable' && previous?.points.length
          ? { ...data, points: previous.points.filter(point => point.date >= data.from_date && point.date <= data.to_date) } : data);
        setHistoryError(data.status === 'unavailable' ? data.message || '历史更新失败' : '');
      }
    } catch (err) {
      if (!controller.signal.aborted) setHistoryError((err as Error).message);
    }
  };

  const fetchSentiment = async (force = false) => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError('');
    try {
      const data = await api<MarketSentiment>(`/api/market/sentiment${force ? '?refresh=true' : ''}`, undefined, controller.signal);
      if (!controller.signal.aborted) { setSentiment(data); void fetchHistory(); }
    } catch (err) {
      if (!controller.signal.aborted) setError((err as Error).message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    void fetchSentiment();
    const timer = window.setInterval(() => void fetchSentiment(), 90_000);
    return () => { window.clearInterval(timer); active.current?.abort(); };
  }, []);

  useEffect(() => {
    void fetchHistory();
    const timer = window.setInterval(() => void fetchHistory(), history?.refreshing ? 5_000 : 90_000);
    return () => { window.clearInterval(timer); historyActive.current?.abort(); };
  }, [history?.refreshing]);

  const score = sentiment?.score ?? 50;
  const level = sentiment?.level ?? 'neutral';

  const levelColor = (lvl: string) => {
    switch (lvl) {
      case 'extreme_greed': return 'var(--up)';
      case 'greed': return '#e26d80';
      case 'neutral': return '#8ea2b4';
      case 'fear': return '#3aa992';
      case 'extreme_fear': return 'var(--down)';
      default: return '#8ea2b4';
    }
  };

  const timeLabel = formatMarketTime(sentiment?.market_as_of, sentiment?.updated_at);

  return (
    <section className="panel market-sentiment-card market-overview" aria-label="大盘概览">
      <div className="market-sentiment-header">
        <div>
          <span className="eyebrow">沪深全市场</span>
          <div className="title-with-hint">
            <h2>大盘概览</h2>
            <span
              className="info-hint"
              title={`计算模型：(上涨家数 + 0.5 × 平盘家数) / 总家数 × 100\n基于沪深 A 股涨跌家数；成交额单独展示，不参与评分`}
            >
              <Info size={13} />
            </span>
          </div>
          <p className="market-overview-scope">全市场情绪与涨跌广度</p>
        </div>
        <button
          className="subtle-button"
          disabled={loading}
          onClick={() => void fetchSentiment(true)}
          title="刷新全市场情绪"
        >
          {loading ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}
        </button>
      </div>

      {sentiment?.status === 'ok' || sentiment?.status === 'stale' ? (
        <div className="sentiment-body">
          <div className="market-overview-mood">
          <div className="gauge-display">
            <div className="gauge-score-wrap">
              <strong className="gauge-score" style={{ color: levelColor(level) }}>
                {sentiment.score ?? '—'}
              </strong>
              <span className="gauge-max">/ 100</span>
            </div>
            <span
              className={`sentiment-level-badge level-${level}`}
              style={{ borderColor: levelColor(level), color: levelColor(level) }}
            >
              {sentiment.level_name}
            </span>
          </div>

          <div className="gauge-meter-track" title={`当前指数: ${score}/100`}>
            <div className="meter-segment seg-extreme-fear" title="极度恐慌 (0-25)" />
            <div className="meter-segment seg-fear" title="偏弱/谨慎 (25-45)" />
            <div className="meter-segment seg-neutral" title="中性震荡 (45-55)" />
            <div className="meter-segment seg-greed" title="偏强/乐观 (55-75)" />
            <div className="meter-segment seg-extreme-greed" title="极度过热 (75-100)" />
            <div
              className="gauge-pointer"
              style={{ left: `${Math.max(2, Math.min(98, score))}%` }}
            />
          </div>

          <div className="gauge-labels">
            <span>恐慌探底</span>
            <span>中性均衡</span>
            <span>多头亢奋</span>
          </div>

          <div className="sentiment-summary-row">
            <span className="summary-dot" style={{ backgroundColor: levelColor(level) }} />
            <p className="sentiment-summary">{sentiment.summary}</p>
          </div>
          </div>
          <div className="market-overview-breadth">
          <div className="market-breadth-grid">
            <div className="breadth-item up">
              <small>上涨家数</small>
              <strong>{sentiment.advancing.toLocaleString()}</strong>
            </div>
            <div className="breadth-item down">
              <small>下跌家数</small>
              <strong>{sentiment.declining.toLocaleString()}</strong>
            </div>
            <div className="breadth-item neutral">
              <small>平盘</small>
              <strong>{sentiment.flat.toLocaleString()}</strong>
            </div>
            <div className="breadth-item turnover">
              <small>两市成交</small>
              <strong>{formatTurnover(sentiment.turnover_cny)}</strong>
            </div>
          </div>

          <div className="sentiment-card-footer">
            <span
              className="source-tag"
              title={`数据来源: ${sentiment.source ?? '沪深全市场数据'}\n计算方法: ${sentiment.method ?? '全市场广度代理指标'}`}
            >
              沪深全市场广度
            </span>
            {timeLabel && (
              <span className="time-tag">
                行情时间 {timeLabel}
              </span>
            )}
          </div>
          </div>
        </div>
      ) : (
        <div className="sentiment-empty">
          <Activity size={20} />
          <p>{error || sentiment?.summary || '正在读取全市场宏观数据…'}</p>
        </div>
      )}
      <SentimentTrend history={history} error={historyError} />
      {sentiment?.status === 'stale' && <p className="source-note">数据稍有延迟，当前展示最近有效快照。</p>}
      {error && <p className="source-note">刷新失败：{error}</p>}
    </section>
  );
}
