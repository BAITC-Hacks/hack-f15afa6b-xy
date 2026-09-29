import {addIncidentTimeMachine} from './incident-time-machine.js?v=20260929-heat-demo-2';

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
    const fragment=document.createDocumentFragment();
    data.items.forEach(result=>{
      const option=document.createElement('button');
      option.type='button';option.className='geocode-result';option.textContent=result.label;
      option.setAttribute('aria-pressed','false');option.addEventListener('click',()=>selectResult(form,map,marker,result,option));
      fragment.append(option);
    });
    results.append(fragment);
    const matches=data.items.filter(result=>result.address_match);
    if(matches.length===1) {
      const index=data.items.indexOf(matches[0]);
      selectResult(form,map,marker,matches[0],results.children[index]);
    } else setStatus(form,'Проверьте найденные варианты и выберите место. Точный дом автоматически не определён.');
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

async function searchViewerAddress(element, map, maplibregl) {
  const card=element.closest('.location-card'), status=card.querySelector('[data-map-status]');
  const results=card.querySelector('[data-map-results]'), button=card.querySelector('[data-map-search]');
  const alternatives=card.querySelector('[data-map-alternatives]');
  const fallback=element.dataset.pointLabel?` ${element.dataset.pointLabel}`:'';
  button.disabled=true;results.replaceChildren();status.textContent='Ищем адрес на карте…';element.dataset.lookupState='loading';
  alternatives.hidden=true;
  try {
    const response=await fetch(`/api/workspace/geocode?q=${encodeURIComponent(element.dataset.address)}&city_code=${encodeURIComponent(element.dataset.cityCode)}`,{headers:{Accept:'application/json'}});
    if(!response.ok) throw new Error('Поиск адреса временно недоступен. Повторите поиск или откройте 2ГИС.');
    const data=await response.json();
    if(!element.isConnected) return;
    const matches=data.items.filter(result=>result.address_match);
    if(matches.length!==1) {
      element._pulseMarker?.remove();delete element.dataset.pointLabel;
      card.querySelector('[data-map-coordinates]').hidden=true;
      card.querySelector('[data-map-link]').href='https://2gis.kz/search/'+encodeURIComponent(data.query);
    }
    if(!matches.length) {element.dataset.lookupState='empty';status.textContent='Дом по этому адресу не найден. Уточните город, улицу и номер дома или проверьте адрес в 2ГИС.';return;}
    const show=(result,option)=>{
      element.setAttribute('aria-busy','true');
      element._pulseMarker?.remove();
      element._pulseMarker=new maplibregl.Marker({color:'#b77923'}).setLngLat([result.longitude,result.latitude]).addTo(map);
      map.jumpTo({center:[result.longitude,result.latitude],zoom:17});
      results.querySelectorAll('button').forEach(node=>node.setAttribute('aria-pressed',String(node===option)));
      status.textContent=`Найден дом: ${result.label}. Метка указывает на здание; вход или место аварии уточните у жителя.`;
      const coords=`${result.longitude},${result.latitude}`;
      card.querySelector('[data-map-link]').href=`https://2gis.kz/geo/${coords}?m=${coords}/17`;
      const coordinates=card.querySelector('[data-map-coordinates]');
      coordinates.textContent=`Координаты: ${result.latitude}, ${result.longitude}`;coordinates.hidden=false;
      element.dataset.pointLabel=status.textContent;
      element.dataset.lookupState='selected';
    };
    matches.forEach(result=>{
      const option=document.createElement('button');option.type='button';option.className='geocode-result';
      option.textContent=result.label;option.setAttribute('aria-pressed','false');
      option.addEventListener('click',()=>show(result,option));results.append(option);
    });
    alternatives.hidden=matches.length<2;
    if(matches.length===1) show(matches[0],results.firstElementChild);
    else {
      alternatives.open=true;element.dataset.lookupState='ambiguous';
      status.textContent='Найдено несколько домов с этим адресом. Уточните район и выберите нужный дом.';
    }
  } catch(error) {if(element.isConnected) {element.dataset.lookupState='error';status.textContent=error.message+fallback;}}
  finally {button.disabled=false;}
}

async function mountViewer(element) {
  const maplibregl=await loadLibrary();
  if(!element.isConnected || element.dataset.mounted) return;
  const byAddress=element.dataset.mapMode==='address', area=element.dataset.mapMode==='area';
  const latitude=Number(element.dataset.latitude), longitude=Number(element.dataset.longitude);
  const radius=Number(element.dataset.radiusM);
  const point=element.dataset.latitude!=null&&element.dataset.longitude!=null&&Number.isFinite(latitude)&&Number.isFinite(longitude);
  if(!byAddress&&!point) return;
  element.dataset.mounted='true';
  const map=new maplibregl.Map({container:element,style:MAP_STYLE,center:point?[longitude,latitude]:[67.5,48],zoom:point?(area?11:17):3.5,pitch:0,interactive:true,renderWorldCopies:false});
  element._pulseMap=map;
  element.setAttribute('aria-busy','true');
  map.on('idle',()=>element.setAttribute('aria-busy','false'));
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
  const resize=new ResizeObserver(()=>map.resize());resize.observe(element);map.on('remove',()=>resize.disconnect());
  if(point&&!area) {
    element.dataset.pointLabel=element.closest('.location-card')?.querySelector('[data-map-status]')?.textContent||element.dataset.label||'Место обращения';
    const popup=new maplibregl.Popup({offset:28,closeButton:false}).setText(element.dataset.label||'Место обращения');
    element._pulseMarker=new maplibregl.Marker({color:'#3982c1'}).setLngLat([longitude,latitude]).setPopup(popup).addTo(map);
  }
  if(point&&area&&Number.isFinite(radius)&&radius>0) map.on('load',()=>{
    const coordinates=[];
    const latStep=radius/111320, lngStep=radius/(111320*Math.max(.2,Math.cos(latitude*Math.PI/180)));
    for(let degree=0;degree<=360;degree+=6) {
      const angle=degree*Math.PI/180;
      coordinates.push([longitude+Math.cos(angle)*lngStep,latitude+Math.sin(angle)*latStep]);
    }
    map.addSource('incident-area',{type:'geojson',data:{type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[coordinates]}}});
    map.addLayer({id:'incident-area-fill',type:'fill',source:'incident-area',paint:{'fill-color':'#3982c1','fill-opacity':.2}});
    map.addLayer({id:'incident-area-line',type:'line',source:'incident-area',paint:{'line-color':'#2f6fa8','line-width':2}});
    map.fitBounds([[longitude-lngStep,latitude-latStep],[longitude+lngStep,latitude+latStep]],{padding:24,maxZoom:14,duration:0});
  });
  if(!byAddress||!element.dataset.cityCode) return;
  const card=element.closest('.location-card'), button=card.querySelector('[data-map-search]');
  button.addEventListener('click',()=>searchViewerAddress(element,map,maplibregl));
  button.disabled=true;
  if(!point) try {
    const response=await fetch(`/api/workspace/city-map?city_code=${encodeURIComponent(element.dataset.cityCode)}`,{headers:{Accept:'application/json'}});
    if(response.ok&&element.isConnected) {const city=await response.json();map.fitBounds(city.bounds,{padding:20,duration:0});}
  } catch { /* Address search and the external map remain available. */ }
  if(element.isConnected) await searchViewerAddress(element,map,maplibregl);
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

export async function mountTimeMachine(element, items, day, onChange) {
  const maplibregl=await loadLibrary();
  if(!element.isConnected) return;
  const map=new maplibregl.Map({container:element,style:MAP_STYLE,center:[76.925,43.24],zoom:12,renderWorldCopies:false});
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
  const timeline=addIncidentTimeMachine(map,items,day,onChange);
  const observer=new MutationObserver(()=>{
    if(!element.isConnected) {observer.disconnect();timeline.remove();map.remove();}
  });
  observer.observe(document.body,{childList:true,subtree:true});
  return {...timeline,resize:()=>map.resize()};
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
      if(element.isConnected) {
        element.innerHTML='<p class="map-fallback">Карта временно недоступна. Попробуйте открыть её в 2ГИС.</p>';
        const card=element.closest('.location-card');
        if(card) {card.querySelector('[data-map-status]').textContent='Ссылка на 2ГИС доступна выше.';const retry=card.querySelector('[data-map-search]');if(retry) retry.disabled=true;}
      }
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
