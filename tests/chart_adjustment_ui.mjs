import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const runtime=process.env.PLAYWRIGHT_CORE_PATH||'/Users/hesiqi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/index.mjs';
const { chromium }=await import(runtime);
const base=process.env.CHART_UI_BASE_URL||'http://127.0.0.1:5183';
const output=process.env.CHART_UI_OUTPUT||'.runtime/chart-adjustment/screenshots';
await mkdir(output,{recursive:true});
const dates=[];
for(let date=new Date('2026-01-05T12:00:00Z');dates.length<140;date.setUTCDate(date.getUTCDate()+1)) {
  if(date.getUTCDay()>0&&date.getUTCDay()<6)dates.push(date.toISOString().slice(0,10));
}
let extraDay=false,prependHistory=false,tailCloseDelta=0,tailVolumeDelta=0,rawRevision=0;
const raw=()=>{
  const bars=[...dates,...(extraDay?['2026-07-20']:[])].map((date,i)=>({date,open:20+i*.02-.2,close:20+i*.02,high:20+i*.02+.5,low:20+i*.02-.6,volume:10000+i*100}));
  const tail=bars.at(-1);tail.close+=tailCloseDelta;tail.high+=tailCloseDelta;tail.volume+=tailVolumeDelta;
  if(prependHistory)bars.unshift({date:'2011-01-04',open:1,close:1,high:1.1,low:.9,volume:1000});
  return bars;
};
const stocks=[{code:'600519',name:'贵州茅台',asset_type:'stock',exchange:'SH'},
  {code:'300750',name:'宁德时代',asset_type:'stock',exchange:'SZ'},
  {code:'sh000300',symbol:'000300',name:'沪深300',asset_type:'index',exchange:'SH'}]
  .map(stock=>({...stock,initials:stock.code,industry:'隔离测试',price:25,change:1}));
function dashboard(code){return {stock:{...(stocks.find(stock=>stock.code===code)||{...stocks[0],code}),price:25+tailCloseDelta*2},candles:raw(),news:[],
  as_of:`2026-11-06T10:00:0${(extraDay?1:0)+rawRevision}+08:00`,quote:{status:'ok',source:'原始行情',is_realtime:false,as_of_date:raw().at(-1).date,volume_unit:'shares'},
  daily_request:{refreshing:false,error:null,next_poll_seconds:300,market:{label:'测试'}},pipeline:null,
  sentiment:{status:'unavailable',bull:null,bear:null,neutral:null,sample_count:0,keywords:[],posts:[],note:'隔离测试'},backtest:{items:[],note:'隔离测试'}};}
function bundle(code){return {dashboard:dashboard(code),minutes:Object.fromEntries([1,5,15,30,60].map(period=>[String(period),{code,period,
  candles:[{date:'2026-11-06 09:31:00',open:25,close:25,high:25,low:25,volume:1000}],as_of:'2026-11-06 09:31:00',fetched_at:'2026-11-06T09:31:00+08:00',source:'原始分钟',
  status:'ok',refreshing:false,forming:false,is_realtime:false,error:null,next_poll_seconds:300,note:'测试',
  quote:{price:25,change:1,open:25,high:25,low:25,volume:1000},market:{state:'morning',label:'测试时段',is_trading:true,is_trade_day:true,server_time:'2026-11-06T09:31:00+08:00',expected_data_time:'2026-11-06T09:31:00+08:00',calendar_year:2026}}])),status:'ok',errors:{},next_poll_seconds:300};}
const requests=[],errors=[],aborted=[];
let qfqMode='poll',hfqMode='unavailable',releaseOld,delayedStarted=false;
const adjusted=(code,adjustment,status='ready',fixed)=>({code,adjustment,status,
  candles:status==='ready'?raw().map((bar,i,bars)=>{const price=fixed??(code==='300750'?77.77:adjustment==='hfq'?111.11:bar.date==='2011-01-04'?5:40+(i-Number(prependHistory))*.1+.2+(i===bars.length-1?tailCloseDelta*1.6:0));return {...bar,open:price-.2,close:price,high:price+.3,low:price-.3};}):[],
  from_date:raw()[0].date,to_date:raw().at(-1).date,anchor_date:dates[0],factor_through:raw().at(-1).date,source:'隔离复权源',
  message:status==='unavailable'?'复权因子缺失':'',next_poll_seconds:2});
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
const page=await browser.newPage({viewport:{width:1560,height:1100}});
page.on('pageerror',error=>{errors.push(error.message);console.error(error.stack);});
page.on('requestfailed',request=>{if(request.url().includes('/history?adjustment='))aborted.push(request.url());});
await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname;
  let data={};
  if(path==='/api/auth/me')return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({code:'AUTH_REQUIRED',detail:'请登录'})});
  if(path==='/api/stocks')data={items:stocks,total:stocks.length,scope:'隔离测试',warnings:[]};
  else if(path==='/api/health')data={status:'ok',configured:false,model:null,provider:null,tavily_configured:false,scheduler_enabled:false};
  else if(path.startsWith('/api/dashboard/'))data=dashboard(path.split('/').at(-1));
  else if(path==='/api/market/replay-context')data={server_time:'2026-11-06T10:00:00+08:00',completed_through:dates.at(-1),calendar:dates.map(date=>({date,is_trading:true})),benchmark:{candles:raw(),refreshing:false,error:null}};
  else if(path.endsWith('/bundle'))data=bundle(path.split('/')[3]);
  else if(path.endsWith('/history')&&url.searchParams.has('adjustment')) {
    const code=path.split('/')[3],adjustment=url.searchParams.get('adjustment');
    requests.push({code,adjustment,at:Date.now()});
    if(code==='600519'&&adjustment==='qfq'&&qfqMode==='delay') {
      delayedStarted=true;
      await new Promise(resolve=>{releaseOld=resolve;});
      data=adjusted(code,adjustment,'ready',999);
    } else if(adjustment==='qfq'&&qfqMode==='error')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'复权服务暂不可用'})});
    else if(code==='600519'&&adjustment==='qfq'&&qfqMode==='poll') {qfqMode='ready';data=adjusted(code,adjustment,'loading');}
    else data=adjusted(code,adjustment,adjustment==='hfq'?hfqMode:'ready');
  } else if(path.includes('sentiment'))data={status:'unavailable',score:null,level:null,level_name:'暂无数据',summary:'隔离测试',advancing:0,declining:0,flat:0,total:0,turnover_cny:0,updated_at:null,from_date:dates[0],to_date:dates.at(-1),points:[],trading_dates:[],refreshing:false,missing_days:0,message:'测试',source:'测试'};
  else if(path==='/api/market/quotes')data={status:'ok',source:'测试',is_trading:false,as_of:'2026-11-06T09:31:00+08:00',items:{}};
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)}).catch(error=>{if(!route.request().failure())throw error;});
});
const select=()=>page.getByRole('combobox',{name:'复权方式'});
const readout=()=>page.locator('[data-testid="chart-main-readout"]');
async function waitText(locator,text){await locator.filter({hasText:text}).waitFor({timeout:7000});}
async function pickStock(name){await page.locator('.finance-search').click();await page.locator('.catalog-list').getByRole('button',{name:new RegExp(name)}).click();await page.locator('.live-quote h2').filter({hasText:name}).waitFor();}
async function waitUntil(predicate){const deadline=Date.now()+5000;while(!predicate()){if(Date.now()>deadline)throw Error('condition timed out');await page.waitForTimeout(30);}}
try {
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:'日K',exact:true}).waitFor();
  assert.equal(await select().count(),0,'intraday hides adjustment and keeps preference inactive');
  await page.getByRole('button',{name:'日K',exact:true}).click();
  assert.equal(await select().inputValue(),'none');
  await waitText(readout(),'收 22.78');
  assert.equal(requests.length,0,'raw chart never requests adjusted bars');
  const rawQuote=await page.locator('.live-price-row').innerText();
  await select().selectOption('qfq');
  await waitText(page.locator('.chart-empty'),'正在准备前复权');
  assert.equal(await readout().count(),0,'loading must not show raw candles as adjusted');
  await waitText(readout(),'收 54.10');
  assert.ok(requests[1].at-requests[0].at>=1800,'loading polls at two seconds');
  assert.match(await readout().innerText(),/MA5[\s\S]*?53\.90/);
  assert.equal(await page.locator('.live-price-row').innerText(),rawQuote,'adjustment cannot overwrite quotes/OHLC');
  const beforeRange=requests.length;
  await page.getByRole('button',{name:'图表工具',exact:true}).click();
  await page.getByRole('dialog',{name:'图表工具设置'}).getByRole('button',{name:'全部',exact:true}).click();
  assert.match(await page.locator('.chart-context').innerText(),/全部/);
  await page.getByRole('dialog',{name:'图表工具设置'}).getByRole('button',{name:'近 3 月',exact:true}).click();
  await page.keyboard.press('Escape');
  assert.equal(requests.length,beforeRange,'display range stays client-side');
  await page.getByRole('button',{name:'周K',exact:true}).click();
  await waitText(readout(),'开 53.50');
  assert.match(await readout().innerText(),/MA5[\s\S]*?53\.10/,'weekly indicators consume adjusted then aggregated daily bars');
  await page.getByRole('button',{name:'日K',exact:true}).click();
  await waitText(readout(),'收 54.10');
  const beforeRefresh=requests.length;
  await page.getByRole('button',{name:'图表工具',exact:true}).click();
  await page.getByRole('button',{name:'刷新行情',exact:true}).click();
  await waitUntil(()=>requests.length>beforeRefresh);
  await page.keyboard.press('Escape');
  await waitText(readout(),'收 54.10');
  const beforeRollover=requests.length;
  extraDay=true;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await waitUntil(()=>requests.length>beforeRollover);
  await waitText(readout(),'收 54.20');
  const sameDate=raw().at(-1).date,beforeSameDay=requests.length;
  tailCloseDelta=.25;rawRevision++;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await waitUntil(()=>requests.length>beforeSameDay);
  await waitText(readout(),'收 54.60');
  assert.equal(raw().at(-1).date,sameDate,'same-day price update keeps the raw end date');
  assert.equal(await page.locator('.live-price-row > div > strong').innerText(),'25.50','top quote follows the new raw quote, not adjusted price');
  const beforeVolume=requests.length;
  tailVolumeDelta=10000;rawRevision++;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await waitUntil(()=>requests.length>beforeVolume);
  await waitText(readout(),'收 54.60');
  await waitText(page.locator('[data-indicator-pane="VOL"]'),'3.40 万股');
  assert.equal(raw().at(-1).date,sameDate,'volume-only update keeps the end date and prices');
  assert.equal(await page.locator('.live-price-row > div > strong').innerText(),'25.50');
  const stable=requests.length;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForTimeout(250);
  assert.equal(requests.length,stable,'equivalent raw snapshots do not cause a request loop');
  tailCloseDelta=0;tailVolumeDelta=0;rawRevision++;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await waitText(readout(),'收 54.20');
  const beforeBackfill=requests.length,unchangedTail={...raw().at(-1)};
  prependHistory=true;rawRevision++;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await waitUntil(()=>requests.length>beforeBackfill);
  await waitText(page.locator('.live-quote > .minute-status'),'2011-01-04');
  await waitText(readout(),'收 54.20');
  assert.deepEqual(raw().at(-1),unchangedTail,'prepending 2011 history does not change the tail candle');
  await select().selectOption('hfq');
  await waitText(page.locator('.minute-warning'),'复权因子缺失');
  assert.equal(await readout().count(),0,'unavailable cannot fall back to raw K');
  hfqMode='ready';
  await page.getByRole('button',{name:'重试复权数据'}).click();
  await waitText(readout(),'收 111.11');
  qfqMode='error';
  await select().selectOption('qfq');
  await waitText(page.locator('.minute-warning'),'复权服务暂不可用');
  assert.equal(await readout().count(),0);
  qfqMode='ready';await page.getByRole('button',{name:'重试复权数据'}).click();
  await waitText(readout(),'收 54.20');
  await select().selectOption('hfq');await waitText(readout(),'收 111.11');
  qfqMode='delay';delayedStarted=false;
  await select().selectOption('qfq');await waitUntil(()=>delayedStarted);
  await select().selectOption('hfq');await waitText(readout(),'收 111.11');
  releaseOld();await page.waitForTimeout(100);
  assert.doesNotMatch(await readout().innerText(),/999\.00/,'late old-adjustment response must be ignored');
  delayedStarted=false;
  await select().selectOption('qfq');await waitUntil(()=>delayedStarted);
  await pickStock('宁德时代');await waitText(readout(),'收 77.77');
  releaseOld();await page.waitForTimeout(100);
  assert.doesNotMatch(await readout().innerText(),/999\.00/,'late old-symbol response must be ignored');
  assert.ok(aborted.length>=2,'mode and symbol changes abort their pending fetches');
  assert.match(await page.locator('.live-price-row').innerText(),/25\.00/);
  assert.equal(JSON.parse(await page.evaluate(()=>localStorage.getItem('finpulse-chart-preferences'))).adjustment,'qfq');
  await page.locator('.ticker-card').filter({hasText:'沪深300'}).click();
  await page.locator('.live-quote h2').filter({hasText:'沪深300'}).waitFor();
  await page.getByRole('button',{name:'日K',exact:true}).click();
  assert.equal(await select().count(),0,'index hides adjustment despite saved stock preference');
  await waitText(readout(),'收 22.80');
  assert.ok(requests.every(item=>item.code!=='sh000300'));
  qfqMode='ready';await page.reload();
  await page.getByRole('button',{name:'日K',exact:true}).click();
  await waitText(readout(),'收 54.20');assert.equal(await select().inputValue(),'qfq','preference survives reload');
  await page.setViewportSize({width:390,height:844});
  const bounds=await select().boundingBox();
  assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390,'visible mobile adjustment fits viewport');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur());
  await page.locator('.live-quote').screenshot({path:output+'/adjustment-mobile.png'});
  await page.setViewportSize({width:1560,height:1100});
  await page.locator('.live-quote').screenshot({path:output+'/adjustment-dark.png'});
  await page.getByRole('button',{name:'切换到浅色模式'}).click();
  await page.waitForTimeout(350);
  await page.locator('.live-quote').screenshot({path:output+'/adjustment-light.png'});
  await select().selectOption('none');await waitText(readout(),'收 22.80');
  await page.getByRole('button',{name:'图表工具',exact:true}).click();
  await page.getByRole('button',{name:'1 分钟',exact:true}).click();
  await page.keyboard.press('Escape');
  await waitText(readout(),'收 25.00');
  assert.equal(await select().count(),0,'minute K uses its original raw bars');
  await page.getByRole('button',{name:'分时',exact:true}).click();
  assert.equal(await select().count(),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: raw default, 2s polling, adjusted daily/weekly indicators, quote isolation, refresh/date rollover, same-day price/volume and 2011 backfill updates without request loops, unavailable/error retry, stale request races, index/minute hiding, saved preference and mobile/themes');
} catch(error) {
  console.error('BROWSER_ERRORS',errors);
  await page.screenshot({path:output+'/failure.png',fullPage:true}).catch(()=>{});
  throw error;
} finally {releaseOld?.();await browser.close();}
