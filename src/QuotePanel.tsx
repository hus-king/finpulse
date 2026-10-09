import { useEffect, useState } from 'react';
import { Activity, LoaderCircle } from 'lucide-react';
import PriceChart from './PriceChart';
import IntradayChart from './IntradayChart';
import { buildIntraday, initialChartPeriod, selectSessionDay, sessionPreviousClose } from './intradayMath';
import ChartControls, { chartStyles } from './ChartControls';
import type { ChartOverlay, ChartStyle } from './chartMath';
import useMarketBundle from './useMarketBundle';
import { dateTime, number, percent, tone } from './format';
import type { Dashboard } from './types';

const preferenceKey = 'finpulse-chart-preferences';
interface ChartPreferences {
  visible: boolean; chartStyle: ChartStyle; overlay: ChartOverlay;
  period: string; mode: string; indicator: string;
}
function readPreferences(): ChartPreferences {
  const defaults: ChartPreferences = { visible: true, chartStyle: 'candles', overlay: 'MA', period: initialChartPeriod(), mode: '当日', indicator: '成交量' };
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey) ?? 'null');
    if (!saved || typeof saved !== 'object') return defaults;
    const period=initialChartPeriod(saved);
    return {
      visible: typeof saved.visible === 'boolean' ? saved.visible : defaults.visible,
      chartStyle: chartStyles.some(item => item.value === saved.chartStyle) ? saved.chartStyle : defaults.chartStyle,
      overlay: ['none', 'MA', 'EMA', 'BOLL'].includes(saved.overlay) ? saved.overlay : defaults.overlay,
      period, mode: '当日',
      indicator: ['成交量', 'MACD', 'RSI'].includes(saved.indicator) ? saved.indicator : defaults.indicator,
    };
  } catch { return defaults; }
}

export default function QuotePanel({ data, add, running, onEvent, onMarket }: {
  data: Dashboard; add: () => void; running: boolean; onEvent: (id: string) => void;
  onMarket: (data: Dashboard) => void;
}) {
  const [preferences, setPreferences] = useState(readPreferences);
  const { visible, chartStyle, overlay, mode, period, indicator } = preferences;
  const updatePreferences = (next: Partial<ChartPreferences>) => setPreferences(previous => ({ ...previous, ...next }));
  useEffect(() => {
    try { localStorage.setItem(preferenceKey, JSON.stringify(preferences)); } catch { /* Controls work without browser storage. */ }
  }, [preferences]);
  const intraday=period==='分时';
  const minutePeriod = intraday ? 1 : period.endsWith('分钟') ? parseInt(period) : null;
  // The request depends only on the stock, never on the selected chart period.
  const market = useMarketBundle(data.stock.code, onMarket);
  const minute = { data: minutePeriod ? market.data?.minutes[String(minutePeriod)] : null,
    loading: market.loading, error: market.error, refresh: market.refresh };
  const dailyBusy = market.loading || market.data?.dashboard.daily_request?.refreshing;
  const dailyError = market.error || market.data?.errors.daily;
  const session=intraday && minute.data ? buildIntraday(minute.data.candles,minute.data.market,sessionPreviousClose(selectSessionDay(minute.data.market,minute.data.candles),data.candles,minute.data.candles)) : null;
  const candles = minutePeriod ? minute.data?.candles ?? [] : data.candles;
  const dailyBar = data.candles.at(-1);
  const quote = intraday ? session?.quote : minutePeriod ? minute.data?.quote : null;
  const price = minutePeriod ? quote?.price : data.stock.price;
  const change = minutePeriod ? quote?.change ?? null : data.stock.change;
  const busy = minute.loading || minute.data?.refreshing;
  const lastDate = intraday ? session?.day : minute.data?.as_of?.slice(0, 10);
  const dailyBase = lastDate ? [...data.candles].reverse().find(bar => bar.date < lastDate) : null;
  const priceChange = !intraday && minutePeriod && change == null && price != null && dailyBase ? (price / dailyBase.close - 1) * 100 : change;
  const lastTime = intraday ? session?.as_of : minutePeriod ? minute.data?.as_of : data.quote.as_of_date;
  const modes = minutePeriod ? ['当日', '近 5 日', '全部'] : ['近 1 月', '近 3 月', '全部'];

  return <section className={`panel live-quote ${visible ? '' : 'chart-collapsed'}`}>
    <header><div><span className="industry-tag">{data.stock.industry}</span><h2>{data.stock.name}<small>{data.stock.exchange}:{data.stock.code}</small></h2>
      <p>{minutePeriod ? minute.data?.source ?? '分钟行情' : data.quote.source ?? '尚未采集行情'} · {lastTime ?? '—'} · {intraday ? `分时 · ${session?.day ?? '等待交易日确认'}` : minutePeriod ? '分钟快照 · 未复权' : data.quote.status === 'stale' ? '上次保存的日线，本次更新失败' : '历史日线收盘'}</p>
    </div><ChartControls visible={visible} toggleVisible={() => updatePreferences({ visible: !visible })}
      chartStyle={chartStyle} setChartStyle={chartStyle => updatePreferences({ chartStyle })}
      overlay={overlay} setOverlay={overlay => updatePreferences({ overlay })}
      period={period} setPeriod={period => updatePreferences({ period, mode: period === '分时' || period.endsWith('分钟') ? '当日' : '近 3 月' })}
      mode={mode} modes={modes} setMode={mode => updatePreferences({ mode })}
      indicator={indicator} setIndicator={indicator => updatePreferences({ indicator })}
      busy={!!(minutePeriod ? busy : dailyBusy)} refresh={market.refresh} add={add} /></header>
    <div className="live-price-row"><div><strong className={`mono ${tone(priceChange)}`}>{number(price)}</strong><span className={tone(priceChange)}>{percent(priceChange)}<small>较前一返回交易日</small></span></div>
      <dl><div><dt>开盘</dt><dd>{number(minutePeriod ? quote?.open : dailyBar?.open)}</dd></div><div><dt>最高</dt><dd>{number(minutePeriod ? quote?.high : dailyBar?.high)}</dd></div><div><dt>最低</dt><dd>{number(minutePeriod ? quote?.low : dailyBar?.low)}</dd></div><div><dt>{minutePeriod ? '当日成交量' : '成交量'}</dt><dd>{minutePeriod ? quote?.volume != null ? `${(quote.volume / 10000).toFixed(1)} 万股` : '—' : dailyBar ? `${(dailyBar.volume / 10000).toFixed(1)} 万股` : '—'}</dd></div></dl>
    </div>
    {visible && <>
    <div className="chart-context"><span>{intraday ? `分时走势 · ${session?.day ?? '等待交易日确认'}` : `${chartStyles.find(item => item.value === chartStyle)?.label} · ${period} · ${mode}`}</span><span>{intraday ? '分钟价格 / 成交量' : overlay === 'MA' || overlay === 'EMA' ? `${overlay}5 / 10 / 20` : overlay === 'BOLL' ? 'BOLL 20 · 2σ' : '不叠加均线'}{!intraday && ` · ${indicator}`}</span></div>
    {minutePeriod && <div className={`minute-status ${minute.data?.market.is_trading ? 'trading' : ''}`} role="status">
      <span className="minute-market-state"><span className={`status-dot ${busy ? 'pending' : ''}`} />{minute.data?.market.label ?? '正在确认交易状态'}</span>
      <span>{busy ? '后台更新中 · 2 秒检查' : minute.data?.market.is_trading ? '30 秒轮询行情' : minute.data?.market.state === 'pre_open' && intraday ? '等待当日开盘' : '保留最近交易数据'}</span>
      <span>{minute.data?.status === 'stale' ? '数据延迟 / 更新失败' : minute.data?.forming ? '最新一根尚未完成' : minute.data?.is_realtime ? '分钟行情已更新' : '历史分钟快照'}</span>
    </div>}
    {!minutePeriod && <div className="minute-status" role="status"><span className="minute-market-state"><span className={`status-dot ${dailyBusy ? 'pending' : ''}`} />{dailyBusy ? candles.length ? '日线缓存已展示，后台更新中' : '正在获取日线' : dailyError ? '日线更新失败' : '日线行情已就绪'}</span><span>分时与日线分别就绪 · 切换周期直接绘图</span></div>}
    {!minutePeriod && dailyError && <div className="minute-warning" role="alert">{dailyError}{candles.length ? '；保留已获取的日线。' : '；可点击刷新行情重试。'}</div>}
    {(minute.error || minute.data?.error) && minutePeriod && <div className="minute-warning" role="alert">{minute.error || minute.data?.error}{candles.length ? '；保留已获取的分钟数据。' : ''}</div>}
    {intraday && session ? <IntradayChart session={session} loading={!!busy} /> : candles.length && !intraday ? <PriceChart key={`${data.stock.code}:${period}`} candles={candles} news={data.news} mode={mode} period={period} indicator={indicator} chartStyle={chartStyle} overlay={overlay} onEvent={onEvent} /> : <div className="chart-empty">{market.loading ? <LoaderCircle size={32} className="spin" /> : <Activity size={32} />}<h3>{market.loading ? intraday ? '正在读取分时缓存' : '正在读取行情缓存' : '暂未取得该周期行情'}</h3><p>{market.loading ? '已保存的数据优先展示，最新行情在后台更新。' : '行情源未返回可用数据，可在图表工具中刷新行情。'}</p></div>}
    <footer className="chart-footer"><span>{intraday ? '分时走势 · 未来时段与缺失分钟留空' : chartStyle === 'heikin' ? '平滑 K 线为计算后的趋势价格' : minutePeriod ? 'Ctrl + 滚轮缩放' : '新闻日期标记 · 点击查看原文与研判'}</span></footer>
    {!minutePeriod && market.data?.errors.minute && <div className="minute-warning" role="alert">分钟行情：{market.data.errors.minute}；日线可正常查看，分钟线可稍后刷新。</div>}
    {minutePeriod && <div className="minute-caption"><span>数据时间：{intraday ? session?.as_of ?? '—' : minute.data?.as_of ?? '—'}</span><span>获取时间：{minute.data?.fetched_at ? dateTime(minute.data.fetched_at) : '—'}</span><p>全部分钟周期来自同一份 1 分钟数据，历史范围相同。{minute.data?.partial_bars ? '部分 K 线含未结束或缺失的分钟；不补造数据。' : ''}获取中每 2 秒检查，完成后 30 秒检查；行情源更新可能延迟。{intraday ? '分时横轴固定全天，午间休市折叠。' : 'Ctrl + 滚轮缩放。'}</p></div>}
    </>}
  </section>;
}
