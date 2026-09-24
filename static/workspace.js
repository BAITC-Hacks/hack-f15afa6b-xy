import {esc, button, badge, groups, time, queueView, dashboardView, incidentsView, operatorsView, citizenView, trackingView} from './views.js';
import {caseView} from './case.js';

const state={page:'queue',group:'',search:'',region:'KZ-ALA',items:[],incidents:[],operators:[],topics:[],metrics:{},detail:null,trackingId:'',tracking:null};
try {state.trackingId=localStorage.getItem('pulse109-last-receipt')||'';} catch { /* Lookup also works without browser storage. */ }
const main=document.querySelector('#main'), dialog=document.querySelector('#case-dialog'), content=document.querySelector('#case-content');
const playbookDialog=document.querySelector('#playbook-dialog'), playbookContent=document.querySelector('#playbook-content');
let loadVersion=0, caseVersion=0, trackingVersion=0, previewVersion=0, preview=null, toastTimer;
const titles={queue:'Обращения',incidents:'Инциденты',dashboard:'Аналитика',operators:'Команда',quarantine:'Карантин',citizen:'Кабинет гражданина'};
async function api(path, data) {
  let response;
  try {response=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});}
  catch {throw new Error('Сервер недоступен. Проверьте подключение и повторите действие.');}
  const result=await response.json().catch(()=>({detail:'Не удалось получить ответ сервера. Обновите карточку и повторите действие.'}));
  if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'Не удалось выполнить действие. Проверьте поля и повторите.');
  return result;
}
function toast(text, error=false) {
  const node=document.querySelector('#toast');
  node.textContent=text; node.className=error?'error':''; node.hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>node.hidden=true,6000);
}
function render() {
  state.page=titles[location.hash.slice(1)]?location.hash.slice(1):'queue';
  document.querySelectorAll('[data-nav]').forEach(a=>{
    a.classList.toggle('active',a.dataset.nav===state.page);
    if(a.dataset.nav===state.page) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  document.querySelector('#breadcrumb').textContent=titles[state.page];
  document.querySelector('#nav-count').textContent=state.metrics.pending;
  document.querySelector('#incident-count').textContent=state.metrics.active_incidents;
  document.querySelector('#quarantine-count').textContent=state.metrics.quarantined;
  const views={queue:queueView,quarantine:queueView,dashboard:dashboardView,incidents:incidentsView,operators:operatorsView,citizen:citizenView};
  main.innerHTML=views[state.page](state);
}
async function refresh(renderPage=true) {
  const version=++loadVersion;
  const [queue,incidents,operators,metrics]=await Promise.all([
    api('/api/workspace/queue'),api('/api/workspace/incidents'),api('/api/workspace/operators'),api('/api/workspace/metrics')]);
  if(version!==loadVersion) return;
  Object.assign(state,{items:queue.items,incidents:incidents.items,operators:operators.items,metrics});
  if(renderPage) render();
}
async function openCase(id) {
  const version=++caseVersion;
  const [analysis,history,playbooks]=await Promise.all([api(`/api/workspace/complaints/${encodeURIComponent(id)}/triage`,{}).catch(error=>({error})),api(`/api/complaints/${encodeURIComponent(id)}`),api(`/api/workspace/complaints/${encodeURIComponent(id)}/playbooks`).catch(error=>({items:[],error:error.message}))]);
  if(version!==caseVersion) return;
  const detail=analysis.error?manualDetail(history.complaint,analysis.error.message):analysis;
  detail.events=history.events;
  detail.playbooks=playbooks.items;
  detail.playbookError=playbooks.error;
  detail.selectedTopic=detail.complaint.topic||(detail.triage.confidence_band==='high'?detail.triage.category:null);
  detail.selectedPriority=detail.complaint.priority||detail.triage.urgency;
  state.detail=detail;
  content.innerHTML=caseView(detail,state.topics);
  if(!dialog.open) dialog.showModal();
}
function manualDetail(c, message) {
  return {complaint:c,triage:{unavailable:true,category:null,urgency:null,confidence_band:'low',alternatives:[],extracted_address:c.address,scope:'Не уточнён',onset:'Не уточнено',summary:c.text,reasoning_short:message,service_name:'Выберите категорию вручную',clarification_question:'Уточните адрес и что произошло.'},
    similar:[],incident_candidate:null,routing:{operator:null,reason:'Выберите категорию для проверки маршрута.'},risk:{reasons:[]},flags:[],group:c.resolved_at?'resolved':c.quarantined?'quarantine':c.decision_status==='confirmed'?'awaiting_service':c.decision_status==='needs_clarification'?'awaiting_citizen':'attention',
    sla_remaining:null,priority_score:0,priority_factors:{urgency:0,sla_risk:0,waiting:0,incident:0,review:0},suggested_response:'Ваше обращение зарегистрировано. Оператор проверит информацию.'};
}
function rerenderCase() {
  const draft=document.querySelector('#reply-text')?.value;
  const check=document.querySelector('#include-incident')?.checked;
  content.innerHTML=caseView(state.detail,state.topics);
  if(draft && document.querySelector('#reply-text')) document.querySelector('#reply-text').value=draft;
  if(check!==undefined && document.querySelector('#include-incident')) document.querySelector('#include-incident').checked=check;
}
async function mutate(path, data, message) {
  const id=state.detail.complaint.id;
  const version=caseVersion;
  await api(`/api/workspace/complaints/${encodeURIComponent(id)}/${path}`,data);
  await refresh();
  if(dialog.open && version===caseVersion) await openCase(id);
  toast(message);
}
async function trackCase(id) {
  const version=++trackingVersion, result=document.querySelector('#tracking-result');
  state.tracking=null;
  result.textContent='Проверяем статус…';
  try {
    const data=await api(`/api/workspace/tracking/${encodeURIComponent(id.trim())}`);
    if(version!==trackingVersion || !result.isConnected) return;
    state.tracking=data; state.trackingId=data.id;
    try {localStorage.setItem('pulse109-last-receipt',data.id);} catch { /* The number remains visible in the receipt. */ }
    document.querySelector('#tracking-id').value=data.id;
    result.innerHTML=trackingView(data);
  } catch(err) {
    if(version===trackingVersion && result.isConnected) result.textContent=err.message;
  }
}
async function showPlaybook(kind, extra={}) {
  const d=state.detail, cid=d.complaint.id, version=++previewVersion;
  const request={topic:d.selectedTopic,priority:d.selectedPriority,manual:!!d.manual,...extra};
  preview={cid,kind,request};
  playbookContent.innerHTML=`<div class="case-header"><h2 id="playbook-title">Проверяем действия…</h2>${button('cancel-playbook','×','icon-button','aria-label="Отменить сценарий"')}</div><div class="playbook-body"><p role="status">Проверяем состояние обращения и доступность назначения.</p><p id="playbook-error" role="alert"></p>${button('refresh-playbook','Повторить загрузку','ghost')}</div>`;
  if(!playbookDialog.open) playbookDialog.showModal();
  try {
    const data=await api(`/api/workspace/complaints/${encodeURIComponent(cid)}/playbooks/${kind}/preview`,request);
    if(version!==previewVersion || !playbookDialog.open) return;
    preview={cid,kind,request,...data};
    playbookContent.innerHTML=`<div class="case-header"><div><span class="eyebrow">СЦЕНАРИЙ · ${esc(cid)}</span><h2 id="playbook-title">${esc(data.title)}</h2></div>${button('cancel-playbook','×','icon-button','aria-label="Отменить сценарий"')}</div><div class="playbook-body"><p class="micro">Проверьте изменения. Они будут сохранены вместе только после вашего подтверждения.</p><ol class="playbook-actions">${data.actions.map(a=>`<li><span aria-hidden="true">✓</span><div><strong>${esc(a.label)}</strong><p>${esc(a.value)}</p></div></li>`).join('')}</ol><p id="playbook-error" class="danger-text" role="alert">${esc(data.blocked_reason||'')}</p><p class="micro">Синтетическое демо · сообщения сохраняются в истории, внешняя отправка не подключена.</p></div><div class="case-footer">${button('cancel-playbook','Отмена','ghost')}${button('refresh-playbook','Обновить preview','ghost')}${button('execute-playbook',`Подтвердить ${data.actions.length} действий`,'primary',data.can_execute?'':'disabled')}</div>`;
  } catch(err) {
    if(version===previewVersion && playbookDialog.open) document.querySelector('#playbook-error').textContent=err.message;
  }
}
async function handleAction(node) {
  const action=node.dataset.action, d=state.detail, c=d?.complaint;
  if(action==='cancel-playbook') {previewVersion++;playbookDialog.close();return;}
  if(action==='refresh-playbook') {await showPlaybook(preview.kind,preview.request);return;}
  if(action==='playbook') {await showPlaybook(node.dataset.kind);return;}
  if(action==='execute-playbook') {
    const p=preview, version=caseVersion;
    try {
      await api(`/api/workspace/complaints/${encodeURIComponent(p.cid)}/playbooks/${p.kind}/execute`,{preview_token:p.preview_token});
    } catch(err) {
      document.querySelector('#playbook-error').textContent=err.message;
      node.dataset.action='blocked-playbook'; node.setAttribute('aria-disabled','true');
      return;
    }
    previewVersion++;playbookDialog.close();
    await refresh();if(dialog.open && version===caseVersion) await openCase(p.cid);
    toast('Сценарий применён. Изменения и аудит сохранены.');return;
  }
  if(action==='close') {caseVersion++;dialog.close();return;}
  if(action==='refresh') {await refresh();toast('Данные обновлены');return;}
  if(action==='open') {await openCase(node.dataset.id);return;}
  if(action==='filter') {state.group=node.dataset.group;render();return;}
  if(action==='demo') {
    const data=await api('/api/workspace/intake',{text:'Добрый день, на Абая 44 с утра нет воды, весь дом без воды, когда включат?',region_id:'KZ-ALA',district:'Алмалинский',language:'ru',channel:'web'});
    location.hash='queue'; await refresh(); await openCase(data.id); toast('Новое обращение поступило · анализ готов');return;
  }
  if(action==='incident') {
    const i=state.incidents.find(x=>x.id===node.dataset.id);
    content.innerHTML=`<div class="case-header"><div><span class="eyebrow">ИНЦИДЕНТ ${esc(i.id)}</span><h2 id="case-title">${esc(i.title)}</h2></div>${button('close','×','icon-button','aria-label="Закрыть инцидент"')}</div><div class="incident-detail">${badge(i.status,'green')}<p>${i.count} обращений · ${i.streets} улицы · ${esc(i.service_name)}</p><p>Первое обращение ${i.minutes} мин назад · Следующее обновление демо-статуса ${time(i.next_update)}</p><h3>Связанные обращения</h3><div class="incident-members">${i.members.map(m=>button('open',`<strong>${esc(m.id)}</strong><span>${esc(m.text)}</span> →`,'member',`data-id="${esc(m.id)}"`)).join('')}</div></div>`;
    if(!dialog.open) dialog.showModal();return;
  }
  if(action==='subscribe') {
    let subscriber=sessionStorage.getItem('pulse-demo-subscriber');
    if(!subscriber) {subscriber=crypto.randomUUID();sessionStorage.setItem('pulse-demo-subscriber',subscriber);}
    await api(`/api/workspace/incidents/${encodeURIComponent(node.dataset.id)}/subscribe`,{subscriber_key:subscriber});
    node.textContent='✓ Подписка сохранена в демо';node.dataset.action='subscribed';toast('Демо-подписка сохранена. Новое обращение не создано.');return;
  }
  if(action==='subscribed') {toast('Подписка уже сохранена в демо');return;}
  if(action==='anyway') {document.querySelector('#citizen-text').focus();return;}
  if(action==='track') {await trackCase(node.dataset.id);document.querySelector('#tracking-result')?.scrollIntoView({block:'nearest'});return;}
  if(action==='reload-case') {await openCase(c.id);return;}
  if(action==='category') {
    d.selectedTopic=node.dataset.topic;
    const topic=d.selectedTopic, version=caseVersion;
    const routing=await api(`/api/workspace/complaints/${encodeURIComponent(c.id)}/routing?topic=${encodeURIComponent(topic)}`);
    if(version!==caseVersion || d.selectedTopic!==topic || !dialog.open) return;
    d.routing=routing.routing; d.triage.service_name=routing.service_name; rerenderCase();return;
  }
  if(action==='priority') {d.selectedPriority=node.dataset.priority;rerenderCase();return;}
  if(action==='link') {await showPlaybook('link_mass_incident',{incident_id:node.dataset.id});return;}
  if(action==='separate') {await showPlaybook('unlink_incident');return;}
  if(action==='quarantine'||action==='restore') {await showPlaybook(action);return;}
  if(action==='confirm') {
    const include=document.querySelector('#include-incident')?.checked;
    await showPlaybook(include?'link_mass_incident':'route_service',include?{incident_id:d.incident_candidate.id}:{});return;
  }
  if(action==='clarify') {
    await showPlaybook('request_clarification',{question:d.triage.clarification_question});return;
  }
  if(action==='resume') {
    const version=caseVersion;
    const text=document.querySelector('#clarification-answer').value.trim();
    if(!text) throw new Error('Введите полученное уточнение');
    await api(`/api/complaints/${encodeURIComponent(c.id)}/clarification-response`,{text});
    await api(`/api/complaints/${encodeURIComponent(c.id)}/resume`,{});
    await refresh();if(dialog.open && version===caseVersion) await openCase(c.id);toast('Уточнение сохранено; анализ обновлён');return;
  }
  if(['insert-reply','edit-reply','reject-reply'].includes(action)) {
    const field=document.querySelector('#reply-text');
    if(!field) {toast('Сначала дождитесь уточнения гражданина');return;}
    field.value=action==='reject-reply'?'':d.suggested_response;
    field.focus(); if(action==='edit-reply') field.select();
    if(action==='reject-reply') toast('Шаблон отклонён. Можно написать свой ответ.');return;
  }
  if(action==='save-reply') {
    const text=document.querySelector('#reply-text').value.trim();
    if(!text) throw new Error('Вставьте шаблон или введите ответ');
    await mutate('reply',{text},'Ответ сохранён в истории демо');return;
  }
}
document.addEventListener('click',async e=>{
  const node=e.target.closest('[data-action]'); if(!node||node.disabled) return;
  node.disabled=true;
  try {await handleAction(node);} catch(err) {toast(err.message,true);} finally {if(node.isConnected && node.getAttribute('aria-disabled')!=='true') node.disabled=false;}
});
document.addEventListener('input',e=>{
  if(e.target.id!=='search') return;
  state.search=e.target.value;
  const position=e.target.selectionStart;
  render(); const input=document.querySelector('#search');input.focus();
  input.setSelectionRange(position,position);
});
document.addEventListener('change',async e=>{
  if(e.target.id==='queue-region') {state.region=e.target.value;render();}
  if(e.target.id==='citizen-district') document.querySelector('#citizen-notice').hidden=e.target.value!=='Алмалинский';
  if(e.target.id==='manual-category' && e.target.value) {
    state.detail.manual=true;
    if(!state.detail.selectedPriority) state.detail.selectedPriority='normal';
    try {await handleAction({dataset:{action:'category',topic:e.target.value}});} catch(err) {toast(err.message,true);}
  }
});
document.addEventListener('submit',async e=>{
  if(e.target.id==='tracking-form') {
    e.preventDefault();
    const id=new FormData(e.target).get('complaint_id').trim();
    if(id) await trackCase(id); else toast('Введите номер обращения',true);
    return;
  }
  if(e.target.id!=='citizen-form') return;
  e.preventDefault(); const form=e.target, submit=form.querySelector('[type=submit]');submit.disabled=true;
  try {
    const data=new FormData(form), text=data.get('text').trim();
    if(!text) throw new Error('Опишите проблему');
    const result=await api('/api/workspace/intake',{text,district:data.get('district'),language:data.get('language'),region_id:'KZ-ALA',channel:'web'});
    state.trackingId=result.id;
    try {localStorage.setItem('pulse109-last-receipt',result.id);} catch { /* The receipt is usable without storage. */ }
    document.querySelector('#receipt').innerHTML=`<div class="receipt"><strong>✓ Обращение зарегистрировано</strong><p>${esc(result.id)} · Ожидает решения оператора</p>${button('track','Проверить статус','ghost',`data-id="${esc(result.id)}"`)}</div>`;
    document.querySelector('#tracking-id').value=result.id;
    await trackCase(result.id);
    form.querySelector('textarea').value=''; await refresh(false);
  } catch(err) {toast(err.message,true);} finally {submit.disabled=false;}
});
window.addEventListener('hashchange',()=>{trackingVersion++;state.tracking=null;state.group='';state.search='';render();main.focus();});
dialog.addEventListener('cancel',()=>caseVersion++);
playbookDialog.addEventListener('cancel',()=>previewVersion++);
document.querySelector('#today').textContent=new Date().toLocaleDateString('ru-RU',{day:'numeric',month:'long',timeZone:'Asia/Almaty'});
async function start() {
  try {await api('/api/workspace/seed',{});state.topics=(await api('/api/topics')).topics;await refresh();}
  catch(err) {main.innerHTML=`<div class="empty" role="alert"><h1>Не удалось загрузить очередь</h1><p>${esc(err.message)}</p>${button('retry','Повторить','primary')}</div>`;}
}
document.addEventListener('click',e=>{if(e.target.closest('[data-action="retry"]')) start();});
setInterval(async()=>{
  if(document.hidden||dialog.open||state.page==='citizen'||document.activeElement?.tagName==='INPUT') return;
  try {await refresh();} catch { /* Preserve last usable queue during a temporary connection failure. */ }
},60000);
start();
