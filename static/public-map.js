import {mountPublicMap} from './map.js';

const cards=document.querySelector('#public-cards'), status=document.querySelector('#public-status');
const labels={pending:'На рассмотрении',confirmed:'Принято в работу',needs_clarification:'Нужно уточнение',resolved:'Решено'};
let focusMap=()=>{};

function place(item) {
  return [item.city,item.district,item.address].filter(Boolean).join(' · ')||'Место отмечено на карте';
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
  const meta=document.createElement('small');meta.textContent=place(item);
  const date=document.createElement('time');date.dateTime=item.registered_at;date.textContent=new Date(item.registered_at).toLocaleString('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Almaty'});
  card.append(text,meta,date);
  card.addEventListener('click',()=>{selectCard(item,false);focusMap(item.id);});
  return card;
}

async function load() {
  try {
    const response=await fetch('/api/workspace/public/complaints',{headers:{Accept:'application/json'}});
    if(!response.ok) throw new Error('Не удалось загрузить обращения');
    const data=await response.json();
    document.querySelector('#public-count').textContent=data.count;
    cards.replaceChildren(...data.items.map(caseCard));
    status.textContent=data.count?'Нажмите на карточку, чтобы приблизить её на карте.':'Пока нет обращений с выбранной точкой.';
    focusMap=await mountPublicMap(document.querySelector('#public-map'),data.items,item=>selectCard(item));
  } catch(error) {status.textContent=error.message;status.classList.add('error');}
}

load();
