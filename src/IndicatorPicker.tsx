import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { IndicatorId } from './technicalIndicators';
import { displayedIndicators, indicatorCatalog, normalizeIndicatorSelection, selectIndicator } from './indicatorCatalog';
import type { IndicatorCatalogEntry, IndicatorSelection } from './indicatorCatalog';
import './indicator-picker.css';

export { displayedIndicators, normalizeIndicatorSelection, selectIndicator } from './indicatorCatalog';
export type { IndicatorSelection } from './indicatorCatalog';

export default function IndicatorPicker({ selection, onChange }: {
  selection: IndicatorSelection;
  onChange: (value: IndicatorSelection) => void;
}) {
  const current = normalizeIndicatorSelection(selection);
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<IndicatorCatalogEntry | null>(null);
  const [notice, setNotice] = useState('');
  const [placement, setPlacement] = useState({ left: 12, top: 12, width: 360, maxHeight: 520 });
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const dialogId = useId();
  const active = indicatorCatalog.find(entry => entry.id === current.active)!;
  const shown = displayedIndicators(current);

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  useLayoutEffect(() => {
    if (!open) return;
    function position() {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const margin = 12;
      const width = Math.min(360, window.innerWidth - margin * 2);
      const below = Math.max(0, window.innerHeight - rect.bottom - margin - 6);
      const above = Math.max(0, rect.top - margin - 6);
      const useBelow = below >= Math.min(420, above);
      const maxHeight = Math.min(520, useBelow ? below : above);
      const height = Math.min(panel.current?.scrollHeight ?? 420, maxHeight);
      setPlacement({
        left: Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin)),
        top: useBelow ? rect.bottom + 6 : Math.max(margin, rect.top - height - 6),
        width,
        maxHeight,
      });
    }
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    const observer = new ResizeObserver(position);
    if (panel.current) observer.observe(panel.current);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
      observer.disconnect();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLButtonElement>('.indicator-grid button[aria-pressed="true"]')?.focus();
    function outside(event: PointerEvent) {
      const node = event.target as Node;
      if (!panel.current?.contains(node) && !trigger.current?.contains(node)) close();
    }
    function keyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      }
    }
    function focusOutside(event: FocusEvent) {
      const node = event.target as Node;
      if (!panel.current?.contains(node) && !trigger.current?.contains(node)) setOpen(false);
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', keyboard);
    document.addEventListener('focusin', focusOutside);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', keyboard);
      document.removeEventListener('focusin', focusOutside);
    };
  }, [open]);

  function pick(entry: IndicatorCatalogEntry) {
    setInfo(entry);
    setNotice('');
    if (!entry.available) return;
    if (current.mode === 'multiple' && current.selected.length === 4 && !current.selected.includes(entry.id as IndicatorId)) {
      setNotice('最多同时显示 4 项，请先取消一项。');
      return;
    }
    if (current.mode === 'multiple' && current.selected.length === 1 && current.selected[0] === entry.id) {
      setNotice('至少保留一项指标。');
      return;
    }
    onChange(selectIndicator(current, entry.id as IndicatorId));
    if (current.mode === 'single') close();
  }

  return <div className="indicator-picker">
    <button
      ref={trigger}
      type="button"
      className="indicator-picker-trigger"
      aria-label="选择技术指标"
      aria-expanded={open}
      aria-haspopup="dialog"
      aria-controls={open ? dialogId : undefined}
      onClick={() => {
        setInfo(null);
        setNotice('');
        if (open) close();
        else setOpen(true);
      }}
    >
      {active.label}{current.mode === 'multiple' && shown.length > 1 ? ` 等 ${shown.length} 项` : ''}
      <span aria-hidden="true">▾</span>
    </button>
    {open && createPortal(<div
      ref={panel}
      id={dialogId}
      role="dialog"
      aria-label="技术指标"
      className="indicator-picker-panel"
      style={placement}
    >
      <div className="indicator-picker-heading">
        <strong>技术指标</strong>
        <button type="button" className="indicator-picker-close" aria-label="关闭指标选择" onClick={close}>×</button>
      </div>
      <div className="indicator-picker-mode" role="group" aria-label="指标显示方式">
        <button type="button" aria-pressed={current.mode === 'single'} onClick={() => {
          setNotice('');
          onChange({ ...current, mode: 'single' });
        }}>单项</button>
        <button type="button" aria-pressed={current.mode === 'multiple'} onClick={() => {
          setNotice('');
          onChange(normalizeIndicatorSelection({ ...current, mode: 'multiple' }));
        }}>同时显示</button>
        <small>{current.mode === 'multiple' ? `已选 ${current.selected.length}/4` : '选择一项显示在主图下方'}</small>
      </div>
      <div className="indicator-grid" role="group" aria-label="可用技术指标">
        {indicatorCatalog.filter(entry => entry.available).map(entry => <button
          key={entry.id}
          type="button"
          title={entry.description}
          aria-pressed={shown.includes(entry.id as IndicatorId)}
          onMouseEnter={() => { setInfo(entry); setNotice(''); }}
          onFocus={() => { setInfo(entry); setNotice(''); }}
          onClick={() => pick(entry)}
        >{entry.label}</button>)}
      </div>
      <details className="indicator-data-details">
        <summary>更多数据指标<span>当前数据不支持</span></summary>
        <p>以下项目缺少所需历史字段、逐笔数据或公开算法。点击查看原因。</p>
        <div className="indicator-grid indicator-data-grid" role="group" aria-label="暂不可用的数据指标">
          {indicatorCatalog.filter(entry => !entry.available).map(entry => <button
            key={entry.id}
            type="button"
            data-unavailable="true"
            title={entry.reason}
            onMouseEnter={() => { setInfo(entry); setNotice(''); }}
            onFocus={() => { setInfo(entry); setNotice(''); }}
            onClick={() => pick(entry)}
          >{entry.label}</button>)}
        </div>
      </details>
      <p className="indicator-picker-description" aria-live="polite">{notice || info?.reason || info?.description || active.description}</p>
    </div>, document.body)}
  </div>;
}
