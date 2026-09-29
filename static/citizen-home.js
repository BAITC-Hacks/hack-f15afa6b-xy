import {esc,heading,button,subscriptionsView,trackingView} from './views.js?v=20260929-2gis';

export const citizenPages=['home','requests','citizen','map','help'];
export function citizenList(s) {
  const query=(s.citizenSearch||'').trim().toLocaleLowerCase('ru');
  const items=s.subscriptions.filter(item=>`${item.id} ${item.text}`.toLocaleLowerCase('ru').includes(query))
    .filter(item=>!s.citizenFilter||(s.citizenFilter==='active'?item.status!=='resolved':item.status===s.citizenFilter));
  if(!items.length&&s.subscriptions.length) return '<p class="micro">По этим условиям обращений нет. Измените поиск или статус.</p>';
  return subscriptionsView(items,true);
}
export function citizenContent(s) {
  if(!s.citizenLoaded) return '<p role="status">Загружаем ваши обращения…</p>';
  if(s.page==='requests') return `<div class="citizen-filters"><label>Найти обращение<input id="citizen-search" type="search" value="${esc(s.citizenSearch)}" placeholder="Номер или слова из описания"></label><label>Статус<select id="citizen-filter"><option value="">Все обращения</option>${[['active','В работе'],['needs_clarification','Запрошено уточнение'],['resolved','Завершены']].map(([value,label])=>`<option value="${value}" ${s.citizenFilter===value?'selected':''}>${label}</option>`).join('')}</select></label></div><div id="citizen-case-list">${citizenList(s)}</div>`;
  const active=s.subscriptions.filter(item=>item.status!=='resolved').length;
  const attention=s.subscriptions.filter(item=>item.status==='needs_clarification');
  const recent=[...s.subscriptions].sort((a,b)=>b.last_updated.localeCompare(a.last_updated)).slice(0,3);
  return `<div class="citizen-totals"><div><strong>${s.subscriptions.length}</strong><span>Всего обращений</span></div><div><strong>${active}</strong><span>В работе</span></div><div><strong>${s.subscriptions.length-active}</strong><span>Завершены</span></div></div>
    ${attention.length?`<section class="citizen-attention"><h2>Оператор запросил уточнение</h2><p>Откройте историю обращения, чтобы прочитать вопрос.</p>${attention.map(item=>button('track',item.id,'ghost',`data-id="${esc(item.id)}"`)).join('')}</section>`:''}
    <div class="section-top"><h2>Последние обновления</h2><a class="button ghost" href="#requests">Все обращения</a></div>${recent.length?subscriptionsView(recent,true):'<p class="micro">У вас пока нет обращений. Можно посмотреть опубликованные проблемы на карте или сообщить о новой.</p>'}`;
}
export function citizenHome(s) {
  return heading('Обзор','Статусы ваших обращений и последние ответы оператора.')+
    `<div class="citizen-shortcuts"><a class="panel" href="#citizen"><strong>Подать обращение <span aria-hidden="true">↗</span></strong><p>Описать проблему, указать место и приложить фото.</p></a><a class="panel" href="#map"><strong>Посмотреть карту <span aria-hidden="true">↗</span></strong><p>Найти опубликованные обращения по городу, адресу и теме.</p></a><a class="panel" href="#help"><strong>Как пользоваться <span aria-hidden="true">↗</span></strong><p>Что означают статусы и где искать ответы.</p></a></div><section class="panel citizen-summary"><div id="citizen-content">${citizenContent(s)}</div></section>`;
}
export function citizenRequests(s) {
  return heading('Мои обращения','Здесь доступны только обращения вашего аккаунта.', '<a class="button primary" href="#citizen">Подать обращение</a>')+
    `<div class="citizen-requests-layout"><section class="panel citizen-summary"><div class="section-top"><h2>История обращений</h2>${button('refresh-subscriptions','Обновить','ghost')}</div><div id="citizen-content">${citizenContent(s)}</div></section><section class="panel citizen-summary" aria-label="Статус выбранного обращения"><h2>Статус и ответы</h2><form id="tracking-form"><label for="tracking-id">Номер обращения</label><input id="tracking-id" name="complaint_id" value="${esc(s.trackingId)}" placeholder="Выберите обращение из списка" required><button class="button ghost" type="submit">Проверить статус</button></form><div id="tracking-result" role="status">${trackingView(s.tracking)}</div></section></div>`;
}
export function citizenHelp() {
  return heading('Помощь','Как подать обращение, проверить статус и прочитать ответ.')+
    `<section class="panel citizen-help"><details open><summary>С чего начать?</summary><p>Если хотите узнать, сообщал ли кто-то о похожей проблеме, откройте <a href="#map">карту обращений</a>. Используйте поиск по адресу, фильтр города, категории и статуса. Карта показывает только опубликованные обращения, поэтому отсутствие отметки не означает отсутствие проблемы.</p></details><details><summary>Где посмотреть ответ оператора?</summary><p>Откройте <a href="#requests">«Мои обращения»</a> и нажмите «Открыть историю». Там отображаются статус, комментарии оператора и результат обработки, когда он сохранён.</p></details><details><summary>Что означают статусы?</summary><dl><dt>На рассмотрении</dt><dd>Обращение зарегистрировано и ожидает решения оператора.</dd><dt>Принято в работу</dt><dd>Оператор подтвердил решение по обращению. Это ещё не означает, что проблема устранена.</dd><dt>Нужно уточнение</dt><dd>Оператор запросил дополнительные сведения. Вопрос доступен в истории.</dd><dt>Завершено</dt><dd>В истории указан сохранённый результат обработки.</dd></dl></details><details><summary>Как подать обращение?</summary><p>В разделе <a href="#citizen">«Подать обращение»</a> выберите регион и город, опишите проблему и отметьте место на карте. При необходимости приложите фото или видео. Описание можно заполнить голосом на русском или казахском языке.</p></details><details><summary>Кто видит мои данные?</summary><p>Ваши обращения и вложения доступны вам и операторам. Обезличенное описание появляется на публичной карте только с вашего согласия и после проверки. Фото и видео остаются приватными.</p></details><details><summary>Приходят ли уведомления?</summary><p>Статусы и ответы доступны в личном кабинете. Чтобы проверить изменения, откройте «Мои обращения» и нажмите «Обновить». SMS и push-уведомления пока не подключены.</p></details></section>`;
}
