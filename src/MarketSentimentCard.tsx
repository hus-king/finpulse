import { useEffect, useRef, useState } from 'react';
import { Activity, LoaderCircle, RefreshCw } from 'lucide-react';
import { api } from './api';
import type { MarketSentiment } from './types';

export default function MarketSentimentCard({ initial }: { initial?: MarketSentiment }) {
  const [sentiment, setSentiment] = useState<MarketSentiment | null>(initial ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController | null>(null);

  const fetchSentiment = async (force = false) => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError('');
    try {
      const data = await api<MarketSentiment>(`/api/market/sentiment${force ? '?refresh=true' : ''}`, undefined, controller.signal);
      if (!controller.signal.aborted) setSentiment(data);
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

  return (
    <section className="panel market-sentiment-card">
      <div className="market-sentiment-header">
        <div>
          <span className="eyebrow">MACRO SENTIMENT</span>
          <h2>市场情绪温度计</h2>
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

      {sentiment?.status === 'stale' && <p className="source-note">来源更新未成功或缓存已过期，当前显示上次有效数据。</p>}
      {error && <p className="source-note">刷新失败：{error}</p>}
      {sentiment?.status === 'ok' || sentiment?.status === 'stale' ? (
        <div className="sentiment-body">
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
            <div className="meter-segment seg-extreme-fear" title="下跌集中 (0-25)" />
            <div className="meter-segment seg-fear" title="偏弱 (25-45)" />
            <div className="meter-segment seg-neutral" title="中性震荡 (45-55)" />
            <div className="meter-segment seg-greed" title="偏强/乐观 (55-75)" />
            <div className="meter-segment seg-extreme-greed" title="上涨集中 (75-100)" />
            <div
              className="gauge-pointer"
              style={{ left: `${Math.max(2, Math.min(98, score))}%` }}
            />
          </div>

          <div className="gauge-labels">
            <span>下跌集中</span>
            <span>中性</span>
            <span>上涨集中</span>
          </div>

          <p className="sentiment-summary">{sentiment.summary}</p>

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
              <strong>{(sentiment.turnover_cny / 1e8).toFixed(0)} 亿</strong>
            </div>
          </div>
          <p className="source-note">{sentiment.source ?? '东方财富 · 沪深指数涨跌家数'}。{sentiment.method ?? '涨跌家数的广度情绪代理指标'}；不代表完整的恐惧贪婪指数。</p>
          {sentiment.updated_at && (
            <small className="sentiment-time">
              采集于 {sentiment.updated_at.slice(0, 16).replace('T', ' ')}
              {sentiment.market_as_of && ` · 行情时间 ${sentiment.market_as_of.slice(0, 16).replace('T', ' ')}`}
            </small>
          )}
        </div>
      ) : (
        <div className="sentiment-empty">
          <Activity size={20} />
          <p>{error || sentiment?.summary || '正在读取全市场宏观数据…'}</p>
        </div>
      )}
    </section>
  );
}
