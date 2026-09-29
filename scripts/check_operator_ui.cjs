/* Synthetic browser regression checks; use the same external Playwright as coverage QA. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const baseURL = process.argv[2] || 'http://127.0.0.1:8765';
const html = '\"><img src=x onerror="window.__p109Xss=true"> & <b>Тест</b>';
const complaints = [html, 'Синтетическое обращение: жарық жоқ', 'Синтетическое обращение: нет воды'].map((text, i) => ({
  id: `synthetic-ui-${i}`, text, region_id: 'KZ-AST', data_origin: 'synthetic',
  decision_status: 'pending', topic: null, proposed_topic: null,
}));
const candidate = {
  complaint_id: html, excerpt: html, resolution_text: html,
  decision_status: html, origin: html,
};
const proposal = {topic: 'test_topic', service_id: html, priority: html};
const confirmedComplaint = {...complaints[0], decision_status: 'confirmed', topic: 'test_topic', service_id: 'srv-test', priority: 'normal'};
const queueEnvelope = (items, extra = {}) => ({
  items, page: 1, pages: 1, total: items.length,
  view_counts: {pending: items.length, clarification: 0, confirmed: 0, all: items.length},
  ...extra,
});

async function clickRefreshAndSettle(page, state) {
  const before = state.queueGets.length;
  await page.locator('#queue-refresh').click();
  const deadline = Date.now() + 4000;
  while (state.queueGets.length === before) {
    if (Date.now() > deadline) throw new Error('Queue refresh request was not observed');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  await page.waitForFunction(() => document.getElementById('queue-list').getAttribute('aria-busy') === 'false');
}

async function main() {
  const browser = await chromium.launch({headless: true, channel: process.env.P109_BROWSER || 'msedge'});
  const failures = [];
  let passed = 0;
  async function check(name, run, options = {}) {
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    page.setDefaultTimeout(4000);
    const errors = [];
    const writes = [];
    const state = {complaints: options.complaints || {status: 200, json: queueEnvelope(complaints)}, complaint: options.complaint || {status: 200, json: {complaint: complaints[0]}}, intake: options.intake || {status: 200, json: {id: 'cmp-new'}}, similar: options.similar || {status: 200, json: {candidates: [candidate]}}, classify: options.classify || {status: 200, json: {proposal}}, confirm: options.confirm || {status: 200, json: {complaint: confirmedComplaint}}, queueGets: [], queueUrls: []};
    page.on('pageerror', error => errors.push(error.name));
    await page.route('**/api/**', async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() !== 'GET') writes.push(path);
      let json;
      if (path === '/api/health') json = {mode: 'mock', training_status: 'not_trained'};
      else if (path === '/api/regions') json = {regions: [{id: 'KZ-AST', name_ru: 'Астана', name_kk: 'Астана'}]};
      else if (path === '/api/topics') json = {topics: [{id: 'test_topic', name_ru: html, name_kk: 'Синтетикалық'}]};
      else if (path === '/api/stats') json = {total_complaints: 3, pending_count: 3, confirmed_count: 0, by_topic: {test_topic: 3}};
      else if (path === '/api/complaints') {
        state.queueGets.push(path);
        state.queueUrls.push(request.url());
        const config = state.complaints;
        if (config.delayMs) await new Promise(resolve => setTimeout(resolve, config.delayMs));
        if (config.raw !== undefined) return route.fulfill({status: config.status, contentType: 'application/json', body: config.raw});
        if (typeof config.json === 'function') return route.fulfill({status: config.status, json: config.json(request.url())});
        return route.fulfill({status: config.status, json: config.json});
      } else if (path.endsWith('/similar')) {
        const config = state.similar;
        if (config.delayMs) await new Promise(resolve => setTimeout(resolve, config.delayMs));
        if (config.raw !== undefined) return route.fulfill({status: config.status, contentType: 'application/json', body: config.raw});
        return route.fulfill({status: config.status, json: config.json});
      } else if (path.endsWith('/classify')) {
        const config = state.classify;
        if (config.delayMs) await new Promise(resolve => setTimeout(resolve, config.delayMs));
        if (config.raw !== undefined) return route.fulfill({status: config.status, contentType: 'application/json', body: config.raw});
        return route.fulfill({status: config.status, json: config.json});
      } else if (path.endsWith('/confirm')) {
        const config = state.confirm;
        if (config.delayMs) await new Promise(resolve => setTimeout(resolve, config.delayMs));
        if (config.raw !== undefined) return route.fulfill({status: config.status, contentType: 'application/json', body: config.raw});
        return route.fulfill({status: config.status, json: config.json});
      } else if (path === '/api/intake') {
        const config = state.intake;
        if (config.delayMs) await new Promise(resolve => setTimeout(resolve, config.delayMs));
        if (config.raw !== undefined) return route.fulfill({status: config.status, contentType: 'application/json', body: config.raw});
        return route.fulfill({status: config.status, json: config.json});
      } else if (/^\/api\/complaints\/[^/]+$/.test(path)) {
        const config = state.complaint;
        if (config.delayMs) await new Promise(resolve => setTimeout(resolve, config.delayMs));
        if (config.raw !== undefined) return route.fulfill({status: config.status, contentType: 'application/json', body: config.raw});
        return route.fulfill({status: config.status, json: config.json});
      } else return route.fulfill({status: 503, json: {detail: {error: 'synthetic_test_unavailable'}}});
      return route.fulfill({json});
    });
    try {
      await page.goto(baseURL + '/legacy');
      const waitForItems = options.waitForItems === undefined ? 3 : options.waitForItems;
      if (waitForItems) {
        await page.waitForFunction(count => document.querySelectorAll('#queue-list .queue-item').length === count, waitForItems);
      }
      await run(page, writes, state);
      assert.deepEqual(errors, [], 'No uncaught browser errors');
      assert.equal(await page.evaluate(() => window.__p109Xss), undefined, 'HTML must not execute');
      passed += 1;
      console.log(`PASS OPERATOR: ${name}`);
    } catch (error) {
      failures.push(name);
      console.error(`FAIL OPERATOR: ${name}: ${error.message}`);
    } finally {
      await page.close();
    }
  }
  try {
    await check('queue preview and title preserve literal HTML', async page => {
      const preview = page.locator('#queue-list .queue-item').first().locator('span').first();
      assert.equal(await preview.textContent(), html.substring(0, 55) + '...');
      assert.equal(await preview.getAttribute('title'), html);
      assert.equal(await page.locator('#queue-list img, #queue-list b').count(), 0);
    });

    await check('similar cases and resolutions preserve literal HTML', async page => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.locator('#similar-list .similar-item').waitFor();
      assert.equal((await page.locator('#similar-list').textContent()).split(html).length - 1, 5);
      assert.equal(await page.locator('#similar-list img, #similar-list b').count(), 0);
      assert.equal(await page.locator('#active-text').textContent(), html);
    });

    await check('proposal and topic counters preserve literal HTML', async page => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.locator('#btn-classify').click();
      await page.locator('#proposal-content .proposal-pill').waitFor();
      assert.equal((await page.locator('#proposal-content').textContent()).split(html).length - 1, 3);
      assert.equal(await page.locator('#topics-breakdown span').textContent(), html);
      assert.equal(await page.locator('#proposal-content img, #topics-breakdown img').count(), 0);
    });

    await check('Tab/Enter/Space select native buttons without losing focus or confirming', async (page, writes, state) => {
      const buttons = page.locator('#queue-list button');
      assert.equal(await buttons.count(), 3, 'Every queue item needs a native button');
      await page.locator('#btn-submit-intake').focus();
      for (let i = 0; i < 12; i++) {
        await page.keyboard.press('Tab');
        if (await buttons.first().evaluate(el => el === document.activeElement)) break;
      }
      assert.equal(await buttons.first().evaluate(el => el === document.activeElement), true, 'Queue buttons stay keyboard reachable');
      assert.notEqual(await buttons.first().evaluate(el => getComputedStyle(el).outlineStyle), 'none');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      await page.locator('#similar-list .similar-item').waitFor();
      assert.equal(await buttons.first().evaluate(el => el === document.activeElement), true);
      assert.equal(await buttons.first().getAttribute('aria-pressed'), 'true');
      await page.keyboard.press('Tab');
      assert.equal(await buttons.nth(1).evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Space');
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-1');
      assert.equal(await buttons.nth(1).evaluate(el => el === document.activeElement), true);
      assert.equal(await buttons.first().getAttribute('aria-pressed'), 'false');
      assert.equal(await buttons.nth(1).getAttribute('aria-pressed'), 'true');
      await page.keyboard.press('Shift+Tab');
      assert.equal(await buttons.first().evaluate(el => el === document.activeElement), true);
      const getsBefore = state.queueGets.length;
      await page.locator('#queue-refresh').focus();
      await page.keyboard.press('Enter');
      const deadline = Date.now() + 4000;
      while (state.queueGets.length === getsBefore) {
        if (Date.now() > deadline) throw new Error('Keyboard refresh did not issue a queue GET');
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      assert.deepEqual(writes, [], 'Selection must not submit intake or operator confirmation');
      if (process.argv[3]) await page.locator('.intake-panel').screenshot({path: process.argv[3]});
    });

    await check('loading state is visible until a delayed success renders', async page => {
      const status = page.locator('#queue-status');
      assert.equal(await status.getAttribute('role'), 'status');
      assert.equal(await status.getAttribute('aria-live'), 'polite');
      assert.match(await status.textContent(), /Загружаем очередь/);
      assert.equal(await page.locator('#queue-list').getAttribute('aria-busy'), 'true');
      assert.equal(await page.locator('#queue-list .queue-item').count(), 0);
      await page.waitForFunction(() => document.querySelectorAll('#queue-list .queue-item').length === 3);
      assert.equal(await page.locator('#queue-list').getAttribute('aria-busy'), 'false');
      assert.equal(await status.textContent(), 'Показано обращений: 3');
      assert.equal(await page.locator('#queue-error').isVisible(), false);
    }, {complaints: {status: 200, delayMs: 1200, json: queueEnvelope(complaints)}, waitForItems: 0});

    await check('only the latest overlapping refresh may render', async (page, writes, state) => {
      const stale = {...complaints[0], id: 'synthetic-ui-stale', text: 'Ответ первого запроса'};
      const fresh = {...complaints[1], id: 'synthetic-ui-fresh', text: 'Ответ второго запроса'};
      state.complaints = {status: 200, delayMs: 800, json: queueEnvelope([stale])};
      await page.locator('#queue-refresh').click();
      await page.waitForTimeout(100);
      state.complaints = {status: 200, json: queueEnvelope([fresh])};
      await page.locator('#queue-refresh').click();
      await page.waitForFunction(() => {
        const first = document.querySelector('#queue-list .queue-item');
        return first && first.dataset.complaintId === 'synthetic-ui-fresh';
      });
      await page.waitForTimeout(1000);
      const ids = await page.locator('#queue-list .queue-item').evaluateAll(items => items.map(item => item.dataset.complaintId));
      assert.deepEqual(ids, ['synthetic-ui-fresh']);
      assert.deepEqual(writes, [], 'Refresh must use GET only');
    });

    await check('HTTP failure shows a generic error, never empty queue or zero counts', async page => {
      const errorBox = page.locator('#queue-error');
      await errorBox.waitFor({state: 'visible'});
      assert.match(await errorBox.textContent(), /Не удалось загрузить очередь/);
      assert.equal(await page.locator('#queue-list .empty-state').count(), 0, 'Failure must not look empty');
      assert.equal(await page.locator('#queue-list .queue-item').count(), 0);
      assert.doesNotMatch(await page.locator('#queue-status').textContent(), /[0-9]/);
      assert.equal(await page.locator('#queue-list').getAttribute('aria-busy'), 'false');
      assert.equal(await page.locator('#queue-refresh').isEnabled(), true);
    }, {complaints: {status: 503, json: {detail: {error: 'synthetic_test_unavailable'}}}, waitForItems: 0});

    await check('invalid JSON and invalid envelopes show errors, not empty queue', async (page, writes, state) => {
      const errorBox = page.locator('#queue-error');
      await errorBox.waitFor({state: 'visible'});
      assert.match(await errorBox.textContent(), /Не удалось/);
      assert.equal(await page.locator('#queue-list .empty-state').count(), 0);
      state.complaints = {status: 200, json: {items: 'not-an-array'}};
      await clickRefreshAndSettle(page, state);
      assert.equal(await page.locator('#queue-list .empty-state').count(), 0);
      assert.match(await errorBox.textContent(), /Не удалось/);
      state.complaints = {status: 200, json: {}};
      await clickRefreshAndSettle(page, state);
      assert.equal(await page.locator('#queue-list .empty-state').count(), 0);
      assert.equal(await errorBox.isVisible(), true);
      assert.equal(await page.locator('#queue-list .queue-item').count(), 0);
      assert.deepEqual(writes, []);
    }, {complaints: {status: 200, raw: '{"items": ['}, waitForItems: 0});

    await check('empty queue is only a successful empty list', async page => {
      const empty = page.locator('#queue-list .empty-state');
      await empty.waitFor({state: 'visible'});
      assert.equal(await empty.textContent(), 'Очередь пуста');
      assert.equal(await page.locator('#queue-list .queue-item').count(), 0);
      assert.equal(await page.locator('#queue-error').isVisible(), false);
      assert.equal(await page.locator('#queue-list').getAttribute('aria-busy'), 'false');
      assert.match(await page.locator('#queue-status').textContent(), /Очередь пуста/);
      assert.equal(await page.locator('#queue-refresh').isEnabled(), true);
    }, {complaints: {status: 200, json: queueEnvelope([])}, waitForItems: 0});

    await check('failed refresh keeps rows but marks them stale and leaves selection untouched', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      const topicBefore = await page.locator('#confirm-topic').inputValue();
      state.complaints = {status: 500, json: {detail: 'synthetic_failure'}};
      await clickRefreshAndSettle(page, state);
      const errorBox = page.locator('#queue-error');
      assert.equal(await errorBox.isVisible(), true);
      assert.match(await errorBox.textContent(), /устаре/);
      assert.equal(await page.locator('#queue-list').getAttribute('data-stale'), 'true');
      assert.equal(await page.locator('#queue-list .queue-item').count(), 3);
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-0');
      assert.equal(await page.locator('#confirm-topic').inputValue(), topicBefore);
      assert.deepEqual(writes, []);
    });

    await check('keyboard retry recovers after failure and refresh only reads', async (page, writes, state) => {
      const errorBox = page.locator('#queue-error');
      await errorBox.waitFor({state: 'visible'});
      assert.match(await errorBox.textContent(), /Не удалось загрузить очередь/);
      state.complaints = {status: 200, json: queueEnvelope(complaints)};
      const refresh = page.locator('#queue-refresh');
      await refresh.focus();
      assert.equal(await refresh.evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelectorAll('#queue-list .queue-item').length === 3);
      assert.equal(await errorBox.isVisible(), false);
      assert.equal(await page.locator('#queue-list').getAttribute('data-stale'), null);
      assert.equal(await page.locator('#queue-status').textContent(), 'Показано обращений: 3');
      assert.equal(await refresh.evaluate(el => el === document.activeElement), true);
      assert.deepEqual(writes, []);
    }, {complaints: {status: 503, json: {detail: 'synthetic_failure'}}, waitForItems: 0});

    await check('malformed rows fail safely and keep prior rows stale', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      const topicBefore = await page.locator('#confirm-topic').inputValue();
      state.complaints = {status: 200, json: queueEnvelope([null])};
      await clickRefreshAndSettle(page, state);
      const errorBox = page.locator('#queue-error');
      assert.equal(await errorBox.isVisible(), true);
      assert.match(await errorBox.textContent(), /устаре/);
      assert.equal(await page.locator('#queue-list').getAttribute('data-stale'), 'true');
      assert.equal(await page.locator('#queue-list .queue-item').count(), 3);
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-0');
      assert.equal(await page.locator('#confirm-topic').inputValue(), topicBefore);
      state.complaints = {status: 200, json: queueEnvelope([{id: 'synthetic-ui-x'}])};
      await clickRefreshAndSettle(page, state);
      assert.equal(await errorBox.isVisible(), true);
      assert.equal(await page.locator('#queue-list .queue-item').count(), 3);
      state.complaints = {status: 200, json: queueEnvelope(complaints)};
      await clickRefreshAndSettle(page, state);
      assert.equal(await errorBox.isVisible(), false);
      assert.equal(await page.locator('#queue-list').getAttribute('data-stale'), null);
      assert.equal(await page.locator('#queue-list .queue-item').count(), 3);
      assert.deepEqual(writes, []);
    });

    await check('malformed rows on first load show a generic error, not empty', async page => {
      const errorBox = page.locator('#queue-error');
      await errorBox.waitFor({state: 'visible'});
      assert.match(await errorBox.textContent(), /Не удалось загрузить очередь/);
      assert.equal(await page.locator('#queue-list .empty-state').count(), 0);
      assert.equal(await page.locator('#queue-list .queue-item').count(), 0);
      assert.equal(await page.locator('#queue-list').getAttribute('aria-busy'), 'false');
      assert.doesNotMatch(await page.locator('#queue-status').textContent(), /[0-9]/);
    }, {complaints: {status: 200, json: queueEnvelope([null])}, waitForItems: 0});

    await check('late similar response for a previous selection cannot replace the current one', async (page, writes, state) => {
      state.similar = {status: 200, delayMs: 800, json: {candidates: [{complaint_id: 'cand-a', excerpt: 'Кандидат A', decision_status: 'pending', origin: 'synthetic'}]}};
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForTimeout(100);
      state.similar = {status: 200, json: {candidates: [{complaint_id: 'cand-b', excerpt: 'Кандидат B', decision_status: 'pending', origin: 'synthetic'}]}};
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.locator('#similar-list .similar-item').waitFor();
      await page.waitForTimeout(1000);
      const text = await page.locator('#similar-list').textContent();
      assert.match(text, /Кандидат B/);
      assert.doesNotMatch(text, /Кандидат A/);
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-1');
      assert.deepEqual(writes, []);
    });

    await check('malformed similar response fails visibly, not with stale candidates', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.locator('#similar-list .similar-item').waitFor();
      state.similar = {status: 200, json: {candidates: 'not-an-array'}};
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.waitForFunction(() => document.getElementById('similar-list').textContent.includes('Не удалось'));
      assert.equal(await page.locator('#similar-list .similar-item').count(), 0);
      state.similar = {status: 200, json: {candidates: []}};
      await page.locator('#queue-list .queue-item').nth(2).click();
      await page.waitForFunction(() => document.getElementById('similar-list').textContent.includes('Похожих обращений не найдено'));
      assert.deepEqual(writes, []);
    });

    await check('classification for a replaced selection cannot update the new selection', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      state.classify = {status: 200, delayMs: 800, json: {proposal: {topic: 'test_topic', service_id: 'srv-stale', priority: 'urgent'}}};
      await page.locator('#btn-classify').click();
      assert.equal(await page.locator('#btn-classify').isEnabled(), false);
      await page.waitForTimeout(100);
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-1');
      state.classify = {status: 200, json: {proposal: {topic: 'test_topic', service_id: 'srv-current', priority: 'normal'}}};
      await page.waitForTimeout(1000);
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-1');
      assert.equal(await page.locator('#proposal-content .proposal-pill').count(), 0, 'Stale proposal must not render');
      assert.equal(await page.locator('#confirm-topic').inputValue(), '');
      assert.equal(await page.locator('#confirm-service').inputValue(), '');
      assert.equal(await page.locator('#confirm-priority').inputValue(), '', 'Unknown urgency must not become "normal"');
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/classify']);
    });

    await check('reselecting the original complaint does not revive its stale classification', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      state.classify = {status: 200, delayMs: 600, json: {proposal: {topic: 'test_topic', service_id: 'srv-stale', priority: 'urgent'}}};
      await page.locator('#btn-classify').click();
      await page.waitForTimeout(100);
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      await page.waitForTimeout(900);
      assert.equal(await page.locator('#proposal-content .proposal-pill').count(), 0);
      assert.equal(await page.locator('#confirm-service').inputValue(), '');
      assert.equal(await page.locator('#btn-classify').isEnabled(), true);
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/classify']);
    });

    await check('an older classification cannot unlock the button or overwrite the newer proposal', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      state.classify = {status: 200, delayMs: 400, json: {proposal: {topic: 'test_topic', service_id: 'srv-stale', priority: 'urgent'}}};
      await page.locator('#btn-classify').click();
      await page.waitForTimeout(100);
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-1');
      state.classify = {status: 200, delayMs: 1200, json: {proposal: {topic: 'test_topic', service_id: 'srv-current', priority: 'normal'}}};
      await page.locator('#btn-classify').click();
      await page.waitForTimeout(700);
      assert.equal(await page.locator('#btn-classify').isEnabled(), false, 'Stale completion must not re-enable while the newer request loads');
      assert.equal(await page.locator('#proposal-content .proposal-pill').count(), 0);
      await page.waitForFunction(() => document.querySelector('#proposal-content .proposal-pill') !== null);
      assert.equal(await page.locator('#btn-classify').isEnabled(), true);
      assert.equal(await page.locator('#confirm-service').inputValue(), 'srv-current');
      assert.equal(await page.locator('#confirm-priority').inputValue(), 'normal');
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-1');
    });

    await check('an obsolete classification failure does not alter the current selection', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      state.classify = {status: 500, delayMs: 400, json: {detail: 'synthetic_failure'}};
      await page.locator('#btn-classify').click();
      await page.waitForTimeout(100);
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-1');
      state.classify = {status: 200, json: {proposal: {topic: 'test_topic', service_id: 'srv-current', priority: 'normal'}}};
      await page.waitForTimeout(700);
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-1');
      assert.equal(await page.locator('#proposal-content .proposal-pill').count(), 0, 'Failed stale request must not render');
      assert.equal(await page.locator('#confirm-service').inputValue(), '');
      assert.equal(await page.locator('#btn-classify').isEnabled(), true);
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/classify']);
    });

    await check('a delayed confirmation cannot pull the operator back to the confirmed complaint', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      await page.locator('#confirm-topic').selectOption('test_topic');
      await page.locator('#confirm-service').fill('srv-test');
      await page.locator('#confirm-priority').selectOption('normal');
      state.confirm = {status: 200, delayMs: 800, json: {complaint: confirmedComplaint}};
      await page.locator('#btn-confirm').click();
      await page.waitForTimeout(100);
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-1');
      await page.waitForTimeout(1000);
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-1');
      assert.equal(await page.locator('#confirm-success').isVisible(), true, 'Confirmation outcome must stay visible');
      assert.match(await page.locator('#confirm-success').textContent(), /synthetic-ui-0/);
      assert.equal(await page.locator('#confirm-topic').inputValue(), '');
      assert.equal(await page.locator('#confirm-service').inputValue(), '');
      assert.equal(await page.locator('#confirm-priority').inputValue(), '', 'Unknown urgency must not become "normal" after reselection');
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/confirm']);
    });

    await check('confirming the current complaint still updates card and queue', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      await page.locator('#confirm-topic').selectOption('test_topic');
      await page.locator('#confirm-service').fill('srv-test');
      await page.locator('#confirm-priority').selectOption('normal');
      await page.locator('#btn-confirm').click();
      await page.waitForFunction(() => document.getElementById('active-status-badge').textContent === 'подтверждено');
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-0');
      assert.equal(await page.locator('#confirm-success').isVisible(), true, 'Confirmation feedback must stay visible after the card refresh');
      assert.equal(await page.locator('#queue-list').getAttribute('aria-busy'), 'false');
      assert.equal(await page.locator('#queue-list .queue-item').count(), 3);
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/confirm']);
    });

    await check('malformed confirmation responses fail honestly and keep the selection', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      await page.locator('#confirm-topic').selectOption('test_topic');
      await page.locator('#confirm-service').fill('srv-test');
      await page.locator('#confirm-priority').selectOption('normal');
      state.confirm = {status: 200, json: {}};
      await page.locator('#btn-confirm').click();
      await page.waitForFunction(() => document.getElementById('confirm-error').textContent.includes('некорректный ответ'));
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-0');
      assert.equal(await page.locator('#confirm-success').isVisible(), false);
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/confirm']);
    });

    await check('malformed classification proposals do not touch the interface', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      state.classify = {status: 200, json: {proposal: []}};
      await page.locator('#btn-classify').click();
      await page.waitForTimeout(300);
      assert.equal(await page.locator('#proposal-content .proposal-pill').count(), 0, 'Array-valued proposal must not render');
      assert.match(await page.locator('#proposal-content').textContent(), /Нажмите «Получить рекомендацию»/);
      assert.equal(await page.locator('#btn-classify').isEnabled(), true);
      state.classify = {status: 200, json: {}};
      await page.locator('#btn-classify').click();
      await page.waitForTimeout(300);
      assert.equal(await page.locator('#proposal-content .proposal-pill').count(), 0);
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/classify', '/api/complaints/synthetic-ui-0/classify']);
    });

    await check('intake still reports success when the follow-up fetch fails', async (page, writes, state) => {
      await page.locator('#intake-region').selectOption('KZ-AST');
      await page.locator('#intake-text').fill('Синтетическое обращение для проверки');
      await page.locator('#btn-submit-intake').click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      assert.equal(await page.locator('#intake-error').isVisible(), false);
      state.complaint = {status: 503, json: {detail: 'synthetic_failure'}};
      await page.locator('#intake-region').selectOption('KZ-AST');
      await page.locator('#intake-text').fill('Второе синтетическое обращение');
      await page.locator('#btn-submit-intake').click();
      await page.waitForFunction(() => document.getElementById('intake-error').textContent.includes('автоматический выбор'));
      assert.doesNotMatch(await page.locator('#intake-error').textContent(), /Сетевая ошибка при отправке/);
      assert.equal(await page.locator('#active-id').textContent(), 'synthetic-ui-0');
      assert.equal(await page.locator('#intake-text').inputValue(), '');
      assert.equal(await page.locator('#queue-list .queue-item').count(), 3);
      assert.deepEqual(writes, ['/api/intake', '/api/intake']);
    });

    await check('malformed similar members fail visibly and recover on reselection', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.locator('#similar-list .similar-item').waitFor();
      state.similar = {status: 200, json: {candidates: [null]}};
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.waitForFunction(() => document.getElementById('similar-list').textContent.includes('Не удалось'));
      assert.equal(await page.locator('#similar-list .similar-item').count(), 0);
      state.similar = {status: 200, json: {candidates: [{excerpt: 'candidate without id'}]}};
      await page.locator('#queue-list .queue-item').nth(2).click();
      await page.waitForTimeout(300);
      assert.match(await page.locator('#similar-list').textContent(), /Не удалось/);
      assert.equal(await page.locator('#similar-list .similar-item').count(), 0);
      state.similar = {status: 200, json: {candidates: [candidate]}};
      await page.locator('#queue-list .queue-item').first().click();
      await page.locator('#similar-list .similar-item').waitFor();
      assert.deepEqual(writes, []);
    });

    await check('unknown similar decision fields render as unavailable, not undefined', async (page, writes, state) => {
      state.similar = {status: 200, json: {candidates: [{complaint_id: 'cand-x', excerpt: 'Кандидат без статуса'}]}};
      await page.locator('#queue-list .queue-item').first().click();
      await page.locator('#similar-list .similar-item').waitFor();
      const text = await page.locator('#similar-list').textContent();
      assert.match(text, /Статус: — \| Источник: —/);
      assert.doesNotMatch(text, /undefined/);
      assert.doesNotMatch(text, /null/);
      assert.deepEqual(writes, []);
    });

    await check('confirm is single-submit and restores after failure', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      await page.locator('#confirm-topic').selectOption('test_topic');
      await page.locator('#confirm-service').fill('srv-test');
      await page.locator('#confirm-priority').selectOption('normal');
      state.confirm = {status: 500, delayMs: 600, json: {detail: 'synthetic_failure'}};
      await page.locator('#btn-confirm').click();
      assert.equal(await page.locator('#btn-confirm').isEnabled(), false, 'Confirm must be disabled while in flight');
      await page.locator('#confirm-service').press('Enter');
      await page.locator('#btn-confirm').click({force: true}).catch(() => {});
      await page.waitForFunction(() => document.getElementById('confirm-error').textContent.includes('synthetic_failure'));
      assert.equal(await page.locator('#btn-confirm').isEnabled(), true, 'Confirm must be restored after failure');
      assert.equal(await page.locator('#confirm-topic').inputValue(), 'test_topic', 'Entered topic must survive failure');
      assert.equal(await page.locator('#confirm-service').inputValue(), 'srv-test', 'Entered service must survive failure');
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/confirm'], 'Exactly one confirm POST may fire');
    });

    await check('intake is single-submit and restores after failure', async (page, writes, state) => {
      await page.locator('#intake-region').selectOption('KZ-AST');
      await page.locator('#intake-text').fill('Синтетическое обращение');
      state.intake = {status: 500, delayMs: 600, json: {detail: 'synthetic_failure'}};
      await page.locator('#btn-submit-intake').click();
      assert.equal(await page.locator('#btn-submit-intake').isEnabled(), false, 'Intake must be disabled while in flight');
      await page.locator('#btn-submit-intake').click({force: true}).catch(() => {});
      await page.waitForFunction(() => document.getElementById('intake-error').textContent.includes('synthetic_failure'));
      assert.equal(await page.locator('#btn-submit-intake').isEnabled(), true, 'Intake must be restored after failure');
      assert.equal(await page.locator('#intake-region').inputValue(), 'KZ-AST', 'Entered region must survive failure');
      assert.equal(await page.locator('#intake-text').inputValue(), 'Синтетическое обращение', 'Entered text must survive failure');
      assert.deepEqual(writes, ['/api/intake'], 'Exactly one intake POST may fire');
    });

    await check('failed classification shows a visible error and allows retry', async (page, writes, state) => {
      await page.locator('#queue-list .queue-item').first().click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      state.classify = {status: 500, json: {detail: 'synthetic_failure'}};
      await page.locator('#btn-classify').click();
      const proposalError = page.locator('#proposal-error');
      await proposalError.waitFor({state: 'visible'});
      assert.equal(await proposalError.textContent(), 'Не удалось запросить предложение. Повторите попытку.');
      assert.equal(await page.locator('#btn-classify').isEnabled(), true, 'Classify must be restored after failure');
      state.classify = {status: 200, json: {proposal: {topic: 'test_topic', service_id: 'srv-retry', priority: 'normal'}}};
      await page.locator('#btn-classify').click();
      await page.locator('#proposal-content .proposal-pill').waitFor();
      assert.equal(await proposalError.isVisible(), false, 'Error must clear on successful retry');
      assert.equal(await page.locator('#confirm-service').inputValue(), 'srv-retry');
      assert.deepEqual(writes, ['/api/complaints/synthetic-ui-0/classify', '/api/complaints/synthetic-ui-0/classify']);
    });

    await check('queue views and filters are preserved while opening a card', async (page, writes, state) => {
      await page.locator('[data-queue-view="all"]').click();
      await page.locator('#queue-priority-filter').selectOption('urgent');
      await page.locator('#queue-region-filter').selectOption('KZ-AST');
      const deadline = Date.now() + 4000;
      while (!/view=all/.test(state.queueUrls[state.queueUrls.length - 1] || '') ||
             !/priority=urgent/.test(state.queueUrls[state.queueUrls.length - 1] || '') ||
             !/region_id=KZ-AST/.test(state.queueUrls[state.queueUrls.length - 1] || '')) {
        if (Date.now() > deadline) throw new Error('Filtered queue request was not observed');
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      await page.locator('#queue-list .queue-item').nth(1).click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-1');
      assert.equal(await page.locator('[data-queue-view="all"]').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('#queue-priority-filter').inputValue(), 'urgent');
      assert.equal(await page.locator('#queue-region-filter').inputValue(), 'KZ-AST');
      assert.equal(await page.locator('#queue-page-info').textContent(), 'Страница 1 из 1');
      assert.deepEqual(writes, []);
    });

    await check('next walks the current selection and stops at the end', async (page, writes) => {
      await page.locator('#queue-next').click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-0');
      await page.locator('#queue-next').click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-1');
      await page.locator('#queue-next').click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent === 'synthetic-ui-2');
      assert.equal(await page.locator('#queue-next').isEnabled(), false, 'Next must stop at the last item');
      assert.deepEqual(writes, []);
    });

    await check('pagination requests carry the page and a filter change resets it', async (page, writes, state) => {
      await page.locator('#queue-next-page').click();
      await page.waitForFunction(() => document.getElementById('queue-page-info').textContent === 'Страница 2 из 2');
      assert.match(state.queueUrls[state.queueUrls.length - 1], /page=2/);
      assert.equal(await page.locator('#queue-prev').isEnabled(), true);
      await page.locator('#queue-priority-filter').selectOption('urgent');
      await page.waitForFunction(() => document.getElementById('queue-page-info').textContent === 'Страница 1 из 2');
      assert.match(state.queueUrls[state.queueUrls.length - 1], /page=1/);
      assert.match(state.queueUrls[state.queueUrls.length - 1], /priority=urgent/);
      assert.equal(await page.locator('[data-queue-view="pending"] .queue-count').textContent(), '5');
      assert.deepEqual(writes, []);
    }, {complaints: {status: 200, json: (url) => url.includes('page=2')
      ? {items: complaints.slice(0, 2), page: 2, pages: 2, total: 13, view_counts: {pending: 5, clarification: 1, confirmed: 6, all: 12}}
      : {items: complaints, page: 1, pages: 2, total: 13, view_counts: {pending: 5, clarification: 1, confirmed: 6, all: 12}}}});
  } finally {
    await browser.close();
  }
  assert.deepEqual(failures, [], 'Operator regression checks failed');
  console.log(`ALL ${passed} OPERATOR UI CHECKS PASSED`);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
