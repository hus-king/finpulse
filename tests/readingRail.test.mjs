import assert from 'node:assert/strict';
import test from 'node:test';
import { chapterGeometry, RULER, rulerGeometry } from '../src/readingRailGeometry.ts';

test('ruler scrolls at document scale and compresses all 101 ticks on hover', () => {
  const normal = rulerGeometry(1000, 5000, 1000, 0);
  const zoomed = rulerGeometry(1000, 5000, 1000, 1);
  assert.equal(normal.ticks.length, 101);
  assert.equal(normal.ticks[1].y - normal.ticks[0].y, 50);
  assert.ok(Math.abs(zoomed.ticks[1].y - zoomed.ticks[0].y - 9.28) < 1e-9);
  assert.equal(zoomed.ticks[0].y, 60);
  assert.equal(zoomed.ticks[100].y, 988);
  assert.equal(normal.needle, zoomed.needle);
  assert.equal(normal.progress, .25);
  assert.equal(normal.ticks[5].major, true);
  assert.equal(normal.ticks[6].major, false);
});

test('visible-page bracket covers exactly one viewport, including page ends', () => {
  const top = rulerGeometry(1000, 5000, 0, 1), bottom = rulerGeometry(1000, 5000, 4000, 1);
  assert.equal(top.bracketTop, top.needle);
  assert.ok(Math.abs(top.bracketHeight - 185.6) < 1e-9);
  assert.ok(Math.abs(bottom.bracketTop + bottom.bracketHeight - bottom.needle) < 1e-9);
  assert.equal(bottom.progress, 1);
  const short = rulerGeometry(1000, 900, 0, 1);
  assert.equal(short.progress, 0);
  assert.equal(short.bracketHeight, short.span);
});

const chapters = [
  { selector: 'a', title: '市场研究', anchor: 0, bottom: 350, advances: [1, 1, 1, 1] },
  { selector: 'b', title: '大盘概览', anchor: 500, bottom: 800, advances: [1, 1, 1, 1] },
  { selector: 'c', title: '行情走势', anchor: 900, bottom: 1600, advances: [1, 1, 1, 1] },
  { selector: 'd', title: '新闻研判', anchor: 1800, bottom: 16000, advances: [1, 1, 1, 1] },
];
test('hover maps every chapter horizontally and keeps close labels distinct', () => {
  const layout = chapterGeometry(chapters, 1000, 1600, 20000, 1500, 1);
  layout.forEach(chapter => {
    assert.ok(chapter.width > 0 && chapter.height > 0);
    chapter.letters.forEach(letter => { assert.equal(letter.rotation, 0); assert.ok(Math.abs(letter.scale * RULER.heading - 16) < 1e-9); });
  });
  for (let index = 1; index < layout.length; index++) assert.ok(layout[index].top - layout[index - 1].top >= 19.99);
});

test('chapter paths reverse continuously and tail chapters reach the top pile', () => {
  const a = chapterGeometry(chapters, 1000, 1600, 20000, 1500, .4);
  const b = chapterGeometry(chapters, 1000, 1600, 20000, 1500, .4);
  assert.deepEqual(a, b);
  const before = chapterGeometry(chapters, 1000, 1600, 20000, 1499.99, .4);
  assert.ok(Math.abs(a[3].letters[0].y - before[3].letters[0].y) < 1);
  const end = chapterGeometry(chapters, 1000, 1600, 2000, 1000, 0);
  assert.equal(end.at(-1).flip, 1);
  assert.equal(chapterGeometry([], 1000, 1600, 2000, 0, 0).length, 0);
});
