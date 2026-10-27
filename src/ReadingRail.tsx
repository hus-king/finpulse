import { useEffect, useRef, useState } from 'react';
import type { PointerEvent } from 'react';
import { ArrowUp } from 'lucide-react';

type Chapter = { selector: string; title: string };
type Glyph = { character: string; x: number; y: number; size: number };
type PlacedChapter = Chapter & { x: number; y: number; width: number; height: number; glyphs: Glyph[] };
type RailState = { progress: number; active: number; height: number; width: number; zoom: number; viewportTop: number; viewportSize: number; chapters: PlacedChapter[] };
const clamp = (value: number) => Math.max(0, Math.min(1, value));
const mix = (from: number, to: number, progress: number) => from + (to - from) * progress;
const ease = (value: number) => { const t = clamp(value); return t * t * (3 - 2 * t); };
const chaptersByView: Record<string, Chapter[]> = {
  dashboard: [
    { selector: '.finance-heading', title: '市场研究' },
    { selector: '.market-overview', title: '大盘概览' },
    { selector: '.live-quote', title: '行情走势' },
    { selector: '.live-news', title: '新闻研判' },
    { selector: '.returns-panel', title: '历史观察' },
  ],
  'hot-news': [
    { selector: '.finance-heading', title: '热点榜单' },
    { selector: '.hot-source-grid', title: '热点来源' },
    { selector: '.hot-board', title: '市场事件' },
  ],
  screener: [
    { selector: '.finance-heading', title: '估值筛选' },
    { selector: '.peg-intro', title: '筛选方法' },
    { selector: '.peg-table-wrap', title: '股票榜单' },
  ],
  paper: [
    { selector: '.finance-heading', title: '模拟交易' },
    { selector: '.paper-stats', title: '账户资产' },
    { selector: '.paper-holdings', title: '当前持仓' },
    { selector: '.paper-history', title: '交易记录' },
  ],
  briefing: [
    { selector: '.finance-heading', title: '自选早报' },
    { selector: '.digest-panel', title: '消息汇总' },
    { selector: '.subscription-panel', title: '订阅设置' },
  ],
  lab: [{ selector: '.finance-heading', title: '连接状态' }],
  admin: [{ selector: '.finance-heading', title: '管理中心' }],
};

// Project every word into an ordered rail, using its current animated bounds.
// The two passes are continuous in scroll position, including on reversal.
function arrange(chapters: (Chapter & { glyphs: Glyph[] })[], top: number, bottom: number): PlacedChapter[] {
  const gap = 12;
  let boxes = chapters.map(chapter => {
    const x = Math.min(...chapter.glyphs.map(glyph => glyph.x));
    const y = Math.min(...chapter.glyphs.map(glyph => glyph.y));
    return { ...chapter, x, y,
      width: Math.max(...chapter.glyphs.map(glyph => glyph.x + glyph.size)) - x,
      height: Math.max(...chapter.glyphs.map(glyph => glyph.y + glyph.size)) - y,
    };
  });
  const total = boxes.reduce((sum, box) => sum + box.height, 0);
  const scale = Math.min(1, Math.max(1, bottom - top - gap * (boxes.length - 1)) / Math.max(1, total));
  if (scale < 1) boxes = boxes.map(box => ({ ...box,
    width: box.width * scale, height: box.height * scale,
    glyphs: box.glyphs.map(glyph => ({ ...glyph,
      x: box.x + (glyph.x - box.x) * scale,
      y: box.y + (glyph.y - box.y) * scale,
      size: glyph.size * scale,
    })),
  }));
  const ys = boxes.map(box => Math.max(top, Math.min(bottom - box.height, box.y)));
  for (let i = 1; i < boxes.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + boxes[i - 1].height + gap);
  if (boxes.length) ys[boxes.length - 1] = Math.min(ys.at(-1)!, bottom - boxes.at(-1)!.height);
  for (let i = boxes.length - 2; i >= 0; i--) ys[i] = Math.min(ys[i], ys[i + 1] - gap - boxes[i].height);
  return boxes.map((box, i) => ({ ...box, y: ys[i],
    glyphs: box.glyphs.map(glyph => ({ ...glyph, y: glyph.y + ys[i] - box.y })),
  }));
}

export default function ReadingRail({ view }: { view: string }) {
  const [state, setState] = useState<RailState>({ progress: 0, active: 0, height: 0, width: 0, zoom: 0, viewportTop: 0, viewportSize: 100, chapters: [] });
  const [preview, setPreview] = useState<number | null>(null);
  const [pressed, setPressed] = useState<string | null>(null);
  const interaction = useRef({ zoom: 0, hovering: false, dragging: false });
  const scheduleRef = useRef(() => {});
  useEffect(() => {
    let frame = 0;
    const definitions = chaptersByView[view] ?? chaptersByView.dashboard;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const wide = window.matchMedia('(min-width: 1200px)');
    const measure = () => {
      frame = 0;
      if (!wide.matches) return;
      const height = window.innerHeight;
      const width = document.documentElement.clientWidth;
      const documentHeight = document.documentElement.scrollHeight;
      const range = documentHeight - height;
      const fraction = range > 0 ? clamp(window.scrollY / range) : 0;
      const target = interaction.current.hovering || interaction.current.dragging ? 1 : 0;
      const delta = target - interaction.current.zoom;
      const zoom = interaction.current.zoom = motion.matches || Math.abs(delta) < .002 ? target : interaction.current.zoom + delta * .18;
      const drift = 60 - fraction * 72;
      const present = definitions.flatMap(chapter => {
        const element = document.querySelector(chapter.selector);
        if (!element) return [];
        const rect = element.getBoundingClientRect();
        return [{ ...chapter, anchor: rect.top + drift, documentY: rect.top + window.scrollY }];
      });
      const phases = present.map((chapter, i) => ({ ...chapter,
        detach: i === 0 ? 1 : ease((height - 130 - chapter.anchor) / 200),
        flip: i === 0 ? 1 : ease((180 - chapter.anchor) / 200),
      }));
      const active = phases.reduce((current, chapter, index) => chapter.flip > .5 ? index : current, 0);
      const top = 68, bottom = height - 76, edge = width - 84;
      const compact = height < 650;
      let stackY = top;
      const raw = phases.map((chapter, index) => {
        const chars = [...chapter.title];
        const next = phases[index + 1]?.flip ?? 0;
        const stackSize = mix(compact ? 15 : 17, 11, next);
        const rideSize = compact ? 12 : 13;
        const ridePitch = compact ? 18 : 22;
        const queueSize = compact ? 10 : 11;
        const queuedBelow = phases.slice(index + 1).reduce((sum, item) => sum + (1 - item.detach) * 24, 0);
        const queueY = bottom - queueSize - queuedBelow;
        const targetY = stackY;
        stackY += chapter.flip * (stackSize + 14);
        const stagger = motion.matches || compact ? 0 : .055;
        const phase = (value: number, position: number) => ease((value - position * stagger) / (1 - (chars.length - 1) * stagger));
        const glyphs = chars.map((character, j) => {
          const detach = phase(chapter.detach, j), flip = phase(chapter.flip, j);
          const qx = edge - (chars.length - j) * (queueSize + 4);
          const rx = width - 34 - rideSize / 2;
          const ry = chapter.anchor + j * ridePitch;
          const sx = edge - (chars.length - j) * (stackSize + 3);
          const fromX = mix(qx, rx, detach);
          const fromY = mix(queueY, ry, detach);
          return { character,
            x: mix(fromX, sx, flip) - (motion.matches ? 0 : 16 * Math.sin(Math.PI * flip)),
            y: mix(fromY, targetY, flip),
            size: mix(mix(queueSize, rideSize, detach), stackSize, flip),
          };
        });
        const mapSize = compact ? 11 : 12;
        const mapY = top + clamp(chapter.documentY / documentHeight) * (bottom - top);
        return { selector: chapter.selector, title: chapter.title, glyphs: glyphs.map((glyph, j) => ({ ...glyph,
          x: mix(glyph.x, edge - (chars.length - j) * (mapSize + 4), zoom),
          y: mix(glyph.y, mapY, zoom),
          size: mix(glyph.size, mapSize, zoom),
        })) };
      });
      setState({ progress: fraction * 100, active, height, width, zoom,
        viewportTop: window.scrollY / documentHeight * 100, viewportSize: Math.min(100, height / documentHeight * 100),
        chapters: arrange(raw, top, bottom) });
      if (zoom !== target) schedule();
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    scheduleRef.current = schedule;
    const resize = new ResizeObserver(schedule);
    resize.observe(document.body);
    const mutations = new MutationObserver(schedule);
    const main = document.querySelector('.finance-main');
    if (main) mutations.observe(main, { childList: true, subtree: true });
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    motion.addEventListener('change', schedule);
    measure();
    return () => {
      cancelAnimationFrame(frame); resize.disconnect(); mutations.disconnect();
      window.removeEventListener('scroll', schedule); window.removeEventListener('resize', schedule);
      motion.removeEventListener('change', schedule);
      scheduleRef.current = () => {};
    };
  }, [view]);
  const behavior = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' as const : 'smooth' as const;
  const hover = (value: boolean) => { interaction.current.hovering = value; scheduleRef.current(); };
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const value = clamp((event.clientY - rect.top) / rect.height);
    setPreview(value * 100);
    return value * Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
  };
  const finishDrag = (event: PointerEvent<HTMLDivElement>) => {
    interaction.current.dragging = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
      setPreview(null); hover(false);
    }
    scheduleRef.current();
  };
  return <aside className="reading-rail" aria-label="页面阅读进度">
    <div className={`reading-scale ${state.zoom > .01 ? 'expanded' : ''}`} role="scrollbar" tabIndex={0} aria-controls="workspace-content" aria-orientation="vertical" aria-label="阅读进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(state.progress)}
      onPointerEnter={() => hover(true)} onPointerLeave={() => { if (!interaction.current.dragging) { setPreview(null); hover(false); } }}
      onFocus={() => hover(true)} onBlur={() => hover(false)}
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault();
        interaction.current.dragging = true; hover(true);
        event.currentTarget.setPointerCapture(event.pointerId);
        window.scrollTo({ top: point(event), behavior: behavior() });
      }}
      onPointerMove={event => {
        const top = point(event);
        if (interaction.current.dragging) window.scrollTo({ top, behavior: 'instant' });
      }} onPointerUp={finishDrag} onPointerCancel={finishDrag}
      onKeyDown={event => {
        const range = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        const targets: Record<string, number> = { ArrowUp: window.scrollY - 80, ArrowDown: window.scrollY + 80,
          PageUp: window.scrollY - window.innerHeight, PageDown: window.scrollY + window.innerHeight, Home: 0, End: range };
        if (event.key in targets) { event.preventDefault(); window.scrollTo({ top: targets[event.key], behavior: behavior() }); }
      }}>
      <span className="reading-viewport" style={{ top: `${state.viewportTop}%`, height: `${state.viewportSize}%`, opacity: state.zoom }} aria-hidden="true" />
      {Array.from({ length: 21 }, (_, i) => {
        const y = 60 + (state.height - 140) * i / 20;
        const occupied = state.chapters.some(chapter => chapter.x + chapter.width > state.width - 70 && y > chapter.y - 12 && y < chapter.y + chapter.height + 12);
        return <span key={i} className={`reading-tick ${i % 5 === 0 ? 'major' : ''}`} style={{ top: `${i * 5}%`, width: (i % 5 === 0 ? 16 : 7) + state.zoom * (i % 5 === 0 ? 10 : 7) }} aria-hidden="true">{i % 5 === 0 && <small style={{ opacity: occupied ? 0 : 1 }}>{i * 5}</small>}</span>;
      })}
      <span className="reading-indicator" style={{ top: `${state.progress}%` }} aria-hidden="true"><span className="reading-indicator-badge"><span>{String(Math.round(state.progress)).padStart(3, '0')}</span><small>%</small></span></span>
      {preview !== null && <span className="reading-preview" style={{ top: `${preview}%` }} aria-hidden="true"><small>{Math.round(preview)}%</small></span>}
    </div>
    <nav className="reading-chapters" aria-label="页面章节">{state.chapters.map((chapter, index) => <button key={chapter.selector} type="button" className={`reading-chapter ${index === state.active ? 'current' : ''} ${pressed === chapter.selector ? 'pressed' : ''}`} aria-label={`跳转到${chapter.title}`} aria-current={index === state.active ? 'location' : undefined}
      style={{ left: chapter.x - 3, top: chapter.y - 3, width: chapter.width + 6, height: chapter.height + 6 }}
      onClick={() => { setPressed(chapter.selector); document.querySelector(chapter.selector)?.scrollIntoView({ behavior: behavior(), block: 'start' }); }}
      onAnimationEnd={event => { if (event.target === event.currentTarget.lastElementChild) setPressed(null); }}>
      {chapter.glyphs.map((glyph, j) => <span key={j} className="reading-glyph" aria-hidden="true" style={{ left: glyph.x - chapter.x + 3, top: glyph.y - chapter.y + 3, width: glyph.size, height: glyph.size, fontSize: glyph.size, animationDelay: `${j * 24}ms` }}>{glyph.character}</span>)}
    </button>)}</nav>
    <button type="button" className="reading-top" aria-label="返回顶部" title="返回顶部" onClick={() => window.scrollTo({ top: 0, behavior: behavior() })}>
      <ArrowUp size={14} strokeWidth={1.8} />
      <span className="reading-top-label">TOP</span>
    </button>
  </aside>;
}
