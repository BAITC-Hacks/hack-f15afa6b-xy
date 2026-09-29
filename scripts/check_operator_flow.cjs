/* End-to-end operator flow against the real API and UI, without response interception.
   Scenario: intake -> clarification -> supplement -> resume -> classification -> confirmation,
   verifying queue views and counters through the real server. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const baseURL = process.argv[2] || 'http://127.0.0.1:8765';

async function api(page, path, method = 'GET', data) {
  const options = {method};
  if (data !== undefined) options.data = data;
  const res = await page.request.fetch(`${baseURL}${path}`, options);
  let json = null;
  try { json = await res.json(); } catch (error) { json = null; }
  return {status: res.status(), json};
}

async function stats(page) {
  const {status, json} = await api(page, '/api/stats');
  assert.equal(status, 200);
  return json;
}

async function complaint(page, id) {
  const {status, json} = await api(page, `/api/complaints/${id}`);
  assert.equal(status, 200);
  return json;
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.P109_BROWSER || 'chrome'});
  const page = await browser.newPage({viewport: {width: 1440, height: 1200}});
  page.setDefaultTimeout(8000);
  const failures = [];
  const check = async (name, fn) => {
    try { await fn(); console.log(`PASS FLOW: ${name}`); }
    catch (error) { failures.push(name); console.error(`FAIL FLOW: ${name}: ${error.message}`); }
  };
  try {
    await page.goto(baseURL + '/legacy');
    await page.waitForFunction(() => document.querySelectorAll('#queue-list .queue-item').length > 0);
    const before = await stats(page);

    await check('intake creates a pending complaint without a guessed topic', async () => {
      await page.locator('#intake-region').selectOption('KZ-ALA');
      await page.locator('#intake-text').fill('Заявка без конкретики, помогите разобраться');
      await page.locator('#btn-submit-intake').click();
      await page.waitForFunction(() => document.getElementById('active-id').textContent.startsWith('cmp-'));
      const detail = await complaint(page, await page.locator('#active-id').textContent());
      assert.equal(detail.complaint.decision_status, 'pending');
      assert.equal(detail.complaint.priority, null, 'No priority before any human decision');
      assert.equal(detail.complaint.proposed_priority, null, 'No urgency signal in this text');
    });

    let complaintId = null;
    await check('clarification request marks the complaint and counters', async () => {
      complaintId = await page.locator('#active-id').textContent();
      const pre = await stats(page);
      await page.locator('#btn-request-clarification').click();
      await page.waitForFunction(() => document.getElementById('clarification-success').style.display === 'block');
      const detail = await complaint(page, complaintId);
      assert.equal(detail.complaint.decision_status, 'needs_clarification');
      assert.equal(detail.complaint.priority, null);
      const now = await stats(page);
      assert.equal(now.clarification_count, pre.clarification_count + 1);
      assert.equal(now.pending_count, pre.pending_count - 1);
      assert.equal(now.total_complaints, pre.total_complaints);
    });

    await check('supplement is stored separately and the complaint returns to pending', async () => {
      const supplement = 'Мұнда мусор шығарылмайды, контейнерлер жоқ';
      await page.locator('#clarification-supplement').fill(supplement);
      await page.locator('#btn-save-supplement').click();
      await page.waitForFunction(() => document.getElementById('clarification-response-success').textContent.includes('сохранено'));
      let detail = await complaint(page, complaintId);
      assert.equal(detail.complaint.decision_status, 'needs_clarification');
      const received = detail.events.filter(e => e.event_type === 'clarification_received').at(-1);
      assert.equal(JSON.parse(received.payload).text, supplement);
      assert.equal(detail.complaint.text, 'Заявка без конкретики, помогите разобраться', 'Original text is never rewritten');
      await page.locator('#btn-resume').click();
      await page.waitForFunction(() => document.getElementById('active-status-badge').textContent === 'в ожидании');
      detail = await complaint(page, complaintId);
      assert.equal(detail.complaint.decision_status, 'pending');
      const now = await stats(page);
      assert.equal(now.clarification_count, before.clarification_count, 'Clarification counter returns to baseline');
      assert.equal(now.pending_count, before.pending_count + 1, 'Only the newly created complaint stays pending');
      assert.equal(now.total_complaints, before.total_complaints + 1);
    });

    await check('re-classification uses the clarification and stays a proposal', async () => {
      await page.locator('#btn-classify').click();
      await page.waitForFunction(() => document.querySelector('#proposal-content .proposal-pill') !== null);
      const detail = await complaint(page, complaintId);
      assert.equal(detail.complaint.proposed_topic, 'waste_management', 'Classification must see the supplement');
      assert.equal(detail.complaint.proposed_priority, null, 'No urgency evidence in the supplement');
      assert.equal(detail.complaint.priority, null, 'Proposal must not confirm priority');
      const proposed = detail.events.filter(e => e.event_type === 'classification_proposed').at(-1);
      assert.equal(JSON.parse(proposed.payload).clarification_count, 1);
      assert.equal(await page.locator('#confirm-topic').inputValue(), 'waste_management');
    });

    await check('human confirmation fixes topic and priority, leaving the proposal behind', async () => {
      await page.locator('#confirm-priority').selectOption('normal');
      await page.locator('#btn-confirm').click();
      await page.waitForFunction(() => document.getElementById('active-status-badge').textContent === 'подтверждено');
      const detail = await complaint(page, complaintId);
      assert.equal(detail.complaint.decision_status, 'confirmed');
      assert.equal(detail.complaint.topic, 'waste_management');
      assert.equal(detail.complaint.priority, 'normal');
      assert.equal(detail.complaint.proposed_priority, null);
      const eventOrder = detail.events.map(e => e.event_type);
      assert.deepEqual(eventOrder, [
        'intake',
        'clarification_requested',
        'clarification_received',
        'clarification_resolved',
        'classification_proposed',
        'operator_confirmed',
      ]);
      const now = await stats(page);
      assert.equal(now.confirmed_count, before.confirmed_count + 1);
      assert.equal(now.total_complaints, before.total_complaints + 1);
      assert.equal(now.pending_count, before.pending_count);
    });

    await check('queue view and counters reflect the confirmed complaint', async () => {
      await page.locator('[data-queue-view="confirmed"]').click();
      const now = await stats(page);
      await page.waitForFunction(count => document.getElementById('queue-status').textContent === `Показано обращений: ${count}`, now.confirmed_count);
      const queue = await api(page, '/api/complaints?view=confirmed&page_size=50');
      assert.equal(queue.status, 200);
      const found = queue.json.items.filter(c => c.id === complaintId);
      assert.equal(found.length, 1);
      assert.equal(found[0].priority, 'normal');
      assert.equal(queue.json.view_counts.confirmed, now.confirmed_count);
      assert.equal(queue.json.total, now.confirmed_count);
      await page.locator('#queue-next-page').click();
      await page.waitForSelector(`#queue-list .queue-item[data-complaint-id="${complaintId}"]`);
      const row = page.locator(`#queue-list .queue-item[data-complaint-id="${complaintId}"]`);
      assert.equal(await row.locator('.badge').textContent(), 'подтверждено');
    });
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error(`FAILED: ${failures.join(', ')}`);
    process.exitCode = 1;
  } else {
    console.log('ALL OPERATOR FLOW CHECKS PASSED');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
