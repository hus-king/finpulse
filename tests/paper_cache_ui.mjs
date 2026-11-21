import assert from 'node:assert/strict';
import { homedir } from 'node:os';

const { chromium } = await import(process.env.PLAYWRIGHT_CORE_PATH ??
  `${homedir()}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/index.mjs`);
const base = process.env.PAPER_UI_BASE_URL ?? 'http://127.0.0.1:5192';
const chrome = process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/opt/google/chrome/chrome';
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH ?? chrome, headless: true, args: ['--no-sandbox', '--no-proxy-server'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(8000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));

const indices = { sh000300: '沪深300', sh000001: '上证指数' };
const session = id => ({ user: { id, username: id, nickname: id, role: 'user', is_active: true, created_at: '2025-01-01' }, csrf_token: 'fixture' });
const market = { can_trade: true, label: '交易中', server_time: '2026-11-20T10:00:00+08:00' };
function account(id) {
  const equity = id === 'alice' ? 21829489 : 17113555;
  return { initial_cash_fen: 20000000, cash_fen: equity, market_value_fen: 0, equity_fen: equity,
    realized_pnl_fen: equity - 20000000, unrealized_pnl_fen: 0, total_pnl_fen: equity - 20000000,
    return_percent: (equity / 20000000 - 1) * 100, total_fees_fen: 0, positions: [], market };
}
function comparison(code, id, status) {
  const day = id === 'alice' ? '2026-11-19' : '2026-11-20';
  const metrics = { strategy_profit_fen: 100000, strategy_return_percent: .5, benchmark_profit_fen: 20000,
    benchmark_return_percent: .1, excess_profit_fen: 80000, excess_return_percent: .4 };
  const ready = status === 'ready';
  return { status, benchmark: { code, name: indices[code] }, benchmark_options: Object.entries(indices).map(([code, name]) => ({ code, name })),
    reason: status === 'loading' ? '正在补齐历史收盘数据' : status === 'empty' ? '首笔成交后开始展示收益对比' : null,
    initial_cash_fen: ready ? 20000000 : null, start_date: ready ? day : null, as_of: ready ? day : null,
    points: ready ? [{ ...metrics, date: day, trading_date: day, phase: 'close' }] : [], summary: ready ? metrics : null };
}

let owner = 'alice', curveStatus = 'ready', decisions = 0;
const waits = new Map();
function hold(request) {
  let release;
  waits.set(request, new Promise(resolve => { release = resolve; }));
  return () => { waits.delete(request); release(); };
}

await page.route('**/api/**', async route => {
  const { pathname, searchParams } = new URL(route.request().url());
  if (pathname === '/api/auth/logout') { owner = null; return route.fulfill({ json: {} }); }
  if (pathname === '/api/paper/trades' && route.request().method() === 'POST') {
    return route.fulfill({ json: { ...route.request().postDataJSON(), name: '测试股票', price_fen: 1000 } });
  }
  if (route.request().method() !== 'GET') throw Error('Unexpected write in paper-cache test: ' + pathname);
  const id = owner, status = curveStatus;
  await waits.get(pathname);
  let data;
  switch (pathname) {
    case '/api/auth/me':
      if (!id) return route.fulfill({ status: 401, json: { detail: '未登录' } });
      data = session(id); break;
    case '/api/paper/account': data = account(id); break;
    case '/api/paper/trades': data = { items: [], next_before_seq: null }; break;
    case '/api/paper/performance': data = comparison(searchParams.get('benchmark'), id, status); break;
    case '/api/paper/strategies': data = { items: [] }; break;
    case '/api/paper/automation': data = { mode: 'manual', revision: 1, strategy_id: null, runtime: {} }; break;
    case '/api/paper/automation/decisions': decisions++; data = { items: [], next_before: null }; break;
    case '/api/watchlist': data = { codes: [], items: [], jobs: [] }; break;
    case '/api/stocks': data = { items: [] }; break;
    case '/api/health': data = { status: 'ok' }; break;
    default:
      if (pathname.startsWith('/api/paper/quote/')) {
        data = { stock: { code: pathname.split('/').pop(), name: '示例股票' }, price_fen: 1000, quote_status: 'fresh', can_trade: true,
          rules: { board: '主板', min_quantity: 100, quantity_step: 100, max_quantity: 1000000 }, next_poll_seconds: 60, market };
      } else return route.fulfill({ status: 503, json: { detail: '未配置此测试接口' } });
  }
  return route.fulfill({ json: data });
});

const nav = name => page.getByRole('navigation').getByRole('button', { name, exact: true });
const equity = amount => page.getByTestId('paper-equity').filter({ hasText: amount });
const chart = (index, date) => page.getByRole('img', { name: new RegExp(index + '截至' + date) });
const curveRefresh = page.getByRole('button', { name: '刷新收益对比' });
async function openPaper() { await nav('模拟盘').click(); await page.getByTestId('paper-equity').waitFor(); }
async function asUser(id) {
  owner = id;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.getByRole('button', { name: '打开账号菜单' }).filter({ hasText: id }).waitFor();
}
try {
  const releaseHistory = hold('/api/paper/trades');
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await openPaper();
  await equity('218,294.89').waitFor(); // History is still held.
  await chart('沪深300', '2026-11-19').waitFor();
  releaseHistory();
  await page.getByText('手动操作', { exact: true }).waitFor();
  assert.equal(decisions, 0, 'Closed decisions history must not load during entry');
  const decisionRequest = page.waitForResponse(r => new URL(r.url()).pathname === '/api/paper/automation/decisions');
  await page.locator('.paper-decisions summary').click();
  await decisionRequest;

  curveStatus = 'loading';
  await curveRefresh.click();
  await page.getByText('正在补齐历史收盘数据', { exact: true }).waitFor();
  assert.equal(await chart('沪深300', '2026-11-19').count(), 1);
  curveStatus = 'ready';
  const releaseIndex = hold('/api/paper/performance');
  await page.getByLabel('对比指数').selectOption('sh000001');
  assert.equal(await chart('沪深300', '2026-11-19').count(), 0, 'Uncached benchmark must not show the other curve');
  releaseIndex();
  await chart('上证指数', '2026-11-19').waitFor();
  const releaseCurve = hold('/api/paper/performance');
  await page.getByLabel('对比指数').selectOption('sh000300');
  await chart('沪深300', '2026-11-19').waitFor();

  const releaseAccount = hold('/api/paper/account');
  await nav('行情与新闻').click();
  await openPaper();
  await equity('218,294.89').waitFor();
  await chart('沪深300', '2026-11-19').waitFor();
  assert.equal(new URL(page.url()).searchParams.get('view'), 'paper');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await equity('218,294.89').waitFor();
  await chart('沪深300', '2026-11-19').waitFor();
  await page.getByText('等待核实最新账户余额与持仓。', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '确认模拟买入', exact: true }).isDisabled(), true);

  await asUser('bob'); // Different identity while Alice's HTTP requests are still pending.
  assert.equal(await equity('218,294.89').count(), 0);
  assert.equal(await chart('沪深300', '2026-11-19').count(), 0);
  releaseAccount(); releaseCurve();
  await equity('171,135.55').waitFor();
  await chart('沪深300', '2026-11-20').waitFor();

  await page.getByLabel('对比指数').selectOption('sh000001');
  await chart('上证指数', '2026-11-20').waitFor();
  const amountMode = page.getByRole('button', { name: '收益金额', exact: true });
  await amountMode.click();
  const releaseAfterTrade = hold('/api/paper/performance');
  await page.getByRole('button', { name: '确认模拟买入', exact: true }).click();
  await page.getByText(/模拟买入成功/).waitFor();
  await chart('上证指数', '2026-11-20').waitFor({ state: 'detached' });
  assert.equal(await page.getByLabel('对比指数').inputValue(), 'sh000001');
  assert.equal(await amountMode.getAttribute('aria-pressed'), 'true');
  releaseAfterTrade();
  await chart('上证指数', '2026-11-20').waitFor();

  curveStatus = 'empty';
  await curveRefresh.click();
  await page.getByText('首笔成交后开始展示收益对比').waitFor();
  assert.equal(await chart('上证指数', '2026-11-20').count(), 0);
  await page.getByRole('button', { name: '打开账号菜单' }).click();
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await page.getByRole('button', { name: '登录体验模拟盘' }).waitFor();
  const releaseRelogin = hold('/api/paper/account');
  await asUser('bob');
  await page.getByTestId('paper-equity').waitFor();
  assert.equal(await equity('171,135.55').count(), 0, 'Logout must discard even the same user’s previous snapshot');
  releaseRelogin();
  await equity('171,135.55').waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS paper account loads before trades; chart survives loading; per-benchmark snapshots; navigation/reload; account switch/logout; lazy decisions.');
} finally {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await browser.close();
}
