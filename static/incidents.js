import {esc,button,badge,heading,empty,topicName} from './views.js?v=20260929-copy';

export const stamp=value=>value?new Date(value).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Almaty'}):'Не указано';
const localInput=value=>{
  if(!value) return '';
  const date=new Date(value);
  return new Date(date-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
};

const origins={citizen:'Обращения граждан',organizer:'Данные организаторов',public:'Импортированные данные',synthetic:'Демонстрационные данные'};
const statusName=i=>i.ignored?'Отклонён':i.overdue?'Пора проверить':i.snoozed?'Отложен':i.status==='confirmed'?'Инцидент создан':i.owner_id?'В работе':'Новый';
const eventNames={detected:'Сигнал обнаружен',claim:'Взят в работу',schedule:'Срок проверки изменён',snooze:'Проверка отложена',resume:'Возвращён в работу',dismissed:'Сигнал отклонён',new:'Сигнал восстановлен',reviewing:'Сигнал восстановлен',confirmed:'Создание или связь с инцидентом подтверждены'};

export function radarView(s) {
  const radar=s.radar, scope=s.radarFilter||'active';
  const signals=radar.items.filter(i=>scope==='all'||(scope==='dismissed'?i.ignored:scope==='snoozed'?i.snoozed:scope==='confirmed'?i.status==='confirmed':!i.ignored&&!i.snoozed&&i.status!=='confirmed'));
  const coverage=radar.coverage||{};
  return heading('Радар','Проверьте группы похожих обращений и решите, нужна ли общая работа службы.',button('refresh','↻ Обновить','ghost'))+
    '<div class="radar-toolbar"><label for="radar-source">Источник<select id="radar-source">'+
    [['real','Реальные обращения'],['synthetic','Демонстрационные данные']].map(([value,label])=>'<option value="'+value+'" '+(s.radarSource===value?'selected':'')+'>'+label+'</option>').join('')+
    '</select></label><label for="radar-filter">Показать<select id="radar-filter">'+
    [['active','Требуют внимания'],['snoozed','Отложенные'],['dismissed','Отклонённые'],['confirmed','Инцидент создан'],['all','Все сигналы']].map(([value,label])=>'<option value="'+value+'" '+(scope===value?'selected':'')+'>'+label+'</option>').join('')+
    '</select></label>'+(s.radarSource==='synthetic'&&radar.demo_available?button('radar-demo','＋ Добавить демо-сигнал','ghost'):'')+'</div>'+
    (s.radarError?'<p class="danger-text" role="alert">'+esc(s.radarError)+'</p>':'')+
    '<div class="radar-explainer"><div><strong>Порог: '+radar.min_cases+' похожих обращений за '+radar.window_minutes+' минут</strong><p>За этот период: '+(coverage.recent_count??'—')+' активных обращений с известным временем. Для поиска групп подходят '+(coverage.eligible_count??'—')+'. Не хватает места, текста или уверенной категории у '+(coverage.excluded_count??'—')+'.</p><p>Источник не подтверждает полноту данных. Карточки остаются до решения оператора.</p></div></div>'+
    '<div class="signal-grid">'+signals.map(i=>
      '<article class="signal-card '+(i.ignored?'dismissed ':'')+(i.overdue?'signal-overdue':'')+'"><div class="section-top"><span class="eyebrow">'+esc(origins[i.data_origin]||i.data_origin)+'</span>'+badge(statusName(i),i.overdue?'red':i.ignored?'neutral':i.owner_id?'blue':'amber')+'</div>'+
      '<h2>'+esc(topicName(i.category,s.topics))+'</h2><p class="muted">'+esc(s.regions.find(r=>r.id===i.region_id)?.name_ru||i.region_id)+' · '+esc(i.district)+' район</p>'+
      '<div class="signal-volume"><strong>'+i.count+'</strong><span>обращений<br>'+(i.in_current_window?'за '+i.window_minutes+' минут':'в сохранённой группе')+'</span></div>'+
      '<p class="micro">'+stamp(i.first_at)+' — '+stamp(i.last_at)+'</p>'+
      (!i.in_current_window?'<p class="micro">Сохранённая группа, вне текущего окна.</p>':'')+
      '<div class="signal-assignment"><strong>'+esc(i.owner_name||'Нужен ответственный')+'</strong><p class="'+(i.overdue?'danger-text':'micro')+'">'+(i.check_at?'Проверить до '+stamp(i.check_at):'Следующий шаг: открыть и взять в работу')+'</p></div>'+
      (i.incident_id?'<p class="micro">Связан с '+esc(i.incident_id)+'</p>':'')+
      button('radar-preview','Открыть сигнал →','primary wide','data-id="'+esc(i.id)+'"')+'</article>'
    ).join('')+(signals.length?'':empty('Сигналов в этом списке нет',s.radarSource==='real'?'Новые группы появятся, когда поступят подходящие реальные обращения. Демонстрационные данные доступны в отдельном источнике.':'Можно добавить демонстрационный сигнал для проверки действий.'))+'</div>';
}

export function signalView(i,topics,user) {
  const canWork=!i.owner_id||i.owner_id===user?.id;
  const workForm=!i.ignored&&i.status!=='confirmed'?'<section class="control-panel signal-controls"><h3>Следующее действие</h3><p>'+esc(i.owner_name||'Ответственный не назначен')+'</p>'+
    (i.check_at?'<p class="'+(i.overdue?'danger-text':'micro')+'">Проверить до '+stamp(i.check_at)+'</p>':'')+
    (canWork?'<form id="radar-work-form"><label for="radar-minutes">Проверить через</label><select id="radar-minutes" name="minutes"><option value="15">15 минут</option><option value="30" selected>30 минут</option><option value="60">1 час</option><option value="120">2 часа</option></select><label for="radar-note">Комментарий, если нужен</label><textarea id="radar-note" name="note" rows="2" maxlength="1000" placeholder="Например: ожидаем ответ службы"></textarea><div class="radar-work-actions"><button type="submit" name="action" value="'+(i.snoozed?'resume':i.owner_id?'schedule':'claim')+'" class="button primary">'+(i.snoozed?'Вернуть в работу':i.owner_id?'Сохранить срок':'Взять в работу')+'</button><button type="submit" name="action" value="snooze" class="button ghost">Отложить проверку</button></div></form>':'<p class="micro">Проверкой занимается другой оператор.</p>')+'</section>':'';
  return '<div class="case-header"><div><span class="eyebrow">ПРОВЕРКА СИГНАЛА · '+esc(origins[i.data_origin]||i.data_origin)+'</span><h2 id="case-title">'+esc(topicName(i.category,topics))+' · '+esc(i.district)+'</h2></div>'+button('close','×','icon-button','aria-label="Закрыть сигнал"')+'</div>'+
    '<div class="incident-detail"><div class="signal-review"><h3>'+i.count+' обращений · '+statusName(i)+'</h3><p>'+esc(i.reason)+'</p>'+
    (!i.in_current_window?'<p>Показана сохранённая группа. Новых обращений за последние '+i.window_minutes+' минут нет.</p>':'')+
    '<p>'+(i.incident_id?'Уже есть '+esc(i.incident_id)+'. Проверьте новые связи.':'Создание инцидента объединит работу по этим обращениям.')+' Оригиналы, категории и решения по обращениям сохранятся.</p></div>'+workForm+
    '<h3>Обращения</h3><div class="incident-members">'+i.members.map(m=>button('open','<strong>'+esc(m.id)+'</strong><span>'+esc(m.text)+'</span> →','member','data-id="'+esc(m.id)+'"')).join('')+'</div>'+
    '<details class="signal-history"><summary>История действий</summary><ol class="timeline">'+(i.history||[]).map(e=>'<li><span class="timeline-dot"></span><div><strong>'+esc(eventNames[e.action]||e.action)+'</strong><small>'+stamp(e.occurred_at)+(e.payload.owner_name?' · '+esc(e.payload.owner_name):'')+'</small>'+(e.payload.note?'<p>'+esc(e.payload.note)+'</p>':'')+'</div></li>').join('')+'</ol></details><p id="signal-error" class="danger-text" role="alert"></p></div>'+
    '<div class="case-footer"><span>Отклонение сигнала сохраняет обращения в очереди.</span><div>'+button('radar-reload','Обновить карточку','ghost')+button('radar-ignore',i.ignored?'Восстановить':'Отклонить сигнал','ghost')+(i.incident_id?button('incident','Открыть '+esc(i.incident_id),'ghost','data-id="'+esc(i.incident_id)+'"'):'')+(!i.ignored&&!i.snoozed&&(!i.incident_id||i.unlinked_count)?button('radar-confirm',i.incident_id?'Связать новые обращения ('+i.unlinked_count+')':'Создать инцидент','primary'):'')+'</div></div>';
}

export function incidentView(i,operators) {
  if(i.incident_owner_id&&!operators.some(o=>o.id===i.incident_owner_id)) operators=[...operators,{id:i.incident_owner_id,name:i.owner_name||i.incident_owner_id,status:'online'}];
  const owner=operators.find(o=>o.id===i.incident_owner_id);
  const events=[...i.timeline].reverse(), timeline=items=>items.map(e=>`<li><span class="timeline-dot"></span><div><strong>${esc(e.text)}</strong><small>${stamp(e.at)}</small></div></li>`).join('');
  const statuses=['Проверяется','Передано службе','Работы ведутся','Завершён'];
  return `<div class="case-header"><div><span class="eyebrow">ИНЦИДЕНТ · ${esc(origins[i.data_origin]||i.data_origin)} · ${esc(i.id)}</span><h2 id="case-title">${esc(i.title)}</h2></div>${button('close','×','icon-button','aria-label="Закрыть инцидент"')}</div>
    <div class="incident-detail"><div class="incident-overview"><div>${badge(i.status,'green')}<p>${esc(i.district)} район · ${esc(i.service_name)}</p></div><div class="update-status ${i.overdue_minutes>0?'danger-text':''}"><strong>${i.status==='Завершён'?'Инцидент завершён':i.overdue_minutes>0?`Обновление просрочено на ${i.overdue_minutes} мин`:'Следующее обновление'}</strong><p>${i.status==='Завершён'?stamp(i.last_update_at):stamp(i.next_update)}</p></div></div>
    <div class="incident-facts"><div><strong>${i.count}</strong><span>связанных обращений</span></div><div><strong>${i.streets}</strong><span>улиц</span></div><div><strong>${stamp(i.first_signal_at)}</strong><span>первое обращение</span></div><div><strong>${stamp(i.last_related_case_at)}</strong><span>последнее обращение</span></div></div>
    <div class="control-grid"><section><div class="section-top"><h3>Ход работы</h3><span class="micro">Только сохранённые события</span></div><ol class="timeline">${timeline(events.slice(0,8))}</ol>${events.length>8?`<details><summary>Ранее · ещё ${events.length-8} событий</summary><ol class="timeline">${timeline(events.slice(8))}</ol></details>`:''}<h3>Связанные обращения</h3><div class="incident-members">${i.members.map(m=>button('open',`<strong>${esc(m.id)}</strong><span>${esc(m.text)}</span> →`,'member',`data-id="${esc(m.id)}"`)).join('')}</div></section>
    <aside class="control-panel"><h3>${esc(owner?.name||'Ответственный не назначен')}</h3><p class="micro">Важность: ${['Низкая','Повышенная','Высокая'][i.severity-1]}</p><p class="micro">Обновлено ${stamp(i.last_update_at)}</p><details><summary>Обновить статус и ответственного</summary><form id="incident-update-form"><label for="incident-status">Статус</label><select id="incident-status" name="status">${statuses.map(s=>`<option ${s===i.status?'selected':''}>${s}</option>`).join('')}</select><label for="incident-owner">Ответственный оператор</label><select id="incident-owner" name="incident_owner_id"><option value="">Не назначен</option>${operators.map(o=>`<option value="${esc(o.id)}" ${o.id===i.incident_owner_id?'selected':''}>${esc(o.name)}${o.status==='online'?'':' · не на линии'}</option>`).join('')}</select><label for="incident-severity">Важность</label><select id="incident-severity" name="severity">${[1,2,3].map(n=>`<option value="${n}" ${n===i.severity?'selected':''}>${n} — ${['Низкая','Повышенная','Высокая'][n-1]}</option>`).join('')}</select><label for="incident-next">Следующее обновление · местное время</label><input id="incident-next" type="datetime-local" name="next_update" value="${localInput(i.next_update)}" ${i.status==='Завершён'?'':'required'}><label for="incident-note">Подтверждённая информация</label><textarea id="incident-note" name="note" rows="3" maxlength="2000" required placeholder="Что известно и откуда получено обновление?"></textarea><p class="micro">Закрытие инцидента не закрывает обращения автоматически.</p><button class="button primary wide" type="submit">Проверить изменения</button></form></details></aside></div></div>`;
}
