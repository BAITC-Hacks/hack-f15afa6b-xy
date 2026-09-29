import assert from 'node:assert/strict';

// Run in cua_repl with a tab and the browser's viewport capability.
export async function checkDesignUi(tab, viewport, base) {
  const results=[];
  const pass=message=>results.push(`PASS: ${message}`);
  const fits=()=>tab.playwright.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
  await tab.goto(`${base}/operator#queue`);
  await tab.playwright.locator('.queue-row').first().waitFor({state:'visible'});
  for(const width of [320,390,768,1024,1440]) {
    await viewport.set({width,height:844});
    assert.ok(await fits(),`Queue overflows at ${width}`);
    const metrics=await tab.playwright.evaluate(()=>({
      title:+getComputedStyle(document.querySelector('.row-title')).fontSize.replace('px',''),
      meta:+getComputedStyle(document.querySelector('.row-meta')).fontSize.replace('px',''),
      firstRow:document.querySelector('.queue-row').getBoundingClientRect().top,
    }));
    assert.ok(metrics.title>=14 && metrics.meta>=12,`Small queue text at ${width}`);
    if(width===390) assert.ok(metrics.firstRow<650,`First row too low: ${metrics.firstRow}`);
  }
  pass('Queue fits five widths; readable text; first mobile complaint above 650px');
  await viewport.set({width:320,height:844});
  assert.equal(await tab.playwright.locator('.mobile-appbar').isVisible(),true);
  assert.equal(await tab.playwright.locator('.queue-row.is-recommended').count(),1);
  assert.equal(await tab.playwright.evaluate(()=>getComputedStyle(document.querySelector('.sidebar')).position),'fixed');
  assert.equal(await tab.playwright.evaluate(()=>getComputedStyle(document.querySelector('.queue-controls')).position),'sticky');
  await viewport.set({width:1440,height:900});
  assert.equal(await tab.playwright.locator('.mobile-appbar').isVisible(),false);
  pass('Mobile app bar, bottom navigation, sticky controls and recommended action respond correctly');
  await viewport.set({width:390,height:844});
  const queueCount=await tab.playwright.locator('.queue-row').count();
  await tab.playwright.locator('[data-action="filter"][data-group="urgent"]').press('Enter');
  assert.ok(await tab.playwright.locator('.queue-row').count()<queueCount);
  await tab.playwright.locator('#search').fill('нет-такого-обращения');
  assert.equal(await tab.playwright.getByText('Обращений по этим условиям нет',{exact:true}).isVisible(),true);
  await tab.playwright.locator('[data-action="clear-queue-filters"]').press('Enter');
  assert.equal(await tab.playwright.locator('.queue-row').count(),queueCount);
  assert.equal(await tab.playwright.evaluate(()=>document.activeElement?.id),'search');
  pass('Queue filters and empty-state reset work with keyboard focus restored');
  await tab.playwright.locator('.queue-row').first().press('Enter');
  await tab.playwright.locator('#case-dialog').waitFor({state:'visible'});
  for(const width of [320,390,1440]) {
    await viewport.set({width,height:844});
    assert.ok(await tab.playwright.evaluate(()=>{
      const dialog=document.querySelector('#case-dialog');
      return dialog.scrollWidth<=dialog.clientWidth;
    }),`Complaint dialog overflows at ${width}`);
  }
  await tab.playwright.locator('[data-action="close"]').press('Escape');
  await tab.playwright.locator('#case-dialog').waitFor({state:'hidden'});
  assert.equal(await tab.playwright.locator('#case-dialog').isVisible(),false);
  pass('Complaint detail fits three widths and closes with Escape');
  await tab.playwright.locator('.nav-more summary').press('Enter');
  assert.ok(await tab.playwright.locator('[data-nav="dashboard"]').isVisible());
  await tab.playwright.locator('[data-nav="dashboard"]').press('Enter');
  await tab.playwright.locator('[data-nav="dashboard"][aria-current="page"]').waitFor({state:'attached'});
  assert.equal(await tab.playwright.locator('.nav-more').getAttribute('open'),null);
  pass('Secondary navigation opens by keyboard and closes after selection');
  await tab.goto(`${base}/operator#map`);
  await viewport.set({width:390,height:844});
  await tab.playwright.locator('.public-card').first().waitFor({state:'visible'});
  const count=await tab.playwright.locator('.public-card').count();
  assert.ok(count>0);
  assert.equal(await tab.playwright.locator('.public-map-shell').isVisible(),false);
  assert.ok(await tab.playwright.evaluate(()=>document.querySelector('#public-filters').getBoundingClientRect().top<document.querySelector('.public-layout').getBoundingClientRect().top));
  await tab.playwright.locator('[name="search"]').fill('нет-совпадений-109-test');
  assert.equal(await tab.playwright.locator('.public-card').count(),0);
  assert.match(await tab.playwright.locator('#public-status').innerText(),/сбросьте фильтры/);
  await tab.playwright.locator('[name="search"]').press('Enter');
  assert.equal(await tab.playwright.locator('.public-card').count(),0);
  await tab.playwright.locator('[type="reset"]').press('Enter');
  await tab.playwright.locator('.public-card').first().waitFor({state:'visible'});
  assert.equal(await tab.playwright.locator('.public-card').count(),count);
  assert.equal(await tab.playwright.locator('[type="reset"]').isEnabled(),false);
  assert.equal(await tab.playwright.evaluate(()=>document.activeElement?.getAttribute('name')),'search');
  pass('Map filters precede results; empty search, Enter and keyboard reset work');
  await tab.playwright.locator('[data-map-view="map"]').press('Enter');
  assert.ok(await tab.playwright.locator('.public-map-shell').isVisible());
  assert.equal(await tab.playwright.locator('.public-feed').isVisible(),false);
  await tab.playwright.locator('[data-map-view="list"]').press('Enter');
  await tab.playwright.locator('.public-card').first().press('Enter');
  assert.ok(await tab.playwright.locator('.public-map-shell').isVisible());
  assert.equal(await tab.playwright.locator('.public-card.selected').count(),1);
  pass('Mobile list/map switching and complaint-to-map selection work');
  await tab.goto(`${base}/map`);
  await viewport.set({width:1440,height:900});
  await tab.playwright.locator('.public-card').first().waitFor({state:'visible'});
  for(const width of [320,390,768,1024,1440]) {
    await viewport.set({width,height:900});
    assert.ok(await fits(),`Public map overflows at ${width}`);
  }
  pass('Anonymous public map fits five widths');
  for(const page of ['radar','incidents','dashboard','operators','routing','quarantine']) {
    await tab.goto(`${base}/operator#${page}`);
    await tab.playwright.locator('#main h1').waitFor({state:'visible'});
    for(const width of [390,1440]) {
      await viewport.set({width,height:900});
      assert.ok(await fits(),`${page} overflows at ${width}`);
    }
  }
  pass('Six operator supporting screens fit mobile and desktop');
  return results;
}
