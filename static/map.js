const MAPLIBRE_JS = 'https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.js';
const MAPLIBRE_CSS = 'https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.css';
const MAPLIBRE_JS_INTEGRITY = 'sha384-GfxBM9x46BaAFxtCq39Fxir8fNZ4VDnwgfi6Kzi5/F1tAFsm0amuuV8kd+Pxzuf/';
const MAPLIBRE_CSS_INTEGRITY = 'sha384-Nq6PQ+9vJPvw7U/VfDELyrWoGQMsy0gi6QShhaSrGzkpF5KkM40csg2leky+YMTd';
const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
const ALMATY = [76.945, 43.238];
const ALMATY_BOUNDS = [[76.55, 42.95], [77.45, 43.55]];

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

async function mountPicker(element) {
  const maplibregl=await loadLibrary();
  if(!element.isConnected || element.dataset.mounted) return;
  element.dataset.mounted='true';
  const form=element.closest('form');
  const map=new maplibregl.Map({container:element,style:MAP_STYLE,center:ALMATY,zoom:12.4,pitch:18,maxBounds:ALMATY_BOUNDS,renderWorldCopies:false});
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
  form.querySelector('[data-location-clear]').addEventListener('click',()=>resetLocationPicker(form));
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
  element._pulseMarker?.remove();
  setStatus(root.elements?root:element.closest('form'),'Нажмите на карту или используйте кнопку геопозиции');
}
