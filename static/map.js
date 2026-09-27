const MAPLIBRE_JS = 'https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.js';
const MAPLIBRE_CSS = 'https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.css';
const MAPLIBRE_JS_INTEGRITY = 'sha384-GfxBM9x46BaAFxtCq39Fxir8fNZ4VDnwgfi6Kzi5/F1tAFsm0amuuV8kd+Pxzuf/';
const MAPLIBRE_CSS_INTEGRITY = 'sha384-Nq6PQ+9vJPvw7U/VfDELyrWoGQMsy0gi6QShhaSrGzkpF5KkM40csg2leky+YMTd';
const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
const CITIES = {
  'KZ-ALA':{name:'Алматы',center:[76.945,43.238],bounds:[[76.55,42.95],[77.45,43.55]]},
  'KZ-AST':{name:'Астана',center:[71.4304,51.1282],bounds:[[70.9,50.8],[72,51.4]]},
  'KZ-SHY':{name:'Шымкент',center:[69.5901,42.3417],bounds:[[69.2,42.1],[70,42.6]]},
};

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

function setCity(form, map, reset=true) {
  const city=CITIES[form.elements.region_id.value]||CITIES['KZ-ALA'];
  if(reset) {
    resetLocationPicker(form);form.elements.district.value='';
    form.elements.district.dispatchEvent(new Event('change',{bubbles:true}));
  }
  form.querySelector('[data-city-label]').textContent=`${city.name} · интерактивная карта`;
  form.querySelector('[data-map-mode="picker"]').setAttribute('aria-label',`Выберите место обращения на карте ${city.name}`);
  map.setMaxBounds(city.bounds);
  map.flyTo({center:city.center,zoom:12.4,duration:600});
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
  const regionId=form.elements.region_id.value, city=CITIES[regionId];
  button.disabled=true;button.textContent='Ищем…';results.textContent='';setStatus(form,`Ищем адрес в городе ${city.name}…`);
  try {
    const response=await fetch(`/api/workspace/geocode?q=${encodeURIComponent(query)}&region_id=${encodeURIComponent(regionId)}`,{headers:{Accept:'application/json'}});
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
  const city=CITIES[form.elements.region_id.value]||CITIES['KZ-ALA'];
  const map=new maplibregl.Map({container:element,style:MAP_STYLE,center:city.center,zoom:12.4,pitch:18,maxBounds:city.bounds,renderWorldCopies:false});
  const marker=new maplibregl.Marker({color:'#157665',draggable:true});
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
  form.elements.region_id.addEventListener('change',()=>setCity(form,map));
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
  new maplibregl.Marker({color:'#157665'}).setLngLat([longitude,latitude]).setPopup(popup).addTo(map);
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
