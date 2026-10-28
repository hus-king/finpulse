// Geometry matched to ArtCraft's published ruler: 48px navigation + 12px edge pad.
export const clamp = (value: number) => Math.max(0, Math.min(1, value));
export const mix = (from: number, to: number, progress: number) => from + (to - from) * progress;
export const easeCubic = (value: number) => value < .5 ? 4 * value ** 3 : 1 - (-2 * value + 2) ** 3 / 2;
export const easeExpo = (value: number) => value >= 1 ? 1 : 1 - 2 ** (-10 * value);
export const RULER = { width: 60, nav: 48, edge: 12, heading: 34, riding: 20, queue: 16,
  top: 28, currentGap: 26, slot: 20, bottom: 36, flip: 200, detach: 240, stagger: .16, arc: 28 };
export type Chapter = { selector: string; title: string; anchor: number; bottom: number; advances: number[] };
type Position = { x: number; y: number; rotation: number; scale: number; alpha: number };
export type Letter = Position & { character: string; advance: number };
export type ChapterLayout = { selector: string; title: string; letters: Letter[]; flip: number; detach: number;
  ridingY: number; ridingHeight: number; left: number; top: number; width: number; height: number };

export function rulerGeometry(height: number, documentHeight: number, scrollY: number, zoom: number) {
  const range = Math.max(0, documentHeight - height);
  const progress = range > 0 ? clamp(scrollY / range) : 0;
  const base = RULER.nav + RULER.edge, span = Math.max(1, height - RULER.nav - RULER.edge * 2);
  const drift = base - progress * (RULER.nav + RULER.edge * 2);
  return { progress, range, base, span, drift, needle: base + progress * span,
    bracketTop: base + scrollY / Math.max(1, documentHeight) * span,
    bracketHeight: Math.min(1, height / Math.max(1, documentHeight)) * span,
    ticks: Array.from({ length: 101 }, (_, percent) => ({ percent, major: percent % 5 === 0,
      documentY: percent / 100 * documentHeight,
      y: mix(percent / 100 * documentHeight - scrollY + drift, base + percent / 100 * span, zoom),
    })),
  };
}

export function chapterGeometry(chapters: Chapter[], height: number, width: number, documentHeight: number, scrollY: number, zoom: number): ChapterLayout[] {
  const geometry = rulerGeometry(height, documentHeight, scrollY, zoom), r = RULER;
  const phases = chapters.map((chapter, index) => {
    const ridingY = chapter.anchor - scrollY + geometry.drift;
    let flip = index === 0 ? 1 : clamp((height * .12 + r.flip - ridingY) / r.flip);
    if (index > 0 && chapter.anchor - geometry.range > height * .12) {
      const start = Math.min(chapter.bottom - height, geometry.range - 24);
      flip = Math.max(flip, clamp((scrollY - start) / Math.max(24, geometry.range - start)));
    }
    return { ridingY, flip, detach: index === 0 ? 1 : 0, queueY: height - r.bottom,
      ridingHeight: chapter.advances.reduce((sum, value) => sum + value, 0) * r.riding };
  });
  let queued = 0;
  for (let index = chapters.length - 1; index > 0; index--) {
    const phase = phases[index]; phase.queueY -= r.slot * queued;
    phase.detach = clamp((phase.queueY + r.detach - phase.ridingY - phase.ridingHeight) / r.detach);
    queued += 1 - phase.detach;
  }
  const settled = phases.filter(phase => phase.flip >= 1).length;
  const unfolding = phases.reduce((sum, phase) => sum + (phase.flip > 0 && phase.flip < 1 ? phase.flip : 0), 0);
  // FinPulse's overview and quote anchors can be close together; keep Chinese map labels legible.
  const mapYs = chapters.map(chapter => geometry.base + chapter.anchor / Math.max(1, documentHeight) * geometry.span);
  for (let i = 1; i < mapYs.length; i++) mapYs[i] = Math.max(mapYs[i], mapYs[i - 1] + r.slot);
  if (mapYs.length) mapYs[mapYs.length - 1] = Math.min(mapYs.at(-1)!, height - r.edge - r.queue / 2);
  for (let i = mapYs.length - 2; i >= 0; i--) mapYs[i] = Math.min(mapYs[i], mapYs[i + 1] - r.slot);
  const mapMix = easeCubic(zoom);
  return chapters.map((chapter, index) => {
    const phase = phases[index], total = chapter.advances.reduce((sum, value) => sum + value, 0);
    let advance = 0;
    const horizontal = (center: number, y: number, scale: number, alpha: number): Position => ({
      x: width - r.width - (total - center) * r.heading * scale, y, rotation: 0, scale, alpha,
    });
    const vertical = (center: number, y: number): Position => ({ x: width - r.width / 2,
      y: y + (total - center) * r.riding, rotation: -90, scale: r.riding / r.heading, alpha: .35 });
    const letters = [...chapter.title].map((character, position) => {
      const step = chapter.advances[position] ?? 1, center = advance + step / 2; advance += step;
      let from: Position, to: Position, amount: number;
      if (phase.flip > 0) {
        from = vertical(center, phase.ridingY); amount = phase.flip;
        const compact = phase.flip >= 1 ? index === settled - 1 ? unfolding : 1 : 0;
        to = horizontal(center, r.nav + r.top + index * r.slot + r.currentGap * (1 - compact),
          mix(1, r.queue / r.heading, compact), mix(1, .35, compact));
      } else {
        amount = phase.detach;
        from = horizontal(center, phase.queueY, r.queue / r.heading, .35);
        to = vertical(center, phase.detach < 1 ? phase.queueY - phase.ridingHeight : phase.ridingY);
      }
      const order = phase.flip > 0 || phase.detach < 1 ? chapter.title.length - 1 - position : position;
      const t = easeCubic(clamp(amount * (1 + (chapter.title.length - 1) * r.stagger) - order * r.stagger));
      const map = horizontal(center, mapYs[index], r.queue / r.heading, .85);
      return { character, advance: step,
        x: mix((1 - t) ** 2 * from.x + 2 * (1 - t) * t * ((from.x + to.x) / 2 - r.arc) + t ** 2 * to.x, map.x, mapMix),
        y: mix(mix(from.y, to.y, t), map.y, mapMix), rotation: mix(mix(from.rotation, to.rotation, t), 0, mapMix),
        scale: mix(mix(from.scale, to.scale, t), map.scale, mapMix), alpha: mix(mix(from.alpha, to.alpha, t), map.alpha, mapMix),
      };
    });
    const bounds = letters.map(letter => {
      const rotated = Math.abs(letter.rotation) > 45;
      return { left: letter.x - (rotated ? 1 : letter.advance) * r.heading * letter.scale / 2 - 2,
        right: letter.x + (rotated ? 1 : letter.advance) * r.heading * letter.scale / 2 + 2,
        top: letter.y - (rotated ? letter.advance : 1) * r.heading * letter.scale / 2 - 2,
        bottom: letter.y + (rotated ? letter.advance : 1) * r.heading * letter.scale / 2 + 2 };
    });
    const left = Math.min(...bounds.map(bound => bound.left)), top = Math.min(...bounds.map(bound => bound.top));
    return { selector: chapter.selector, title: chapter.title, letters, ...phase, left, top,
      width: Math.max(...bounds.map(bound => bound.right)) - left, height: Math.max(...bounds.map(bound => bound.bottom)) - top };
  });
}
