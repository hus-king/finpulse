import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const runtime = process.env.PLAYWRIGHT_CORE_PATH || '/home/kislate/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/index.mjs';
const { chromium } = await import(runtime);
const base = process.env.PAPER_UI_BASE_URL || 'http://127.0.0.1:8012';
const output = process.env.PAPER_UI_OUTPUT || '.superpowers/sdd/2026-10-08-paper-trading/screenshots';
await mkdir(output,{recursive:true});
const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || '/opt/google/chrome/chrome',headless:true,args:['--no-sandbox']});
const context = await browser.newContext({viewport:{width:1440,height:1080}});
const page = await context.newPage();
await page.addInitScript(() => { Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true}); });
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const username = 'paper_ui_'+Date.now();
const password = 'Test-password-123!';
async function register(name) {
  await page.getByRole('button',{name:'登录体验模拟盘'}).click();
  await page.getByRole('button',{name:'没有账号？注册普通用户'}).click();
  await page.getByLabel('用户名',{exact:true}).fill(name);
  await page.getByLabel('昵称',{exact:true}).fill('模拟盘测试');
  await page.getByLabel('密码',{exact:true}).fill(password);
  await page.getByLabel('确认密码',{exact:true}).fill(password);
  await page.getByRole('button',{name:'创建账号并登录'}).click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="paper-equity"]')?.textContent.includes('200,000.00'));
}
async function select(code,name,wait=true) {
  await page.getByRole('button',{name:'更换交易股票'}).click();
  await page.getByLabel('搜索交易股票').fill(code);
  await page.getByRole('button',{name:new RegExp(name+'.*'+code)}).click();
  if (wait) await page.waitForFunction(code=>document.querySelector('[data-testid="paper-quote"]')?.getAttribute('data-code')===code,code);
}
async function refresh() {
  await page.getByRole('button',{name:'刷新模拟账户'}).click();
  await page.getByRole('button',{name:'刷新交易报价'}).click();
}
try {
  await context.request.post(base+'/__test/clock',{data:{now:'2026-10-08T10:00:00+08:00',prices:{'000001':10}}});
  await page.goto(base);
  await page.getByRole('navigation').getByRole('button',{name:'模拟盘',exact:true}).click({timeout:5000});
  await register(username);
  assert.match(await page.locator('[data-testid="paper-equity"]').innerText(),/200,000\.00/);
  await select('000001','平安银行');
  await page.getByLabel('交易股数').fill('100');
  await page.getByRole('button',{name:'确认模拟买入',exact:true}).click();
  await page.getByRole('status').filter({hasText:'模拟买入成功'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-testid="paper-cash"]')?.textContent.includes('198,994.99'));
  assert.match(await page.locator('[data-testid="paper-cash"]').innerText(),/198,994\.99/);
  const holding = page.locator('[data-testid="paper-holding-000001"]');
  assert.equal(await holding.getAttribute('data-sellable'),'0');
  await page.getByRole('button',{name:'卖出',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'确认模拟卖出',exact:true}).isDisabled(),true);
  await page.screenshot({path:output+'/paper-dark.png',fullPage:true});
  await page.getByRole('button',{name:'切换到浅色模式'}).click();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
  await page.screenshot({path:output+'/paper-light.png',fullPage:true});
  await context.request.post(base+'/__test/clock',{data:{now:'2026-10-09T10:00:00+08:00',prices:{'000001':10.5}}});
  await refresh();
  await page.waitForFunction(()=>document.querySelector('[data-testid="paper-holding-000001"]')?.getAttribute('data-sellable')==='100');
  await page.getByRole('button',{name:'确认模拟卖出',exact:true}).click();
  await page.getByRole('status').filter({hasText:'模拟卖出成功'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-testid="paper-equity"]')?.textContent.includes('200,039.45'));
  await page.getByRole('button',{name:'买入',exact:true}).click();
  let intercepted = false, rejectedRetry = false, lostId;
  await page.route('**/api/paper/trades',async route=>{
    if (route.request().method()==='POST' && !intercepted) {
      intercepted=true; lostId=route.request().postDataJSON().request_id;
      await route.fetch(); await route.abort('failed');
    } else if (route.request().method()==='POST' && !rejectedRetry) {
      rejectedRetry=true;
      assert.equal(route.request().postDataJSON().request_id,lostId);
      await route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({code:'CSRF_FAILED',detail:'登录状态已更新，请刷新页面。'})});
    } else await route.continue();
  });
  await page.getByRole('button',{name:'确认模拟买入',exact:true}).click();
  await page.getByRole('button',{name:'重试确认成交',exact:true}).waitFor();
  assert.equal(await page.getByLabel('交易股数').isDisabled(),true);
  await page.reload();
  await page.getByRole('navigation').getByRole('button',{name:'模拟盘',exact:true}).click();
  await page.getByRole('button',{name:'重试确认成交',exact:true}).waitFor({timeout:5000});
  await context.request.post(base+'/__test/clock',{data:{now:'2026-10-09T16:00:00+08:00'}});
  const csrfRejected = page.waitForResponse(response=>response.url().endsWith('/api/paper/trades') && response.status()===403);
  await page.getByRole('button',{name:'重试确认成交',exact:true}).click();
  await csrfRejected;
  await page.waitForTimeout(100);
  assert.equal(await page.getByRole('button',{name:'重试确认成交',exact:true}).count(),1,'CSRF rejection must retain the original uncertain request');
  assert.equal(await page.getByLabel('交易股数').isDisabled(),true);
  await page.reload();
  await page.getByRole('navigation').getByRole('button',{name:'模拟盘',exact:true}).click();
  const retried = page.waitForRequest(request=>request.url().endsWith('/api/paper/trades') && request.method()==='POST');
  await page.getByRole('button',{name:'重试确认成交',exact:true}).click();
  assert.equal((await retried).postDataJSON().request_id,lostId);
  await page.getByRole('status').filter({hasText:'模拟买入成功'}).waitFor();
  const history = await (await context.request.get(base+'/api/paper/trades')).json();
  assert.equal(history.items.filter(trade=>trade.request_id===lostId).length,1);
  assert.equal(history.items.length,3);
  await page.unroute('**/api/paper/trades');
  await context.request.post(base+'/__test/clock',{data:{now:'2026-10-09T10:00:00+08:00'}});
  let releaseOld, capturedOld;
  const releaseGate = new Promise(resolve=>{releaseOld=resolve;});
  const capturedGate = new Promise(resolve=>{capturedOld=resolve;});
  let delayed = false;
  await page.route('**/api/paper/account',async route=>{
    if (!delayed) {
      delayed=true;
      const response = await route.fetch(); capturedOld();
      await releaseGate; await route.fulfill({response});
    } else await route.continue();
  });
  await page.getByRole('button',{name:'刷新模拟账户'}).click();
  await capturedGate;
  await page.getByRole('button',{name:'刷新交易报价'}).click();
  await page.getByRole('button',{name:'确认模拟买入',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="paper-cash"]')?.textContent.includes('197,929.43'));
  releaseOld();
  await page.waitForTimeout(300);
  assert.match(await page.locator('[data-testid="paper-cash"]').innerText(),/197,929\.43/);
  await page.unroute('**/api/paper/account');
  await page.route('**/api/paper/quote/600519*',async route=>{
    await new Promise(resolve=>setTimeout(resolve,400));
    await route.continue().catch(()=>{});
  });
  await select('600519','贵州茅台',false);
  await select('688981','中芯国际');
  await page.waitForTimeout(500);
  assert.equal(await page.locator('[data-testid="paper-quote"]').getAttribute('data-code'),'688981');
  assert.equal(await page.getByLabel('交易股数').inputValue(),'200');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),true);
  await page.screenshot({path:output+'/paper-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1080});
  await page.getByRole('button',{name:'打开账号菜单'}).click();
  await page.getByRole('button',{name:'退出登录',exact:true}).click();
  await page.getByRole('button',{name:'登录体验模拟盘'}).waitFor();
  assert.equal(await page.locator('[data-testid="paper-holding-000001"]').count(),0);
  await register(username+'_other');
  assert.match(await page.locator('[data-testid="paper-equity"]').innerText(),/200,000\.00/);
  assert.equal(await page.locator('[data-testid="paper-holding-000001"]').count(),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: 200k, buy/sell, T+1, lost-response replay across CSRF rejection/reload/close, stale account response ordering, stock switching, theme/mobile, account isolation');
} catch(error) {
  await page.screenshot({path:output+'/paper-failure.png',fullPage:true}).catch(()=>{});
  throw error;
} finally { await browser.close(); }
