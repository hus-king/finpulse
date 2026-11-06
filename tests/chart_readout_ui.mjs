import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const {chromium}=await import(process.env.PLAYWRIGHT_CORE_PATH||'/tmp/finpulse-n2-ui-check/node_modules/playwright-core/index.mjs');
const base=process.env.CHART_UI_BASE_URL||'http://202.114.212.116:8000';
const output=process.env.CHART_UI_OUTPUT||'.runtime/readout-wrap-20261106/ui';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
const page=await browser.newPage({viewport:{width:1560,height:1100}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
if(base.includes('127.0.0.1'))await page.route('**/api/**',async route=>{
  const request=route.request();assert.equal(request.method(),'GET');
  const url=new URL(request.url());
  await route.fulfill({response:await route.fetch({url:'http://202.114.212.116:8000'+url.pathname+url.search})});
});
async function check(label){
  await page.waitForFunction(()=>{
    const values=[...document.querySelectorAll('.chart-readout-values')];
    return values.length>0&&values.every(e=>e.clientHeight>0);
  });
  const metrics=await page.locator('.chart-readout').evaluateAll(readouts=>readouts.map(readout=>{
    const values=readout.querySelector('.chart-readout-values'),box=values.getBoundingClientRect();
    const items=[...values.querySelectorAll('.chart-ohlc > span,.chart-readout-value')];
    return {width:values.clientWidth,scrollWidth:values.scrollWidth,height:values.clientHeight,
      clipped:items.filter(item=>{const r=item.getBoundingClientRect();return r.left<box.left-1||r.right>box.right+1||r.bottom>box.bottom+1;}).map(item=>item.textContent)};
  }));
  await page.locator('.technical-chart').screenshot({path:`${output}/${label}.png`});
  assert(metrics.length>0);
  for(const m of metrics){assert(m.scrollWidth<=m.width+1,`${label}: horizontal overflow ${JSON.stringify(m)}`);assert.deepEqual(m.clipped,[],`${label}: truncated readouts`);}
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${label}: page overflow`);
  console.log(label,JSON.stringify(metrics));
}
try{
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:'日K',exact:true}).click();
  const adjustment=page.getByRole('combobox',{name:'复权方式'});
  await adjustment.selectOption('hfq');
  await page.waitForFunction(()=>document.querySelector('.live-quote > .minute-status')?.textContent.includes('后复权日线已就绪'),{},{timeout:120000});
  await check('desktop-long-prices');
  await page.getByRole('button',{name:'选择技术指标',exact:true}).click();
  const picker=page.getByRole('dialog',{name:'技术指标'});
  await picker.getByRole('button',{name:'同时显示',exact:true}).click();
  for(const name of ['MACD','KDJ','DMI'])await picker.getByRole('button',{name,exact:true}).click();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('[data-indicator-pane]').count(),4);
  await check('desktop-multiple-panes');
  for(const width of [768,390,320]){
    await page.setViewportSize({width,height:1000});
    await check(`width-${width}`);
  }
  await page.getByRole('button',{name:'周K',exact:true}).click();
  await page.locator('[data-testid="chart-main-readout"] strong').filter({hasText:'周 K'}).waitFor();
  await check('mobile-weekly');
  await page.getByRole('button',{name:'全屏图表',exact:true}).click();
  await check('mobile-fullscreen');
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'切换到浅色模式',exact:true}).click();
  for(const width of [768,390]){
    await page.setViewportSize({width,height:1000});
    await check(`light-width-${width}`);
  }
  const plot=await page.locator('.technical-chart-canvas').boundingBox();
  await page.mouse.move(plot.x+plot.width/2,plot.y+150);
  await check('light-crosshair');
  assert.deepEqual(errors,[]);
  console.log('PASS: long OHLC/indicator values fit without horizontal scrolling at desktop/tablet/mobile widths, multiple panes, weekly, fullscreen, light/dark themes and crosshair');
}catch(error){console.error('BROWSER_ERRORS',errors);throw error;}finally{await browser.close();}
