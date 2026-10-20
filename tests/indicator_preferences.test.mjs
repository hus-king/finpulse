import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeIndicatorSelection, selectIndicator, displayedIndicators } from '../src/indicatorCatalog.ts';

// These fixtures catch invalid persisted values leaking into the chart.
test('missing and malformed preferences fall back to a single volume pane', () => {
  for (const value of [undefined, null, false, 12, [], {}, 'unknown', { mode: 'bad', active: 'fake', selected: 'RSI' }]) {
    assert.deepEqual(normalizeIndicatorSelection(value), { mode: 'single', active: 'VOL', selected: ['VOL'] });
  }
});

test('legacy indicator names load the same single pane', () => {
  assert.deepEqual(normalizeIndicatorSelection('成交量'), { mode: 'single', active: 'VOL', selected: ['VOL'] });
  assert.deepEqual(normalizeIndicatorSelection('MACD'), { mode: 'single', active: 'MACD', selected: ['MACD'] });
  assert.deepEqual(normalizeIndicatorSelection('RSI'), { mode: 'single', active: 'RSI', selected: ['RSI'] });
});

// Dropping validation/deduplication or allowing a fifth pane must fail here.
test('multiple preferences remove unknown and duplicate ids and cap at four', () => {
  assert.deepEqual(normalizeIndicatorSelection({ mode: 'multiple', active: 'MACD', selected: ['RSI', 'fake', 'RSI', 'MACD', 'KDJ', 'CCI', 'ATR'] }), {
    mode: 'multiple', active: 'MACD', selected: ['RSI', 'MACD', 'KDJ', 'CCI'],
  });
});

test('multiple preferences keep active inside the displayed set', () => {
  assert.deepEqual(normalizeIndicatorSelection({ mode: 'multiple', active: 'ATR', selected: ['RSI', 'MACD', 'KDJ', 'CCI'] }), {
    mode: 'multiple', active: 'ATR', selected: ['ATR', 'RSI', 'MACD', 'KDJ'],
  });
  assert.deepEqual(normalizeIndicatorSelection({ mode: 'multiple', active: 'fake', selected: ['CCI', 'ATR'] }), {
    mode: 'multiple', active: 'CCI', selected: ['CCI', 'ATR'],
  });
  assert.deepEqual(normalizeIndicatorSelection({ mode: 'multiple', active: 'RSI', selected: [] }), {
    mode: 'multiple', active: 'RSI', selected: ['RSI'],
  });
});

test('single selection replaces its pane without mutating the old preference', () => {
  const original = Object.freeze({ mode: 'single', active: 'VOL', selected: Object.freeze(['VOL']) });
  assert.deepEqual(selectIndicator(original, 'MACD'), { mode: 'single', active: 'MACD', selected: ['MACD'] });
  assert.deepEqual(original.selected, ['VOL']);
});

test('multiple selection toggles panes and always retains at least one', () => {
  const original = { mode: 'multiple', active: 'VOL', selected: ['VOL'] };
  const added = selectIndicator(original, 'RSI');
  assert.deepEqual(added, { mode: 'multiple', active: 'RSI', selected: ['VOL', 'RSI'] });
  assert.deepEqual(selectIndicator(added, 'RSI'), original);
  assert.deepEqual(selectIndicator(original, 'VOL'), original);
});

test('a fifth multiple selection leaves the existing four panes intact', () => {
  const selection = { mode: 'multiple', active: 'CCI', selected: ['VOL', 'RSI', 'MACD', 'CCI'] };
  assert.deepEqual(selectIndicator(selection, 'ATR'), selection);
});

test('single display retains saved multiple choices while showing only active', () => {
  const saved = { mode: 'single', active: 'MACD', selected: ['VOL', 'MACD', 'RSI'] };
  assert.deepEqual(displayedIndicators(saved), ['MACD']);
  assert.deepEqual(displayedIndicators({ ...saved, mode: 'multiple' }), ['VOL', 'MACD', 'RSI']);
});
