import { displayCode, isIndex } from './instruments';
import { useEffect, useState } from 'react';
import { Activity, LoaderCircle } from 'lucide-react';
import PriceChart from './PriceChart';
import IntradayChart from './IntradayChart';
import { buildIntraday, initialChartPeriod, selectSessionDay, sessionPreviousClose } from './intradayMath';
import ChartControls, { chartAdjustments, chartStyles } from './ChartControls';
import type { ChartOverlay, ChartStyle } from './chartMath';
import useMarketBundle from './useMarketBundle';
import useAdjustedHistory from './useAdjustedHistory';
import { dateTime, number, percent, tone } from './format';
import type { Dashboard, MarketAdjustment } from './types';
import { normalizeIndicatorSelection, displayedIndicators, indicatorCatalog } from './indicatorCatalog';
import type { IndicatorSelection } from './indicatorCatalog';

const preferenceKey = 'finpulse-chart-preferences';
interface ChartPreferences {
  visible: boolean; chartStyle: ChartStyle; overlay: ChartOverlay;
  period: string; mode: string; indicators: IndicatorSelection;
  adjustment: MarketAdjustment;
}
function readPreferences(): ChartPreferences {
  const defaults: ChartPreferences = { visible: true, chartStyle: 'candles', overlay: 'MA', period: initialChartPeriod(), mode: '当日', indicators: normalizeIndicatorSelection(null), adjustment: 'none' };
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey) ?? 'null');
    if (!saved || typeof saved !== 'object') return defaults;
    const period=initialChartPeriod(saved);
    return {
      visible: typeof saved.visible === 'boolean' ? saved.visible : defaults.visible,
      chartStyle: chartStyles.some(item => item.value === saved.chartStyle) ? saved.chartStyle : defaults.chartStyle,
      overlay: ['none', 'MA', 'EMA', 'BOLL'].includes(saved.overlay) ? saved.overlay : defaults.overlay,
      period, mode: '当日',
      adjustment: chartAdjustments.some(item => item.value === saved.adjustment) ? saved.adjustment : 'none',
      indicators: normalizeIndicatorSelection(saved.indicators ?? saved.indicator),
    };
  } catch { return defaults; }
}

export default function QuotePanel({ data, add, running, onMarket }: {
  data: Dashboard; add: () => void; running: boolean;
  onMarket: (data: Dashboard) => void;
}) {
  const [preferences, setPreferences] = useState(readPreferences);
  const { visible, chartStyle, overlay, mode, period, indicators } = preferences;
  const updatePreferences = (next: Partial<ChartPreferences>) => setPreferences(previous => ({ ...previous, ...next }));
  useEffect(() => {
    try { localStorage.setItem(preferenceKey, JSON.stringify(preferences)); } catch { /* Controls work without browser storage. */ }
  }, [preferences]);
  const indexView = isIndex(data.stock);
  const volumeUnit = indexView ? '源单位' : '股';
  const intraday=period==='分时';
  const minutePeriod = intraday ? 1 : period.endsWith('分钟') ? parseInt(period) : null;
  // The request depends only on the stock, never on the selected chart period.
  const market = useMarketBundle(data.stock.code, onMarket);
  const minute = { data: minutePeriod ? market.data?.minutes[String(minutePeriod)] : null,
    loading: market.loading, error: market.error, refresh: market.refresh };
  const dailyBusy = market.loading || market.data?.dashboard.daily_request?.refreshing;
  const dailyError = market.error || market.data?.errors.daily;
  const dailyChart = period === '日 K' || period === '周 K';
  const adjustment = dailyChart && !indexView ? preferences.adjustment : 'none';
  const dailyBar = data.candles.at(-1);
  const rawFingerprint = JSON.stringify([data.candles[0]?.date, data.candles.length,
    dailyBar?.date, dailyBar?.open, dailyBar?.high, dailyBar?.low, dailyBar?.close, dailyBar?.volume]);
  const adjusted = useAdjustedHistory(data.stock.code, adjustment, period, rawFingerprint, visible && dailyChart && !indexView);
  const adjustedChart = adjustment !== 'none';
  const adjustmentLabel = chartAdjustments.find(item => item.value === adjustment)!.label;
  const adjustedError = adjusted.error || (adjusted.data?.status === 'unavailable' ? adjusted.data.message || '复权数据暂不可用。' : '');
  const refreshMarket = () => { market.refresh(); adjusted.refresh(); };
  const session=intraday && minute.data ? buildIntraday(minute.data.candles,minute.data.market,sessionPreviousClose(selectSessionDay(minute.data.market,minute.data.candles),data.candles,minute.data.candles)) : null;
  const candles = minutePeriod ? minute.data?.candles ?? [] : adjustedChart ? adjusted.data?.status === 'ready' ? adjusted.data.candles : [] : data.candles;
  const quote = intraday ? session?.quote : minutePeriod ? minute.data?.quote : null;
  const price = (minutePeriod ? quote?.price : null) ?? data.stock.price;
  const change = (minutePeriod ? quote?.change : null) ?? data.stock.change;
  const busy = minute.loading || minute.data?.refreshing;
  const lastDate = intraday ? session?.day : minute.data?.as_of?.slice(0, 10);
  const dailyBase = lastDate ? [...data.candles].reverse().find(bar => bar.date < lastDate) : null;
  const priceChange = !intraday && minutePeriod && change == null && price != null && dailyBase ? (price / dailyBase.close - 1) * 100 : change;
  const lastTime = intraday ? session?.as_of : minutePeriod ? minute.data?.as_of : data.quote.as_of_date;
  const modes = minutePeriod ? ['当日', '近 5 日', '全部'] : ['近 1 月', '近 3 月', '近 1 年', '全部'];

  return <section className={`panel live-quote ${visible ? '' : 'chart-collapsed'}`}>
    <header><div><span className="industry-tag">{data.stock.industry}</span><h2>{data.stock.name}<small>{data.stock.exchange}:{displayCode(data.stock)}</small></h2>
      <p>{minutePeriod ? minute.data?.source ?? '分钟行情' : data.quote.source ?? market.data?.dashboard.quote.source ?? '尚未采集行情'} · {lastTime ?? '—'} · {intraday ? `分时 · ${session?.day ?? '等待交易日确认'}` : minutePeriod ? '分钟快照 · 未复权' : data.quote.status === 'stale' ? '上次保存的日线，本次更新失败' : '历史日线收盘'}</p>
    </div><ChartControls visible={visible} toggleVisible={() => updatePreferences({ visible: !visible })}
      chartStyle={chartStyle} setChartStyle={chartStyle => updatePreferences({ chartStyle })}
      overlay={overlay} setOverlay={overlay => updatePreferences({ overlay })}
      period={period} setPeriod={period => updatePreferences({ period, mode: period === '分时' || period.endsWith('分钟') ? '当日' : '近 3 月' })}
      mode={mode} modes={modes} setMode={mode => updatePreferences({ mode })}
      adjustment={dailyChart && !indexView ? adjustment : undefined} setAdjustment={adjustment => updatePreferences({ adjustment })}
      busy={!!(minutePeriod ? busy : adjustedChart ? adjusted.loading : dailyBusy)} refresh={refreshMarket} add={add} /></header>
    <div className="live-price-row"><div><strong key={price} className={`mono quote-update ${tone(priceChange)}`}>{number(price)}{indexView && <small className="index-point-unit">点</small>}</strong><span className={tone(priceChange)}>{percent(priceChange)}<small>较前一返回交易日</small></span></div>
      <dl><div><dt>开盘</dt><dd>{number(minutePeriod ? quote?.open : dailyBar?.open)}</dd></div><div><dt>最高</dt><dd>{number(minutePeriod ? quote?.high : dailyBar?.high)}</dd></div><div><dt>最低</dt><dd>{number(minutePeriod ? quote?.low : dailyBar?.low)}</dd></div><div><dt>{indexView ? '源成交量' : minutePeriod ? '当日成交量' : '成交量'}</dt><dd>{minutePeriod ? quote?.volume != null ? `${(quote.volume / 10000).toFixed(1)} 万${volumeUnit}` : '—' : dailyBar ? `${(dailyBar.volume / 10000).toFixed(1)} 万${volumeUnit}` : '—'}</dd></div></dl>
    </div>
    {visible && <>
    <div className="chart-context"><span>{intraday ? `分时走势 · ${session?.day ?? '等待交易日确认'}` : `${chartStyles.find(item => item.value === chartStyle)?.label} · ${period} · ${mode}${dailyChart && !indexView ? ` · ${adjustmentLabel}` : ''}`}</span><span>{intraday ? '分钟价格' : overlay === 'MA' || overlay === 'EMA' ? `${overlay}5 / 10 / 20` : overlay === 'BOLL' ? 'BOLL 20 · 2σ' : '不叠加均线'}{` · ${displayedIndicators(indicators).map(id => indicatorCatalog.find(entry => entry.id === id)?.label ?? id).join(' / ')}`}</span></div>
    {minutePeriod && <div className={`minute-status ${minute.data?.market.is_trading ? 'trading' : ''}`} role="status">
      <span className="minute-market-state"><span className={`status-dot ${busy ? 'pending' : ''}`} />{minute.data?.market.label ?? '正在确认交易状态'}</span>
      <span>{busy ? '后台更新中 · 2 秒检查' : minute.data?.market.is_trading ? '30 秒轮询行情' : minute.data?.market.state === 'pre_open' && intraday ? '等待当日开盘' : '保留最近交易数据'}</span>
      <span>{minute.data?.status === 'stale' ? '数据延迟 / 更新失败' : minute.data?.forming ? '最新一根尚未完成' : minute.data?.is_realtime ? '分钟行情已更新' : '历史分钟快照'}</span>
    </div>}
    {!minutePeriod && (adjustedChart ? <div className="minute-status" role="status">
      <span className="minute-market-state"><span className={`status-dot ${adjusted.loading ? 'pending' : ''}`} />{adjusted.loading ? `正在准备${adjustmentLabel}` : adjusted.data?.status === 'ready' ? `${adjustmentLabel}日线已就绪` : `${adjustmentLabel}暂不可用`}</span>
      <span>{adjusted.loading ? '加载中 · 每 2 秒检查' : adjusted.data?.status === 'ready' ? `${adjusted.data.from_date ?? '—'} 至 ${adjusted.data.to_date ?? '—'} · 报价仍为真实价格` : '请重试或切换复权方式'}</span>
      <button type="button" onClick={adjusted.refresh} aria-label="重试复权数据" style={{ padding: 0, fontSize: 'inherit', color: 'var(--theme-accent-text, #16856f)', textDecoration: 'underline', whiteSpace: 'nowrap' }}>{adjusted.loading ? '重新检查' : '重试'}</button>
    </div> : <div className="minute-status" role="status"><span className="minute-market-state"><span className={`status-dot ${dailyBusy ? 'pending' : ''}`} />{dailyBusy ? candles.length ? '日线缓存已展示，后台更新中' : '正在获取日线' : dailyError ? '日线更新失败' : '日线行情已就绪'}</span><span>分时与日线分别就绪 · 切换周期直接绘图</span></div>)}
    {!minutePeriod && adjustedChart && adjustedError && <div className="minute-warning" role="alert">{adjustedError}</div>}
    {!minutePeriod && !adjustedChart && dailyError && <div className="minute-warning" role="alert">{dailyError}{candles.length ? '；保留已获取的日线。' : '；可点击刷新行情重试。'}</div>}
    {!minutePeriod && candles.length > 0 && <p className="minute-caption">可用日线：{candles[0].date} ～ {candles.at(-1)?.date} · {candles.length} 根 · 选择“全部”查看完整历史，或缩放后向左拖动。</p>}
    {!minutePeriod && data.quote.history_error && <div className="minute-warning" role="alert">{data.quote.history_error}</div>}
    {(minute.error || minute.data?.error) && minutePeriod && <div className="minute-warning" role="alert">{minute.error || minute.data?.error}{candles.length ? '；保留已获取的分钟数据。' : ''}</div>}
    {intraday && session ? <IntradayChart instrument={data.stock.name} key={data.stock.code} volumeUnit={volumeUnit} session={session} candles={candles} loading={!!busy} selection={indicators} onSelectionChange={indicators => updatePreferences({ indicators })} /> : candles.length && !intraday ? <PriceChart instrument={data.stock.name} volumeUnit={volumeUnit} key={`${data.stock.code}:${period}:${adjustment}`} candles={candles} mode={mode} period={period} selection={indicators} onSelectionChange={indicators => updatePreferences({ indicators })} chartStyle={chartStyle} overlay={overlay} /> : <div className="chart-empty">{(adjustedChart ? adjusted.loading : market.loading) ? <LoaderCircle size={32} className="spin" /> : <Activity size={32} />}<h3>{adjustedChart ? adjusted.loading ? `正在准备${adjustmentLabel}行情` : `${adjustmentLabel}数据暂不可用` : market.loading ? intraday ? '正在读取分时缓存' : '正在读取行情缓存' : '暂未取得该周期行情'}</h3><p>{adjustedChart ? adjusted.loading ? '正在加载，请稍候。' : '请重试或切换复权方式。' : market.loading ? '已保存的数据优先展示，最新行情在后台更新。' : '行情源未返回可用数据，可在图表工具中刷新行情。'}</p></div>}
    {adjustedChart && adjusted.data?.status === 'ready' && <details className="quote-data-note"><summary>复权数据说明</summary><div><p>{adjustmentLabel} · {adjusted.data.source}</p><p>价格锚点：{adjusted.data.anchor_date ?? '—'} · 复权因子截至：{adjusted.data.factor_through ?? '—'}</p><p>日K使用复权日线；周K先按复权日线聚合，所有图表指标使用相同口径。上方报价与开高低仍为真实行情。</p>{adjusted.data.message && <p>{adjusted.data.message}</p>}</div></details>}
    <footer className="chart-footer"><span>{intraday ? '分时走势 · 未来时段与缺失分钟留空' : chartStyle === 'heikin' ? '平滑 K 线为计算后的趋势价格' : minutePeriod ? '滚轮缩放 · 拖动平移' : indexView ? '指数历史点位 · 日线与周线' : '滚轮缩放 · 拖动平移 · 新闻在下方列表查看'}</span></footer>
    {!minutePeriod && market.data?.errors.minute && <div className="minute-warning" role="alert">分钟行情：{market.data.errors.minute}；日线可正常查看，分钟线可稍后刷新。</div>}
    {minutePeriod && <details className="quote-data-note"><summary>行情数据说明</summary><div><p>数据时间：{intraday ? session?.as_of ?? '—' : minute.data?.as_of ?? '—'}</p><p>获取时间：{minute.data?.fetched_at ? dateTime(minute.data.fetched_at) : '—'}</p><p>各分钟周期使用相同的历史范围，行情源可能延迟。{minute.data?.partial_bars ? '部分 K 线尚未结束或存在缺失分钟。' : ''}{intraday ? '分时横轴固定全天，缺失分钟与未来时段留空。' : '可滚轮缩放、拖动平移，双击坐标轴复位。'}</p></div></details>}
    </>}
  </section>;
}
