import {mountPublicMap} from './map.js?v=20260927-7';

const labels={pending:'На рассмотрении',confirmed:'Принято в работу',needs_clarification:'Нужно уточнение',resolved:'Решено'};

export function publicMapView() {
  return `<section class="workspace-public-map" data-public-map-explorer aria-labelledby="map-page-title">
    <div class="page-heading"><div><p class="eyebrow">ОТКРЫТАЯ КАРТА · СИНТЕТИЧЕСКОЕ ДЕМО</p><h1 id="map-page-title">Что происходит в городе</h1><p class="subtitle">Выберите метку или карточку, чтобы увидеть проблему и прикреплённое фото.</p></div><div class="heading-actions"><div class="map-page-count"><strong id="public-count">—</strong><small>публичных обращений</small></div><a class="button ghost" href="/map">Публичная ссылка ↗</a></div></div>
    <div class="public-layout">
      <div class="public-map-shell"><div id="public-map" class="issue-map public-map" aria-label="Интерактивная карта обращений"></div><span class="map-tip">Карта обращений Pulse 109</span></div>
      <aside class="public-feed" aria-label="Список обращений">
        <div class="public-feed-head"><div><p class="eyebrow">ОБРАЩЕНИЯ</p><strong>Последние сигналы</strong></div><span class="live"><i></i> Демо</span></div>
        <form id="public-filters" class="public-filters">
          <label><span>Поиск</span><input name="search" type="search" placeholder="Номер или текст"></label>
          <label><span>Город</span><select name="city"><option value="">Все города</option></select></label>
          <label><span>Категория</span><select name="topic"><option value="">Все категории</option></select></label>
          <label><span>Статус</span><select name="status"><option value="">Все статусы</option><option value="pending">На рассмотрении</option><option value="confirmed">Принято в работу</option><option value="needs_clarification">Нужно уточнение</option><option value="resolved">Решено</option></select></label>
        </form>
        <p id="public-status" class="public-status" role="status">Загружаем обращения…</p>
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
  let allItems=[], mapController={focus(){},setItems(){}};

  const place=item=>[item.city,item.district].filter(Boolean).join(' · ')||'Примерное место отмечено на карте';
  const selectCard=(item,scroll=true)=>{
    explorer.querySelectorAll('.public-card').forEach(card=>card.classList.toggle('selected',card.dataset.caseId===item.id));
    const card=[...explorer.querySelectorAll('.public-card')].find(node=>node.dataset.caseId===item.id);
    if(scroll) card?.scrollIntoView({behavior:'smooth',block:'nearest'});
  };
  const caseCard=item=>{
    const card=document.createElement('button');card.type='button';card.className='public-card';card.dataset.caseId=item.id;
    const top=document.createElement('span');top.className='public-card-top';
    const id=document.createElement('strong');id.textContent=item.id;
    const badge=document.createElement('span');badge.className=`public-status-badge ${item.status}`;badge.textContent=labels[item.status]||'В обработке';
    top.append(id,badge);card.append(top);
    if(item.has_photo) {const image=document.createElement('img');image.src=`/api/workspace/public/complaints/${encodeURIComponent(item.id)}/photo`;image.alt=`Фото проблемы к обращению ${item.id}`;image.loading='lazy';card.append(image);}
    const text=document.createElement('span');text.className='public-card-text';text.textContent=item.text;
    const meta=document.createElement('small');meta.textContent=[place(item),item.location_label,item.topic_name,item.service_name].filter(Boolean).join(' · ');
    const date=document.createElement('time');date.dateTime=item.registered_at;date.textContent=new Date(item.registered_at).toLocaleString('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Almaty'});
    const update=document.createElement('small');update.textContent=`Обновлено ${new Date(item.last_updated).toLocaleString('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Almaty'})}${item.subscribers?` · ${item.subscribers} подписок`:''}`;
    card.append(text,meta,date,update);
    card.addEventListener('click',()=>{selectCard(item,false);mapController.focus(item.id);});
    return card;
  };
  const options=(select,values)=>values.forEach(([value,label])=>select.append(new Option(label,value)));
  const applyFilters=()=>{
    const data=new FormData(filters), needle=String(data.get('search')).trim().toLocaleLowerCase();
    const items=allItems.filter(item=>(!data.get('city')||item.city===data.get('city'))&&
      (!data.get('topic')||item.topic===data.get('topic'))&&(!data.get('status')||item.status===data.get('status'))&&
      (!needle||[item.id,item.text,item.city,item.district,item.topic_name].join(' ').toLocaleLowerCase().includes(needle)));
    cards.replaceChildren(...items.map(caseCard));mapController.setItems(items);
    explorer.querySelector('#public-count').textContent=items.length;
    status.textContent=items.length?`${items.length} обращений · метки объединяются при отдалении карты.`:'По выбранным фильтрам обращений нет.';
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
    mapController=await mountPublicMap(explorer.querySelector('#public-map'),allItems,item=>selectCard(item));
    applyFilters();
  } catch(error) {status.textContent=error.message;status.classList.add('error');}
}

const standalone=document.querySelector('[data-public-map-explorer]');
if(standalone) mountPublicIssueExplorer(standalone);
