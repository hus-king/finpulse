import { useState } from 'react';
import { Activity, ChevronRight, LoaderCircle, RefreshCw } from 'lucide-react';
import PriceChart from './PriceChart';
import useMinuteMarket from './useMinuteMarket';
import useDailyMarket from './useDailyMarket';
import { dateTime, number, percent, tone } from './format';
import type { Dashboard } from './types';

export default function QuotePanel({ data, add, running, onEvent, onMarket }: {
  data: Dashboard; add: () => void; running: boolean; onEvent: (id: string) => void;
  onMarket: (data: Dashboard) => void;
}) {
  const [mode, setMode] = useState('近 3 月');
  const [period, setPeriod] = useState('日 K');
  const [indicator, setIndicator] = useState('成交量');
  const minutePeriod = period.endsWith('分钟') ? parseInt(period) : null;
  const minute = useMinuteMarket(data.stock.code, minutePeriod);
  // Daily history loads when the stock is opened, including in minute mode.
  const daily = useDailyMarket(data.stock.code, onMarket);
  const dailyBusy = daily.loading || data.daily_request?.refreshing;
  const dailyError = daily.error || data.daily_request?.error;
  const candles = minutePeriod ? minute.data?.candles ?? [] : data.candles;
  const dailyBar = data.candles.at(-1);
  const quote = minutePeriod ? minute.data?.quote : null;
  const price = minutePeriod ? quote?.price : data.stock.price;
  const change = minutePeriod ? quote?.change ?? null : data.stock.change;
  const busy = minute.loading || minute.data?.refreshing;
  const lastDate = minute.data?.as_of?.slice(0, 10);
  const dailyBase = lastDate ? [...data.candles].reverse().find(bar => bar.date < lastDate) : null;
  const priceChange = minutePeriod && change == null && price != null && dailyBase ? (price / dailyBase.close - 1) * 100 : change;
  const lastTime = minutePeriod ? minute.data?.as_of : data.quote.as_of_date;
  const modes = minutePeriod ? ['当日', '近 5 日', '全部'] : ['近 1 月', '近 3 月', '全部'];

  return <section className="panel live-quote">
    <header><div><span className="industry-tag">{data.stock.industry}</span><h2>{data.stock.name}<small>{data.stock.exchange}:{data.stock.code}</small></h2>
      <p>{minutePeriod ? 'AkShare / 新浪分钟行情' : data.quote.source ?? '尚未采集行情'} · {lastTime ?? '—'} · {minutePeriod ? '分钟快照 · 未复权' : data.quote.status === 'stale' ? '上次保存的日线，本次更新失败' : '历史日线收盘'}</p>
    </div><button className="subtle-button" onClick={add}>切换标的<ChevronRight size={14} /></button></header>
    <div className="live-price-row"><div><strong className={`mono ${tone(priceChange)}`}>{number(price)}</strong><span className={tone(priceChange)}>{percent(priceChange)}<small>较前一返回交易日</small></span></div>
      <dl><div><dt>开盘</dt><dd>{number(minutePeriod ? quote?.open : dailyBar?.open)}</dd></div><div><dt>最高</dt><dd>{number(minutePeriod ? quote?.high : dailyBar?.high)}</dd></div><div><dt>最低</dt><dd>{number(minutePeriod ? quote?.low : dailyBar?.low)}</dd></div><div><dt>{minutePeriod ? '当日成交量' : '成交量'}</dt><dd>{minutePeriod ? quote?.volume != null ? `${(quote.volume / 10000).toFixed(1)} 万股` : '—' : dailyBar ? `${(dailyBar.volume / 10000).toFixed(1)} 万股` : '—'}</dd></div></dl>
    </div>
    <div className="live-chart-toolbar"><div className="segmented">{modes.map(item => <button key={item} className={mode === item ? 'active' : ''} onClick={() => setMode(item)}>{item}</button>)}</div>
      <select aria-label="K线周期" value={period} onChange={e => { setPeriod(e.target.value); setMode(e.target.value.endsWith('分钟') ? '当日' : '近 3 月'); }}><option>日 K</option><option>周 K</option>{[1, 5, 15, 30, 60].map(value => <option key={value}>{value} 分钟</option>)}</select><span>MA5 / MA10 / MA20</span>
    </div>
    {minutePeriod && <div className={`minute-status ${minute.data?.market.is_trading ? 'trading' : ''}`} role="status">
      <span className="minute-market-state"><span className={`status-dot ${busy ? 'pending' : ''}`} />{minute.data?.market.label ?? '正在确认交易状态'}</span>
      <span>{minute.data?.market.is_trading ? '30 秒轮询行情' : '保留最近交易数据'}</span>
      <span>{minute.data?.status === 'stale' ? '数据延迟 / 更新失败' : minute.data?.forming ? '最新一根尚未完成' : minute.data?.is_realtime ? '分钟行情已更新' : '历史分钟快照'}</span>
      <button className="subtle-button" disabled={busy} onClick={minute.refresh}>{busy ? <LoaderCircle size={13} className="spin" /> : <RefreshCw size={13} />}{busy ? '获取中' : '刷新行情'}</button>
    </div>}
    {!minutePeriod && <div className="minute-status" role="status"><span className="minute-market-state"><span className={`status-dot ${dailyBusy ? 'pending' : ''}`} />{dailyBusy ? '正在获取日线行情' : dailyError ? '日线更新失败' : '日线行情已就绪'}</span><span>自动获取股价与历史 K 线</span><button className="subtle-button" disabled={dailyBusy} onClick={daily.refresh}>{dailyBusy ? <LoaderCircle size={13} className="spin" /> : <RefreshCw size={13} />}{dailyBusy ? '获取中' : '刷新行情'}</button></div>}
    {!minutePeriod && dailyError && <div className="minute-warning" role="alert">{dailyError}{candles.length ? '；保留已获取的日线。' : '；可点击刷新行情重试。'}</div>}
    {(minute.error || minute.data?.error) && minutePeriod && <div className="minute-warning" role="alert">{minute.error || minute.data?.error}{candles.length ? '；保留已获取的分钟数据。' : ''}</div>}
    {candles.length ? <PriceChart key={`${data.stock.code}:${period}`} candles={candles} news={data.news} mode={mode} period={period} indicator={indicator} onEvent={onEvent} /> : <div className="chart-empty">{(minutePeriod ? busy : dailyBusy) ? <LoaderCircle size={32} className="spin" /> : <Activity size={32} />}<h3>{minutePeriod ? '正在准备分钟 K 线' : dailyError ? '暂未取得日线行情' : '正在准备日 K 线'}</h3><p>{minutePeriod ? minute.error || minute.data?.error ? '暂无可用行情，请稍后点击刷新重试。' : '独立获取真实分钟数据，新闻和 AI 研判无需重新采集。' : dailyError ? '行情源请求失败，可独立刷新重试。' : '打开股票后自动获取真实价格与历史日线，新闻和 AI 研判无需重新采集。'}</p></div>}
    <footer className="chart-footer"><div className="indicator-switch">{['成交量', 'MACD', 'RSI'].map(item => <button key={item} className={indicator === item ? 'active' : ''} onClick={() => setIndicator(item)}>{item}</button>)}</div><span>{minutePeriod ? '切换日 / 周 K 查看新闻标记' : '新闻日期标记 · 点击查看原文与研判'}</span></footer>
    {minutePeriod && <div className="minute-caption"><span>数据时间：{minute.data?.as_of ?? '—'}</span><span>获取时间：{minute.data?.fetched_at ? dateTime(minute.data.fetched_at) : '—'}</span><p>休市不补造 K 线；30 秒为请求间隔，行情源更新可能延迟。Ctrl + 滚轮缩放。</p></div>}
  </section>;
}
