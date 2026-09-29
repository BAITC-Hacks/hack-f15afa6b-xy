import {mountPublicMap,mountTimeMachine} from './map.js?v=20260929-heat-demo-2';
import {demoIncidentHistory} from './incident-time-machine.js?v=20260929-heat-demo-2';

const labels={pending:'На рассмотрении',confirmed:'Принято в работу',needs_clarification:'Нужно уточнение',resolved:'Решено'};

export function publicMapView() {
  const operator=document.body.dataset.citizen==='false'&&!document.querySelector('#public-page');
  return `<section class="workspace-public-map" data-public-map-explorer aria-labelledby="map-page-title">
    <div class="page-heading"><div><h1 id="map-page-title">Обращения на карте</h1><p class="subtitle">Найдите проблему по городу, адресу или номеру обращения.</p></div><a class="button public-share" href="/map">Публичная ссылка ↗</a></div>
    ${operator?`<div class="public-results" role="group" aria-label="Режим карты">
      <button type="button" class="button" data-current-map aria-pressed="true">Текущие обращения</button>
      <button type="button" class="button" data-time-machine-toggle aria-pressed="false" aria-controls="incident-time-machine">История за сутки</button>
    </div><section data-time-machine-panel hidden>
      <form class="public-filters" data-history-filters aria-label="Фильтры истории за сутки">
        <label><span>Дата</span><input type="date" name="day" required aria-label="Дата истории"></label>
        <label><span>Источник</span><select name="source"><option value="demo">Демо heat points · 12 городов</option><option value="actual">Реальные обращения</option><option value="synthetic">Учебные обращения из базы</option><option value="all">Все обращения из базы</option></select></label>
        <label><span>Город</span><select name="city"><option value="">Все города</option></select></label>
        <label><span>Категория</span><select name="topic"><option value="">Все категории</option></select></label>
        <button type="submit" class="button">Обновить историю</button>
      </form>
      <p data-history-status class="public-status" role="status">Выберите дату для просмотра поступивших обращений.</p>
      <div id="incident-time-machine" class="issue-map" style="height:560px;min-height:440px" aria-label="История обращений за сутки"></div>
      <p class="micro" data-history-note>Время — Казахстан (UTC+5). Демо-точки синтетические и нужны только для показа тепловой карты.</p>
      <div data-history-cards class="public-cards" aria-label="Обращения за выбранное время"></div>
    </section>`:''}
        <form id="public-filters" class="public-filters" aria-label="Фильтры обращений">
          <label><span>Поиск</span><input name="search" type="search" placeholder="Адрес, номер, проблема"></label>
          <label><span>Город</span><select name="city"><option value="">Все города</option></select></label>
          <label><span>Категория</span><select name="topic"><option value="">Все категории</option></select></label>
          <label><span>Статус</span><select name="status"><option value="">Все статусы</option><option value="pending">На рассмотрении</option><option value="confirmed">Принято в работу</option><option value="needs_clarification">Нужно уточнение</option><option value="resolved">Решено</option></select></label>
          <button type="reset" class="button" disabled>Сбросить</button>
        </form>
    <div class="public-results"><p id="public-status" class="public-status" role="status">Загружаем обращения…</p><div class="public-view-switch" role="group" aria-label="Вид обращений"><button type="button" class="button" data-map-view="list" aria-pressed="true" aria-controls="public-cards">Список</button><button type="button" class="button" data-map-view="map" aria-pressed="false" aria-controls="public-map">Карта</button></div></div>
    <div class="public-layout" data-view="list">
      <div class="public-map-shell"><div id="public-map" class="issue-map public-map" aria-label="Интерактивная карта обращений"></div><span class="map-tip" id="public-scope">Все города</span><p id="public-map-error" class="public-map-error" role="status" hidden></p></div>
      <aside class="public-feed" aria-label="Список обращений">
        <div class="public-feed-head"><h2>Последние обращения</h2><span class="live">Демо-данные</span></div>
        <div id="public-cards" class="public-cards"></div>
      </aside>
    </div>
  </section>`;
}

export async function mountPublicIssueExplorer(root=document) {
  const explorer=root.matches?.('[data-public-map-explorer]')?root:root.querySelector('[data-public-map-explorer]');
  if(!explorer || explorer.dataset.mapMounted) return;
  explorer.dataset.mapMounted='true';
  const cards=explorer.querySelector('#public-cards'), status=explorer.querySelector('#public-status');
  const filters=explorer.querySelector('#public-filters');
  let allItems=[], visibleItems=[], mapController={focus(){},setItems(){},resize(){}};
  const layout=explorer.querySelector('.public-layout'), reset=filters.querySelector('[type="reset"]');
  const timelineToggle=explorer.querySelector('[data-time-machine-toggle]');
  const historyForm=explorer.querySelector('[data-history-filters]');
  const historyStatus=explorer.querySelector('[data-history-status]');
  const historyCards=explorer.querySelector('[data-history-cards]');
  let timeline, timelinePromise, historyItems=[], historyVersion=0, loadedDay='';
  function showHistoryItems(items, mapped) {
    historyStatus.textContent=items.length?`Обращений: ${items.length} · На карте: ${mapped} · Без координат: ${items.length-mapped}`:'За выбранное время обращений нет. Проверьте дату, источник или передвиньте ползунок.';
    historyCards.replaceChildren(...items.map(item=>{
      const card=document.createElement('button');card.type='button';card.className='public-card';card.dataset.action='open';card.dataset.id=item.id;
      const title=document.createElement('strong');title.textContent=`${item.id} · ${labels[item.status]||'В обработке'}`;
      const text=document.createElement('span');text.textContent=item.text||'Текст не указан';
      const meta=document.createElement('small');meta.textContent=[item.city,item.address,item.topic_name,
        new Date(item.registered_at).toLocaleTimeString(globalThis.pulseLocale||'ru-RU',{timeZone:'Asia/Almaty'}),
        ['synthetic','synthetic_demo'].includes(item.data_origin)?'Учебное обращение':'Обращение из базы',
        item.latitude==null||item.longitude==null?'Координаты не указаны':''].filter(Boolean).join(' · ');
      card.append(title,text,meta);return card;
    }));
  }
  function applyHistory() {
    const items=historyItems.filter(item=>(!historyForm.elements.city.value||item.city===historyForm.elements.city.value)&&
      (!historyForm.elements.topic.value||item.topic===historyForm.elements.topic.value));
    timeline?.setItems(items,loadedDay);
  }
  async function loadHistory() {
    const version=++historyVersion, day=historyForm.elements.day.value;
    historyItems=[];historyCards.replaceChildren();
    timeline?.setItems([],day||loadedDay);
    if(!day) {historyStatus.textContent='Выберите дату.';return;}
    historyStatus.textContent='Загружаем обращения за сутки…';
    try {
      const source=historyForm.elements.source.value;
      let data;
      if(source==='demo') data={items:demoIncidentHistory(day),day};
      else {
        const response=await fetch('/api/workspace/map-history?'+new URLSearchParams({day,source}),{headers:{Accept:'application/json'}});
        if(!response.ok) throw new Error(response.status===401?'Войдите в кабинет оператора.':'Не удалось загрузить историю. Нажмите «Обновить историю».');
        data=await response.json();
      }
      if(version!==historyVersion||!explorer.isConnected) return;
      historyItems=data.items;loadedDay=data.day;
      explorer.querySelector('[data-history-note]').textContent=source==='demo'
        ?'Синтетические heat points по 12 городам. Передвигайте ползунок времени.'
        :'Время — Казахстан (UTC+5). На карте только сохранённые координаты. Статусы показаны на текущий момент.';
      for(const [field,label,name] of [['city','Все города','city'],['topic','Все категории','topic_name']]) {
        const select=historyForm.elements[field], previous=select.value;
        select.replaceChildren(new Option(label,''));
        for(const [value,text] of new Map(historyItems.filter(item=>item[field]).map(item=>[item[field],item[name]]))) select.append(new Option(text,value));
        if([...select.options].some(option=>option.value===previous)) select.value=previous;
      }
      timelinePromise??=mountTimeMachine(explorer.querySelector('#incident-time-machine'),[],data.day,showHistoryItems);
      timeline=await timelinePromise;
      if(version!==historyVersion) return;
      applyHistory();timeline?.resize();
    } catch(error) {if(!timeline) timelinePromise=null;if(version===historyVersion) historyStatus.textContent=error.message;}
  }
  async function historyMode(active) {
    timelineToggle.setAttribute('aria-pressed',String(active));
    explorer.querySelector('[data-current-map]').setAttribute('aria-pressed',String(!active));
    explorer.querySelector('[data-time-machine-panel]').hidden=!active;
    for(const node of [filters,layout,explorer.querySelector('.public-results:not([role])')]) node.hidden=active;
    if(!active) {historyVersion++;mapController.resize();return;}
    await loadHistory();
  }
  if(timelineToggle) {
    historyForm.elements.day.value=new Date(Date.now()+5*3600000).toISOString().slice(0,10);
    timelineToggle.addEventListener('click',()=>historyMode(true));
    explorer.querySelector('[data-current-map]').addEventListener('click',()=>historyMode(false));
    historyForm.addEventListener('submit',event=>{event.preventDefault();loadHistory();});
    historyForm.addEventListener('change',event=>{if(['day','source'].includes(event.target.name)) loadHistory();else applyHistory();});
  }
  const setView=view=>{
    layout.dataset.view=view;
    explorer.querySelectorAll('[data-map-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mapView===view)));
    if(view==='map') requestAnimationFrame(()=>mapController.resize());
  };
  explorer.querySelectorAll('[data-map-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.mapView)));
  filters.addEventListener('submit',event=>event.preventDefault());
  filters.addEventListener('reset',()=>{
    requestAnimationFrame(()=>{applyFilters();filters.elements.search.focus();});
  });

  const place=item=>[item.city,item.district].filter(Boolean).join(' · ')||'Примерное место отмечено на карте';
  const selectCard=(item,scroll=true)=>{
    explorer.querySelectorAll('.public-card').forEach(card=>card.classList.toggle('selected',card.dataset.caseId===item.id));
    const card=[...explorer.querySelectorAll('.public-card')].find(node=>node.dataset.caseId===item.id);
    if(scroll) {setView('list');card?.focus({preventScroll:true});card?.scrollIntoView({behavior:'instant',block:'nearest'});}
  };
  const caseCard=item=>{
    const card=document.createElement('button');card.type='button';card.className='public-card';card.dataset.caseId=item.id;
    const top=document.createElement('span');top.className='public-card-top';
    const id=document.createElement('strong');id.textContent=item.id;
    const badge=document.createElement('span');badge.className=`public-status-badge ${item.status}`;badge.textContent=labels[item.status]||'В обработке';
    top.append(id,badge);card.append(top);
    if(item.has_photo) {const image=document.createElement('img');image.src=`/api/workspace/public/complaints/${encodeURIComponent(item.id)}/photo`;image.alt=`Фото проблемы к обращению ${item.id}`;image.loading='lazy';card.append(image);}
    if(item.has_video) {const video=document.createElement('video');video.src=`/api/workspace/public/complaints/${encodeURIComponent(item.id)}/video`;video.controls=true;video.preload='metadata';video.setAttribute('aria-label',`Видео проблемы к обращению ${item.id}`);card.append(video);}
    const text=document.createElement('span');text.className='public-card-text';text.textContent=item.text;
    const meta=document.createElement('small');meta.textContent=[place(item),item.location_label,item.topic_name,item.service_name].filter(Boolean).join(' · ');
    const date=document.createElement('time');date.dateTime=item.registered_at;date.textContent=new Date(item.registered_at).toLocaleString(globalThis.pulseLocale||'ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Almaty'});
    const update=document.createElement('small');update.textContent=`Обновлено ${new Date(item.last_updated).toLocaleString(globalThis.pulseLocale||'ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Almaty'})}${item.subscribers?` · ${item.subscribers} подписок`:''}`;
    card.append(text,meta,date,update);
    card.addEventListener('click',()=>{selectCard(item,false);setView('map');requestAnimationFrame(()=>{mapController.focus(item.id);explorer.querySelector('#public-map canvas')?.focus({preventScroll:true});explorer.querySelector('#public-map').scrollIntoView({block:'nearest'});});});
    return card;
  };
  const options=(select,values)=>values.forEach(([value,label])=>select.append(new Option(label,value)));
  const applyFilters=()=>{
    const data=new FormData(filters), needle=String(data.get('search')).trim().toLocaleLowerCase();
    const items=allItems.filter(item=>(!data.get('city')||item.city===data.get('city'))&&
      (!data.get('topic')||item.topic===data.get('topic'))&&(!data.get('status')||item.status===data.get('status'))&&
      (!needle||[item.id,item.text,item.city,item.district,item.topic_name].join(' ').toLocaleLowerCase().includes(needle)));
    visibleItems=items;
    cards.replaceChildren(...items.map(caseCard));mapController.setItems(items);
    const city=data.get('city')||'Все города';
    explorer.querySelector('#public-scope').textContent=city;
    reset.disabled=![...data.values()].some(value=>String(value).trim());
    status.textContent=items.length?`${city} · обращений: ${items.length}`:'Обращений не найдено. Измените запрос или сбросьте фильтры.';
  };

  filters.addEventListener('input',applyFilters);
  try {
    const response=await fetch('/api/workspace/public/complaints',{headers:{Accept:'application/json'}});
    if(!response.ok) throw new Error('Не удалось загрузить обращения');
    const data=await response.json();
    if(!explorer.isConnected) return;
    allItems=data.items;
    options(filters.elements.city,[...new Set(allItems.map(item=>item.city).filter(Boolean))].sort().map(value=>[value,value]));
    options(filters.elements.topic,[...new Map(allItems.filter(item=>item.topic).map(item=>[item.topic,item.topic_name]))].sort((a,b)=>a[1].localeCompare(b[1],'ru')));
    applyFilters();
    try {
      mapController=await mountPublicMap(explorer.querySelector('#public-map'),visibleItems,item=>selectCard(item));
      mapController.setItems(visibleItems);
    } catch {
      const message=explorer.querySelector('#public-map-error');
      message.textContent='Карта временно недоступна. Обращения доступны в списке.';message.hidden=false;
    }
  } catch(error) {status.textContent=error.message;status.classList.add('error');}
}

const standalone=document.querySelector('#public-page');
if(standalone) {standalone.innerHTML=publicMapView();mountPublicIssueExplorer(standalone);}
