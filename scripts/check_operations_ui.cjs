/* P0 jury storyline against a real isolated Pulse 109 API. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.argv[2] || 'http://127.0.0.1:8769';

(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.P109_BROWSER||'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(10000);
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error') errors.push(message.text());});
  try {
    await page.goto(base+'/#live');
    await page.locator('[data-action="start-live-microphone"]').waitFor();
    await page.locator('[data-action="start-live-demo"]').click();
    await page.locator('[data-live-category]').filter({hasText:'Водоснабжение'}).waitFor();
    await page.getByText('Весь дом',{exact:true}).waitFor();
    assert.match(await page.locator('[data-live-panel]').innerText(),/Абая 44[\s\S]*нет холодной воды/);
    const livePanel=await page.locator('.live-assist-panel').innerText();
    assert.match(livePanel,/human confirmation required/);
    assert.match(livePanel,/INC-204 · 17 похожих обращений/);
    assert.match(livePanel,/Весь дом/);
    console.log('PASS UI 1: microphone control, live transcript, Laya/fallback signal and incident candidate appear');

    await page.locator('[data-action="apply-live"]').click();
    await page.locator('#case-dialog[open]').waitFor();
    assert.match(await page.locator('#case-dialog').innerText(),/Абая 44[\s\S]*INC-204/);
    await page.locator('[data-action="close"]').click();
    console.log('PASS UI 2: operator Apply creates and opens a pending linked case');

    await page.locator('[data-nav="operations"]').click();
    await page.getByRole('heading',{name:'География текущей ситуации'}).waitFor();
    assert.match(await page.locator('.ops-map-layout aside').innerText(),/local_hex_v1[\s\S]*Cluster detection[\s\S]*Водоснабжение/);
    await page.locator('#operations-map[data-cell-features]').waitFor();
    assert.ok(Number(await page.locator('#operations-map').getAttribute('data-cell-features'))>0);
    await page.locator('[data-action="ops-map-mode"][data-mode="sla_risk"]').click();
    assert.equal(await page.locator('[data-action="ops-map-mode"][data-mode="sla_risk"]').getAttribute('aria-pressed'),'true');
    console.log('PASS UI 3: internal hex map shows hotspot data and mode switching');

    await page.locator('[data-nav="supervisor"]').click();
    await page.locator('.command-cards').waitFor();
    const command=await page.locator('#main').innerText();
    assert.match(command,/Queue[\s\S]*SLA Risk[\s\S]*Operators Online/i);
    assert.match(command,/Staffing actions/);
    assert.match(command,/резервных операторов/);
    await page.getByRole('button',{name:'SIMULATE'}).click();
    await page.locator('[data-simulation-result]').waitFor();
    assert.match(await page.locator('[data-simulation-result]').innerText(),/Best tested scenario[\s\S]*SLA risk/i);
    console.log('PASS UI 4: supervisor forecast, staffing action and what-if result render');

    for(const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`No overflow at ${width}`);
    }
    assert.deepEqual(errors,[]);
    console.log('PASS UI 5: P0 screens fit four breakpoints with no runtime errors');
  } finally {await browser.close();}
  console.log('ALL 5 OPERATIONS UI CHECKS PASSED');
})().catch(error=>{console.error(error);process.exitCode=1;});
