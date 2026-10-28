import { useEffect, useRef, useState } from 'react';
import { chapterGeometry, clamp, easeExpo, RULER, rulerGeometry } from './readingRailGeometry';
import type { Chapter } from './readingRailGeometry';
const chaptersByView: Record<string, { selector: string; title: string }[]> = {
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
const digits = [...'0123456789'];
export default function ReadingRail({ view }: { view: string }) {
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const scale = useRef<HTMLDivElement>(null), needle = useRef<HTMLDivElement>(null), readout = useRef<HTMLSpanElement>(null);
  const bracket = useRef<HTMLDivElement>(null), ghost = useRef<HTMLDivElement>(null);
  const ticks = useRef<(HTMLDivElement | null)[]>([]), marks = useRef<(HTMLSpanElement | null)[]>([]);
  const labels = useRef<(HTMLSpanElement | null)[]>([]), columns = useRef<(HTMLSpanElement | null)[]>([]);
  const hits = useRef<(HTMLButtonElement | null)[]>([]), letters = useRef<(HTMLSpanElement | null)[][]>([]);
  const pools = useRef<(HTMLDivElement | null)[]>([]);
  const redraw = useRef(() => {});
  const actions = useRef({ hover: (_value: boolean) => {}, move: (_y: number) => {}, down: (_y: number) => {},
    up: (_x: number, _y: number) => {}, cancel: () => {}, click: (_y: number) => {}, jump: (_index: number) => {},
    chapterHover: (_index: number) => {}, key: (_key: string): boolean => false });
  useEffect(() => {
    const media = matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)');
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    const definitions = chaptersByView[view] ?? chaptersByView.dashboard;
    let frame = 0, timer = 0, lastTime = 0, zoom = 0, intent = false, dragging = false, dragStart = 0, dragMoved = 0;
    let ghostY: number | null = null, dragTarget: number | null = null, hovered = -1;
    let velocity = 0, lastY = scrollY, placed: Chapter[] = [], previousNames = '';
    let journey: { from: number; to: number; start: number; duration: number } | null = null;
    const hoverAlpha: number[] = [], flashUntil: number[] = [], measurements = new Map<string, number[]>();
    const schedule = () => { if (!frame) frame = requestAnimationFrame(paint); };
    redraw.current = schedule;
    const jump = (top: number, duration: number) => {
      dragTarget = null;
      const to = Math.max(0, Math.min(document.documentElement.scrollHeight - innerHeight, top));
      if (motion.matches) { window.scrollTo({ top: to, behavior: 'instant' }); schedule(); return; }
      journey = { from: scrollY, to, start: performance.now(), duration }; schedule();
    };
    const measureTitle = (title: string) => {
      if (measurements.has(title)) return measurements.get(title)!;
      const probe = document.createElement('span');
      probe.className = 'reading-font-probe'; probe.setAttribute('aria-hidden', 'true');
      const chars = [...title].map(character => { const span = document.createElement('span'); span.textContent = character; probe.appendChild(span); return span; });
      document.body.appendChild(probe);
      const values = chars.map(char => char.getBoundingClientRect().width / 100);
      probe.remove(); measurements.set(title, values); return values;
    };
    const measure = () => {
      if (!media.matches) { delete document.documentElement.dataset.readingRuler; journey = null; dragTarget = null; dragging = intent = false; zoom = 0; clearTimeout(timer); return; }
      document.documentElement.dataset.readingRuler = motion.matches ? 'static' : 'on';
      placed = definitions.flatMap((chapter, index) => {
        const element = document.querySelector(chapter.selector);
        if (!element) return [];
        const rect = element.getBoundingClientRect();
        return [{ ...chapter, anchor: index === 0 ? 0 : rect.top + scrollY, bottom: rect.bottom + scrollY, advances: measureTitle(chapter.title) }];
      });
      const names = placed.map(chapter => chapter.title).join('|');
      if (names !== previousNames) { previousNames = names; setChapters(placed); }
      schedule();
    };
    function paint(now: number) {
      frame = 0;
      if (!media.matches) return;
      const dt = Math.min(.1, (now - (lastTime || now - 16)) / 1000); lastTime = now;
      let moving = false;
      if (journey) {
        const t = clamp((now - journey.start) / journey.duration);
        window.scrollTo({ top: journey.from + (journey.to - journey.from) * easeExpo(t), behavior: 'instant' });
        if (t === 1) journey = null; else moving = true;
      } else if (dragTarget !== null) {
        const next = scrollY + (dragTarget - scrollY) * (motion.matches ? 1 : 1 - .82 ** (dt * 60));
        window.scrollTo({ top: next, behavior: 'instant' });
        if (Math.abs(dragTarget - scrollY) < 1) dragTarget = null; else moving = true;
      }
      const target = !motion.matches && (intent || dragging) ? 1 : 0;
      zoom += (target - zoom) * (1 - Math.exp(-10 * dt));
      if (Math.abs(target - zoom) < .0005) zoom = target; else moving = true;
      const y = scrollY, height = innerHeight, width = innerWidth, docHeight = document.documentElement.scrollHeight;
      velocity += ((y - lastY) / Math.max(.001, dt) - velocity) * (1 - Math.exp(-6 * dt)); lastY = y;
      const g = rulerGeometry(height, docHeight, y, zoom);
      scale.current?.setAttribute('aria-valuenow', String(Math.round(g.progress * 100)));
      if (needle.current) {
        needle.current.style.transform = `translate3d(0,${g.needle}px,0)`;
        needle.current.style.opacity = String(clamp(g.progress / .03));
      }
      if (readout.current) readout.current.style.top = g.needle > height - 26 ? '-14px' : '4px';
      [...String(Math.round(g.progress * 100)).padStart(3, '0')].forEach((digit, index) => {
        const column = columns.current[index]; if (column) column.style.transform = `translateY(-${digit}em)`;
      });
      if (bracket.current) Object.assign(bracket.current.style, { transform: `translate3d(0,${g.bracketTop}px,0)`, height: `${g.bracketHeight}px`, opacity: String(zoom) });
      if (ghost.current) Object.assign(ghost.current.style, { transform: `translate3d(0,${ghostY ?? 0}px,0)`, opacity: ghostY === null ? '0' : String(.2 + .8 * zoom) });
      const layout = chapterGeometry(placed, height, width, docHeight, y, zoom);
      const active = layout.reduce((current, chapter, index) => chapter.flip > .5 ? index : current, 0);
      layout.forEach((chapter, index) => {
        const hit = hits.current[index];
        if (hit) {
          Object.assign(hit.style, { left: `${chapter.left}px`, top: `${chapter.top}px`, width: `${chapter.width}px`, height: `${chapter.height}px` });
          if (index === active) hit.setAttribute('aria-current', 'location'); else hit.removeAttribute('aria-current');
        }
        const targetAlpha = hovered === index ? 1 : 0;
        hoverAlpha[index] = (hoverAlpha[index] ?? 0) + (targetAlpha - (hoverAlpha[index] ?? 0)) * (motion.matches ? 1 : 1 - Math.exp(-dt / .08));
        if (Math.abs(targetAlpha - hoverAlpha[index]) > .001) moving = true;
        const flash = motion.matches ? 0 : clamp(((flashUntil[index] ?? 0) - now) / 350);
        if (flash > 0) moving = true;
        chapter.letters.forEach((letter, position) => {
          const element = letters.current[index]?.[position]; if (!element) return;
          const alpha = letter.alpha + (1 - letter.alpha) * hoverAlpha[index];
          const color = `color-mix(in srgb,var(--theme-heading) ${alpha * 100}%,var(--theme-background))`;
          element.style.transform = `translate3d(${letter.x}px,${letter.y}px,0) translate(-50%,-50%) rotate(${letter.rotation}deg) scale(${letter.scale})`;
          element.style.color = flash > .01 ? `color-mix(in srgb,var(--theme-accent-text) ${flash * 70}%,${color})` : color;
        });
      });
      g.ticks.forEach((tick, index) => {
        const wrapper = ticks.current[index], mark = marks.current[index], label = labels.current[index];
        if (wrapper) wrapper.style.transform = `translate3d(0,${tick.y}px,0)`;
        const proximity = clamp(1 - Math.abs(tick.documentY - g.progress * docHeight) / 180);
        const stretch = clamp(Math.abs(velocity) / 3000) * proximity ** 2;
        if (mark) { mark.style.transform = `scaleX(${1 + .6 * stretch})`; mark.style.opacity = String(Math.min(1, (tick.major ? .8 : .4) + .6 * stretch)); }
        if (label) {
          let visible = 1;
          layout.forEach(chapter => {
            const distance = Math.max(chapter.ridingY - tick.y, tick.y - chapter.ridingY - chapter.ridingHeight, 0);
            if (distance < 24) visible = Math.min(visible, 1 - chapter.detach * (1 - chapter.flip) * (1 - distance / 24));
          });
          label.style.opacity = String(.5 * (1 - (1 - visible) * (1 - zoom)));
        }
      });
      if (pools.current[0]) pools.current[0].style.opacity = String(Math.max(0, ...layout.map(chapter => chapter.flip)));
      if (pools.current[1]) pools.current[1].style.opacity = String(Math.max(0, ...layout.slice(1).map(chapter => 1 - chapter.detach)));
      if (moving || Math.abs(velocity) > 1) schedule();
    }
    const hover = (value: boolean) => {
      clearTimeout(timer);
      if (!value && !dragging) ghostY = null;
      timer = window.setTimeout(() => { intent = value; schedule(); }, 150);
      schedule();
    };
    const position = (clientY: number) => {
      const g = rulerGeometry(innerHeight, document.documentElement.scrollHeight, scrollY, zoom);
      return { y: Math.max(g.base, Math.min(g.base + g.span, clientY)), top: clamp((clientY - g.base) / g.span) * g.range };
    };
    const cancel = () => { dragging = false; dragTarget = null; journey = null; intent = false; ghostY = null; clearTimeout(timer); schedule(); };
    actions.current = { hover,
      move: clientY => { const point = position(clientY); ghostY = point.y; if (dragging) { dragMoved = Math.max(dragMoved, Math.abs(clientY - dragStart)); dragTarget = point.top; } schedule(); },
      down: clientY => { dragging = true; dragStart = clientY; dragMoved = 0; journey = null; schedule(); },
      up: (x, clientY) => { dragging = false; if (x < innerWidth - RULER.width || x > innerWidth || clientY < 0 || clientY > innerHeight) hover(false); schedule(); },
      cancel,
      click: clientY => { if (dragMoved <= 4) { const point = position(clientY); jump(point.top, 1250 * (.4 + .6 * Math.min(1, Math.abs(point.top - scrollY) / (2.5 * innerHeight)))); } },
      jump: index => { const chapter = placed[index]; if (chapter) { flashUntil[index] = performance.now() + 350; jump(index === 0 ? 0 : chapter.anchor - RULER.nav, 1250); } },
      chapterHover: index => { hovered = index; schedule(); },
      key: key => {
        const range = Math.max(0, document.documentElement.scrollHeight - innerHeight);
        const targets: Record<string, number> = { ArrowUp: scrollY - 80, ArrowDown: scrollY + 80, PageUp: scrollY - innerHeight, PageDown: scrollY + innerHeight, Home: 0, End: range };
        if (!(key in targets)) return false;
        jump(targets[key], 500); return true;
      },
    };
    const interrupt = () => { journey = null; dragTarget = null; };
    const resize = new ResizeObserver(measure); resize.observe(document.body);
    const mutations = new MutationObserver(measure), main = document.querySelector('.finance-main');
    if (main) mutations.observe(main, { childList: true, subtree: true });
    window.addEventListener('scroll', schedule, { passive: true }); window.addEventListener('resize', measure);
    window.addEventListener('wheel', interrupt, { passive: true }); window.addEventListener('touchstart', interrupt, { passive: true });
    media.addEventListener('change', measure); motion.addEventListener('change', measure);
    let disposed = false;
    document.fonts.ready.then(() => { if (!disposed) { measurements.clear(); measure(); } });
    measure();
    return () => {
      disposed = true; cancelAnimationFrame(frame); clearTimeout(timer); resize.disconnect(); mutations.disconnect();
      window.removeEventListener('scroll', schedule); window.removeEventListener('resize', measure);
      window.removeEventListener('wheel', interrupt); window.removeEventListener('touchstart', interrupt);
      media.removeEventListener('change', measure); motion.removeEventListener('change', measure);
      delete document.documentElement.dataset.readingRuler; redraw.current = () => {};
    };
  }, [view]);
  // Ref nodes become available after the measured chapter list renders.
  useEffect(() => redraw.current(), [chapters]);
  return <aside className="reading-rail" aria-label="页面阅读进度">
    <div ref={scale} className="reading-scale" role="scrollbar" tabIndex={0} aria-controls="workspace-content" aria-orientation="vertical" aria-label="阅读进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={0}
      onPointerEnter={() => actions.current.hover(true)} onPointerLeave={() => actions.current.hover(false)}
      onFocus={() => actions.current.hover(true)} onBlur={() => actions.current.hover(false)}
      onPointerMove={event => actions.current.move(event.clientY)}
      onPointerDown={event => { if (event.button === 0) { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); actions.current.down(event.clientY); } }}
      onPointerUp={event => { actions.current.up(event.clientX, event.clientY); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onPointerCancel={() => actions.current.cancel()} onClick={event => actions.current.click(event.clientY)}
      onKeyDown={event => { if (actions.current.key(event.key)) event.preventDefault(); }}>
      <div className="reading-frost" aria-hidden="true" />
      {Array.from({ length: 101 }, (_, index) => <div key={index} ref={node => { ticks.current[index] = node; }} className={`reading-tick ${index % 5 === 0 ? 'major' : ''}`} aria-hidden="true">
        <span ref={node => { marks.current[index] = node; }} className="reading-mark" style={{ animationDelay: `${index * 10}ms` }} />
        {index % 5 === 0 && <span ref={node => { labels.current[index] = node; }} className="reading-tick-label">{index}</span>}
      </div>)}
      <div ref={bracket} className="reading-viewport" aria-hidden="true" />
      <div ref={needle} className="reading-indicator" aria-hidden="true"><span className="reading-needle" /><span ref={readout} className="reading-readout"><span className="reading-digits">{[0, 1, 2].map(index => <span key={index} ref={node => { columns.current[index] = node; }} className="reading-digit-column">{digits.map(digit => <span key={digit}>{digit}</span>)}</span>)}</span><span>%</span></span></div>
    </div>
    <div ref={ghost} className="reading-preview" aria-hidden="true" />
    <div className="reading-contrast reading-contrast-top" ref={node => { pools.current[0] = node; }} aria-hidden="true" />
    <div className="reading-contrast reading-contrast-bottom" ref={node => { pools.current[1] = node; }} aria-hidden="true" />
    <nav className="reading-chapters" aria-label="页面章节">{chapters.map((chapter, index) => <div key={chapter.selector}>
      <button ref={node => { hits.current[index] = node; }} type="button" className="reading-chapter" aria-label={`跳转到${chapter.title}`}
        onClick={() => actions.current.jump(index)} onPointerEnter={() => actions.current.chapterHover(index)} onPointerLeave={() => actions.current.chapterHover(-1)}
        onFocus={() => actions.current.chapterHover(index)} onBlur={() => actions.current.chapterHover(-1)}><span className="reading-static-label">{chapter.title}</span></button>
      {[...chapter.title].map((character, position) => <span key={position} ref={node => { (letters.current[index] ??= [])[position] = node; }} className="reading-glyph" aria-hidden="true">{character}</span>)}
    </div>)}</nav>
  </aside>;
}
