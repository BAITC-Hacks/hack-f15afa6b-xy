import {mountPublicMap} from './map.js?v=20260927-7';

const cards=document.querySelector('#public-cards'), status=document.querySelector('#public-status');
const labels={pending:'На рассмотрении',confirmed:'Принято в работу',needs_clarification:'Нужно уточнение',resolved:'Решено'};
const filters=document.querySelector('#public-filters');
let allItems=[], mapController={focus(){},setItems(){}};

function place(item) {
  return [item.city,item.district].filter(Boolean).join(' · ')||'Примерное место отмечено на карте';
}

function selectCard(item,scroll=true) {
  document.querySelectorAll('.public-card').forEach(card=>card.classList.toggle('selected',card.dataset.caseId===item.id));
  const card=[...document.querySelectorAll('.public-card')].find(node=>node.dataset.caseId===item.id);
  if(scroll) card?.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function caseCard(item) {
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
}

function options(select,values) {
  values.forEach(([value,label])=>select.append(new Option(label,value)));
}

function applyFilters() {
  const data=new FormData(filters), needle=String(data.get('search')).trim().toLocaleLowerCase();
  const items=allItems.filter(item=>(!data.get('city')||item.city===data.get('city'))&&
    (!data.get('topic')||item.topic===data.get('topic'))&&(!data.get('status')||item.status===data.get('status'))&&
    (!needle||[item.id,item.text,item.city,item.district,item.topic_name].join(' ').toLocaleLowerCase().includes(needle)));
  cards.replaceChildren(...items.map(caseCard));mapController.setItems(items);
  document.querySelector('#public-count').textContent=items.length;
  status.textContent=items.length?`${items.length} обращений · метки объединяются при отдалении карты.`:'По выбранным фильтрам обращений нет.';
}

async function load() {
  try {
    const response=await fetch('/api/workspace/public/complaints',{headers:{Accept:'application/json'}});
    if(!response.ok) throw new Error('Не удалось загрузить обращения');
    const data=await response.json();
    allItems=data.items;
    options(filters.elements.city,[...new Set(allItems.map(item=>item.city).filter(Boolean))].sort().map(value=>[value,value]));
    options(filters.elements.topic,[...new Map(allItems.filter(item=>item.topic).map(item=>[item.topic,item.topic_name]))].sort((a,b)=>a[1].localeCompare(b[1],'ru')));
    mapController=await mountPublicMap(document.querySelector('#public-map'),allItems,item=>selectCard(item));
    applyFilters();
  } catch(error) {status.textContent=error.message;status.classList.add('error');}
}

filters.addEventListener('input',applyFilters);
load();
