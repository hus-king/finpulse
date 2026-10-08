import { useEffect, useId, useRef, useState } from 'react';
import { ChevronRight, Eye, EyeOff, RefreshCw, SlidersHorizontal, X } from 'lucide-react';
import type { ChartOverlay, ChartStyle } from './chartMath';

export const chartStyles: { value: ChartStyle; label: string }[] = [
  { value: 'candles', label: '标准 K 线' }, { value: 'hollow', label: '空心 K 线' },
  { value: 'heikin', label: '平滑 K 线' }, { value: 'line', label: '收盘折线' },
  { value: 'area', label: '面积图' },
];
export const chartOverlays: { value: ChartOverlay; label: string }[] = [
  { value: 'none', label: '不叠加' }, { value: 'MA', label: 'MA 均线' },
  { value: 'EMA', label: 'EMA 均线' }, { value: 'BOLL', label: '布林带' },
];

function Choices<T extends string>({ label, options, value, onChange }: {
  label: string; options: { value: T; label: string }[]; value: T; onChange: (value: T) => void;
}) {
  return <fieldset className="chart-control-group"><legend>{label}</legend>
    <div className="chart-control-options">{options.map(option => <button type="button" key={option.value}
      aria-pressed={value === option.value} onClick={() => onChange(option.value)}>{option.label}</button>)}</div>
  </fieldset>;
}

export default function ChartControls({ visible, toggleVisible, chartStyle, setChartStyle, overlay, setOverlay,
  period, setPeriod, mode, modes, setMode, indicator, setIndicator, busy, refresh, add }: {
  visible: boolean; toggleVisible: () => void; chartStyle: ChartStyle; setChartStyle: (value: ChartStyle) => void;
  overlay: ChartOverlay; setOverlay: (value: ChartOverlay) => void; period: string; setPeriod: (value: string) => void;
  mode: string; modes: string[]; setMode: (value: string) => void; indicator: string; setIndicator: (value: string) => void;
  busy: boolean; refresh: () => void; add: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState({ above: false, height: 650 });
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const firstAction = useRef<HTMLButtonElement>(null);
  const id = useId();
  const close = (restoreFocus = false) => { setOpen(false); if (restoreFocus) trigger.current?.focus(); };

  useEffect(() => {
    if (!open) return;
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const below = window.innerHeight - rect.bottom - 20;
      const above = rect.top - 20;
      const openAbove = below < 320 && above > below;
      setPlacement({ above: openAbove, height: Math.max(120, Math.min(650, openAbove ? above : below)) });
    };
    position();
    firstAction.current?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !container.current?.contains(event.target)) setOpen(false);
    };
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', keyboard);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', keyboard);
      window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true);
    };
  }, [open]);

  return <div className="chart-controls" ref={container} onBlur={event => {
    if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button type="button" ref={trigger} className={`chart-controls-trigger ${visible ? '' : 'is-collapsed'}`}
      aria-label="图表工具" title={visible ? '图表工具' : '图表工具 · 图表已隐藏'}
      aria-expanded={open} aria-haspopup="dialog" aria-controls={open ? id : undefined} onClick={() => setOpen(value => !value)}>
      <SlidersHorizontal size={14} aria-hidden="true" />
    </button>
    {open && <div className="chart-controls-popover" id={id} role="dialog" aria-label="图表工具设置" style={{
      maxHeight: placement.height, ...(placement.above ? { top: 'auto', bottom: 'calc(100% + 8px)' } : {}),
    }}>
      <div className="chart-controls-heading"><strong>图表工具</strong><button type="button" className="chart-controls-close"
        aria-label="关闭图表工具" onClick={() => close(true)}><X size={14} aria-hidden="true" /></button></div>
      <button type="button" ref={firstAction} className="chart-visibility-action" onClick={() => { toggleVisible(); close(true); }}>
        {visible ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
        <span>{visible ? '隐藏图表' : '显示图表'}<small>{visible ? '只保留股价信息卡片' : '展开走势与技术指标'}</small></span>
      </button>
      <Choices label="图表样式" options={chartStyles} value={chartStyle} onChange={setChartStyle} />
      <Choices label="行情周期" options={['日 K', '周 K', '1 分钟', '5 分钟', '15 分钟', '30 分钟', '60 分钟'].map(value => ({ value, label: value }))} value={period} onChange={setPeriod} />
      <Choices label="显示范围" options={modes.map(value => ({ value, label: value }))} value={mode} onChange={setMode} />
      <Choices label="主图指标" options={chartOverlays} value={overlay} onChange={setOverlay} />
      <Choices label="副图指标" options={['成交量', 'MACD', 'RSI'].map(value => ({ value, label: value }))} value={indicator} onChange={setIndicator} />
      {chartStyle === 'heikin' && <div className="chart-controls-note">平滑 K 线由原始行情计算，适合观察趋势；卡片价格仍为真实行情。</div>}
      <button type="button" className="chart-menu-action" disabled={busy} onClick={refresh}><RefreshCw size={14} className={busy ? 'spin' : ''} aria-hidden="true" />{busy ? '行情获取中' : '刷新行情'}</button>
      <button type="button" className="chart-menu-action" onClick={() => { close(); add(); }}><ChevronRight size={14} aria-hidden="true" />切换标的</button>
    </div>}
  </div>;
}
