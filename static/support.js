import {esc,button,badge,heading,empty,topicName} from './views.js?v=20260927-2';
import {authFetch} from './auth.js?v=20260927-2';

export function routingHealthView(s) {
  const h=s.routingHealth;
  if(!h) return heading('ДЕМО-МАРШРУТЫ','Проверка маршрутизации','Загружаем все комбинации…');
  const m=h.summary, region=id=>s.regions.find(r=>r.id===id)?.name_ru||id;
  return heading('ДЕМО-МАРШРУТЫ','Каждое обращение получает маршрут','Категория × регион × язык × приоритет · текущая синтетическая смена.',button('load-health','↻ Проверить','ghost'))+
    `<div class="radar-explainer"><div><strong>Проверка структуры, а не реального покрытия служб</strong><p>${esc(m.note)}</p></div></div><div class="dashboard-metrics">${[['Проверено',m.total,'комбинаций'],['Профильный оператор',m.primary,m.primary_percent+'% комбинаций'],['Резервный маршрут',m.fallback,'старший оператор или общая очередь'],['Без маршрута',m.uncovered,'требуют исправления']].map(([label,value,note])=>`<article class="metric-card"><p>${label}</p><strong>${value}</strong><small>${note}</small></article>`).join('')}</div>
    <section class="panel"><div class="section-top"><h2>Где нужен резервный маршрут</h2><label for="health-region">Регион примеров <select id="health-region"><option value="">Все регионы</option>${s.regions.map(r=>`<option value="${esc(r.id)}" ${r.id===s.healthRegion?'selected':''}>${esc(r.name_ru)}</option>`).join('')}</select></label></div><p class="micro">Показано ${h.items.length} из ${h.item_count} совпадений. Счётчики выше всегда относятся ко всем регионам.</p><div class="health-table"><table><thead><tr><th>Категория</th><th>Регион</th><th>Язык / приоритет</th><th>Резерв</th></tr></thead><tbody>${h.items.map(i=>`<tr><td>${esc(topicName(i.category,s.topics))}</td><td>${esc(region(i.region_id))}</td><td>${esc(i.language)} · ${i.priority==='urgent'?'срочный':'обычный'}</td><td>${esc(i.operator_name||'Общая очередь')}<small>${esc(i.reason)}</small></td></tr>`).join('')}</tbody></table></div>${!h.items.length?empty('Для выбранного региона резерв не требуется'):''}</section>`;
}

export function routingAlternatives(r) {
  return r.candidates?.length?`<details class="routing-alternatives"><summary>Почему выбран этот оператор</summary>${r.candidates.map(o=>`<div><strong>${esc(o.name)}</strong> · ${o.workload}/${o.workload_capacity} баллов<p>${esc(o.reason)}</p><small>${esc(o.match_reasons.join(' · '))}</small></div>`).join('')}<p>Демо-веса: дубль 15 → звонок 100 → чат 50 → срочное/сложное 40 → обычное 30. Применяется первое подходящее правило.</p></details>`:'';
}

let timer, watched=null, revision=null, changed=false, editing=false, requestVersion=0;
const session=crypto.randomUUID();
let identity='op-senior';
export function stopPresence() {
  clearInterval(timer);requestVersion++;
  if(watched) authFetch(`/api/workspace/complaints/${encodeURIComponent(watched)}/presence/leave`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:session}),keepalive:true}).catch(()=>{});
  watched=null;
}
export function watchPresence(id,operators,api) {
  stopPresence();watched=id;revision=null;changed=false;editing=false;
  const host=document.querySelector('#presence');
  host.innerHTML=`<div class="presence-bar"><label>Демо-оператор <select id="presence-operator">${operators.map(o=>`<option value="${esc(o.id)}" ${identity===o.id?'selected':''}>${esc(o.name)}</option>`).join('')}</select></label><span id="presence-peers" role="status">Проверяем присутствие…</span>${button('commands','⌘ / Ctrl K · Команды','small ghost')}</div><div id="case-changed" class="change-notice" role="status" hidden>Карточка изменена другим оператором. Черновик сохранён на экране. ${button('reload-case','Загрузить изменения','small ghost')}</div>`;
  const version=requestVersion;
  const poll=async()=>{
    if(document.hidden||version!==requestVersion) return;
    try {
      const data=await api(`/api/workspace/complaints/${encodeURIComponent(id)}/presence`,{session_id:session,operator_id:identity,mode:editing?'editing':'viewing'});
      if(version!==requestVersion||!host.isConnected) return;
      if(revision && revision!==data.revision) changed=true;
      revision=data.revision;
      document.querySelector('#case-changed').hidden=!changed;
      document.querySelector('#presence-peers').textContent=data.peers.length?data.peers.map(p=>`${p.name} ${p.mode==='editing'?'редактирует':'просматривает'} карточку`).join(' · '):'Других операторов в карточке нет';
    } catch {
      if(version===requestVersion && host.isConnected) document.querySelector('#presence-peers').textContent='Присутствие временно недоступно';
    }
  };
  host.querySelector('select').addEventListener('change',e=>{identity=e.target.value;poll();});
  poll();timer=setInterval(poll,5000);
}
document.addEventListener('input',e=>{if(watched && e.target.closest('#case-content') && e.target.id!=='presence-operator') editing=true;});
document.addEventListener('change',e=>{if(watched && e.target.closest('#case-content') && e.target.id!=='presence-operator') editing=true;});
window.addEventListener('pagehide',stopPresence);

export function caseCommands(d) {
  const list=(d.playbooks||[]).filter(p=>p.can_execute).map(p=>({id:p.id,label:p.title,kind:'playbook'}));
  if(d.complaint.decision_status==='pending'&&!d.complaint.quarantined&&!d.complaint.resolved_at) {
    list.push({id:'urgent',label:'Повысить приоритет до срочного и направить',kind:'priority'},{id:'normal',label:'Обычный приоритет и направить',kind:'priority'});
  }
  return list;
}
export function commandList(commands,query='') {
  return commands.filter(c=>c.label.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'))).map(c=>button('run-command',esc(c.label),'command-option',`data-command="${esc(c.id)}" data-kind="${c.kind}"`)).join('')||'<p class="micro">Подходящих команд нет.</p>';
}
