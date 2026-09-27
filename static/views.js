export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
export const pct = value => Math.round(value * 100) + '%';
export const groups = {urgent:'Срочные', attention:'Требуют внимания', normal:'Обычные', awaiting_citizen:'Ожидают гражданина', awaiting_service:'Ожидают службу', resolved:'Завершены', quarantine:'Карантин'};
export const channels = {web:'Веб-форма', phone:'Звонок 109', telegram:'Telegram', whatsapp:'WhatsApp'};
export const time = value => value ? new Date(value).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit', timeZone:'Asia/Almaty'}) : '—';
export const topicName = (id, topics) => topics.find(t => t.id === id)?.name_ru || 'Нужно уточнение';
export const badge = (text, tone='neutral') => `<span class="badge ${tone}">${esc(text)}</span>`;
export const button = (action, text, cls='', attrs='') => `<button class="button ${cls}" data-action="${action}" ${attrs}>${text}</button>`;
export const empty = text => `<div class="empty"><span aria-hidden="true">✓</span><h3>${esc(text)}</h3><p>Измените фильтр или создайте демо-обращение.</p></div>`;
export function locationMap(location,id,label) {
  if(location?.latitude==null || location?.longitude==null) return '';
  return `<section class="location-card"><div class="section-top"><h3>Место обращения</h3>${badge('Точка подтверждена','green')}</div><div id="${id}" class="issue-map issue-map-view" data-map-mode="view" data-latitude="${location.latitude}" data-longitude="${location.longitude}" data-label="${esc(label)}" aria-label="${esc(label)}"></div><p class="micro">Точная точка доступна заявителю и оператору в этом демо.</p></section>`;
}
export function heading(eyebrow, title, subtitle, actions='') {
  return `<div class="page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p class="subtitle">${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`;
}
export function incidentCard(i, compact=false) {
  return `<article class="incident-card ${compact?'compact':''}"><div class="section-top"><span class="eyebrow">МАССОВЫЙ ИНЦИДЕНТ</span>${badge(i.status,'green')}</div>
    <span class="incident-symbol" aria-hidden="true">≈</span><h3>${esc(i.title)}</h3><p class="muted">${esc(i.id)} · ${esc(i.service_name)}</p>
    <div class="incident-numbers"><div><strong>${i.count}</strong><span>обращений</span></div><div><strong>${i.streets}</strong><span>улицы</span></div><div><strong>${i.minutes}</strong><span>минут</span></div></div>
    <div class="street-map" aria-hidden="true"><span>Абая</span><i></i><span>Масанчи</span><i></i><span>Шевченко</span></div>
    <p class="micro">Оригиналы сохранены. Связи подтверждены в демо.</p>
    ${button('incident', 'Открыть инцидент <span>↗</span>', 'wide ghost', `data-id="${esc(i.id)}"`)}</article>`;
}
export function queueView(s) {
  const quarantine = s.page === 'quarantine';
  const visible = s.items.filter(d => (!s.region || d.complaint.region_id === s.region) && (quarantine ? d.group === 'quarantine' : d.group !== 'quarantine'));
  const counts = Object.fromEntries(Object.keys(groups).map(g => [g, visible.filter(d=>d.group===g).length]));
  const needle = s.search.toLocaleLowerCase();
  const rows = visible.filter(d => (s.group ? d.group === s.group : d.group !== 'resolved') && (!needle || [d.complaint.id,d.complaint.text,d.triage.extracted_address].join(' ').toLocaleLowerCase().includes(needle)));
  const tabs = ['','urgent','attention','normal','awaiting_citizen','awaiting_service','resolved'];
  let lastGroup = '';
  const ordered = [...rows].sort((a,b)=>Object.keys(groups).indexOf(a.group)-Object.keys(groups).indexOf(b.group) || b.priority_score-a.priority_score);
  const rowHTML = ordered.map(d => {
    const c=d.complaint, ai=d.triage;
    const section = lastGroup !== d.group ? `<div class="queue-group"><span class="group-dot ${d.group}"></span>${groups[d.group]}<span>${counts[d.group]}</span>${d.group==='urgent'?'<small>Открыть в первую очередь</small>':''}</div>` : '';
    lastGroup=d.group;
    const sla = d.sla_remaining == null ? 'На контроле' : d.sla_remaining < 0 ? `Просрочено ${Math.abs(d.sla_remaining)} м` : `${d.sla_remaining} мин`;
    return `${section}<button class="queue-row" data-action="open" data-id="${esc(c.id)}" aria-label="Открыть ${esc(c.id)}: ${esc(ai.summary)}">
      <span class="row-icon ${d.group}">${d.group==='urgent'?'!':ai.category==='water_supply'?'≈':d.group==='awaiting_citizen'?'?':'▤'}</span>
      <span class="row-content"><span class="row-meta"><span class="record-id">${esc(c.id)}</span><span>${esc(channels[c.channel]||'Веб-форма')} · ${esc(c.language.toUpperCase())}</span>${c.incident_id?badge(c.incident_id,'green'):d.flags.includes('duplicate_candidate')?badge(`${d.similar.length} похожих`,'blue'):d.flags.includes('spam_suspected')?badge('Проверить спам','amber'):''}</span>
      <strong class="row-title">${esc(c.text)}</strong><span class="row-sub">${esc(ai.extracted_address||'Адрес не уточнён')} <span>·</span> ${esc(topicName(c.topic||ai.category,s.topics))}</span></span>
      <span class="row-confidence"><span class="ai-label">✦ ${pct(ai.category_confidence)}</span><small>${ai.confidence_kind==='laya_probability'?'Laya confidence':'demo confidence'}</small></span>
      <span class="row-sla ${d.sla_remaining!=null && d.sla_remaining<10?'danger-text':''}"><strong>${sla}</strong><small>${d.waiting_minutes} мин в системе</small></span><span class="row-arrow">›</span></button>`;
  }).join('');
  return heading(s.region==='KZ-ALA'?'ОПЕРАТОРСКАЯ · АЛМАТЫ':'ОПЕРАТОРСКАЯ · ВСЕ РЕГИОНЫ',quarantine?'Карантин':'Всё важное — в одной очереди',quarantine?'Подозрение требует проверки. Каждое обращение можно вернуть.':'Приоритеты расставлены. Следующее решение — за вами.',
    `${button('refresh','↻ Обновить','ghost')}${button('demo','＋ Демо: нет воды','primary')}`) +
    `<div class="metrics-strip"><div><span>Ждут решения</span><strong>${visible.filter(d=>['urgent','attention','normal'].includes(d.group)).length}<small>обращений</small></strong></div><div><span>Срочные</span><strong class="danger-text">${counts.urgent||0}<small>проверить сейчас</small></strong></div><div><span>Связаны с инцидентами</span><strong>${s.metrics.linked}<small>оригиналы сохранены</small></strong></div><div><span>Нагрузка команды</span><strong>${s.metrics.operator_load}%<small>занято от ёмкости</small></strong></div></div>
    <div class="inbox-layout"><section class="inbox" aria-label="Очередь обращений"><div class="inbox-toolbar"><label class="search"><span aria-hidden="true">⌕</span><input id="search" type="search" placeholder="Найти по тексту, адресу или номеру" value="${esc(s.search)}" aria-label="Поиск обращений"></label><select id="queue-region" class="region-filter" aria-label="Регион очереди"><option value="KZ-ALA" ${s.region==='KZ-ALA'?'selected':''}>Алматы</option><option value="" ${s.region===''?'selected':''}>Все регионы</option></select><span class="sort-label">↓ По приоритету</span></div>
    ${quarantine?'':`<div class="tabs" aria-label="Группы очереди">${tabs.map(g=>button('filter',`${g?groups[g]:'Все'} <span>${g?counts[g]:visible.filter(d=>d.group!=='resolved').length}</span>`,s.group===g?'selected':'',`data-group="${g}" aria-pressed="${s.group===g}"`)).join('')}</div>`}
    <div id="queue-rows">${rowHTML||empty(quarantine?'Карантин пуст':'В этой группе нет обращений')}</div><div class="list-footer">${rows.length} обращений · Срочность + SLA + ожидание + инцидент</div></section>
    <aside class="insights"><div class="section-top"><h2>Пульс города</h2><span class="live"><i></i> Демо</span></div>${s.incidents.slice(0,1).map(i=>incidentCard(i,true)).join('')}
    <div class="how-card"><span class="eyebrow">ВАШ СЛЕДУЮЩИЙ ШАГ</span><h3>От сообщения<br>к решению за 3 шага</h3><ol><li><b>01</b><span>Откройте обращение<small>Срочные всегда сверху</small></span></li><li><b>02</b><span>Проверьте предложение AI<small>Категория, инцидент, оператор</small></span></li><li><b>03</b><span>Подтвердите решение<small>Очередь и аналитика обновятся</small></span></li></ol></div></aside></div>`;
}
export function dashboardView(s) {
  const m=s.metrics;
  const q=m.laya_quality||{};
  const values = [['Обращений сегодня',m.today,`${m.total} всего в демо`],['Активные инциденты',m.active_incidents,'единая картина проблемы'],['Обращения в инцидентах',m.linked,'каждый оригинал сохранён'],['В карантине',m.quarantined,'убраны из основной очереди'],['Среднее до решения',m.average_response_seconds==null?'—':`${Math.floor(m.average_response_seconds/60)}м ${m.average_response_seconds%60}с`,'по подтверждённым в демо'],['SLA под риском',m.sla_at_risk,'менее 10 минут или просрочено']];
  const cats=Object.entries(m.categories).sort((a,b)=>b[1]-a[1]);
  return heading('СИТУАЦИОННЫЙ ЦЕНТР','Каждое решение меняет картину','Живые показатели синтетической базы · обновляются после действий оператора.',button('refresh','↻ Обновить','ghost'))+
    `<div class="dashboard-metrics">${values.map(([label,value,note])=>`<article class="metric-card"><p>${label}</p><strong>${value}</strong><small>${note}</small></article>`).join('')}</div>
    <div class="dashboard-grid"><section class="panel"><div class="section-top"><h2>Обращения по категориям</h2>${badge('Все записи')}</div><div class="bars">${cats.map(([id,n])=>`<div class="bar-row"><span>${esc(topicName(id,s.topics))}</span><div><i style="width:${n/Math.max(...cats.map(c=>c[1]))*100}%"></i></div><b>${n}</b></div>`).join('')}</div></section>
    <section class="impact-card"><span class="eyebrow">✦ AI IMPACT · ДЕМО</span><h2>Меньше повторного<br>разбора. Больше внимания.</h2><div><strong>${m.consolidated}</strong><span>повторных разборов можно объединить<br>в рамках подтверждённых инцидентов</span></div><div><strong>${m.quarantined}</strong><span>подозрительных сообщений<br>вне основной очереди</span></div><div><strong>${m.high_confidence_percent}%</strong><span>с высокой уверенностью<br>требуют подтверждения человеком</span></div><p>Экономия времени не измерялась. Это демонстрация процесса, а не оценка эффективности модели.</p></section>
    <section class="panel"><div class="section-top"><h2>Laya Classification</h2>${badge(q.label||'Synthetic / demo metrics','amber')}</div><div class="dashboard-metrics"><article class="metric-card"><p>Без изменений</p><strong>${q.confirmed_without_changes_percent==null?'—':q.confirmed_without_changes_percent+'%'}</strong><small>${q.decisions||0} решений</small></article><article class="metric-card"><p>Исправления оператора</p><strong>${q.operator_override_percent==null?'—':q.operator_override_percent+'%'}</strong><small>только синтетические обращения</small></article><article class="metric-card"><p>RU / KK agreement</p><strong>${q.language_agreement_percent?.ru??'—'} / ${q.language_agreement_percent?.kk??'—'}</strong><small>проценты, если есть подтверждения</small></article></div><p class="micro">Чаще исправляют: ${q.most_corrected?esc(topicName(q.most_corrected,s.topics)):'—'}. Это не benchmark accuracy.</p></section></div>`;
}
export function operatorsView(s) {
  return heading('КОМАНДА','Нужный человек для каждого случая','Служба, район, язык и взвешенная нагрузка. Веса синтетические: звонок 100, чат 50, срочное 40, обычное 30, связанный дубль 15.')+
    `<div class="operator-grid">${s.operators.map(o=>`<article class="panel operator-card"><div class="section-top"><span class="avatar">${esc(o.name.slice(0,2).toUpperCase())}</span>${badge(o.status==='online'?'На линии':'Перерыв',o.status==='online'?'green':'neutral')}</div><h2>${esc(o.name)}</h2><p>${esc(o.languages.join(' / ').toUpperCase())} · ${esc(o.district||'Все районы')}</p><div class="skill-tags">${o.skills.map(t=>badge(topicName(t,s.topics))).join('')}</div><div class="load-label"><span>Занято слотов</span><strong>${o.current_load} / ${o.capacity}</strong></div><progress value="${o.workload}" max="${o.workload_capacity}" aria-label="Нагрузка ${esc(o.name)}"></progress><p class="micro">${o.workload}/${o.workload_capacity} баллов · ${o.workload>=o.workload_capacity||o.current_load>=o.capacity?'Перегружен':'Есть ёмкость'}</p></article>`).join('')}</div>`;
}
export function incidentsView(s) {
  return heading('ЕДИНАЯ КАРТИНА','Инциденты вместо сотен повторов','Связанные обращения остаются в системе. Решение о связи принимает оператор.')+
    `<div class="incident-grid">${s.incidents.map(i=>incidentCard(i)).join('')||empty('Нет активных инцидентов')}</div>`;
}
export function trackingView(t) {
  if(!t) return '<p class="micro">Введите номер из квитанции, чтобы проверить статус.</p>';
  const statuses={pending:'Ожидает решения оператора',confirmed:'Решение оператора подтверждено',needs_clarification:'Нужно уточнение',clarification_received:'Уточнение получено · ожидает проверки',under_review:'На проверке у оператора',resolved:'Обращение завершено'};
  const names={reply_saved:'Ответ оператора · демо',clarification_requested:'Вопрос оператора',clarification_received:'Полученное уточнение'};
  const date=value=>new Date(value).toLocaleString('ru-RU',{timeZone:'Asia/Almaty'});
  return `<article class="tracking-card"><h3>${esc(t.id)}</h3><p>${badge(statuses[t.status]||'В обработке',t.status==='needs_clarification'?'amber':'green')}</p><p class="micro">Зарегистрировано ${esc(date(t.registered_at))}</p>
    ${locationMap(t.location,'tracking-map',`Место обращения ${t.id}`)}
    ${t.service_name?`<p>Ответственная служба в демо: <strong>${esc(t.service_name)}</strong></p>`:''}
    ${t.incident?`<div class="receipt"><strong>${esc(t.incident.title)}</strong><p>${esc(t.incident.id)} · ${esc(t.incident.status)}</p>${t.incident.next_update?`<p>Следующее обновление демо-статуса: ${esc(date(t.incident.next_update))}</p>`:''}</div>`:''}
    ${t.resolved_at?`<div class="receipt"><strong>Завершено ${esc(date(t.resolved_at))}</strong><p>${esc(t.resolution_text||'Комментарий о результате не указан.')}</p></div>`:''}
    ${t.updates.length?`<ul class="timeline">${t.updates.map(u=>`<li><span class="timeline-dot"></span><div><strong>${names[u.type]}</strong><small>${esc(date(u.at))}</small><p>${esc(u.text)}</p></div></li>`).join('')}</ul>`:'<p class="micro">Сохранённых сообщений пока нет.</p>'}
    <p class="micro">Статус из демо-базы. Внешняя доставка сообщений не подключена.</p></article>`;
}
export function citizenView(s) {
  const incident=s.incidents.find(i=>i.district==='Алмалинский' && i.status!=='Завершён');
  return heading('ГОРОД НА СВЯЗИ','Расскажите, что произошло','Демонстрационная форма. Не вводите персональные данные.')+
    `<div class="citizen-layout"><form id="citizen-form" class="panel"><h2>Новое обращение</h2><label for="citizen-district">Район Алматы</label><select id="citizen-district" name="district"><option>Алмалинский</option><option>Бостандыкский</option><option>Медеуский</option><option>Ауэзовский</option></select><label for="citizen-language">Язык обращения</label><select id="citizen-language" name="language"><option value="ru">Русский</option><option value="kk">Қазақша</option></select><label for="citizen-text">Что произошло?</label><textarea id="citizen-text" name="text" rows="6" maxlength="10000" required placeholder="Например: на Абая 44 с утра нет воды…"></textarea><label for="citizen-address">Адрес или ориентир</label><input id="citizen-address" name="address" maxlength="200" autocomplete="street-address" placeholder="Например: Абая 44"><fieldset class="location-field"><legend>Где это произошло?</legend><p class="micro">Нажмите на карту, перетащите метку или определите текущую геопозицию.</p><div class="map-shell"><div id="citizen-map" class="issue-map issue-map-picker" data-map-mode="picker" aria-label="Выберите место обращения на карте Алматы"></div><span class="map-tip">Алматы · интерактивная карта</span></div><div class="location-status"><span data-location-status role="status">Нажмите на карту или используйте кнопку геопозиции</span><button type="button" class="button ghost small" data-location-clear>Сбросить</button></div><input type="hidden" name="latitude"><input type="hidden" name="longitude"><input type="hidden" name="location_accuracy_m"></fieldset><button class="button primary wide" type="submit">Зарегистрировать обращение</button><div id="receipt" role="status"></div></form>
    <aside><div id="citizen-notice">${incident?`<div class="deflection"><span class="eyebrow">ПРЕЖДЕ ЧЕМ ОТПРАВИТЬ</span><span class="incident-symbol">≈</span><h2>В вашем районе уже зарегистрировано отключение воды</h2><p>${incident.count} обращений · ${esc(incident.district)} район</p>${badge(incident.status,'green')}<p>Следующее обновление демо-статуса: <strong>${time(incident.next_update)}</strong></p>${button('subscribe','Подписаться на обновления','primary wide',`data-id="${esc(incident.id)}"`)}${button('anyway','Всё равно создать обращение','ghost wide')}<small>Подписка сохраняется локально в демо. Внешние уведомления не подключены.</small></div>`:''}</div>
    <section class="panel tracking-panel" aria-labelledby="tracking-title"><h2 id="tracking-title">Статус обращения</h2><form id="tracking-form"><label for="tracking-id">Номер обращения</label><input id="tracking-id" name="complaint_id" value="${esc(s.trackingId)}" placeholder="PULSE-…" maxlength="100" required autocomplete="off" spellcheck="false"><button class="button primary wide" type="submit">Проверить статус</button></form><div id="tracking-result" role="status">${trackingView(s.tracking)}</div></section></aside></div>`;
}
