/* Run against an isolated demo DB; needs the existing external Playwright runtime. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const base=process.argv[2]||'http://127.0.0.1:8769';

(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.P109_BROWSER||'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.setDefaultTimeout(8000);
  const errors=[], failed=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400) failed.push([r.status(),new URL(r.url()).pathname]);});
  const action=name=>page.locator(`[data-action="${name}"]`);
  const call=async(path,body)=>{
    const r=body===undefined?await page.request.get(base+path):await page.request.post(base+path,{data:body});
    assert.equal(r.ok(),true,await r.text());return r.json();
  };
  const get=async id=>(await call(`/api/complaints/${id}`));
  const apply=async()=>{
    await action('execute-playbook').click();
    await page.locator('#playbook-dialog').waitFor({state:'hidden'});
    await page.locator('#case-dialog').waitFor();
  };
  const open=async id=>{
    await action('refresh').click();
    await page.getByRole('searchbox',{name:'Поиск обращений'}).fill(id);
    await page.locator(`.queue-row[data-id="${id}"]`).click();
    await page.locator('#case-title').filter({hasText:id}).waitFor();
  };
  try {
    await page.goto(base);
    await action('demo').click();
    await page.getByRole('heading',{name:'17 похожих обращений',exact:true}).waitFor();
    const id=await page.locator('#case-title').innerText();
    await action('confirm').click();
    await action('execute-playbook').waitFor();
    assert.ok(await page.locator('.playbook-actions li').count()>=5);
    if(process.argv[3]) await page.screenshot({path:path.join(process.argv[3],'pulse109-playbook.png')});
    assert.equal((await get(id)).complaint.decision_status,'pending');
    await page.getByRole('button',{name:'Отмена',exact:true}).click();
    assert.equal((await get(id)).complaint.incident_id,null);
    console.log('PASS PLAYBOOK UI 1: preview exposes changes; cancel leaves complaint untouched');

    await action('confirm').click();
    await action('execute-playbook').waitFor();
    await call(`/api/workspace/complaints/${id}/safety`,{quarantine:true});
    await action('execute-playbook').click();
    await page.locator('#playbook-error').filter({hasText:/измен|устар|обнов/i}).waitFor();
    assert.equal((await get(id)).complaint.decision_status,'pending');
    assert.equal((await get(id)).complaint.assigned_operator,null);
    await page.getByRole('button',{name:'Отмена',exact:true}).click();
    await call(`/api/workspace/complaints/${id}/safety`,{quarantine:false});
    await page.locator('#case-dialog .case-footer [data-action="reload-case"]').click();
    await action('confirm').click();
    await apply();
    await page.getByRole('button',{name:'Решение подтверждено',exact:true}).waitFor();
    const saved=await get(id);
    assert.equal(saved.complaint.incident_id,'INC-204');
    assert.ok(saved.events.some(e=>e.event_type==='reply_saved'));
    assert.ok(saved.events.some(e=>e.event_type==='playbook_executed'));
    await action('close').click();
    console.log('PASS PLAYBOOK UI 2: stale preview rejected; fresh confirmation links, assigns and saves reply/audit');

    const faq=await call('/api/workspace/intake',{text:'Как проверить статус обращения?',region_id:'KZ-ALA',language:'ru'});
    await open(faq.id);
    await page.locator('[data-kind="close_faq"]').click();
    await apply();
    await page.getByRole('button',{name:'Обращение завершено',exact:true}).waitFor();
    assert.ok((await get(faq.id)).complaint.resolved_at);
    await action('close').click();
    await page.reload();
    await page.locator('[data-group="resolved"]').click();
    await page.getByRole('searchbox',{name:'Поиск обращений'}).fill(faq.id);
    await page.locator(`.queue-row[data-id="${faq.id}"]`).click();
    await page.locator('[data-kind="reopen_faq"]').click();
    await apply();
    assert.equal((await get(faq.id)).complaint.resolved_at,null);
    console.log('PASS PLAYBOOK UI 3: informational FAQ closes explicitly and can be reopened after page reload');

    await action('close').click();
    await page.locator('[data-group=""]').click();
    const manual=await call('/api/workspace/intake',{text:'Здесь снова проблема, помогите разобраться',region_id:'KZ-ALA',language:'ru'});
    await page.route(`**/complaints/${manual.id}/triage`,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'AI-рекомендация временно недоступна'})}));
    await open(manual.id);
    await page.getByText('AI недоступен',{exact:true}).waitFor();
    await page.getByText('Выбрать категорию вручную',{exact:true}).click();
    await page.getByLabel('Категория оператора',{exact:true}).selectOption('water_supply');
    await action('confirm').click();
    await apply();
    await page.getByRole('button',{name:'Решение подтверждено',exact:true}).waitFor();
    const corrected=await get(manual.id);
    assert.equal(corrected.complaint.topic,'water_supply');
    assert.ok(corrected.events.some(e=>JSON.parse(e.payload).manual_override===true));
    console.log('PASS PLAYBOOK UI 4: AI failure remains visible; manual classification is previewed, confirmed and audited');
    assert.deepEqual(errors,[]);
    assert.equal(failed.length,3); // Stale execute plus two deliberate triage failures (open and post-confirm refresh).
    assert.equal(failed[0][0],409);
    assert.match(failed[0][1],/playbooks\/link_mass_incident\/execute$/);
    assert.ok(failed.slice(1).every(([status,path])=>status===503 && path.endsWith(`/${manual.id}/triage`)));
    console.log('ALL 4 PLAYBOOK UI CHECKS PASSED');
  } catch(err) {
    console.error('Preview failure:',await page.locator('#playbook-content').innerText(),failed,errors);
    throw err;
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
