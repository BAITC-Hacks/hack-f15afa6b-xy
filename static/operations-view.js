import {esc, badge, button, heading, topicName} from './views.js?v=20260928-6';

const wait=value=>value==null?'Нет доступной ёмкости':value<60?`${value} сек`:`${Math.floor(value/60)}м ${value%60}с`;

export function liveOperatorView(state) {
  const live=state.live||{}, session=live.session, data=session?.state||{}, detected=data.detected||{};
  const candidate=data.incident_candidate, active=session?.status==='active';
  const actions=live.mode==='microphone'&&live.running
    ?button('stop-live-microphone','■ Завершить звонок','primary')
    :button('start-live-microphone','🎙 Живой звонок','primary',live.running?'disabled':'')+button('start-live-demo',live.running?'Live demo идёт…':session?'Новый демо-звонок':'▶ Демо-звонок','ghost',live.running?'disabled':'');
  return heading('LIVE CALL · OPERATOR ASSIST','Понимание разговора до завершения звонка',
    'Partial transcript обновляется по паузе, новой сущности или раз в 3 секунды. Laya остаётся shadow-сигналом.',
    actions)+
    `<div class="live-call-layout"><section class="panel live-transcript-panel" data-live-panel>
      <div class="section-top"><div><span class="eyebrow">LIVE CALL</span><h2>${esc(session?.id||'Сессия не начата')}</h2></div>${badge(session?.status||'готов','green')}</div>
      <div class="live-clock"><i></i><strong>${esc(live.elapsed||'00:00')}</strong><span>${live.running?'слушаем':'готов'}</span></div>
      <h3>Transcript</h3><blockquote id="live-transcript">${esc(data.transcript||'Начните живой звонок, демо или введите partial transcript ниже.')}</blockquote>
      <form id="live-checkpoint-form" class="live-checkpoint"><label for="live-text">Partial transcript</label><textarea id="live-text" name="text" rows="3" maxlength="10000" ${active?'':'disabled'}>${esc(data.transcript||'')}</textarea><button class="button ghost" type="submit" ${active?'':'disabled'}>Обработать паузу</button></form>
      <p class="micro">Промежуточные буквы не пишутся в audit. Сохраняются только semantic checkpoints.</p>
    </section>
    <section class="panel live-assist-panel" aria-live="polite">
      <div class="section-top"><h2>Detected</h2>${detected.category?badge('AI observation','amber'):badge('Ожидание')}</div>
      <dl class="live-detected"><div><dt>Адрес</dt><dd>${esc(detected.address||'—')}</dd></div><div><dt>Категория</dt><dd data-live-category>${esc(topicName(detected.category,state.topics))}${detected.confidence!=null?` · ${Math.round(detected.confidence*100)}%`:''}</dd></div><div><dt>Масштаб</dt><dd>${esc(detected.scope||'—')}</dd></div></dl>
      <p class="micro">${esc(data.triage?.provider||'AI ещё не вызывался')} · human confirmation required${data.triage?.fallback_reason?' · fallback active':''}</p>
      <div class="live-candidate"><span class="eyebrow">POSSIBLE INCIDENT</span>${candidate?`<h3>${esc(candidate.id)} · ${candidate.similar_complaints} похожих обращений</h3><p>${esc(candidate.title)}</p><small>${esc(candidate.reason)}</small>`:'<h3>Кандидат пока не найден</h3><p>Поиск обновится на следующем semantic checkpoint.</p>'}</div>
      <div class="live-response"><span class="eyebrow">SUGGESTED RESPONSE</span><p>${esc(data.suggested_response||'—')}</p></div>
      ${active&&detected.category?`<div class="case-footer">${button('ignore-live','Ignore','ghost')}${button('apply-live',candidate?'Apply · связать с инцидентом':'Apply · создать обращение','primary')}</div>`:''}
      ${data.timings_ms?`<p class="micro">STT ${data.timings_ms.stt_latency??'—'} ms · triage ${data.timings_ms.live_triage_latency} ms · similarity ${data.timings_ms.similarity_latency} ms</p>`:''}
    </section></div>`;
}

export function operationsMapView(state) {
  const ops=state.operations||{}, data=ops.map, modes={complaints:'Обращения',incidents:'Инциденты',heat:'Heat',sla_risk:'SLA Risk',operator_load:'Нагрузка',category:'Категория'};
  if(!data) return heading('OPERATIONS MAP','География текущей ситуации','Загружаем внутреннюю карту…')+'<div class="loading">Строим hex-слой…</div>';
  const cells=[...data.cells.features].sort((a,b)=>b.properties.count-a.properties.count);
  return heading('INTERNAL · OPERATIONS MAP','География текущей ситуации',
    'Внутренний слой. Публичная карта остаётся отдельной; синтетические приблизительные точки подписаны.',
    button('refresh-operations','↻ Обновить','ghost'))+
    `<section class="panel ops-map-panel"><div class="ops-map-toolbar" role="group" aria-label="Режим карты">${Object.entries(modes).map(([id,label])=>button('ops-map-mode',label,ops.mode===id?'selected':'',`data-mode="${id}" aria-pressed="${ops.mode===id}"`)).join('')}</div>
      <div class="ops-map-layout"><div><div id="operations-map" class="operations-map" aria-label="Внутренняя карта hex-ячеек"></div><div class="ops-time"><label for="ops-time">Эволюция инцидента · <strong>${Math.abs(ops.offset||0)} мин назад</strong></label><input id="ops-time" type="range" min="-180" max="0" step="15" value="${ops.offset||0}"></div></div>
      <aside><span class="eyebrow">${esc(data.data_origin==='synthetic_demo'?'SYNTHETIC SIMULATION':'INTERNAL OPERATIONS')}</span><h2>${data.points.length} обращений · ${cells.length} ячеек</h2><p class="micro">${esc(data.cell_method)} · сторона ${data.cell_size_metres} м. Это совместимый внутренний hex-grid, не H3.</p>
      <div class="ops-cell-list">${cells.slice(0,8).map(cell=>`<article><strong>${cell.properties.count}</strong><span>${esc(topicName(cell.properties.category,state.topics))}</span><small>${cell.properties.sla_risk} SLA risk · load ${cell.properties.operator_load}%</small></article>`).join('')||'<p>Нет данных в окне.</p>'}</div>
      <div class="ops-clusters"><h3>Cluster detection</h3>${data.clusters.slice(0,4).map(item=>`<p><strong>${item.count}</strong> · ${esc(topicName(item.category,state.topics))} · ${item.neighbor_cells} соседних cells</p>`).join('')||'<p class="micro">Порог 5 обращений ещё не достигнут.</p>'}</div></aside></div></section>`;
}

export function commandCenterView(state) {
  const data=state.operations?.command, simulation=state.operations?.simulation;
  if(!data) return heading('SUPERVISOR','Live command center','Загружаем текущую операционную картину…')+'<div class="loading">Считаем очереди…</div>';
  const cards=[['Queue',data.cards.queue],['Critical',data.cards.critical],['SLA Risk',data.cards.sla_risk],['Operators Online',data.cards.operators_online],['Active Incidents',data.cards.active_incidents],['System Health',data.cards.system_health]];
  const queue=data.hot_queues[0];
  return heading('LIVE SUPERVISOR','Command Center','Компактная картина очередей, инцидентов и решений на 60 минут.',button('refresh-operations','↻ Обновить','ghost'))+
    `<p class="data-origin-note"><strong>${esc(data.label)}.</strong> Прогноз и what-if основаны на текущей synthetic demo базе и не являются гарантией.</p>
    <div class="command-cards">${cards.map(([label,value])=>`<article><span>${label}</span><strong>${esc(value)}</strong></article>`).join('')}</div>
    <div class="command-grid"><section class="panel"><div class="section-top"><h2>Hot queues</h2>${badge('15–120 min')}</div><div class="forecast-queues">${data.hot_queues.map(item=>`<article><div><strong>${esc(topicName(item.queue,state.topics))}</strong><small>${item.current_operators} операторов · backlog ${item.current_backlog}</small></div><div><b>${item.predicted_backlog}</b><small>через 60 мин</small></div><div><b>${wait(item.estimated_wait_seconds)}</b><small>ожидание</small></div><div><b>${item.sla_risk_count}</b><small>SLA risk</small></div><span>${esc(item.recommendation)}</span></article>`).join('')}</div></section>
    <section class="panel"><div class="section-top"><h2>Staffing actions</h2>${badge('Supervisor confirms','amber')}</div>${data.staffing_recommendations.map(item=>`<article class="staffing-action"><strong>${esc(topicName(item.queue,state.topics))}</strong><b>+${item.recommended_operators}</b><p>${esc(item.reasons.join(' · '))}</p></article>`).join('')||'<p class="micro">Дополнительные операторы сейчас не требуются.</p>'}</section></div>
    <section class="panel simulation-panel"><div class="section-top"><div><span class="eyebrow">OPERATIONAL SIMULATION</span><h2>What-if · 60 минут</h2></div>${badge('Reproducible seed 109')}</div>
      <form id="simulation-form" class="simulation-controls"><label>Очередь<select name="queue">${data.hot_queues.map(item=>`<option value="${esc(item.queue)}">${esc(topicName(item.queue,state.topics))}</option>`).join('')}</select></label><label>Incoming volume <output>+0%</output><input name="incoming_percent" type="range" min="0" max="200" value="0"></label><label>Operators <output>+0</output><input name="operator_delta" type="range" min="-3" max="10" value="0"></label><label>Handle time <output>+0%</output><input name="handle_time_percent" type="range" min="-30" max="50" value="0"></label><label>Priority volume <output>20%</output><input name="priority_percent" type="range" min="0" max="100" value="20"></label><label class="incident-toggle"><input name="active_incident" type="checkbox" checked> Active incident</label><button class="button primary" type="submit" ${queue?'':'disabled'}>SIMULATE</button></form>
      ${simulation?`<div class="simulation-result" data-simulation-result><article><span>Текущий сценарий</span><strong>Queue ${simulation.current.queue}</strong><p>Wait ${wait(simulation.current.wait_seconds)} · SLA risk ${simulation.current.sla_risk}</p></article><span class="simulation-arrow">→</span><article><span>Best tested scenario</span><strong>+${simulation.best_tested_scenario.added_operators} operators</strong><p>Queue ${simulation.best_tested_scenario.queue} · Wait ${wait(simulation.best_tested_scenario.wait_seconds)} · SLA risk ${simulation.best_tested_scenario.sla_risk}</p></article><small>${esc(simulation.disclaimer)} · ${simulation.simulation_latency_ms} ms</small></div>`:''}</section>`;
}
