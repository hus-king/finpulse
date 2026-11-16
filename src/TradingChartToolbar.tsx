import type { ReactNode, RefObject } from 'react';
import { ChevronRight, Maximize2, Minimize2, Minus, Plus, RotateCcw } from 'lucide-react';

type Props = {
  title: string;
  subtitle?: string;
  expanded: boolean;
  fullScreenButton: RefObject<HTMLButtonElement | null>;
  onZoom: (factor: number) => void;
  onReset: () => void;
  onLatest: () => void;
  onFullScreen: () => void;
  children?: ReactNode;
};

export default function TradingChartToolbar({
  title, subtitle, expanded, fullScreenButton, onZoom, onReset, onLatest, onFullScreen, children,
}: Props) {
  return <div className="chart-navigation" role="group" aria-label="图表交互工具">
    <span className="chart-navigation-title">{title}{subtitle && <small>{subtitle}</small>}</span>
    <button type="button" onClick={() => onZoom(.75)} aria-label="放大图表" title="放大"><Plus size={15} /></button>
    <button type="button" onClick={() => onZoom(1.33)} aria-label="缩小图表" title="缩小"><Minus size={15} /></button>
    <button type="button" onClick={onReset} aria-label="复位图表" title="恢复默认范围及自动刻度"><RotateCcw size={14} /></button>
    <button type="button" onClick={onLatest} aria-label="回到最新" title="回到最新"><ChevronRight size={16} /></button>
    {children}
    <button type="button" ref={fullScreenButton} onClick={onFullScreen}
      aria-label={expanded ? '退出全屏图表' : '全屏图表'} title={expanded ? '退出全屏 · Esc' : '全屏图表'}>
      {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
    </button>
  </div>;
}
