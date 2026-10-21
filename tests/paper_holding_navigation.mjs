import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const { chromium }=await import(process.env.PLAYWRIGHT_CORE_PATH||'/home/kislate/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/index.mjs');
const base=process.env.PAPER_UI_BASE_URL||'http://127.0.0.1:8012';
const output=process.env.PAPER_NAV_OUTPUT||'.runtime/holding-navigation';await mkdir(output,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',headless:true,args:['--no-sandbox']});
const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
try {
 await context.request.post(base+'/__test/clock',{data:{now:'2026-10-09T10:00:00+08:00',prices:{'000001':10}}});
 await page.goto(base);await page.getByRole('navigation').getByRole('button',{name:'模拟盘',exact:true}).click();
 await page.getByRole('button',{name:'登录体验模拟盘'}).click();await page.getByRole('button',{name:'没有账号？注册普通用户'}).click();
 await page.getByLabel('用户名',{exact:true}).fill('holding_nav_'+Date.now());await page.getByLabel('昵称',{exact:true}).fill('持仓跳转验收');
 await page.getByLabel('密码',{exact:true}).fill('Test-password-123!');await page.getByLabel('确认密码',{exact:true}).fill('Test-password-123!');
 await page.getByRole('button',{name:'创建账号并登录'}).click();
 await page.getByRole('button',{name:'更换交易股票'}).click();await page.getByLabel('搜索交易股票').fill('000001');await page.getByRole('button',{name:/平安银行.*000001/}).click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="paper-quote"]')?.getAttribute('data-code')==='000001');
 await page.getByLabel('交易股数').fill('100');await page.getByRole('button',{name:'确认模拟买入',exact:true}).click();
 await page.getByRole('status').filter({hasText:'模拟买入成功'}).waitFor();
 const watched=(await (await context.request.get(base+'/api/watchlist')).json()).codes;
 const sideEffects=[];page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(watchlist|research|chat|analyze)/.test(r.url()))sideEffects.push(r.url());});
 for(const mode of ['manual','algorithm']) {
  const holding=page.getByTestId('paper-holding-000001');await holding.waitFor();
  if(mode==='algorithm') {
   const pilot=page.getByRole('region',{name:'模拟盘操作方式'});
   await pilot.getByLabel('操作方式',{exact:true}).selectOption('official-multifactor-v1');await pilot.getByRole('button',{name:'启用官方算法',exact:true}).click();
   await pilot.getByText('算法托管中',{exact:true}).waitFor();
  }
  const button=holding.getByRole('button');assert.equal(await button.isEnabled(),true,mode+' holding navigation stays enabled');
  await button.focus();await page.keyboard.press('Enter');
  await page.getByRole('region',{name:'个股行情与新闻'}).waitFor({timeout:2000});
  assert.match(await page.locator('.finance-nav button.active').innerText(),/行情与新闻/);
  await page.waitForFunction(()=>[...document.querySelectorAll('h2,h3')].some(e=>e.textContent.includes('平安银行')),undefined,{timeout:2000});
  assert.deepEqual((await (await context.request.get(base+'/api/watchlist')).json()).codes,watched);
  await page.screenshot({path:output+'/holding-'+mode+'-detail.png',fullPage:true});
  await page.getByRole('navigation').getByRole('button',{name:'模拟盘',exact:true}).click();
 }
 assert.deepEqual(sideEffects,[],'navigation must not add watchlist, collect news or call model');assert.deepEqual(errors,[]);
 console.log('PASS holdings open matching stock detail with keyboard in manual/algorithm mode; watchlist unchanged; no paid API or order side effects');
}finally{await browser.close();}
