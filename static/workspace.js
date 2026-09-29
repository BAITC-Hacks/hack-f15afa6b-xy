import {esc, button, badge, groups, time, queueView, dashboardView, incidentsView, operatorsView, citizenView, trackingView, subscriptionsView} from './views.js?v=20260929-accounts&analytics=1';
import {routingHealthView,watchPresence,stopPresence,caseCommands,commandList} from './support.js?v=20260929-unified-login';
import {caseView} from './case.js?v=20260929-clarity';
import {radarView,signalView,incidentView,stamp} from './incidents.js?v=20260927-2';
import {authFetch,bootstrapAuth} from './auth.js?v=20260929-unified-login';
import {mountMaps,resetLocationPicker} from './map.js?v=20260929-brand';
import {mountPublicIssueExplorer,publicMapView} from './public-map.js?v=20260929-brand';
import {clearLanguageResult,handleVoiceAction,selectedIntakeLanguage} from './voice.js?v=20260928-motion-orb';
import {mountThinkingOrbs} from './thinking-orb.js?v=20260929-brand';
import {registerAnalyticsActions} from './analytics-actions.js?v=1';
registerAnalyticsActions(authFetch,esc);
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
if(globalThis.gsap&&globalThis.Flip) globalThis.gsap.registerPlugin(globalThis.Flip);
async function mediaData(input) {
  const file=input.files[0];
  if(!file) return {photo_data:null,video_data:null};
  const images=['image/jpeg','image/png','image/webp'], videos=['video/mp4','video/webm'];
  if(![...images,...videos].includes(file.type)) throw new Error('Выберите фото JPEG/PNG/WebP или видео MP4/WebM');
  const limit=images.includes(file.type)?4:12;
  if(file.size>limit*1024*1024) throw new Error(`Файл должен быть не больше ${limit} МБ`);
  const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('Не удалось прочитать файл'));reader.readAsDataURL(file);});
  return images.includes(file.type)?{photo_data:data,video_data:null}:{photo_data:null,video_data:data};
}
const state={page:'queue',group:'',search:'',region:'',items:[],incidents:[],operators:[],topics:[],metrics:{},detail:null,trackingId:'',tracking:null,subscriptions:[],analytics:{alerts:{items:[]},forecast:null,query:null,alertsError:null,forecastError:null,filters:{region_id:'',topic:'',data_origin:'synthetic_demo'},alertHistory:[]}};
Object.assign(state,{radar:{items:[],min_cases:5,window_minutes:15},showIgnored:false,signal:null,incident:null});
try {state.trackingId=localStorage.getItem('pulse109-last-receipt')||'';} catch { /* Lookup also works without browser storage. */ }
const main=document.querySelector('#main'), dialog=document.querySelector('#case-dialog'), content=document.querySelector('#case-content');
const playbookDialog=document.querySelector('#playbook-dialog'), playbookContent=document.querySelector('#playbook-content');
let loadVersion=0, analyticsVersion=0, caseVersion=0, trackingVersion=0, previewVersion=0, preview=null, toastTimer, toastHideTimer, pendingIntake=null;
const commandDialog=document.querySelector('#command-dialog');
Object.assign(state,{routingHealth:null,healthRegion:'',regions:[],cities:[]});
const titles={routing:'Распределение обращений',queue:'Обращения',radar:'Радар',incidents:'Инциденты',map:'Карта обращений',dashboard:'Аналитика',operators:'Команда',quarantine:'Карантин',citizen:'Мои обращения'};
async function api(path, data) {
  let response;
  try {response=await authFetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});}
  catch {throw new Error('Сервер недоступен. Проверьте подключение и повторите действие.');}
  const result=await response.json().catch(()=>({detail:'Не удалось получить ответ сервера. Обновите карточку и повторите действие.'}));
  if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'Не удалось выполнить действие. Проверьте поля и повторите.');
  return result;
}
function toast(text, error=false) {
  const node=document.querySelector('#toast');
  clearTimeout(toastTimer);clearTimeout(toastHideTimer);
  node.textContent=text;node.className=error?'error':'';node.hidden=false;
  requestAnimationFrame(()=>node.classList.add('is-visible'));
  toastTimer=setTimeout(()=>{
    node.classList.remove('is-visible');
    toastHideTimer=setTimeout(()=>node.hidden=true,120);
  },6000);
}
function closeDialog(node) {
  if(!node.open) return Promise.resolve();
  node.classList.add('is-closing');
  return new Promise(resolve=>setTimeout(()=>{node.close();node.classList.remove('is-closing');resolve();},160));
}
function highlightCaseUpdate() {
  const item=content.querySelector('.timeline li:last-child');
  if(item) item.classList.add('is-updated');
}
function alertKey(item) {return [item.data_origin||state.analytics.alerts.data_origin||'organizer',item.region_id,item.topic,item.observed_month].join('|');}
function saveAlertHistory() {
  try {localStorage.setItem('pulse109-alert-history',JSON.stringify(state.analytics.alertHistory.slice(0,20)));} catch { /* History remains available for this session. */ }
}
function recordAlerts(payload) {
  const items=payload.items||[], keys=items.map(alertKey);
  const scope=[payload.data_origin||state.analytics.filters.data_origin,state.analytics.filters.region_id,state.analytics.filters.topic].join('|');
  let previous=null;
  try {
    const raw=localStorage.getItem('pulse109-alert-snapshot');
    const snapshots=raw&&!Array.isArray(JSON.parse(raw))?JSON.parse(raw):{};
    previous=snapshots[scope]||null;snapshots[scope]=keys;
    localStorage.setItem('pulse109-alert-snapshot',JSON.stringify(snapshots));
  } catch { /* Automatic signals still render without browser storage. */ }
  const known=new Set(state.analytics.alertHistory.map(item=>item.key));
  const fresh=previous===null?[]:items.filter(item=>!previous.includes(alertKey(item)));
  const additions=(previous===null?items.slice(0,4):fresh).filter(item=>!known.has(alertKey(item))).map(item=>({key:alertKey(item),region_id:item.region_id,topic:item.topic,observed_month:item.observed_month,increase_percent:item.increase_percent,detected_at:new Date().toISOString(),acknowledged:previous===null,baseline:previous===null}));
  if(additions.length) {state.analytics.alertHistory=[...additions,...state.analytics.alertHistory].slice(0,20);saveAlertHistory();}
  if(!fresh.length) return;
  toast(`Новый сигнал: ${fresh.length} ${fresh.length===1?'всплеск':'всплеска'} в данных`);
  if('Notification' in window&&Notification.permission==='granted') try {new Notification('Pulse 109 · новый сигнал',{body:`Обнаружено всплесков: ${fresh.length}`});} catch { /* The in-app notification remains visible. */ }
}
function analyticsParams() {
  const params=new URLSearchParams({horizon_months:'3',data_origin:state.analytics.filters.data_origin});
  if(state.analytics.filters.region_id) params.set('region_id',state.analytics.filters.region_id);
  if(state.analytics.filters.topic) params.set('topic',state.analytics.filters.topic);
  return params;
}
async function loadAnalytics(renderPage=true) {
  const version=++analyticsVersion, params=analyticsParams();
  const [alerts,forecast]=await Promise.allSettled([api(`/api/alerts?${params}`),api(`/api/forecast?${params}`)]);
  if(version!==analyticsVersion) return;
  if(alerts.status==='fulfilled') {state.analytics.alerts=alerts.value;state.analytics.alertsError=null;recordAlerts(alerts.value);}
  else state.analytics.alertsError=alerts.reason.message;
  if(forecast.status==='fulfilled') {state.analytics.forecast=forecast.value;state.analytics.forecastError=null;}
  else {state.analytics.forecast=null;state.analytics.forecastError=forecast.reason.message;}
  if(renderPage&&state.page==='dashboard') render();
}
try {state.analytics.alertHistory=JSON.parse(localStorage.getItem('pulse109-alert-history')||'[]');} catch { /* Start with an empty notification history. */ }
let subscriberId;
function subscriberKey() {
  if(subscriberId) return subscriberId;
  try {
    subscriberId=localStorage.getItem('pulse109-subscriber')||sessionStorage.getItem('pulse-demo-subscriber');
    if(!subscriberId) subscriberId=crypto.randomUUID();
    localStorage.setItem('pulse109-subscriber',subscriberId);sessionStorage.removeItem('pulse-demo-subscriber');
  } catch {subscriberId=crypto.randomUUID();}
  return subscriberId;
}
async function loadSubscriptions() {
  if(state.user?.role==='citizen') {
    state.subscriptions=(await api('/api/workspace/citizen/complaints')).items;
    const node=document.querySelector('#subscriptions-list');
    if(node) node.innerHTML=subscriptionsView(state.subscriptions,true);
    return;
  }
  const result=await api(`/api/workspace/public/subscriptions/${encodeURIComponent(subscriberKey())}`);
  let seen={};try {seen=JSON.parse(localStorage.getItem('pulse109-subscriptions-seen')||'{}');} catch { /* New updates still load without storage. */ }
  state.subscriptions=result.items.map(item=>({...item,changed:!!seen[item.id]&&seen[item.id]!==item.last_updated}));
  const node=document.querySelector('#subscriptions-list');if(node) node.innerHTML=subscriptionsView(state.subscriptions);
  try {localStorage.setItem('pulse109-subscriptions-seen',JSON.stringify(Object.fromEntries(result.items.map(item=>[item.id,item.last_updated]))));} catch { /* no-op */ }
}
async function subscribeCase(id) {
  const result=await api(`/api/workspace/public/complaints/${encodeURIComponent(id)}/subscribe`,{subscriber_key:subscriberKey()});
  await loadSubscriptions();return result;
}
function showSimilar(items) {
  const notice=document.querySelector('#citizen-notice');
  notice.innerHTML=`<div class="deflection"><span class="eyebrow">ВОЗМОЖНО, УЖЕ СООБЩИЛИ</span><span class="incident-symbol" aria-hidden="true">≈</span><h2>Нашли похожие обращения рядом</h2><p>Подпишитесь на существующее обращение или создайте отдельное, если проблема отличается.</p><div class="duplicate-list">${items.map(item=>`<article class="duplicate-card"><div>${badge(item.status==='confirmed'?'Принято в работу':'На рассмотрении',item.status==='confirmed'?'green':'amber')}<strong>${esc(item.id)}</strong></div><p>${esc(item.text)}</p><small>${esc(item.reason)}${item.subscribers?` · ${item.subscribers} подписок`:''}</small>${button('subscribe-case','Это та же проблема · подписаться','primary wide',`data-id="${esc(item.id)}"`)}</article>`).join('')}</div>${button('create-anyway','Проблема другая · создать отдельно','ghost wide')}<small>Совпадение рассчитано по категории, характеру проблемы и месту. Решение остаётся за вами.</small></div>`;
  notice.hidden=false;notice.scrollIntoView({behavior:'smooth',block:'nearest'});
}
async function registerIntake(form,payload) {
  const result=await api('/api/workspace/intake',payload);
  state.trackingId=result.id;pendingIntake=null;
  try {localStorage.setItem('pulse109-last-receipt',result.id);} catch { /* The receipt is usable without storage. */ }
  const synthetic=result.data_origin==='synthetic';
  document.querySelector('#receipt').innerHTML=`<div class="receipt"><strong>✓ Обращение зарегистрировано</strong><p>${esc(result.id)} · ${synthetic?'Ожидает решения оператора':'Приватное обращение; публикация возможна только после согласия и проверки оператором'}</p>${synthetic||state.user?.role==='citizen'?button('track','Проверить статус','ghost',`data-id="${esc(result.id)}"`):''}</div>`;
  if(synthetic||state.user?.role==='citizen') document.querySelector('#tracking-id').value=result.id;
  document.querySelector('#citizen-notice').hidden=true;
  if(synthetic) {
    try {await subscribeCase(result.id);} catch {toast('Обращение создано, но подписку на обновления не удалось включить.',true);}
    await trackCase(result.id);
  } else if(state.user?.role==='citizen') {
    await trackCase(result.id);await loadSubscriptions();
  } else {
    state.tracking=null;
    document.querySelector('#tracking-result').innerHTML='<p class="micro">Статус реального обращения будет доступен после подключения защищённого кабинета заявителя.</p>';
  }
  form.querySelector('textarea').value='';form.elements.address.value='';form.elements.media.value='';resetLocationPicker(form);await refresh(false);
}
function render() {
  const citizen=state.user?.role==='citizen', requested=location.hash.slice(1);
  const nextPage=citizen?(['citizen','map'].includes(requested)?requested:'citizen'):(titles[requested]&&requested!=='citizen'?requested:'queue');
  const oldRows=main.querySelectorAll('[data-flip-id]');
  const flipState=!reducedMotion.matches&&['queue','quarantine'].includes(nextPage)&&oldRows.length&&globalThis.Flip?globalThis.Flip.getState(oldRows):null;
  state.page=nextPage;
  document.querySelectorAll('[data-nav]').forEach(a=>{
    a.classList.toggle('active',a.dataset.nav===state.page);
    if(a.dataset.nav===state.page) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  document.querySelector('#breadcrumb').textContent=titles[state.page];
  document.querySelector('#nav-count').textContent=state.metrics.pending;
  document.querySelector('#incident-count').textContent=state.metrics.active_incidents;
  document.querySelector('#quarantine-count').textContent=state.metrics.quarantined;
  document.querySelector('#radar-count').textContent=state.radar.items.filter(i=>!i.ignored&&!i.incident_id).length;
  const views={routing:routingHealthView,queue:queueView,quarantine:queueView,radar:radarView,map:publicMapView,dashboard:dashboardView,incidents:incidentsView,operators:operatorsView,citizen:citizenView};
  main.innerHTML=views[state.page](state);
  if(flipState) globalThis.Flip.from(flipState,{targets:main.querySelectorAll('[data-flip-id]'),duration:.22,ease:'power1.out',fade:true,absolute:true,scale:true,simple:true});
  mountThinkingOrbs(main);
  mountMaps(main);
  if(state.page==='map') mountPublicIssueExplorer(main);
  if(state.page==='citizen') loadSubscriptions().catch(err=>{const node=document.querySelector('#subscriptions-list');if(node) node.textContent=err.message;});
}
async function refresh(renderPage=true) {
  if(state.user?.role==='citizen') {if(renderPage) render();return;}
  const version=++loadVersion;
  const queue=await api('/api/workspace/queue');
  const [incidents,operators,metrics,radar]=await Promise.allSettled([
    api('/api/workspace/incidents'),api('/api/workspace/operators'),api('/api/workspace/metrics'),api('/api/workspace/radar')]);
  if(version!==loadVersion) return;
  state.items=queue.items;
  if(incidents.status==='fulfilled') state.incidents=incidents.value.items;
  if(operators.status==='fulfilled') state.operators=operators.value.items;
  if(metrics.status==='fulfilled') state.metrics=metrics.value;
  if(radar.status==='fulfilled') state.radar=radar.value;
  if(renderPage) render();
  await loadAnalytics(renderPage);
}
async function openCase(id) {
  const version=++caseVersion;
  stopPresence();state.incident=null;state.signal=null;
  const [analysis,history,playbooks,analogues]=await Promise.all([api(`/api/workspace/complaints/${encodeURIComponent(id)}/triage`,{}).catch(error=>({error})),api(`/api/complaints/${encodeURIComponent(id)}`),api(`/api/workspace/complaints/${encodeURIComponent(id)}/playbooks`).catch(error=>({items:[],error:error.message})),api(`/api/complaints/${encodeURIComponent(id)}/similar?limit=3`).catch(()=>({candidates:[],mode:'unavailable'}))]);
  if(version!==caseVersion) return;
  const detail=analysis.error?manualDetail(history.complaint,analysis.error.message):analysis;
  detail.events=history.events;
  detail.playbooks=playbooks.items;
  detail.playbookError=playbooks.error;
  detail.analogues=analogues.candidates;
  detail.analogueMode=analogues.mode;
  detail.selectedTopic=detail.complaint.topic||(detail.triage.confidence_band==='high'?detail.triage.category:null);
  detail.selectedPriority=detail.complaint.priority||detail.triage.urgency;
  state.detail=detail;
  content.innerHTML=caseView(detail,state.topics);
  mountMaps(content);
  watchPresence(id,state.operators,api);
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
  const presence=document.querySelector('#presence');
  content.innerHTML=caseView(state.detail,state.topics);
  mountMaps(content);
  if(presence) document.querySelector('#presence').replaceWith(presence);
  if(draft && document.querySelector('#reply-text')) document.querySelector('#reply-text').value=draft;
  if(check!==undefined && document.querySelector('#include-incident')) document.querySelector('#include-incident').checked=check;
}
async function mutate(path, data, message) {
  const id=state.detail.complaint.id;
  const version=caseVersion;
  await api(`/api/workspace/complaints/${encodeURIComponent(id)}/${path}`,data);
  await refresh();
  if(dialog.open && version===caseVersion) {await openCase(id);highlightCaseUpdate();}
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
    mountMaps(result);
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
    renderPreview(data,cid);
  } catch(err) {
    if(version===previewVersion && playbookDialog.open) document.querySelector('#playbook-error').textContent=err.message;
  }
}
function renderPreview(data,subject) {
  playbookContent.innerHTML=`<div class="case-header"><div><span class="eyebrow">ПРОВЕРКА ИЗМЕНЕНИЙ · ${esc(subject)}</span><h2 id="playbook-title">${esc(data.title)}</h2></div>${button('cancel-playbook','×','icon-button','aria-label="Отменить сценарий"')}</div><div class="playbook-body"><p class="micro">Проверьте изменения. Они будут сохранены вместе только после вашего подтверждения.</p><ol class="playbook-actions">${data.actions.map(a=>`<li><span aria-hidden="true">✓</span><div><strong>${esc(a.label)}</strong><p>${esc(a.value)}</p></div></li>`).join('')}</ol><p id="playbook-error" class="danger-text" role="alert">${esc(data.blocked_reason||'')}</p><p class="micro">Синтетическое демо · внешняя отправка сообщений не подключена.</p></div><div class="case-footer">${button('cancel-playbook','Отмена','ghost')}${button('refresh-playbook','Обновить preview','ghost')}${button('execute-playbook',`Подтвердить ${data.actions.length} действий`,'primary',data.can_execute?'':'disabled')}</div>`;
}
async function openIncident(id) {
  const version=++caseVersion;
  stopPresence();state.detail=null;state.signal=null;
  const incident=await api(`/api/workspace/incidents/${encodeURIComponent(id)}`);
  if(version!==caseVersion) return;
  state.incident=incident;
  content.innerHTML=incidentView(incident,state.operators);
  if(!dialog.open) dialog.showModal();
}
function showSignal(signal) {
  stopPresence();caseVersion++;state.signal=signal;state.detail=null;state.incident=null;
  content.innerHTML=signalView(signal,state.topics);
  if(!dialog.open) dialog.showModal();
}
function previewIncident(incident,request) {
  previewVersion++;
  const name=id=>state.operators.find(o=>o.id===id)?.name||'Не назначен';
  preview={cid:incident.id,kind:'incident_update',path:`/api/workspace/incidents/${encodeURIComponent(incident.id)}/update`,request:{...request,expected_revision:incident.revision}};
  renderPreview({title:'Обновить инцидент',can_execute:true,actions:[
    {label:'Статус',value:`${incident.status} → ${request.status}`},
    {label:'Ответственный',value:`${name(incident.incident_owner_id)} → ${name(request.incident_owner_id)}`},
    {label:'Важность',value:`${incident.severity} → ${request.severity} · демо-шкала`},
    {label:'Следующее обновление',value:request.next_update?stamp(request.next_update):'Не назначено — инцидент завершён'},
    {label:'Подтверждённая информация',value:request.note},
    {label:'Связанные обращения',value:`${incident.count} обращений сохранят свои решения и статусы.`},
  ]},incident.id);
  if(!playbookDialog.open) playbookDialog.showModal();
}
async function loadHealth() {
  const region=state.healthRegion;
  const data=await api('/api/workspace/routing-health'+(region?'?region_id='+encodeURIComponent(region):''));
  if(region!==state.healthRegion) return;
  state.routingHealth=data;if(state.page==='routing') render();
}
function openCommands() {
  if(!dialog.open || !state.detail || playbookDialog.open) return;
  document.querySelector('#command-search').value='';
  document.querySelector('#command-options').innerHTML=commandList(caseCommands(state.detail));
  commandDialog.showModal();document.querySelector('#command-search').focus();
}
async function handleAction(node) {
  const action=node.dataset.action, d=state.detail, c=d?.complaint;
  if(action==='enable-alert-notifications') {
    if(!('Notification' in window)) {toast('Системные уведомления не поддерживаются этим браузером',true);return;}
    const permission=await Notification.requestPermission();
    toast(permission==='granted'?'Системные уведомления включены':'Разрешение не выдано; сигналы останутся внутри Pulse 109',permission!=='granted');render();return;
  }
  if(action==='acknowledge-alert'||action==='acknowledge-all-alerts') {
    state.analytics.alertHistory=state.analytics.alertHistory.map(item=>({...item,acknowledged:action==='acknowledge-all-alerts'||item.key===node.dataset.key?true:item.acknowledged}));
    saveAlertHistory();render();return;
  }
  if(action==='voice-record'||action==='voice-stop') {await handleVoiceAction(node,api,toast);return;}
  if(action==='commands') {openCommands();return;}
  if(action==='close-commands') {await closeDialog(commandDialog);return;}
  if(action==='run-command') {
    await closeDialog(commandDialog);
    await showPlaybook(node.dataset.kind==='priority'?'route_service':node.dataset.command,node.dataset.kind==='priority'?{priority:node.dataset.command}:{});return;
  }
  if(action==='load-health') {await loadHealth();return;}
  if(action==='cancel-playbook') {previewVersion++;await closeDialog(playbookDialog);return;}
  if(action==='refresh-playbook') {
    if(preview.kind==='incident_update') {
      const p=preview, version=++previewVersion, latest=await api(`/api/workspace/incidents/${encodeURIComponent(p.cid)}`);
      if(version===previewVersion && playbookDialog.open) previewIncident(latest,p.request);
    }
    else await showPlaybook(preview.kind,preview.request);
    return;
  }
  if(action==='playbook') {await showPlaybook(node.dataset.kind);return;}
  if(action==='execute-playbook') {
    const p=preview, version=caseVersion;
    try {
      await api(p.path||`/api/workspace/complaints/${encodeURIComponent(p.cid)}/playbooks/${p.kind}/execute`,p.path?p.request:{preview_token:p.preview_token});
    } catch(err) {
      document.querySelector('#playbook-error').textContent=err.message;
      node.dataset.action='blocked-playbook'; node.setAttribute('aria-disabled','true');
      return;
    }
    previewVersion++;await closeDialog(playbookDialog);
    await refresh();if(dialog.open && version===caseVersion) {await (p.path?openIncident(p.cid):openCase(p.cid));highlightCaseUpdate();}
    toast('Сценарий применён. Изменения и аудит сохранены.');return;
  }
  if(action==='close') {stopPresence();caseVersion++;await closeDialog(dialog);return;}
  if(action==='refresh') {await refresh();toast('Данные обновлены');return;}
  if(action==='open') {await openCase(node.dataset.id);return;}
  if(action==='filter') {state.group=node.dataset.group;render();return;}
  if(action==='clear-queue-filters') {state.group='';state.search='';state.region='';render();document.querySelector('#search')?.focus();return;}
  if(action==='demo') {
    const data=await api('/api/workspace/demo/intake',{});
    location.hash='queue'; await refresh(); await openCase(data.id); toast('Новое обращение поступило · анализ готов');return;
  }
  if(action==='incident') {await openIncident(node.dataset.id);return;}
  if(action==='radar-demo') {const result=await api('/api/workspace/radar/demo',{});location.hash='radar';await refresh();toast(`Добавлено ${result.count} синтетических обращений. Проверьте новый сигнал.`);return;}
  if(action==='radar-preview') {showSignal(state.radar.items.find(i=>i.id===node.dataset.id));return;}
  if(action==='radar-confirm'||action==='radar-ignore') {
    const signal=state.signal, version=caseVersion;
    try {
      const path=`/api/workspace/radar/${encodeURIComponent(signal.id)}`;
      if(action==='radar-confirm') {
        const result=await api(path+'/confirm',{case_ids:signal.case_ids,incident_id:signal.incident_id});
        await refresh();if(version===caseVersion && dialog.open) await openIncident(result.incident.id);toast('Инцидент подтверждён. Оригиналы обращений сохранены.');
      } else {
        await api(path+'/ignore',{case_ids:signal.case_ids,ignored:!signal.ignored});
        if(version===caseVersion) {await closeDialog(dialog);caseVersion++;}await refresh();toast(signal.ignored?'Сигнал восстановлен':'Сигнал отклонён. Обращения сохранены.');
      }
    } catch(err) {const field=document.querySelector('#signal-error');if(field) field.textContent=err.message;else toast(err.message,true);}
    return;
  }
  if(action==='subscribe-case') {
    await subscribeCase(node.dataset.id);
    node.textContent='✓ Обновления включены';node.dataset.action='subscribed';toast('Подписка сохранена. Новое обращение не создано.');return;
  }
  if(action==='subscribed') {toast('Подписка уже сохранена');return;}
  if(action==='refresh-subscriptions') {await loadSubscriptions();toast('Подписки обновлены');return;}
  if(action==='create-anyway') {
    const form=document.querySelector('#citizen-form');
    if(!form||!pendingIntake) throw new Error('Данные формы изменились. Проверьте обращение ещё раз.');
    await registerIntake(form,pendingIntake);return;
  }
  if(action==='track') {await trackCase(node.dataset.id);document.querySelector('#tracking-result')?.scrollIntoView({block:'nearest'});return;}
  if(action==='approve-public'||action==='reject-public') {
    const request={status:action==='approve-public'?'approved':'rejected'};
    if(action==='approve-public') {
      request.public_text=document.querySelector('#public-review-text')?.value.trim();
      if(!request.public_text) throw new Error('Введите проверенный текст для публичной карты');
    }
    const version=caseVersion;
    await api(`/api/workspace/complaints/${encodeURIComponent(c.id)}/moderation`,request);
    await refresh();if(dialog.open&&version===caseVersion) await openCase(c.id);
    toast(action==='approve-public'?'Обезличенная версия опубликована':'Публикация отклонена');return;
  }
  if(action==='reload-case') {const draft=document.querySelector('#reply-text')?.value;await openCase(c.id);if(draft && document.querySelector('#reply-text')) document.querySelector('#reply-text').value=draft;return;}
  if(action==='category') {
    d.selectedTopic=node.dataset.topic;
    const topic=d.selectedTopic, version=caseVersion;
    const routing=await api(`/api/workspace/complaints/${encodeURIComponent(c.id)}/routing?topic=${encodeURIComponent(topic)}${d.selectedPriority?'&priority='+encodeURIComponent(d.selectedPriority):''}`);
    if(version!==caseVersion || d.selectedTopic!==topic || !dialog.open) return;
    d.routing=routing.routing; d.triage.service_name=routing.service_name; rerenderCase();return;
  }
  if(action==='priority') {d.selectedPriority=node.dataset.priority;if(d.selectedTopic) await handleAction({dataset:{action:'category',topic:d.selectedTopic}});else rerenderCase();return;}
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
    await refresh();if(dialog.open && version===caseVersion) {await openCase(c.id);highlightCaseUpdate();}toast('Уточнение сохранено; анализ обновлён');return;
  }
  if(action==='ask-copilot') {
    const version=caseVersion;
    const result=await api(`/api/workspace/complaints/${encodeURIComponent(c.id)}/copilot`,{});
    if(version!==caseVersion||!dialog.open) return;
    d.copilot=result;rerenderCase();
    toast(result.available?'ИИ-помощник подготовил рекомендацию':'ИИ-помощник недоступен · показан безопасный шаблон');return;
  }
  if(action==='copilot-helpful'||action==='copilot-unhelpful') {
    await api(`/api/workspace/complaints/${encodeURIComponent(c.id)}/copilot/feedback`,{
      result_id:d.copilot.result_id,helpful:action==='copilot-helpful',
    });
    d.copilot.feedbackSaved=true;rerenderCase();toast('Оценка сохранена для улучшения помощника');return;
  }
  if(['insert-reply','edit-reply','reject-reply'].includes(action)) {
    const field=document.querySelector('#reply-text');
    if(!field) {toast('Сначала дождитесь уточнения гражданина');return;}
    field.value=action==='reject-reply'?'':d.copilot?.suggested_reply||d.suggested_response;
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
  if(e.target.closest('[data-nav]')) document.querySelectorAll('.nav-more[open]').forEach(node=>node.removeAttribute('open'));
  const node=e.target.closest('[data-action]'); if(!node||node.disabled) return;
  node.disabled=true;
  try {await handleAction(node);} catch(err) {toast(err.message,true);} finally {if(node.isConnected && node.getAttribute('aria-disabled')!=='true') node.disabled=false;}
});
document.addEventListener('input',e=>{
  if(pendingIntake&&e.target.closest('#citizen-form')) {pendingIntake=null;document.querySelector('#citizen-notice').hidden=true;}
  if(e.target.id==='command-search') {document.querySelector('#command-options').innerHTML=commandList(caseCommands(state.detail),e.target.value);return;}
  if(e.target.id!=='search') return;
  state.search=e.target.value;
  const position=e.target.selectionStart;
  render(); const input=document.querySelector('#search');input.focus();
  input.setSelectionRange(position,position);
});
document.addEventListener('change',async e=>{
  if(pendingIntake&&e.target.closest('#citizen-form')) {pendingIntake=null;document.querySelector('#citizen-notice').hidden=true;}
  if(e.target.id==='health-region') {state.healthRegion=e.target.value;try {await loadHealth();} catch(err) {toast(err.message,true);}}
  if(e.target.id==='show-ignored') {state.showIgnored=e.target.checked;render();}
  if(e.target.id==='incident-status') document.querySelector('#incident-next').required=e.target.value!=='Завершён';
  if(e.target.id==='queue-region') {state.region=e.target.value;render();}
  if(e.target.id==='queue-status-filter') {state.group=e.target.value;render();}
  if(e.target.id==='citizen-language') clearLanguageResult(e.target);
  if(['analytics-region','analytics-topic','analytics-origin'].includes(e.target.id)) {
    const names={"analytics-region":'region_id',"analytics-topic":'topic',"analytics-origin":'data_origin'};
    state.analytics.filters[names[e.target.id]]=e.target.value;state.analytics.query=null;
    await loadAnalytics(true);return;
  }
  if(e.target.id==='citizen-region') {
    const citySelect=document.querySelector('#citizen-city');
    const cities=state.cities.filter(city=>city.region_id===e.target.value).sort((a,b)=>a.name_ru.localeCompare(b.name_ru,'ru'));
    citySelect.replaceChildren(...cities.map(city=>{
      const option=new Option(city.name_ru,city.code);option.dataset.name=city.name_ru;option.dataset.region=city.region_id;return option;
    }));
    citySelect.dispatchEvent(new Event('change',{bubbles:true}));
  }
  if(e.target.id==='manual-category' && e.target.value) {
    state.detail.manual=true;
    if(!state.detail.selectedPriority) state.detail.selectedPriority='normal';
    try {await handleAction({dataset:{action:'category',topic:e.target.value}});} catch(err) {toast(err.message,true);}
  }
});
document.addEventListener('submit',async e=>{
  if(e.target.id==='analytics-query') {
    e.preventDefault();
    const question=new FormData(e.target).get('question').trim();
    if(!question) {toast('Введите вопрос к данным',true);return;}
    try {state.analytics.query=await api('/api/query',{question,...state.analytics.filters});render();} catch(err) {toast(err.message,true);}
    return;
  }
  if(e.target.id==='incident-update-form') {
    e.preventDefault();
    const data=new FormData(e.target), note=data.get('note').trim(), status=data.get('status');
    if(!note) {toast('Укажите подтверждённую информацию',true);return;}
    previewIncident(state.incident,{status,note,incident_owner_id:data.get('incident_owner_id')||null,severity:Number(data.get('severity')),next_update:status==='Завершён'?null:new Date(data.get('next_update')).toISOString()});
    return;
  }
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
    const number=name=>data.get(name)?Number(data.get(name)):null;
    if(!data.get('latitude')||!data.get('longitude')) throw new Error('Выберите место проблемы на карте');
    const media=await mediaData(form.elements.media);
    const payload={text,address:data.get('address')?.trim()||null,city_code:data.get('city_code'),district:data.get('district')?.trim()||null,language:selectedIntakeLanguage(form.elements.language),region_id:data.get('region_id'),channel:'web',latitude:number('latitude'),longitude:number('longitude'),location_accuracy_m:number('location_accuracy_m'),public_consent:data.get('public_consent')==='on',...media};
    const {photo_data,video_data,...probe}=payload, similar=await api('/api/workspace/public/similar',probe);
    if(similar.items.length) {pendingIntake=payload;showSimilar(similar.items);return;}
    await registerIntake(form,payload);
  } catch(err) {toast(err.message,true);} finally {submit.disabled=false;}
});
window.addEventListener('hashchange',()=>{trackingVersion++;state.tracking=null;state.group='';state.search='';render();main.focus();if(state.page==='routing') loadHealth().catch(err=>toast(err.message,true));});
for(const modal of document.querySelectorAll('dialog')) modal.addEventListener('cancel',event=>{event.preventDefault();closeDialog(modal);});
dialog.addEventListener('cancel',()=>caseVersion++);
dialog.addEventListener('close',stopPresence);
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k') {if(state.detail&&dialog.open) {e.preventDefault();openCommands();}}
  if(commandDialog.open && e.key==='ArrowDown') {e.preventDefault();const options=[...commandDialog.querySelectorAll('.command-option')];options[(options.indexOf(document.activeElement)+1)%options.length]?.focus();}
});
playbookDialog.addEventListener('cancel',()=>previewVersion++);
document.querySelector('#today').textContent=new Date().toLocaleDateString('ru-RU',{day:'numeric',month:'long',timeZone:'Asia/Almaty'});
async function start(user=state.user) {
  state.user=user;
  try {if(user?.role==='citizen') {
    state.trackingId='';state.tracking=null;state.subscriptions=[];
    state.regions=(await api('/api/regions')).regions;state.cities=(await api('/api/workspace/cities')).items;render();return;
  }
  await api('/api/workspace/seed',{});state.topics=(await api('/api/topics')).topics;state.regions=(await api('/api/regions')).regions;state.cities=(await api('/api/workspace/cities')).items;await refresh();if(state.page==='routing') await loadHealth();}
  catch(err) {main.innerHTML=`<div class="empty" role="alert"><h1>Не удалось загрузить очередь</h1><p>${esc(err.message)}</p>${button('retry','Повторить','primary')}</div>`;}
}
document.addEventListener('click',e=>{if(e.target.closest('[data-action="retry"]')) start();});
setInterval(async()=>{
  if(document.hidden||document.querySelector('#workspace-screen').hidden||dialog.open||state.page==='citizen'||document.activeElement?.matches('input,textarea,select')) return;
  try {await refresh();} catch { /* Preserve last usable queue during a temporary connection failure. */ }
},60000);
bootstrapAuth(start);
