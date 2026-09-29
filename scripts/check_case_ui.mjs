import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {historyEntry,trackingView} from '../static/views.js';

const entry={title:'Ответ',text:'<img src=x>\nВторая строка',at:'2026-09-29T12:41:00Z',actor:'usr-example'};
assert.match(historyEntry({...entry,type:'reply_saved'}),/chat-operator/);
assert.match(historyEntry({...entry,type:'clarification_requested'}),/chat-operator/);
assert.match(historyEntry({...entry,type:'clarification_received'}),/chat-citizen.*Гражданин/);
assert.match(historyEntry({...entry,type:'operator_confirmed'}),/history-event/);
assert.match(historyEntry({...entry,type:'reply_saved'}),/29\.09\.2026, 17:41:00/);
assert.doesNotMatch(historyEntry({...entry,type:'reply_saved'}),/<img/);
const tracking=trackingView({id:'PULSE-TEST',registered_at:entry.at,status:'pending',timeline:[
  {...entry,type:'reply_saved'},{...entry,type:'clarification_received'},{...entry,type:'registered'},
]});
assert.ok(['chat-operator','chat-citizen','history-event'].every(role=>tracking.includes(role)));
console.log('PASS: 7 history checks — operator reply, question, citizen reply, system event, date/time, escaping, citizen tracking');

// Run with a fresh local demo database through the browser's cua_repl runtime.
export async function checkCaseUi(tab, viewport, base, output) {
  const results=[];
  const action=name=>tab.playwright.locator(`#case-dialog [data-action="${name}"]`);
  const close=async()=>{
    await tab.playwright.getByRole('button',{name:'Закрыть карточку',exact:true}).click();
    await tab.playwright.locator('#case-dialog').waitFor({state:'hidden'});
  };
  const open=async id=>{
    if(await tab.url()!==`${base}/#queue`) await tab.goto(`${base}/#queue`);
    await tab.playwright.getByRole('searchbox',{name:'Поиск обращений'}).fill(id);
    await tab.playwright.locator(`.queue-row[data-id="${id}"]`).click();
    await tab.playwright.locator('#case-title').filter({hasText:id}).waitFor({state:'visible'});
  };
  await open('PULSE-2450');
  await viewport.set({width:1440,height:1000});
  assert.ok(await tab.playwright.evaluate(()=>['#case-history .timeline','#reply-text','[data-action="ask-copilot"]'].every(selector=>{
    const node=document.querySelector(selector);
    return node&&node.getBoundingClientRect().height>0&&!node.closest('details:not([open])');
  })));
  const layout=await tab.playwright.evaluate(()=>{
    const box=selector=>document.querySelector(selector).getBoundingClientRect();
    const fields=box('.case-decision'), story=box('.case-story'), ai=box('.case-support'), footer=box('.operator-case .case-footer');
    return {width:innerWidth,threeColumns:fields.right<=story.left&&story.right<=ai.left,footerVisible:footer.bottom<=innerHeight};
  });
  assert.ok(layout.threeColumns&&layout.footerVisible,JSON.stringify(layout));
  assert.equal(await action('confirm').innerText(),'Назначить службу');
  await writeFile(`${output}/operator-standard-desktop.png`,await tab.screenshot({fullPage:false}));
  results.push('PASS: parameters, conversation/history, reply and AI are visible without opening accordions');

  await tab.playwright.getByLabel('Текст ответа',{exact:true}).fill('Проверяем указанную проблему.');
  await tab.playwright.getByLabel('Категория обращения',{exact:true}).selectOption('housing_maintenance');
  await tab.playwright.locator('.case-service').filter({hasText:'Жилищная'}).waitFor({state:'visible'});
  await tab.playwright.locator('[data-priority="urgent"]').click();
  await tab.playwright.locator('[data-action="confirm"]:not([disabled])').waitFor({state:'visible'});
  assert.equal(await tab.playwright.evaluate(()=>document.querySelector('#reply-text').value),'Проверяем указанную проблему.');
  assert.equal(await tab.playwright.locator('[data-priority="urgent"]').getAttribute('aria-pressed'),'true');
  await action('confirm').click();
  await tab.playwright.locator('#playbook-dialog .playbook-actions').waitFor({state:'visible'});
  assert.match(await tab.playwright.locator('.playbook-actions').innerText(),/Жилищ|жиль|ЖКХ/);
  await tab.playwright.getByRole('button',{name:'Отмена',exact:true}).click();
  await tab.playwright.locator('#playbook-dialog').waitFor({state:'hidden'});
  results.push('PASS: category and priority update the service, preserve the reply and require confirmation');

  await action('ask-copilot').click();
  await tab.playwright.locator('#case-ai[aria-busy="false"]').waitFor({state:'visible',timeoutMs:30000});
  assert.equal(await action('ask-copilot').isEnabled(),true);
  assert.equal(await tab.playwright.evaluate(()=>document.querySelector('#reply-text').value),'Проверяем указанную проблему.');
  await tab.playwright.getByRole('button',{name:'Вставить в ответ',exact:true}).click();
  assert.ok(await tab.playwright.evaluate(()=>document.querySelector('#reply-text').value.length>0));
  await tab.playwright.getByLabel('Текст ответа',{exact:true}).fill('Проверка интерфейса: ответ сохранён.');
  await action('save-reply').click();
  await tab.playwright.locator('#case-history').getByText('Проверка интерфейса: ответ сохранён.',{exact:true}).last().waitFor({state:'visible'});
  assert.equal(await tab.playwright.evaluate(()=>document.querySelector('#manual-category').value),'housing_maintenance');
  results.push('PASS: AI button stays visible and reusable; suggested reply inserts, saves and appears in history');

  for(const width of [320,390,768,1280,1440]) {
    await viewport.set({width,height:900});
    const screenshot=await tab.screenshot({fullPage:false});
    assert.ok(await tab.playwright.evaluate(()=>{
      const dialog=document.querySelector('#case-dialog'), footer=document.querySelector('.operator-case .case-footer').getBoundingClientRect();
      return dialog.scrollWidth<=dialog.clientWidth&&document.documentElement.scrollWidth<=innerWidth&&footer.bottom<=innerHeight&&footer.top>=0;
    }),`Overflow or missing footer at ${width}`);
    if(width===390) await writeFile(`${output}/operator-standard-mobile.png`,screenshot);
    if(width===390) {
      await tab.playwright.locator('[data-panel="support"]').click();
      assert.equal(await action('ask-copilot').isVisible(),true);
      await tab.playwright.locator('[data-panel="decision"]').click();
      assert.equal(await tab.playwright.getByLabel('Категория обращения',{exact:true}).isVisible(),true);
      await tab.playwright.locator('[data-panel="story"]').click();
      assert.equal(await tab.playwright.locator('#case-history').isVisible(),true);
    }
  }
  results.push('PASS: both footer actions stay visible at 320, 390, 768, 1280 and 1440px');
  await action('confirm').click();
  await tab.playwright.locator('#playbook-dialog [data-action="execute-playbook"]').click();
  await tab.playwright.locator('#playbook-dialog').waitFor({state:'hidden'});
  await action('confirm').filter({hasText:'Служба назначена'}).waitFor({state:'visible'});
  assert.equal(await action('confirm').isEnabled(),false);
  await tab.playwright.locator('#case-history').getByText('Оператор подтвердил решение',{exact:true}).waitFor({state:'visible'});
  assert.equal(await action('ask-copilot').isVisible(),true);
  results.push('PASS: actual assignment is recorded; the action remains in place as “Служба назначена”');
  await close();

  await open('PULSE-2431');
  assert.equal(await action('confirm').isVisible(),true);
  assert.equal(await action('confirm').isEnabled(),false);
  assert.equal(await action('clarify').isEnabled(),true);
  await action('clarify').click();
  await tab.playwright.locator('#playbook-dialog [data-action="execute-playbook"]').click();
  await tab.playwright.locator('#playbook-dialog').waitFor({state:'hidden'});
  await tab.playwright.getByLabel('Ответ жителя на уточнение',{exact:true}).fill('По адресу Абая 10 со вчерашнего вечера нет воды во всём доме.');
  await action('resume').click();
  await action('confirm').waitFor({state:'visible'});
  assert.equal(await tab.playwright.locator('#case-history').getByText('Получено уточнение',{exact:true}).isVisible(),true);
  results.push('PASS: unknown category explains disabled assignment; clarification and resumed review work');
  await close();

  await open('PULSE-2420');
  await action('quarantine').click();
  await tab.playwright.locator('#playbook-dialog [data-action="execute-playbook"]').click();
  await tab.playwright.locator('#playbook-dialog').waitFor({state:'hidden'});
  await action('restore').filter({hasText:'Вернуть в очередь'}).waitFor({state:'visible'});
  await action('restore').click();
  await tab.playwright.locator('#playbook-dialog .playbook-actions').waitFor({state:'visible'});
  await tab.playwright.getByRole('button',{name:'Отмена',exact:true}).click();
  await tab.playwright.locator('#playbook-dialog').waitFor({state:'hidden'});
  assert.equal(await tab.playwright.locator('#case-history').isVisible(),true);
  results.push('PASS: quarantine and return preview retain visible history and explicit actions');
  await close();
  await open('PULSE-2451');
  await viewport.reset();
  return results;
}
