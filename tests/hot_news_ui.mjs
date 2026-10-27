// All news below is an isolated UI fixture, never a live financial assessment.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_CORE_PATH || 'playwright-core');
const base = process.env.HOT_NEWS_UI_BASE_URL || 'http://127.0.0.1:8016';
const output = process.env.HOT_NEWS_UI_OUTPUT || '.runtime/hot-news-board-ui';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined,
  headless: true, args: ['--no-sandbox', '--no-proxy-server'] });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
const page = await context.newPage(), errors = [];
page.on('pageerror', error => errors.push(error.message));
const source = (id, rank, stale = false) => ({ id, name: id === 'baidu' ? '百度热搜' : '微博热搜', rank,
  heat: '123456', url: 'https://example.com/topic', observed_at: '2026-10-26T12:00:00+08:00', observed_timestamp: 1792987200, stale });
const makeTopic = (id, title, decision, channel) => ({ id, title, summary: '界面测试话题摘要，不代表已发生的真实新闻。',
  sources: [source('baidu', Number(id))], rank: Number(id), rank_score: .01,
  sector_impacts: [],
  screening: { id, decision, channels: channel ? [channel] : [], reason: decision === 'keep' ? '测试：事件可能改变贸易成本或能源运输条件，需要核实原始报道。' : '测试：当前材料不足或没有具体市场影响入口。',
    evidence: title, method: 'rules' } });
const topics = [makeTopic('1', '测试情景：美国加征进口关税', 'keep', 'trade'),
  makeTopic('2', '测试情景：霍尔木兹海峡通航受阻', 'keep', 'supply'),
  makeTopic('3', '测试情景：明星红毯同框', 'exclude'), makeTopic('4', '测试情景：有待核实的国际新闻', 'review')];
topics[1].sources.push(source('weibo', 5, true));
topics[1].attention = { mentions: 3, platforms: 1, snapshots: 2, best_rank: 2, priority: true, related_reports: 3, score: .05 };
topics[1].members = [topics[1], { id: '2-followup', title: '测试情景：同一航道事件的后续报道', sources: [source('baidu', 3)] }].map(({ id, title, sources }) => ({ id, title, sources }));
topics[0].sources.push({ ...source('tushare_cls', null), name: '财联社快讯', kind: 'news', publisher: '财联社', published_at: '2026-10-26T11:59:00+08:00', heat: null });
topics[0].sector_impacts = [{ id: 'export_manufacturing', name: '出口制造', direction: 'mixed',
  reason: '测试：关税可能增加成本或改变订单流向，需核实覆盖地区及产品。', evidence: topics[0].title, method: 'rules' }];
topics[1].sector_impacts = [['shipping', '航运港口', 'mixed'], ['oil_gas', '油气开采', 'positive'], ['aviation', '航空运输', 'negative']].map(([id, name, direction]) => ({ id, name, direction,
  reason: '测试：通航变化可能影响供给、收入或燃油成本，需逐一核实。', evidence: topics[1].title, method: 'model' }));
const feed = { items: topics, status: 'partial', fetched_at: '2026-10-26T12:00:00+08:00',
  sources: [{ id: 'baidu', name: '百度热搜', url: 'https://top.baidu.com', status: 'ok', fetched_at: '2026-10-26T12:00:00+08:00', error: null },
    { id: 'weibo', name: '微博热搜', url: 'https://weibo.com', status: 'stale', fetched_at: '2026-10-26T11:50:00+08:00', error: '微博热搜暂不可读取（HTTP 403）' }],
  screening: 'rules', warnings: ['当前为规则初筛，结果需要核对。'], counts: { keep: 2, review: 1, exclude: 1 },
  note: '全领域热榜测试快照，未独立核实原始报道。', ranking_note: '按平台名次融合排序，不直接相加跨平台热度。' };
let loggedIn = false, refreshCount = 0, inferenceCount = 0, batchCount = 0, failImpact = false;
let refreshState = 'idle', refreshPolls = 0;
const savedImpacts = new Map();
let matchJob = null, matchCount = 0, matchPolls = 0, failMatch = false;
const impactStocks = [['601857', '中国石油'], ['601111', '中国国航'], ['600519', '贵州茅台']].map(([code, name]) => ({ code, name, exchange: 'SH', initials: code, industry: '界面测试', price: null, change: null }));
const impactRows = impactStocks.map((stock, i) => ({ code: stock.code, name: stock.name, status: 'ok', model: 'ui-fixture', analyzed_at: '2026-10-26T12:00:00+08:00',
  business: { main_business: '界面测试主营资料', source: '测试主营来源', url: 'https://example.com/business', status: 'ok', fetched_at: 1792987200 },
  analysis: { code: stock.code, relation: i === 2 ? 'none' : 'indirect', direction: ['positive', 'negative', 'unclear'][i], confidence: 'low',
    score: [6, -7, null][i], relationship_evidence: [],
    news_evidence: i === 2 ? [] : ['霍尔木兹海峡通航受阻'], business_evidence: i === 2 ? '' : '界面测试主营资料',
    mechanism: '界面测试：不同业务角色可能承受相反的收入与成本影响。', uncertainty: '测试情景，实际业务敞口需核对。' } }));
function impactSnapshot(id, rows) { return { topic_id: id, title: topics.find(t => t.id === id)?.title, summary: topics.find(t => t.id === id)?.summary, sources: [], version: 'fixture', items: rows,
  counts: { total: rows.length, completed: rows.filter(r => r.status === 'ok').length, pending: rows.filter(r => r.status === 'pending').length,
    failed: rows.filter(r => r.status === 'failed').length, related: rows.filter(r => r.analysis && ['direct', 'indirect'].includes(r.analysis.relation)).length },
  note: '根据热点与主营资料判断业务影响，界面测试内容。' }; }
const storedSnapshot = id => impactSnapshot(id, savedImpacts.get(id) ?? impactRows.map(r => ({ ...r, status: 'pending', analysis: null })));
function finishMatch() {
  const plain = impactRows.map(r => ({ ...r, analysis: { ...r.analysis, relation: 'none', direction: 'unclear', score: null, news_evidence: [], business_evidence: '' } }));
  const related = failMatch ? [impactRows[0], { ...impactRows[1], status: 'failed', analysis: null, error: '测试：资料核对未完成' }, impactRows[2]] : impactRows;
  matchJob.results = [impactSnapshot('1', plain), impactSnapshot('2', related)];
  savedImpacts.set('1', plain); savedImpacts.set('2', related);
  matchJob.status = failMatch ? 'partial' : 'completed'; matchJob.stage = 'ready';
  matchJob.message = failMatch ? '匹配完成；部分判断未完成，可点击重试。' : '匹配完成，已按自选股汇总相关的新闻。';
  matchJob.counts = { total: 6, completed: failMatch ? 5 : 6, failed: failMatch ? 1 : 0, uncertain: 0, related: failMatch ? 1 : 2, related_stocks: failMatch ? 1 : 2 };
  matchJob.topics_completed = 2;
  matchJob.stocks = related.filter(r => r.analysis && ['direct', 'indirect'].includes(r.analysis.relation)).map(r => ({ code: r.code, name: r.name,
    news: [{ topic_id: '2', title: topics[1].title, sources: topics[1].sources, analysis: r.analysis, model: r.model, analyzed_at: r.analyzed_at }] }));
}
const queries = [];
await page.route('**/api/**', async route => {
  const url = new URL(route.request().url()), path = url.pathname;
  let data = {};
  if (path === '/api/auth/me') {
    if (!loggedIn) return route.fulfill({ status: 401, json: { code: 'AUTH_REQUIRED', detail: '请登录' } });
    data = { user: { id: 'fixture-reader', username: 'reader', nickname: '测试读者', role: 'user', created_at: '2026-10-01' }, csrf_token: 'a'.repeat(64) };
  } else if (path === '/api/stocks') data = { items: [] };
  else if (path === '/api/health') data = { configured: false };
  else if (path === '/api/watchlist') data = { codes: impactStocks.map(s => s.code), items: impactStocks, jobs: [] };
  else if (path.startsWith('/api/dashboard/')) data = { stock: { code: path.split('/').at(-1), name: '测试', price: null, change: null }, candles: [], news: [], as_of: null, quote: { status: 'unavailable' }, backtest: { items: [], note: '' } };
  else if (path === '/api/hot-news/matches') {
    assert.equal(loggedIn, true);
    if (matchJob?.status === 'running' && ++matchPolls >= 2) finishMatch();
    data = { job: matchJob };
  } else if (path === '/api/hot-news/match') {
    assert.equal(route.request().headers()['x-csrf-token'], 'a'.repeat(64));
    assert.deepEqual(route.request().postDataJSON(), { topic_ids: ['1', '2'] });
    matchCount++; matchPolls = 0;
    matchJob = { id: `job${matchCount}`, status: 'running', stage: 'profiles', message: '正在准备我的自选股主营资料。',
      started_at: '2026-10-27T12:00:00+08:00', updated_at: '2026-10-27T12:00:00+08:00', topic_ids: ['1', '2'],
      topic_total: 2, stock_total: 3, topics_completed: 0, watchlist_changed: false,
      counts: { total: 6, completed: 0, failed: 0, uncertain: 0, related: 0, related_stocks: 0 }, results: [], errors: [], stocks: [] };
    return route.fulfill({ status: 202, json: { job: matchJob } });
  }
  else if (path === '/api/hot-news') {
    const params = url.searchParams; queries.push(Object.fromEntries(params));
    const selectedSource = params.get('source') || 'all', channel = params.get('channel') || 'all';
    const q = (params.get('q') || '').trim();
    const scoped = topics.filter(topic => (selectedSource === 'all' || topic.sources.some(entry => entry.id === selectedSource))
      && (channel === 'all' || topic.screening.channels.includes(channel)) && (!q || (topic.title + topic.summary).includes(q)));
    const matched = scoped.filter(topic => topic.screening.decision === 'keep');
    if (refreshState === 'running' && ++refreshPolls >= 3) refreshState = 'completed';
    data = { ...feed, items: matched.slice(0, Number(params.get('limit') || 100)),
      counts: { keep: matched.length }, refresh: { status: refreshState, stage: refreshState === 'running' ? 'screening' : 'ready', message: refreshState === 'running' ? '正在筛选具体的重要新事件' : '热点新闻已更新', completed: refreshPolls, total: 4 },
      matched_total: matched.length, candidate_total: 2,
      filters: Object.fromEntries(params), channel_options: [] };
  }
  else if (path === '/api/hot-news/refresh') {
    assert.equal(route.request().headers()['x-csrf-token'], 'a'.repeat(64));
    refreshCount++; refreshState = 'running'; refreshPolls = 0;
    return route.fulfill({ status: 202, json: { ...feed, items: topics.filter(t => t.screening.decision === 'keep'), refresh: { status: 'running', stage: 'collecting', message: '正在采集平台热点', completed: 0, total: 0 } } });
  } else if (path === '/api/hot-news/analysis-batch') {
    assert.equal(loggedIn, true, 'guests must not read private watchlist results'); batchCount++;
    data = { items: url.searchParams.get('ids').split(',').map(storedSnapshot), missing_ids: [] };
  } else if (path.endsWith('/analysis')) data = storedSnapshot(path.split('/').at(-2));
  else if (path.endsWith('/analyze')) {
    assert.equal(route.request().headers()['x-csrf-token'], 'a'.repeat(64)); inferenceCount++;
    data = impactSnapshot(path.split('/').at(-2), failImpact ? [impactRows[0], { ...impactRows[1], status: 'failed', analysis: null, error: '测试：原文校验未完成，可重试。' }, impactRows[2]] : impactRows);
    savedImpacts.set(data.topic_id, data.items);
    failImpact = false;
  }
  return route.fulfill({ status: 200, json: data });
});
async function waitResults(count) {
  await page.waitForFunction(expected => document.querySelector('.hot-board')?.getAttribute('aria-busy') === 'false'
    && document.querySelectorAll('.hot-topic-card').length === expected, count);
}
try {
  await page.goto(base + '/?view=hot-news');
  await page.getByRole('heading', { name: '重要热点新闻', exact: true }).waitFor();
  await waitResults(2);
  assert.equal(await page.locator('.hot-topic-card').count(), 2, 'cached board is visible to guests');
  assert.match(await page.locator('.hot-source-grid').innerText(), /HTTP 403/);
  assert.match(await page.locator('.hot-topic-card').nth(1).locator('.hot-sector-section').innerText(), /油气开采.*利好/s);
  assert.match(await page.locator('.hot-topic-card').nth(1).locator('.hot-sector-section').innerText(), /航空运输.*利空/s);
  assert.match(await page.locator('.hot-watch-section').first().innerText(), /登录后展示/);
  assert.equal(batchCount, 0);
  assert.match(await page.locator('.hot-topic-card').first().locator('.hot-topic-sources').innerText(), /财联社.*报道于/s);
  assert.doesNotMatch(await page.locator('.hot-topic-card').first().innerText(), /null 名/);
  assert.equal(await page.getByRole('navigation', { name: '筛选热点事件' }).count(), 0);
  assert.doesNotMatch(await page.locator('.hot-topic-list').innerText(), /明星红毯|有待核实的国际新闻/);
  await page.getByLabel('筛选热榜来源').selectOption('weibo');
  await waitResults(1);
  assert.equal(await page.locator('.hot-topic-card').count(), 1);
  assert.match(await page.locator('.hot-topic-card').innerText(), /旧快照/);
  await page.getByLabel('筛选热榜来源').selectOption('all');
  await waitResults(2);
  await page.getByLabel('筛选经济影响类型').selectOption('trade');
  await waitResults(1);
  assert.match(await page.locator('.hot-topic-card').innerText(), /关税/);
  assert.match(await page.locator('.hot-result-count').innerText(), /匹配 1 条/);
  await page.getByRole('button', { name: '重置筛选', exact: true }).click();
  await waitResults(2);
  await page.getByLabel('搜索热点新闻').fill('关税');
  await waitResults(1);
  assert.match(await page.locator('.hot-topic-card').innerText(), /关税/);
  await page.getByLabel('搜索热点新闻').fill('不存在的关键词');
  await page.getByText('没有符合当前条件的新闻', { exact: false }).waitFor();
  assert.equal(await page.locator('.hot-topic-card').count(), 0);
  await page.getByRole('button', { name: '重置筛选', exact: true }).first().click();
  await waitResults(2);
  await page.getByLabel('热点排序方式').selectOption('latest');
  await waitResults(2);
  await page.getByLabel('热点展示条数').selectOption('20');
  await waitResults(2);
  assert.equal(queries.at(-1).sort, 'latest');
  assert.equal(queries.at(-1).limit, '20');
  assert.ok(queries.some(query => query.channel === 'trade'));
  assert.ok(queries.some(query => query.q === '关税'));
  await page.getByRole('button', { name: '重置筛选', exact: true }).click();
  await waitResults(2);
  await page.locator('.hot-topic-card').first().getByText('查看筛选依据与采集时间', { exact: true }).click();
  assert.match(await page.locator('.hot-topic-card').first().innerText(), /规则初筛/);
  await page.screenshot({ path: output + '/board-dark.png', fullPage: true });
  await page.getByRole('button', { name: '切换到浅色模式' }).click();
  await page.screenshot({ path: output + '/board-light.png', fullPage: true });
  loggedIn = true;
  await page.reload();
  await page.getByRole('heading', { name: '重要热点新闻', exact: true }).waitFor();
  await waitResults(2);
  await page.getByRole('button', { name: '更新热点', exact: true }).click();
  await page.getByRole('button', { name: '更新进行中…', exact: true }).waitFor();
  await page.getByRole('status').filter({ hasText: '正在后台采集' }).waitFor();
  assert.equal(refreshCount, 1, 'one POST schedules work; polls must be read-only');
  await waitResults(2);
  assert.ok(refreshPolls >= 3, 'background progress must be polled until completion');
  assert.equal(refreshCount, 1);
  assert.equal(inferenceCount, 0, 'reading the board must not invoke stock analysis');
  const affectedCard = page.locator('.hot-topic-card').nth(1);
  await affectedCard.getByRole('button', { name: '分析我的自选股', exact: true }).click();
  await affectedCard.getByText('2 只有关联 · 3/3 只已核对', { exact: true }).waitFor();
  assert.equal(await affectedCard.locator('.hot-inline-stock').count(), 2);
  assert.match(await affectedCard.locator('.hot-inline-stock').first().innerText(), /中国石油.*利好/s);
  assert.match(await affectedCard.locator('.hot-inline-stock').nth(1).innerText(), /中国国航.*利空/s);
  assert.match(await affectedCard.locator('.hot-inline-stock').first().innerText(), /\+6分/);
  assert.match(await affectedCard.locator('.hot-inline-stock').nth(1).innerText(), /-7分/);
  assert.match(await affectedCard.locator('.hot-attention').innerText(), /重点关注.*3 条热搜/s);
  assert.doesNotMatch(await affectedCard.locator('.hot-watch-section').innerText(), /(?<!\d)0分/);
  assert.doesNotMatch((await affectedCard.locator('.hot-inline-stock').allTextContents()).join(' '), /贵州茅台/);
  assert.match(await affectedCard.locator('.hot-watch-section').innerText(), /另有 1 只自选股暂未发现/);
  assert.equal(inferenceCount, 1);
  assert.equal(await page.locator('.hot-topic-card').first().locator('.hot-inline-stock').count(), 0, 'analysis must stay attached to its own topic');
  await affectedCard.scrollIntoViewIfNeeded();
  await page.screenshot({ path: output + '/board-inline.png', fullPage: true });
  await affectedCard.screenshot({ path: output + '/news-sectors-watchlist.png' });
  await page.locator('.hot-open-impact').nth(1).click();
  const impactDialog = page.getByRole('dialog', { name: '关联与股价影响评分' });
  await impactDialog.getByRole('button', { name: '有关联 2', exact: true }).waitFor();
  assert.equal(await impactDialog.locator('.hot-stock-card').count(), 2);
  await impactDialog.getByRole('button', { name: '利空', exact: true }).click();
  assert.match(await impactDialog.locator('.hot-stock-card').innerText(), /中国国航/);
  await impactDialog.getByRole('button', { name: '全部自选 3', exact: true }).click();
  assert.equal(await impactDialog.locator('.hot-stock-card').count(), 3);
  await impactDialog.locator('.hot-stock-card').first().getByText('查看新闻与主营依据', { exact: true }).click();
  assert.equal(await impactDialog.getByRole('link', { name: '测试主营来源' }).count(), 1);
  assert.doesNotMatch(await impactDialog.innerText(), /涨幅|收益|概率|预测股价/);
  await page.screenshot({ path: output + '/business-impact.png', fullPage: true });
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog').count(), 0);
  assert.equal(await page.evaluate(() => document.body.style.overflow), '');
  await page.locator('.hot-open-impact').nth(1).click();
  await impactDialog.getByRole('button', { name: '有关联 2', exact: true }).waitFor();
  assert.equal(inferenceCount, 1, 'reopening must read saved impact results');
  failImpact = true;
  await impactDialog.getByRole('button', { name: '核对并补全', exact: true }).click();
  await impactDialog.getByRole('status').filter({ hasText: '1 只分析未完成' }).waitFor();
  await impactDialog.getByRole('button', { name: '待核实 / 未完成 1', exact: true }).click();
  assert.match(await impactDialog.locator('.hot-stock-card').innerText(), /校验未完成/);
  await impactDialog.getByRole('button', { name: '分析关联与影响', exact: true }).click();
  await impactDialog.getByRole('button', { name: '有关联 2', exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: output + '/business-impact-mobile.png', fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1440, height: 1050 });
  const matchPanel = page.getByRole('region', { name: '一键匹配新闻与自选股' });
  assert.equal(matchCount, 0, 'visiting does not start a bulk match');
  await page.locator('.hot-match-start').click();
  await matchPanel.getByRole('button', { name: '一键分析进行中…', exact: true }).waitFor();
  assert.equal(await page.locator('.hot-analyze-inline').first().isDisabled(), true);
  await page.reload();
  await matchPanel.getByText('匹配完成，已按自选股汇总相关的新闻。', { exact: true }).waitFor();
  assert.equal(matchCount, 1, 'reloading a running job must not submit another one');
  assert.equal(await matchPanel.locator('.hot-match-stock').count(), 2);
  assert.match(await matchPanel.innerText(), /中国国航/);
  assert.match(await matchPanel.innerText(), /霍尔木兹海峡/);
  assert.match(await matchPanel.innerText(), /利空/);
  await matchPanel.locator('summary').first().click();
  assert.match(await matchPanel.innerText(), /界面测试主营资料/);
  await page.screenshot({ path: output + '/one-click-matches.png', fullPage: true });
  failMatch = true;
  await page.locator('.hot-match-start').click();
  await matchPanel.getByText('匹配完成；部分判断未完成，可点击重试。', { exact: true }).waitFor();
  assert.match(await matchPanel.innerText(), /1 对尚未完成/);
  assert.equal(await matchPanel.locator('.hot-match-stock').count(), 1);
  failMatch = false;
  await page.locator('.hot-match-start').click();
  await matchPanel.getByText('匹配完成，已按自选股汇总相关的新闻。', { exact: true }).waitFor();
  assert.equal(await matchPanel.locator('.hot-match-stock').count(), 2);
  assert.equal(matchCount, 3);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: output + '/board-mobile.png', fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  await page.reload();
  await page.getByRole('heading', { name: '重要热点新闻', exact: true }).waitFor();
  await waitResults(2);
  assert.equal(refreshCount, 1, 'reloading must only read snapshots');
  await page.locator('.hot-topic-card').nth(1).getByText('2 只有关联 · 3/3 只已核对', { exact: true }).waitFor();
  assert.equal(inferenceCount, 3, 'reloading cached inline results must not invoke analysis');
  assert.ok(batchCount > 0);
  assert.deepEqual(errors, []);
  console.log('Hot board UI passed: kept news only, asynchronous refresh and polling, one-click private matching with progress/reload/retry, sector directions, inline watchlist analysis, per-topic cache, filters, source failures, business-impact dialog, opposite directions, private batch read/retry, citations, no price outputs, themes, mobile.');
} finally { await browser.close(); }
