// Full-page regression: N2 history stores quantities without optional allocation diagnostics.
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_CORE_PATH || '/home/kislate/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/index.mjs');
const base = process.env.PAPER_UI_BASE_URL || 'http://127.0.0.1:5173';
const output = process.env.PAPER_UI_OUTPUT || '.runtime/paper-n2-history';
await mkdir(output, { recursive: true });
const strategy = { id: 'official-four-factor-n2-v1', name: '四因子 · 双股倒波动', available: true, kind: 'n2', origin: 'official' };
const account = { initial_cash_fen: 20000000, cash_fen: 20000000, market_value_fen: 0, equity_fen: 20000000,
  realized_pnl_fen: 0, unrealized_pnl_fen: 0, total_pnl_fen: 0, return_percent: 0, total_fees_fen: 0,
  positions: [], market: { can_trade: false, label: '休市', server_time: '2026-11-05T18:00:00+08:00', reason: '休市', reason_code: 'MARKET_CLOSED' } };
const summary = { strategy_profit_fen: 0, strategy_return_percent: 0, benchmark_profit_fen: -10000,
  benchmark_return_percent: -.05, excess_profit_fen: 10000, excess_return_percent: .05 };
const fixture = process.env.PAPER_UI_FIXTURE ? JSON.parse(await readFile(process.env.PAPER_UI_FIXTURE, 'utf8')) : {
  account, trades: { items: [], next_before_seq: null }, strategies: { items: [strategy] },
  automation: { mode: 'algorithm', revision: 1, strategy_id: strategy.id, scheduler_enabled: true,
    runtime: {}, last_decision: null, next_check: '下一交易日10:00' },
  decisions: { items: [{ key: '2026-10-08:0000000001', date: '2026-10-08', as_of: '2026-09-30',
    status: 'completed', model_version: 'four-factor-n2-invvol60-k9-v1', target_weights: { '600919': .5 },
    allocation_outcome: { positions: { '600919': { quantity: 100 } }, note: '按实际成交记录' } }], next_before: null },
  performance: { status: 'ready', benchmark: { code: 'sh000300', name: '沪深300' }, initial_cash_fen: 20000000,
    start_date: '2026-10-08', as_of: '2026-10-08', summary,
    points: [{ date: '2026-10-08T15:00:00+08:00', trading_date: '2026-10-08', phase: 'close', ...summary }] },
};
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/opt/google/chrome/chrome', headless: true, args: ['--no-sandbox'] });
try {
  for (const fullDiagnostics of [false, true]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const decisions = structuredClone(fixture.decisions);
    if (fullDiagnostics) for (const row of decisions.items) if (row.allocation_outcome) {
      row.allocation_outcome.differences = {};
      for (const position of Object.values(row.allocation_outcome.positions)) position.weight = .5;
    }
    await page.route('**/api/**', async route => {
      assert.equal(route.request().method(), 'GET', 'regression never submits trades or changes accounts');
      const path = new URL(route.request().url()).pathname;
      const responses = {
        '/api/auth/me': { user: { id: 'ui-regression', username: 'ui-regression', nickname: '回归测试', role: 'user', is_active: true, created_at: '2026-10-01' }, csrf_token: 'test', expires_at: '2099-01-01' },
        '/api/paper/account': fixture.account, '/api/paper/trades': fixture.trades,
        '/api/paper/strategies': fixture.strategies, '/api/paper/automation': fixture.automation,
        '/api/paper/automation/decisions': decisions, '/api/paper/performance': fixture.performance,
        '/api/paper/strategy-schema': fixture.schema,
      };
      const response = responses[path];
      await route.fulfill({ status: response ? 200 : 503, json: response || { detail: '该接口不参与只读回归' } });
    });
    await page.goto(base);
    await page.getByRole('navigation').getByRole('button', { name: '模拟盘', exact: true }).click();
    await page.getByText('算法托管中', { exact: true }).waitFor();
    await page.getByRole('heading', { name: '收益与收益率对比' }).waitFor();
    await page.locator('.paper-performance-chart canvas').waitFor();
    await page.getByText(/算法决策与执行记录/).click();
    await page.getByText(/执行后持仓：/).first().waitFor();
    const outcomes = await page.locator('.paper-decisions').innerText();
    assert.equal(outcomes.includes('undefined'), false);
    assert.equal(outcomes.includes('NaN'), false);
    await page.getByRole('button', { name: '收益金额', exact: true }).click();
    await page.getByText('收益差额', { exact: true }).waitFor();
    if (!fullDiagnostics) {
      await page.screenshot({ path: output + '/paper-dark.png', fullPage: true });
      await page.getByRole('button', { name: '切换到浅色模式' }).click();
      await page.screenshot({ path: output + '/paper-light.png', fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
      await page.screenshot({ path: output + '/paper-mobile.png', fullPage: true });
    }
    assert.deepEqual(errors, [], 'full paper page must stay mounted while history loads');
    await page.close();
  }
  console.log('PASS whole paper page: N2 partial history, legacy complete diagnostics, expanded decisions, returns chart, themes/mobile; no page errors or writes');
} finally { await browser.close(); }
