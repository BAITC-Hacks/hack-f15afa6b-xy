/* Browser regression for provider-independent Copilot and deterministic fallback. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.argv[2]||'http://127.0.0.1:8769';

(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.P109_BROWSER||'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(10000);
  const action=name=>page.locator(`#case-dialog [data-action="${name}"]`);
  const open=async id=>{
    await page.getByRole('searchbox',{name:'Поиск обращений'}).fill(id);
    await page.locator(`.queue-row[data-id="${id}"]`).click();
    await page.locator('#case-title').filter({hasText:id}).waitFor();
  };
  const close=async()=>{
    await page.getByRole('button',{name:'Закрыть карточку',exact:true}).click();
    await page.locator('#case-dialog').waitFor({state:'hidden'});
  };
  try {
    await page.goto(base);
    await open('PULSE-2450');
    await action('ask-copilot').click();
    await page.locator('#case-ai[aria-busy="false"]').waitFor();
    const ru=await page.locator('#case-ai').innerText();
    assert.match(ru,/ИИ-помощник не настроен[\s\S]*безопасная подсказка/);
    assert.match(ru,/Срок устранения пока не подтверждён/);
    assert.equal(await action('ask-copilot').isEnabled(),true);
    await page.locator('#copilot-feedback-reason').selectOption('not_useful');
    await action('copilot-unhelpful').click();
    await page.locator('#case-ai').getByText('Оценка сохранена',{exact:true}).waitFor();
    await close();
    console.log('PASS UI 1: RU fallback remains actionable and feedback with reason is saved');

    await open('PULSE-2451');
    await action('ask-copilot').click();
    await page.locator('#case-ai[aria-busy="false"]').waitFor();
    assert.match(await page.locator('#case-ai').innerText(),/Өтінішіңіз тіркелді[\s\S]*мерзімі әлі расталған жоқ/);
    await close();
    console.log('PASS UI 2: KK fallback keeps natural Kazakh instead of forcing Russian');

    const photo='data:image/png;base64,'+Buffer.from('\x89PNG\r\n\x1a\nfixture','binary').toString('base64');
    const response=await fetch(base+'/api/workspace/intake',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'На дороге видна большая яма',region_id:'KZ-ALA',language:'ru',photo_data:photo})});
    assert.equal(response.status,201);const created=await response.json();
    await page.reload();await open(created.id);
    const consent=page.getByLabel('Учесть фото как неподтверждённое наблюдение ИИ',{exact:true});
    await consent.check();await action('ask-copilot').click();
    await page.locator('#case-ai[aria-busy="false"]').waitFor();
    assert.equal(await consent.isVisible(),true);
    assert.equal(await page.getByRole('button',{name:'Вставить в ответ',exact:true}).isEnabled(),true);
    console.log('PASS UI 3: image inference is explicit opt-in and provider failure cannot block work');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
