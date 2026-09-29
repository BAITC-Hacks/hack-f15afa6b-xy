export function incidentFeatures(items) {
  return {type: 'FeatureCollection', features: items.filter(item =>
    item.latitude != null && item.longitude != null &&
    Number.isFinite(item.latitude) && Number.isFinite(item.longitude) &&
    Math.abs(item.latitude) <= 90 && Math.abs(item.longitude) <= 180 &&
    Number.isFinite(Date.parse(item.registered_at))
  ).map(item => ({type: 'Feature', geometry: {type: 'Point', coordinates: [item.longitude, item.latitude]},
    properties: {id: item.id, timestamp: Date.parse(item.registered_at) / 1000, status: item.status}}))};
}

const demoCities = [
  ['Алматы',43.2383,76.9456],['Астана',51.1694,71.4491],['Шымкент',42.3417,69.5901],
  ['Қарағанды',49.8064,73.0855],['Ақтөбе',50.2839,57.1669],['Тараз',42.9000,71.3667],
  ['Павлодар',52.2873,76.9674],['Өскемен',49.9483,82.6275],['Семей',50.4111,80.2275],
  ['Атырау',47.0945,51.9238],['Қостанай',53.2144,63.6246],['Қызылорда',44.8488,65.4823],
];
const demoTopics = [['water_supply','Водоснабжение'],['heating','Отопление'],
  ['electricity','Электроснабжение'],['roads','Дороги'],['housing_maintenance','Обслуживание жилья']];

export function demoIncidentHistory(day, perCity=24) {
  const start = Date.parse(`${day}T00:00:00+05:00`);
  if (!Number.isFinite(start)) throw new Error('Use a date in YYYY-MM-DD format');
  let seed = [...day].reduce((value, char) => (value * 31 + char.charCodeAt(0)) >>> 0, 2166136261);
  const random = () => ((seed = (1664525 * seed + 1013904223) >>> 0) / 2 ** 32);
  return demoCities.flatMap(([city, latitude, longitude], cityIndex) =>
    Array.from({length:perCity}, (_, index) => {
      const [topic, topicName] = demoTopics[(cityIndex + index) % demoTopics.length];
      const minute = Math.floor(random() * 1440);
      const angle = random() * Math.PI * 2;
      const radius = Math.sqrt(random()) * 0.055;
      return {
        id:`DEMO-${day.replaceAll('-','')}-${cityIndex+1}-${index+1}`,
        text:`Учебное обращение: ${topicName.toLocaleLowerCase('ru-RU')}`,
        city, address:'Учебная точка', topic, topic_name:topicName,
        status:['pending','confirmed','resolved'][(cityIndex + index) % 3], data_origin:'synthetic_demo',
        registered_at:new Date(start + minute * 60000).toISOString(),
        latitude:latitude + Math.sin(angle) * radius,
        longitude:longitude + Math.cos(angle) * radius / Math.cos(latitude * Math.PI / 180),
      };
    })
  ).sort((a,b)=>a.registered_at.localeCompare(b.registered_at));
}

export function addIncidentTimeMachine(map, initialItems, initialDay, onChange) {
  let day = initialDay;
  let start = Date.parse(`${day}T00:00:00+05:00`) / 1000;
  if (!Number.isFinite(start)) throw new Error('Use a date in YYYY-MM-DD format');
  let end = start + 86400;
  const source = 'time-machine-incidents';
  const layers = ['time-machine-heat', 'time-machine-points'];
  let items = initialItems;
  let data = incidentFeatures(items);

  const panel = document.createElement('section');
  panel.className = 'mapboxgl-ctrl maplibregl-ctrl time-machine-control';
  panel.style.cssText = 'width:260px;max-width:calc(100vw - 96px);box-sizing:border-box;' +
    'padding:14px;border-radius:10px;background:#142133;color:#fff;' +
    'box-shadow:0 3px 16px #0005;font:14px/1.5 system-ui;pointer-events:auto;';
  panel.innerHTML = `
    <label style="display:block;color:inherit">Поступили до выбранного времени
      <input type="range" min="0" max="1440" step="1" value="1440"
        aria-label="Показать события до выбранного времени"
        style="display:block;width:100%;height:28px;min-height:28px;padding:0;margin:12px 0;accent-color:#66c2ff">
    </label>
    <output style="display:block;font-variant-numeric:tabular-nums"></output>
    <small>Голубой — на рассмотрении · жёлтый — в работе · зелёный — решены.<br>
    Статусы показаны на текущий момент.</small>`;
  const slider = panel.querySelector('input');
  const output = panel.querySelector('output');
  let frame = null;
  const cutoff = () => start + Number(slider.value) * 60;
  const filter = () => ['all',
    ['>=', ['get', 'timestamp'], start],
    ['<', ['get', 'timestamp'], end],
    ['<=', ['get', 'timestamp'], cutoff()],
  ];

  function update() {
    frame = null;
    const minutes = Number(slider.value);
    const time = `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
    const visible = items.filter(item => {const at = Date.parse(item.registered_at) / 1000; return at >= start && at < end && at <= cutoff();});
    const count = visible.length;
    output.textContent = `${day} · до ${time} (UTC+5) · обращений: ${count}`;
    onChange(visible, incidentFeatures(visible).features.length);
    slider.setAttribute('aria-valuetext', `${time}, показано событий: ${count}`);
    const expression = filter();
    for (const layer of layers) if (map.getLayer(layer)) map.setFilter(layer, expression);
  }
  function onInput() {
    if (frame === null) frame = requestAnimationFrame(update);
  }
  function installLayers() {
    if (!map.getSource(source)) map.addSource(source, {type: 'geojson', data});
    if (!map.getLayer(layers[0])) map.addLayer({
      id: layers[0], type: 'heatmap', source, filter: filter(),
      paint: {
        'heatmap-weight': 1,
        'heatmap-intensity': 1.4,
        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 9, 16, 15, 38],
        'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 12, 0.8, 17, 0],
        'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
          0, 'rgba(45,110,210,0)', 0.2, '#369ee8', 0.5, '#59d4bd',
          0.75, '#ffcb62', 1, '#ee5547'],
      },
    });
    if (!map.getLayer(layers[1])) map.addLayer({
      id: layers[1], type: 'circle', source, filter: filter(),
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 2, 16, 6],
        'circle-color': ['match', ['get', 'status'],
          'pending', '#66c2ff', 'confirmed', '#ffcb62', 'resolved', '#59d4bd', '#aebaca'],
        'circle-opacity': 0.9,
        'circle-stroke-color': '#142133',
        'circle-stroke-width': 1,
      },
    });
    if(frame !== null) cancelAnimationFrame(frame);
    update();
    fit();
  }

  function fit() {
    const points = data.features.map(f => f.geometry.coordinates);
    if (!points.length) {map.jumpTo({center:[67.5,48],zoom:4}); return;}
    const bounds = [[Infinity, Infinity], [-Infinity, -Infinity]];
    for (const [lng, lat] of points) {
      bounds[0][0] = Math.min(bounds[0][0], lng); bounds[0][1] = Math.min(bounds[0][1], lat);
      bounds[1][0] = Math.max(bounds[1][0], lng); bounds[1][1] = Math.max(bounds[1][1], lat);
    }
    map.fitBounds(bounds,{padding:50,maxZoom:14,duration:0});
  }

  const control = {onAdd: () => panel, onRemove: () => panel.remove()};
  map.addControl(control, 'bottom-left');
  slider.addEventListener('input', onInput);
  map.on('style.load', installLayers);
  if (map.isStyleLoaded()) installLayers();
  else update();

  return {
    setItems(nextItems, nextDay) {
      items = nextItems; day = nextDay; start = Date.parse(`${day}T00:00:00+05:00`) / 1000; end = start + 86400;
      data = incidentFeatures(items);
      map.getSource(source)?.setData(data);
      if(frame !== null) cancelAnimationFrame(frame);
      update(); fit();
    },
    remove() {
      slider.removeEventListener('input', onInput);
      if (frame !== null) cancelAnimationFrame(frame);
      map.off('style.load', installLayers);
      map.removeControl(control);
      for (const layer of [...layers].reverse()) if (map.getLayer(layer)) map.removeLayer(layer);
      if (map.getSource(source)) map.removeSource(source);
    },
  };
}
