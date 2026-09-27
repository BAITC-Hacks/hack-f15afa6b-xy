/* Real-API browser regression. Run on an isolated synthetic DB, using external Playwright. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const base = process.argv[2] || 'http://127.0.0.1:8769';

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.P109_BROWSER || 'chrome'});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('console', m=>{if(m.type()==='error') errors.push(m.text());});
  const action = name=>page.locator(`[data-action="${name}"]`);
  const open = async id=>{
    await page.getByRole('searchbox',{name:'Поиск обращений'}).fill(id);
    await page.locator(`.queue-row[data-id="${id}"]`).click();
    await page.locator('#case-title').filter({hasText:id}).waitFor();
  };
  const close = async()=>{await action('close').click();await page.locator('#case-dialog').waitFor({state:'hidden'});};
  const apply = async()=>{
    await page.locator('#playbook-dialog').waitFor();
    await action('execute-playbook').click();
    await page.locator('#playbook-dialog').waitFor({state:'hidden'});
  };
  try {
    await page.goto(base);
    await action('demo').waitFor();
    assert.equal(await page.locator('.queue-row').count(),30);
    assert.equal(await page.locator('.queue-row').first().getAttribute('data-id'),'PULSE-2440');
    if(process.argv[3]) await page.screenshot({path:path.join(process.argv[3],'pulse109-inbox.png')});
    await action('demo').click();
    await page.getByRole('heading',{name:'17 похожих обращений',exact:true}).waitFor();
    assert.match(await page.locator('#case-dialog').innerText(),/94%[\s\S]*Айдана К.[\s\S]*Слоты 2\/5 · 60\/150 баллов/);
    const cid=await page.locator('#case-title').innerText();
    if(process.argv[3]) await page.screenshot({path:path.join(process.argv[3],'pulse109-case.png')});
    await action('confirm').click();
    await apply();
    await page.getByRole('button',{name:'Решение подтверждено',exact:true}).waitFor();
    assert.match(await page.locator('#case-dialog').innerText(),/Связано с инцидентом[\s\S]*Слоты 3\/5 · 75\/150 баллов/);
    await action('insert-reply').click();
    assert.match(await page.locator('#reply-text').inputValue(),/INC-204/);
    await action('save-reply').click();
    await page.locator('.timeline').getByText('Ответ сохранён в демо',{exact:true}).nth(1).waitFor();
    await close();
    console.log('PASS UI 1: 30 active Almaty cases; triage, 17 matches, one-click decision and saved reply');

    await open('PULSE-2420');
    assert.match(await page.locator('#case-dialog').innerText(),/Риск 87%[\s\S]*5 одинаковых/);
    await action('quarantine').click();
    await apply();
    await page.getByText('Обращение сохранено в карантине',{exact:true}).waitFor();
    await close();
    await page.locator('[data-nav="quarantine"]').click();
    await page.locator('.queue-row[data-id="PULSE-2420"]').click();
    await action('restore').click();
    await apply();
    await action('clarify').waitFor();
    assert.equal(await action('quarantine').count(),0);
    await close();
    await page.locator('[data-nav="queue"]').click();
    console.log('PASS UI 2: quarantine excludes the record; restore preserves it');

    await open('PULSE-2430');
    assert.equal(await action('confirm').isEnabled(),false);
    await page.locator('[data-topic="sewerage"]').click();
    await page.getByText('Служба канализации',{exact:true}).waitFor();
    assert.equal(await action('confirm').isEnabled(),true);
    await close();
    await open('PULSE-2431');
    await action('clarify').click();
    await apply();
    await page.locator('#clarification-answer').fill('На Абая 44 нет холодной воды');
    await action('resume').click();
    await page.locator('[data-topic="water_supply"]').waitFor();
    assert.equal(await action('confirm').isEnabled(),true);
    await close();
    console.log('PASS UI 3: medium-confidence selection updates routing; low-confidence clarification reanalyzes');

    await page.locator('[data-nav="dashboard"]').click();
    await page.getByRole('heading',{name:'Каждое решение меняет картину'}).waitFor();
    assert.match(await page.locator('#main').innerText(),/Обращения в инцидентах\s*18/);
    await page.locator('[data-nav="operators"]').click();
    await page.locator('.operator-card').first().waitFor();
    assert.equal(await page.locator('.operator-card').count(),7);
    await page.locator('[data-nav="incidents"]').click();
    await action('incident').click();
    await page.locator('.member').first().waitFor();
    assert.equal(await page.locator('.member').count(),18);
    await close();
    console.log('PASS UI 4: dashboard, operator load and incident members update');

    await page.locator('[data-nav="citizen"]').click();
    await page.locator('#citizen-city').waitFor();
    assert.equal(await page.locator('#citizen-city option').count(),1);
    await page.locator('#citizen-region').selectOption('KZ-AKM');
    assert.equal(await page.locator('#citizen-city option').count(),11);
    assert.match(await page.locator('#citizen-city').innerText(),/Атбасар/);
    await page.locator('#citizen-region').selectOption('KZ-ALA');
    await page.getByLabel('Район (необязательно)',{exact:true}).fill('Алмалинский');
    await page.getByLabel('Район (необязательно)',{exact:true}).press('Tab');
    await action('subscribe').click();
    await page.getByRole('button',{name:'✓ Подписка сохранена в демо',exact:true}).waitFor();
    await page.getByLabel('Что произошло?',{exact:true}).fill('Абай 90 үйде су жоқ. <img src=x onerror="alert(1)">');
    await page.getByLabel('Язык обращения',{exact:true}).selectOption('kk');
    assert.match(await page.getByLabel('Что произошло?',{exact:true}).inputValue(),/Абай 90/);
    await page.getByRole('button',{name:'Зарегистрировать обращение',exact:true}).click();
    await page.locator('#receipt').getByText('✓ Обращение зарегистрировано',{exact:true}).waitFor();
    await page.locator('.tracking-card').waitFor();
    const citizenId=await page.locator('.tracking-card h3').innerText();
    assert.match(await page.locator('#tracking-result').innerText(),/Ожидает решения оператора/);
    assert.equal(await page.locator('#receipt [data-action="open"]').count(),0);
    await page.reload();
    await page.locator('#tracking-id').waitFor();
    assert.equal(await page.locator('#tracking-id').inputValue(),citizenId);
    await page.locator('#tracking-form').getByRole('button',{name:'Проверить статус',exact:true}).click();
    await page.locator('.tracking-card h3').filter({hasText:citizenId}).waitFor();
    await page.locator('[data-nav="queue"]').click();
    await open(citizenId);
    await page.locator('.message').filter({hasText:'<img src=x'}).waitFor();
    assert.equal(await page.locator('.message img').count(),0);
    await close();
    console.log('PASS UI 5: subscription, KK intake, persistent receipt, language change preserves draft, HTML renders literally');

    await open('PULSE-2430');
    await action('clarify').click();
    await apply();
    await page.locator('#clarification-answer').waitFor();
    await close();
    await page.locator('[data-nav="citizen"]').click();
    const track=async id=>{
      await page.getByLabel('Номер обращения',{exact:true}).fill(id);
      await page.locator('#tracking-form').getByRole('button',{name:'Проверить статус',exact:true}).click();
      await page.locator('.tracking-card h3').filter({hasText:id.toUpperCase()}).waitFor();
    };
    await page.getByLabel('Что произошло?',{exact:true}).fill('Черновик нового обращения');
    await track('pulse-2430');
    assert.match(await page.locator('#tracking-result').innerText(),/Нужно уточнение[\s\S]*Вопрос оператора/);
    await track(cid.toLowerCase());
    assert.match(await page.locator('#tracking-result').innerText(),/Решение оператора подтверждено[\s\S]*INC-204[\s\S]*Ответ оператора · демо/);
    assert.equal(await page.getByLabel('Что произошло?',{exact:true}).inputValue(),'Черновик нового обращения');
    assert.equal(await page.locator('#tracking-result [data-action="confirm"]').count(),0);
    for(const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`Tracking fits at ${width}`);
    }
    if(process.argv[3]) {
      await page.setViewportSize({width:1440,height:1100});
      await page.locator('#toast').waitFor({state:'hidden',timeout:8000});
      await page.evaluate(()=>scrollTo(0,0));
      await page.screenshot({path:path.join(process.argv[3],'pulse109-tracking.png'),fullPage:true});
    }
    console.log('PASS UI 6: live citizen tracking shows clarification, confirmed service, incident and reply without losing the draft');

    await page.locator('[data-nav="queue"]').click();
    for(const width of [1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`No overflow at ${width}`);
    }
    await page.setViewportSize({width:390,height:844});
    await open(cid);
    assert.equal(await page.evaluate(()=>document.querySelector('#case-dialog').scrollWidth<=document.querySelector('#case-dialog').clientWidth),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#case-dialog').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log('PASS UI 7: five breakpoints, mobile dialog, Escape, no console/runtime errors');
  } catch(err) {
    console.error('UI failure:',await page.locator('#tracking-result').allTextContents(),errors);
    throw err;
  } finally {await browser.close();}
  console.log('ALL 7 DEMO UI CHECKS PASSED');
})().catch(e=>{console.error(e);process.exitCode=1;});
