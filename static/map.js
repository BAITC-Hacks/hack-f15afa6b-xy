const MAPLIBRE_JS = 'https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.js';
const MAPLIBRE_CSS = 'https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.css';
const MAPLIBRE_JS_INTEGRITY = 'sha384-GfxBM9x46BaAFxtCq39Fxir8fNZ4VDnwgfi6Kzi5/F1tAFsm0amuuV8kd+Pxzuf/';
const MAPLIBRE_CSS_INTEGRITY = 'sha384-Nq6PQ+9vJPvw7U/VfDELyrWoGQMsy0gi6QShhaSrGzkpF5KkM40csg2leky+YMTd';
const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
const DEFAULT_CITY={code:'750000000',name:'Алматы',center:[76.945,43.238],bounds:[[76.55,42.95],[77.45,43.55]]};

let libraryPromise;

function loadLibrary() {
  if(window.maplibregl) return Promise.resolve(window.maplibregl);
  if(libraryPromise) return libraryPromise;
  if(!document.querySelector(`link[href="${MAPLIBRE_CSS}"]`)) {
    const link=document.createElement('link');
    link.rel='stylesheet';link.href=MAPLIBRE_CSS;link.integrity=MAPLIBRE_CSS_INTEGRITY;link.crossOrigin='anonymous';document.head.append(link);
  }
  libraryPromise=new Promise((resolve,reject)=>{
    const script=document.createElement('script');
    script.src=MAPLIBRE_JS;script.integrity=MAPLIBRE_JS_INTEGRITY;script.crossOrigin='anonymous';script.onload=()=>resolve(window.maplibregl);
    script.onerror=()=>reject(new Error('Карта временно недоступна'));
    document.head.append(script);
  });
  return libraryPromise;
}

function setStatus(form, text) {
  const status=form.querySelector('[data-location-status]');
  if(status) status.textContent=text;
}

function setLocation(form, map, marker, point, accuracy=null) {
  const longitude=Math.max(-180,Math.min(180,point.lng));
  const latitude=Math.max(-90,Math.min(90,point.lat));
  form.elements.longitude.value=longitude.toFixed(6);
  form.elements.latitude.value=latitude.toFixed(6);
  form.elements.location_accuracy_m.value=accuracy == null?'':Math.round(accuracy);
  marker.setLngLat([longitude,latitude]).addTo(map);
  setStatus(form,accuracy?`Точка выбрана · точность около ${Math.round(accuracy)} м`:'Точка выбрана · метку можно перетащить');
}

function selectedCity(form) {
  const option=form.elements.city_code.selectedOptions[0];
  return {code:option.value,name:option.dataset.name,regionId:option.dataset.region};
}

async function setCity(form, map, reset=true) {
  const city=selectedCity(form);
  if(reset) {
    resetLocationPicker(form);form.elements.district.value='';
    form.elements.district.dispatchEvent(new Event('change',{bubbles:true}));
  }
  form.querySelector('[data-city-label]').textContent=`${city.name} · интерактивная карта`;
  form.querySelector('[data-map-mode="picker"]').setAttribute('aria-label',`Выберите место обращения на карте ${city.name}`);
  setStatus(form,`Загружаем карту города ${city.name}…`);
  map.setMaxBounds(null);
  const response=await fetch(`/api/workspace/city-map?city_code=${encodeURIComponent(city.code)}`,{headers:{Accept:'application/json'}});
  const data=await response.json().catch(()=>({detail:'Не удалось загрузить карту города'}));
  if(!response.ok) throw new Error(data.detail||'Не удалось загрузить карту города');
  if(selectedCity(form).code!==city.code) return;
  map.setMaxBounds(data.bounds);
  map.fitBounds(data.bounds,{padding:50,maxZoom:12.4,duration:600});
  setStatus(form,'Введите адрес или выберите точку на карте');
}

function selectResult(form, map, marker, result, button) {
  setLocation(form,map,marker,{lng:result.longitude,lat:result.latitude});
  if(result.district && form.elements.district.value!==result.district) {
    form.elements.district.value=result.district;
    form.elements.district.dispatchEvent(new Event('change',{bubbles:true}));
  }
  if(result.bounds) map.fitBounds([[result.bounds[2],result.bounds[0]],[result.bounds[3],result.bounds[1]]],{padding:70,maxZoom:17,duration:700});
  else map.flyTo({center:[result.longitude,result.latitude],zoom:16.5,duration:700});
  form.querySelectorAll('.geocode-result').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  setStatus(form,'Адрес найден · проверьте метку и при необходимости перетащите её');
}

async function searchAddress(form, map, marker) {
  const input=form.elements.address, button=form.querySelector('[data-address-search]');
  const results=form.querySelector('[data-geocode-results]'), query=input.value.trim();
  if(query.length<3) {setStatus(form,'Введите адрес подробнее');input.focus();return;}
  const city=selectedCity(form);
  button.disabled=true;button.textContent='Ищем…';results.textContent='';setStatus(form,`Ищем адрес в городе ${city.name}…`);
  try {
    const response=await fetch(`/api/workspace/geocode?q=${encodeURIComponent(query)}&city_code=${encodeURIComponent(city.code)}`,{headers:{Accept:'application/json'}});
    const data=await response.json().catch(()=>({detail:'Не удалось выполнить поиск'}));
    if(!response.ok) throw new Error(data.detail||'Не удалось выполнить поиск');
    if(!data.items.length) {setStatus(form,'Адрес не найден · уточните написание или поставьте точку вручную');return;}
    const fragment=document.createDocumentFragment();let first;
    data.items.forEach((result,index)=>{
      const option=document.createElement('button');
      option.type='button';option.className='geocode-result';option.textContent=result.label;
      option.setAttribute('aria-pressed','false');option.addEventListener('click',()=>selectResult(form,map,marker,result,option));
      fragment.append(option);if(index===0) first={result,option};
    });
    results.append(fragment);selectResult(form,map,marker,first.result,first.option);
  } catch(error) {setStatus(form,error.message);}
  finally {button.disabled=false;button.textContent='Найти на карте';}
}

async function mountPicker(element) {
  const maplibregl=await loadLibrary();
  if(!element.isConnected || element.dataset.mounted) return;
  element.dataset.mounted='true';
  const form=element.closest('form');
  const map=new maplibregl.Map({container:element,style:MAP_STYLE,center:DEFAULT_CITY.center,zoom:12.4,pitch:18,maxBounds:DEFAULT_CITY.bounds,renderWorldCopies:false});
  const marker=new maplibregl.Marker({color:'#3982c1',draggable:true});
  element._pulseMap=map;element._pulseMarker=marker;
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
  const locate=new maplibregl.GeolocateControl({positionOptions:{enableHighAccuracy:true,timeout:10000},fitBoundsOptions:{maxZoom:16},trackUserLocation:false});
  map.addControl(locate,'top-right');
  map.addControl(new maplibregl.ScaleControl({unit:'metric'}),'bottom-left');
  map.on('click',event=>setLocation(form,map,marker,event.lngLat));
  marker.on('dragend',()=>setLocation(form,map,marker,marker.getLngLat()));
  locate.on('geolocate',event=>setLocation(form,map,marker,{lng:event.coords.longitude,lat:event.coords.latitude},event.coords.accuracy));
  locate.on('error',()=>setStatus(form,'Не удалось определить геопозицию · выберите точку на карте'));
  locate.on('outofmaxbounds',()=>setStatus(form,'Геопозиция находится вне выбранного города · выберите другой город'));
  form.querySelector('[data-location-clear]').addEventListener('click',()=>resetLocationPicker(form));
  form.querySelector('[data-address-search]').addEventListener('click',()=>searchAddress(form,map,marker));
  form.elements.address.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();searchAddress(form,map,marker);}});
  form.elements.city_code.addEventListener('change',async()=>{
    try {await setCity(form,map);form.dispatchEvent(new CustomEvent('pulse-city-ready',{detail:{code:form.elements.city_code.value}}));}
    catch(error) {setStatus(form,error.message);}
  });
  setCity(form,map,false).catch(error=>setStatus(form,error.message));
}

async function mountViewer(element) {
  const maplibregl=await loadLibrary();
  if(!element.isConnected || element.dataset.mounted) return;
  const latitude=Number(element.dataset.latitude), longitude=Number(element.dataset.longitude);
  if(!Number.isFinite(latitude)||!Number.isFinite(longitude)) return;
  element.dataset.mounted='true';
  const map=new maplibregl.Map({container:element,style:MAP_STYLE,center:[longitude,latitude],zoom:15.5,pitch:22,interactive:true,renderWorldCopies:false});
  element._pulseMap=map;
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
  const popup=new maplibregl.Popup({offset:28,closeButton:false}).setText(element.dataset.label||'Место обращения');
  new maplibregl.Marker({color:'#3982c1'}).setLngLat([longitude,latitude]).setPopup(popup).addTo(map);
}

function publicPopup(item) {
  const card=document.createElement('article');card.className='public-popup';
  const title=document.createElement('strong');title.textContent=item.id;
  const text=document.createElement('p');text.textContent=item.text;
  card.append(title,text);
  const location=document.createElement('small');location.textContent=item.location_label;card.append(location);
  if(item.has_photo) {
    const image=document.createElement('img');image.src=`/api/workspace/public/complaints/${encodeURIComponent(item.id)}/photo`;
    image.alt=`Фото проблемы к обращению ${item.id}`;image.loading='lazy';card.append(image);
  }
  if(item.has_video) {
    const video=document.createElement('video');video.src=`/api/workspace/public/complaints/${encodeURIComponent(item.id)}/video`;
    video.controls=true;video.preload='metadata';video.setAttribute('aria-label',`Видео проблемы к обращению ${item.id}`);card.append(video);
  }
  return card;
}

export async function mountPublicMap(element, items, onSelect=()=>{}) {
  const maplibregl=await loadLibrary();
  element._pulseMap?.remove();element.replaceChildren();
  const map=new maplibregl.Map({container:element,style:MAP_STYLE,center:[67.5,48],zoom:4,pitch:0,renderWorldCopies:false,locale:{'NavigationControl.ZoomIn':'Приблизить','NavigationControl.ZoomOut':'Отдалить','Map.Title':'Карта обращений','Popup.Close':'Закрыть'}});
  let currentItems=items, byId=new Map(items.map(item=>[item.id,item]));
  element._pulseMap=map;
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
  map.addControl(new maplibregl.ScaleControl({unit:'metric'}),'bottom-left');
  let markers=[];
  const fit=(duration=0)=>{
    if(!currentItems.length) {map.flyTo({center:[67.5,48],zoom:4,duration});return;}
    const bounds=new maplibregl.LngLatBounds();currentItems.forEach(item=>bounds.extend([item.longitude,item.latitude]));
    map.fitBounds(bounds,{padding:70,maxZoom:14,duration});
  };
  const show=item=>{
    map.flyTo({center:[item.longitude,item.latitude],zoom:Math.max(map.getZoom(),14),duration:550});
    new maplibregl.Popup({offset:18}).setLngLat([item.longitude,item.latitude]).setDOMContent(publicPopup(item)).addTo(map);
  };
  const draw=()=>{
    if(!map.isStyleLoaded()) return;
    markers.forEach(marker=>marker.remove());markers=[];
    const groups=[];
    // ponytail: O(n²) grouping is fine for a demo map; use Supercluster beyond 1,000 visible points.
    currentItems.forEach(item=>{
      const point=map.project([item.longitude,item.latitude]);
      const group=groups.find(candidate=>Math.hypot(candidate.point.x-point.x,candidate.point.y-point.y)<48);
      if(group) group.items.push(item); else groups.push({point,items:[item]});
    });
    markers=groups.map(group=>{
      const latitude=group.items.reduce((sum,item)=>sum+item.latitude,0)/group.items.length;
      const longitude=group.items.reduce((sum,item)=>sum+item.longitude,0)/group.items.length;
      const node=document.createElement('button'), single=group.items.length===1?group.items[0]:null;
      node.type='button';node.className=`public-map-marker ${single?single.status:'cluster'} ${single?.location_source==='user_selected'?'exact':''}`;
      node.textContent=single?'':String(group.items.length);
      node.title=single?`${single.id}: ${single.text}`:`${group.items.length} обращений`;
      const marker=new maplibregl.Marker({element:node}).setLngLat([longitude,latitude]);
      node.setAttribute('aria-label',node.title);
      if(single) marker.setPopup(new maplibregl.Popup({offset:18}).setDOMContent(publicPopup(single)));
      node.addEventListener('click',event=>{
        if(single) onSelect(single);
        else {event.stopPropagation();map.easeTo({center:[longitude,latitude],zoom:Math.min(map.getZoom()+2,16)});}
      });
      return marker.addTo(map);
    });
    element.dataset.clustered='true';element.dataset.sourceFeatures=String(currentItems.length);
    element.dataset.renderedFeatures=String(markers.length);
  };
  map.on('load',()=>{fit();draw();});
  map.on('moveend',draw);
  return {
    focus(id) {const item=byId.get(id);if(item) show(item);},
    resize() {map.resize();fit();draw();},
    setItems(next) {
      currentItems=next;byId=new Map(next.map(item=>[item.id,item]));
      fit(300);draw();
    },
  };
}

export function mountMaps(root=document) {
  root.querySelectorAll('[data-map-mode]:not([data-mounted])').forEach(element=>{
    const mount=element.dataset.mapMode==='picker'?mountPicker(element):mountViewer(element);
    mount.catch(()=>{
      if(element.isConnected) element.innerHTML='<p class="map-fallback">Карта временно недоступна. Координаты обращения сохранены.</p>';
    });
  });
}

export function resetLocationPicker(root=document) {
  const element=root.querySelector('[data-map-mode="picker"]');
  if(!element) return;
  ['latitude','longitude','location_accuracy_m'].forEach(name=>{if(root.elements?.[name]) root.elements[name].value='';});
  root.querySelector('[data-geocode-results]')?.replaceChildren();
  element._pulseMarker?.remove();
  setStatus(root.elements?root:element.closest('form'),'Введите адрес или выберите точку на карте');
}
