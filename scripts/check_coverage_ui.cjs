/* Optional browser QA. Install Playwright outside the repo; see README. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const baseURL = process.argv[2] || 'http://127.0.0.1:8765';
const screenshot = process.argv[3];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.P109_BROWSER || 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.name));
    const ready = async () => {
      await page.locator('#coverage-panel[aria-busy="false"]').waitFor();
      assert.equal(await page.locator('#coverage-error').isVisible(), false);
    };
    const choose = async id => {
      await page.locator('#coverage-region').selectOption(id);
      await ready();
    };
    const apiPattern = '**/api/data-coverage*';
    const original = await (await page.request.get(baseURL + '/api/data-coverage')).json();
    let loadingRoute;
    await page.route(apiPattern, route => { loadingRoute = route; });
    await page.goto(baseURL + '/legacy');
    await page.locator('#coverage-panel[aria-busy="true"]').waitFor();
    assert.equal(await page.locator('#coverage-supplied').textContent(), '—');
    while (!loadingRoute) await page.waitForTimeout(20);
    await loadingRoute.continue();
    await page.unroute(apiPattern);
    await ready();
    assert.equal(await page.locator('#coverage-rows tr').count(), 20);
    assert.equal(await page.locator('#coverage-supplied').textContent(), '7 / 20');
    assert.match(await page.locator('#analytics-heading').textContent(), /синтетического/);
    console.log('PASS UI 1: loading, 20 rows and real-metadata/synthetic boundary');

    await page.locator('#coverage-region').focus();
    await page.keyboard.press('End');
    await ready();
    assert.equal(await page.locator('#coverage-region').inputValue(), 'KZ-SHY');
    assert.equal(await page.locator('#coverage-rows tr').count(), 1);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'coverage-supply');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.locator('#coverage-empty').isVisible(), true);
    console.log('PASS UI 2: keyboard region/filter navigation and empty state');

    await page.locator('#coverage-supply').selectOption('all');
    await choose('KZ-ALA');
    assert.match(await page.locator('#coverage-rows').textContent(), /Нет файла/);
    await choose('KZ-ALM');
    assert.match(await page.locator('#coverage-rows').textContent(), /19\s?912/);
    assert.match(await page.locator('#coverage-rows').textContent(), /Период: не проверен/);
    await choose('KZ-AKM');
    assert.match(await page.locator('#coverage-rows').textContent(), /Кандидат: смысл не проверен/);
    assert.equal(await page.locator('#coverage-missing').textContent(), '13');
    console.log('PASS UI 3: Almaty city/region, audited CSV counts, unverified fields/history');

    await page.route(apiPattern, route => route.fulfill({status: 503, json: {detail: {error: 'coverage_unavailable'}}}));
    await page.locator('#coverage-retry').click();
    await page.locator('#coverage-error').waitFor();
    assert.equal(await page.locator('#coverage-supplied').textContent(), '—');
    assert.equal(await page.locator('#coverage-content').isVisible(), false);
    await page.unroute(apiPattern);
    await page.locator('#coverage-retry').click();
    await ready();
    console.log('PASS UI 4: failure hides stale metrics; retry recovers');

    await page.route(apiPattern, route => route.fulfill({json: {...original, as_of: null}}));
    await page.locator('#coverage-retry').click();
    await page.locator('#coverage-error').waitFor();
    assert.equal(await page.locator('#coverage-supplied').textContent(), '—');
    assert.equal(await page.locator('#coverage-content').isVisible(), false);
    await page.unroute(apiPattern);
    await page.locator('#coverage-retry').click();
    await ready();

    let delayedRoute;
    await page.route(apiPattern, async route => {
      if (route.request().url().includes('KZ-ALA')) delayedRoute = route;
      else await route.continue();
    });
    await page.locator('#coverage-region').selectOption('KZ-ALA');
    await page.locator('#coverage-region').selectOption('KZ-ALM');
    await ready();
    assert.ok(delayedRoute);
    await delayedRoute.fulfill({json: {...original, regions: original.regions.filter(r => r.region_id === 'KZ-ALA')}});
    await page.waitForTimeout(100);
    assert.match(await page.locator('#coverage-rows').textContent(), /19\s?912/);
    await page.unroute(apiPattern);
    console.log('PASS UI 5: late previous-region response cannot overwrite current region');

    await choose('');
    await page.locator('#coverage-supply').selectOption('supplied');
    assert.equal(await page.locator('#coverage-rows tr').count(), 7);
    await page.locator('#coverage-supply').selectOption('missing');
    assert.equal(await page.locator('#coverage-rows tr').count(), 13);
    await page.locator('#coverage-supply').selectOption('all');
    if (screenshot) await page.locator('#coverage-panel').screenshot({path: screenshot});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.locator('#coverage-region').isVisible(), true);
    assert.deepEqual(errors, []);
    console.log('PASS UI 6: supply filters, mobile layout, no uncaught browser errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
