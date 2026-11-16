import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { BarChart3, ExternalLink } from 'lucide-react';
import { ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, ISeriesMarkersPluginApi, MouseEventParams, Time, UTCTimestamp } from 'lightweight-charts';
import { api } from './api';
import TradingChartToolbar from './TradingChartToolbar';
import useEtfSnapshot from './useEtfSnapshot';
import { number, tone } from './format';
import { useTheme } from './ThemeContext';
import { disclosureEvents, holdingPeriods } from './etfHoldingsMath';
import type { Close, DateBasis, Fund, Period } from './etfHoldingsMath';
import { chartTimestamp } from './tradingChartData';
import './etf-funds.css';
import './technical-chart.css';

type Event = ReturnType<typeof disclosureEvents>[number];

const groups = [
  { id: 'all', label: '已披露合计' },
  { id: 'huijin', label: '汇金' },
  { id: 'social-security', label: '社保' },
  { id: 'csf', label: '证金' },
] as const;
const units = (shares: number) => number(shares / 1e8);
const changeLabel = (shares: number) => `${shares > 0 ? '+' : ''}${units(shares)} 亿份`;
const positionLabel = (position: number) => position <= 20 ? '低位' : position >= 80 ? '高位' : '中间';
const SOURCE_RULE_URL = 'https://www.amac.org.cn/xwfb/xhyw/202603/t20260313_27472.html';

function reportStatus(period: Period): string {
  if (period.status === 'missing') return '暂缺报告原文';
  if (period.status === 'anonymous') return '未披露具名持有人';
  return '名单未列该机构';
}

async function indexArchive(signal: AbortSignal): Promise<Close[]> {
  const response = await fetch('/market-archives/sse-composite-close.csv', { signal });
  if (!response.ok) throw new Error('上证指数历史暂不可用');
  return (await response.text()).trim().split('\n').slice(1).map(line => {
    const [date, close] = line.split(',');
    return { date, close: Number(close) };
  }).filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && Number.isFinite(row.close) && row.close > 0);
}

async function loadHoldingsArchive(_key: string, signal: AbortSignal) {
  const [funds, index] = await Promise.all([
    api<Fund[]>('/market-archives/etf-holder-disclosures.json', undefined, signal),
    indexArchive(signal),
  ]);
  if (!funds.length || !index.length) throw new Error('持仓或行情档案为空');
  return { funds, index };
}

async function loadIndexCloses(key: string, signal: AbortSignal): Promise<Close[]> {
  const { candles } = await api<{ candles: Close[] }>(key, undefined, signal);
  return candles.filter(point => Number.isFinite(point.close) && point.close > 0).map(({ date, close }) => ({ date, close }));
}

function HoldingsChart({ index, events, fund, group, basis }: {
  index: Close[]; events: Event[]; fund: Fund; group: (typeof groups)[number]['id']; basis: DateBasis;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const indexSeries = useRef<ISeriesApi<'Line'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const fullScreenButton = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [hoveredTime, setHoveredTime] = useState<number | null>(null);
  const [selectedTime, setSelectedTime] = useState<number | null>(null);
  const { theme } = useTheme();
  const indexByTime = useMemo(() => new Map(index.map(point => [chartTimestamp(point.date), point])), [index]);
  const eventTimes = useMemo(() => events.map(event => ({ ...event, time: chartTimestamp(event.date) as UTCTimestamp }))
    .sort((a, b) => a.time - b.time), [events]);

  useEffect(() => {
    if (!canvas.current) return;
    const instance = createChart(canvas.current, {
      autoSize: true,
      layout: { attributionLogo: false, fontFamily: 'Inter, Segoe UI, Microsoft YaHei, sans-serif', fontSize: 11 },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      kineticScroll: { mouse: true, touch: true },
      timeScale: { minBarSpacing: .02, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      localization: { locale: 'zh-CN', timeFormatter: (time: Time) => new Date(Number(time) * 1000).toISOString().slice(0, 10) },
    });
    const line = instance.addSeries(LineSeries, {
      lineWidth: 2, priceLineVisible: false, priceFormat: { type: 'custom', minMove: .01, formatter: number },
    });
    chart.current = instance;
    indexSeries.current = line;
    markers.current = createSeriesMarkers(line, [], { zOrder: 'top' });
    return () => { instance.remove(); chart.current = null; indexSeries.current = null; markers.current = null; };
  }, []);

  useEffect(() => {
    const instance = chart.current;
    const line = indexSeries.current;
    if (!instance || !line) return;
    const scale = instance.timeScale();
    const hasData = line.data().length > 0;
    line.setData([...indexByTime].map(([time, point]) => ({ time: time as UTCTimestamp, value: point.close })));
    if (!hasData) scale.fitContent();
  }, [indexByTime]);

  useEffect(() => {
    const instance = chart.current;
    const line = indexSeries.current;
    if (!instance || !line) return;
    const styles = getComputedStyle(document.documentElement);
    const color = (name: string) => styles.getPropertyValue(name).trim();
    const border = color('--chart-border');
    const grid = color('--chart-grid');
    const crosshair = color('--chart-crosshair');
    const guide = { color: crosshair, style: LineStyle.Dashed, labelBackgroundColor: crosshair };
    instance.applyOptions({
      layout: { background: { type: ColorType.Solid, color: color('--theme-panel') }, textColor: color('--chart-text') },
      grid: { vertLines: { color: grid, style: LineStyle.Dotted }, horzLines: { color: grid, style: LineStyle.Dotted } },
      crosshair: { vertLine: guide, horzLine: guide },
      rightPriceScale: { borderColor: border, minimumWidth: 62, scaleMargins: { top: .1, bottom: .1 } },
      timeScale: { borderColor: border },
    });
    line.applyOptions({ color: color('--chart-marker-neutral') });
    const up = color('--chart-up');
    const down = color('--chart-down');
    markers.current?.setMarkers(eventTimes.map(({ time, change }) => ({
      time, id: String(time), position: 'inBar', shape: change > 0 ? 'arrowUp' : 'arrowDown', color: change > 0 ? up : down, size: 1.25,
    })));
  }, [eventTimes, theme]);

  useEffect(() => {
    setHoveredTime(null);
    setSelectedTime(null);
    chart.current?.clearCrosshairPosition();
  }, [basis, group]);

  useEffect(() => {
    const instance = chart.current;
    const line = indexSeries.current;
    if (!instance || !line) return;
    const scale = instance.timeScale();
    const move = ({ point, time, hoveredInfo }: MouseEventParams) => {
      const marker = point && hoveredInfo?.objectKind === 'series-marker'
        ? eventTimes.find(event => String(event.time) === hoveredInfo.objectId)
        : undefined;
      if (marker) instance.setCrosshairPosition(marker.close, marker.time, line);
      setHoveredTime(marker?.time ?? (point && typeof time === 'number' ? time : null));
    };
    const click = ({ point }: MouseEventParams) => {
      if (!point) return;
      // On narrow screens several trading days share one pixel. Test the
      // visible marker instead of the trading day under the tap.
      let bestDistance = 12;
      let selected: (typeof eventTimes)[number] | null = null;
      for (const event of eventTimes) {
        const x = scale.timeToCoordinate(event.time);
        const y = line.priceToCoordinate(event.close);
        if (x == null || x < 0 || x > scale.width() || y == null) continue;
        const distance = Math.hypot(point.x - x, point.y - y);
        if (distance >= bestDistance) continue;
        selected = event;
        bestDistance = distance;
      }
      setSelectedTime(selected?.time ?? null);
      setHoveredTime(null);
      if (selected) instance.setCrosshairPosition(selected.close, selected.time, line);
    };
    instance.subscribeCrosshairMove(move);
    instance.subscribeClick(click);
    instance.subscribeDblClick(click);
    return () => {
      instance.unsubscribeCrosshairMove(move);
      instance.unsubscribeClick(click);
      instance.unsubscribeDblClick(click);
    };
  }, [eventTimes]);

  useEffect(() => {
    if (!expanded) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setExpanded(false); fullScreenButton.current?.focus(); }
    };
    document.addEventListener('keydown', escape);
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', escape); };
  }, [expanded]);

  function zoom(factor: number) {
    const scale = chart.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const center = (range.from + range.to) / 2;
    const half = Math.max(2, (range.to - range.from) * factor / 2);
    scale.setVisibleLogicalRange({ from: center - half, to: center + half });
  }

  function reset() {
    chart.current?.timeScale().fitContent();
    indexSeries.current?.priceScale().setAutoScale(true);
    chart.current?.clearCrosshairPosition();
    setHoveredTime(null);
    setSelectedTime(null);
  }

  function latest() {
    chart.current?.timeScale().scrollToRealTime();
    chart.current?.clearCrosshairPosition();
    setHoveredTime(null);
    setSelectedTime(null);
  }

  const activeTime = hoveredTime ?? selectedTime;
  const active = (activeTime != null ? indexByTime.get(activeTime) : undefined) ?? index.at(-1)!;
  const activeEvents = eventTimes.filter(event => event.time === activeTime);
  return <div className={`technical-chart etf-disclosure-chart${expanded ? ' is-expanded' : ''}`}>
    <TradingChartToolbar title="持仓变动与上证指数" expanded={expanded} fullScreenButton={fullScreenButton}
      onZoom={zoom} onReset={reset} onLatest={latest} onFullScreen={() => setExpanded(value => !value)} />
    <div className="etf-flow-chart-readout etf-holdings-readout">
      <ul className="etf-overlay-legend" aria-label="图例">
        <li className="etf-overlay-index">上证指数 <b className="mono">{number(active.close)} 点</b></li>
        <li className="etf-disclosure-marker is-increase">份额增加</li>
        <li className="etf-disclosure-marker is-decrease">份额减少</li>
      </ul>
      <time dateTime={active.date}>{active.date}{activeTime == null && ' · 最新'}</time>
    </div>
    <div className="technical-chart-stage etf-holdings-chart">
      <div ref={canvas} className="technical-chart-canvas" role="img"
        aria-label={`${fund.name}具名份额变化与上证指数 · ${basis === 'published' ? '披露后' : '报告期末'}`} />
      {activeEvents.length > 0 && <div className="etf-disclosure-chart-events">
        {activeEvents.map(({ period, change, position }) => <div key={period.report_date}>
          <strong className={tone(change)}>披露份额变化 {changeLabel(change)}</strong>
          {position != null && <span>近一年区间位置 {position.toFixed(1)}% · {positionLabel(position)}</span>}
          <span>报告期末 {period.report_date}{period.publication_date && ` · 披露日 ${period.publication_date}`}</span>
        </div>)}
      </div>}
    </div>
    <div className="chart-interaction-hint">
      <span>滚轮缩放 · 拖动平移 · 悬停或点按标记查看详情</span>
      <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer" title="TradingView Lightweight Charts™">TradingView</a>
    </div>
  </div>;
}

function PositionSummary({ events }: { events: Event[] }) {
  const measured = events.flatMap(event => event.position == null ? [] : [{ change: event.change, position: event.position }]);
  const increases = measured.filter(event => event.change > 0);
  const decreases = measured.filter(event => event.change < 0);
  const count = (subset: typeof measured, position: string) => subset.filter(event => positionLabel(event.position) === position).length;
  const latest = events.at(-1);
  return <dl className="etf-disclosure-summary" aria-label="历史指数位置分布">
    <div><dt>份额增加时在低位</dt><dd className="mono up">{increases.length ? `${count(increases, '低位')} / ${increases.length}` : '—'}</dd>
      {increases.length > 0 && <small>中间 {count(increases, '中间')} 次 · 高位 {count(increases, '高位')} 次</small>}</div>
    <div><dt>份额减少时在高位</dt><dd className="mono down">{decreases.length ? `${count(decreases, '高位')} / ${decreases.length}` : '—'}</dd>
      {decreases.length > 0 && <small>低位 {count(decreases, '低位')} 次 · 中间 {count(decreases, '中间')} 次</small>}</div>
    <div><dt>最近一次可比变动</dt><dd className={`mono ${latest ? tone(latest.change) : ''}`}>{latest ? changeLabel(latest.change) : '—'}</dd>
      {latest && <small>{latest.date}{latest.position != null && ` · 区间 ${latest.position.toFixed(1)}%`}</small>}</div>
  </dl>;
}

export default function EtfHoldingsComparison() {
  const title = useId();
  const { data: archive, loading, error } = useEtfSnapshot('holdings-archive', loadHoldingsArchive);
  const { data: daily } = useEtfSnapshot('/api/market/sh000001/daily', loadIndexCloses);
  const [fundCode, setFundCode] = useState('510300');
  const [group, setGroup] = useState<(typeof groups)[number]['id']>('all');
  const [basis, setBasis] = useState<DateBasis>('published');
  const funds = archive?.funds ?? [];
  const fund = funds.find(item => item.code === fundCode) ?? funds[0];
  const first = fund?.reports.find(report => report.holders.length)?.report_date;
  const periods = useMemo(() => fund ? holdingPeriods(fund, group) : [], [fund, group]);
  const indexHistory = useMemo(() => {
    if (!archive) return [];
    const last = archive.index.at(-1)!.date;
    return [...archive.index, ...(daily ?? []).filter(point => point.date > last)];
  }, [archive, daily]);
  const indexWindow = useMemo(() => indexHistory.filter(point => first && point.date >= first), [first, indexHistory]);
  const events = useMemo(() => disclosureEvents(periods, indexHistory, basis), [periods, indexHistory, basis]);
  const latestReport = periods.at(-1);

  return (
    <section id="etf-holdings" className="panel etf-funds-card etf-holdings-card" aria-labelledby={title} aria-busy={loading}>
      <header className="etf-funds-heading">
        <h2 id={title}><BarChart3 size={17} aria-hidden="true" />国家队公开持仓与上证指数</h2>
        {first && indexWindow.length > 0 && <span className="etf-funds-status">{first} 至 {indexWindow.at(-1)?.date}</span>}
      </header>
      {error && archive && <p className="etf-funds-notice" role="status">更新失败，显示上次加载的数据。</p>}
      {fund && first && indexWindow.length ? (
        <>
          <div className="etf-holdings-controls">
            <div className="etf-funds-groups" role="group" aria-label="查看持仓的 ETF">
              {funds.map(item => <button key={item.code} type="button" aria-pressed={item.code === fund.code}
                onClick={() => setFundCode(item.code)}>{item.name} <small>{item.code}</small></button>)}
            </div>
            <div className="etf-funds-groups" role="group" aria-label="查看持仓的机构">
              {groups.map(item => <button key={item.id} type="button" aria-pressed={item.id === group}
                onClick={() => setGroup(item.id)}>{item.label}</button>)}
            </div>
          </div>
          <PositionSummary events={events} />
          <div className="etf-funds-groups" role="group" aria-label="对齐时间">
            <button type="button" aria-pressed={basis === 'published'} onClick={() => setBasis('published')}>披露后</button>
            <button type="button" aria-pressed={basis === 'period-end'} onClick={() => setBasis('period-end')}>报告期末</button>
          </div>
          <HoldingsChart key={fund.code} index={indexWindow} events={events} fund={fund} group={group} basis={basis} />
          {events.length === 0 && <p className="etf-funds-note">当前筛选暂无可比的具名份额变动。</p>}
          <p className="etf-funds-note">
            {basis === 'published' ? '标记对齐披露后的首个交易日。' : '标记对齐报告期末或此前最近交易日，当时的持仓数据尚未公开。'}
            指数位置按近 252 个交易日收盘的最低至最高区间计算，≤20% 为低位，≥80% 为高位。
          </p>
          {latestReport && latestReport.shares == null && <p className="etf-funds-note">
            最新报告期末 {latestReport.report_date} · {reportStatus(latestReport)}。
          </p>}
          {!!fund.conversions?.length && (
            <div className="etf-holdings-conversions etf-funds-note">
              <span>份额折算</span>
              {fund.conversions.map(({ date, ratio, source_url }) => (
                <a key={date} href={source_url} target="_blank" rel="noreferrer">{date} {ratio}</a>
              ))}
            </div>
          )}
          <details className="etf-funds-details">
            <summary>持仓明细与披露原文</summary>
            <div className="etf-funds-table etf-holdings-table" tabIndex={0} role="region" aria-label="公开持仓明细">
              <table>
                <thead><tr>
                  <th scope="col">报告期末</th><th scope="col">披露日</th>
                  <th scope="col">占比 / 份额</th><th scope="col">份额变化</th><th scope="col">持有人及来源</th>
                </tr></thead>
                <tbody>{periods.slice().reverse().map(period => (
                  <tr key={period.report_date}>
                    <th scope="row">{period.report_date}</th><td>{period.publication_date ?? '—'}</td>
                    <td title={period.shares == null ? undefined : `${period.shares.toLocaleString('zh-CN')} 份`}>
                      {period.ownershipPercent == null ? '—' : `${number(period.ownershipPercent)}%`}
                      {period.shares != null && <small>{units(period.shares)} 亿份</small>}
                    </td>
                    <td>
                      {period.change == null ? '—' : <span className={period.change ? tone(period.change) : undefined}>{changeLabel(period.change)}</span>}
                      {period.changeNote && <small>{period.changeNote}</small>}
                    </td>
                    <td>
                      {period.matches.length
                        ? period.matches.map(holder => <small key={holder.name}>{holder.name} · {units(holder.shares)} 亿份</small>)
                        : <small>{reportStatus(period)}</small>}
                      {period.source_url && <a href={period.source_url} target="_blank" rel="noreferrer">
                        报告原文 <ExternalLink size={11} aria-hidden="true" />
                      </a>}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </details>
          <p className="etf-funds-note">
            份额变化只比较相邻报告中名单相同的具名账户，拆合份额按同一口径折算。持仓占比也会随基金总份额变化。
            <a href={SOURCE_RULE_URL} target="_blank" rel="noreferrer">2026 年新版披露模板</a>
          </p>
        </>
      ) : <p className="etf-funds-empty" role="status">{error ? '持仓或行情档案暂不可用，请稍后刷新。' : '正在读取持仓和指数历史…'}</p>}
    </section>
  );
}
