import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {locationMap} from '../static/views.js';

assert.equal(locationMap({},'test','Место'),'');
const exact=locationMap({latitude:43.2461819,longitude:76.9269795},'test','Место');
assert.match(exact,/data-map-mode="view"/);
assert.match(exact,/https:\/\/2gis.kz\/geo\/76.9269795,43.2461819\?m=76.9269795,43.2461819\/17/);
assert.match(exact,/Координаты: 43.2461819, 76.9269795/);
const address=locationMap({address:'Абая 78',city_code:'750000000',city_name:'Алматы'},'test','Место');
assert.match(address,/data-map-mode="address"/);
assert.doesNotMatch(address,/data-latitude|Точка подтверждена/);
assert.match(address,/https:\/\/2gis.kz\/search\//);
assert.doesNotMatch(address,/google/);
const unknown=locationMap({address:'Толе би 10'},'test','Место');
assert.match(unknown,/Город не указан/);
assert.doesNotMatch(unknown,/750000000/);
assert.doesNotMatch(locationMap({address:'<img src=x onerror=alert(1)>'},'test','Место'),/<img/);
assert.equal(locationMap({latitude:999,longitude:76.9},'test','Место'),'');
const approximate={latitude:43.25,longitude:76.93,location_source:'district_approximate'};
assert.equal(locationMap(approximate,'test','Место'),'');
assert.match(locationMap({...approximate,address:'Абая 78',city_code:'750000000'},'test','Место'),/data-map-mode="address"/);
assert.doesNotMatch(locationMap({...approximate,address:'Абая 78',city_code:'750000000'},'test','Место'),/data-latitude/);
console.log('PASS: map states — full precision, 2GIS coordinates and search, no fake approximate point, unknown city, escaping and invalid coordinates');

// Run the live checks in cua_repl against the local synthetic demo.
export async function checkCaseMapUi(tab,viewport,base,output) {
  const results=[];
  if(await tab.playwright.locator('#case-dialog').isVisible()) {
    await tab.playwright.getByRole('button',{name:'Закрыть карточку',exact:true}).click();
    await tab.playwright.locator('#case-dialog').waitFor({state:'hidden'});
  }
  if(await tab.url()!==`${base}/#queue`) await tab.goto(`${base}/#queue`);
  await tab.playwright.getByRole('searchbox',{name:'Поиск обращений'}).fill('PULSE-2451');
  await tab.getAXState({emit:false});
  await tab.playwright.locator('.queue-row[data-id="PULSE-2451"]').click();
  await viewport.set({width:1440,height:1000});
  await tab.playwright.locator('#case-map canvas').waitFor({state:'visible',timeoutMs:20000});
  await tab.playwright.locator('#case-map[data-lookup-state="selected"]').waitFor({state:'visible',timeoutMs:20000});
  assert.equal(await tab.playwright.locator('#case-map').getAttribute('data-city-code'),'750000000');
  await tab.playwright.locator('#case-map[aria-busy="false"]').waitFor({state:'visible',timeoutMs:30000});
  assert.equal(await tab.playwright.locator('#case-map .maplibregl-marker').count(),1);
  assert.match(await tab.playwright.locator('[data-map-status]').innerText(),/Метка указывает на здание/);
  assert.match(await tab.playwright.locator('[data-map-status]').innerText(),/60, улица Байтурсынова, Алмалинский район/);
  assert.equal(await tab.playwright.locator('[data-map-results] button').count(),1);
  const coordinates=await tab.playwright.locator('[data-map-coordinates]').innerText();
  assert.match(coordinates,/Координаты: 43.2461819, 76.9269795/);
  results.push('PASS: only the matching house gets a pin; wrong-street and street-only results are excluded');

  await tab.playwright.getByLabel('Текст ответа',{exact:true}).fill('Черновик с картой');
  await tab.playwright.getByLabel('Категория обращения',{exact:true}).selectOption('housing_maintenance');
  await tab.playwright.locator('.case-service').filter({hasText:'Жилищная'}).waitFor({state:'visible'});
  assert.equal(await tab.playwright.locator('#case-map').getAttribute('data-lookup-state'),'selected');
  assert.equal(await tab.playwright.locator('[data-map-results] [aria-pressed="true"]').count(),1);
  assert.equal(await tab.playwright.evaluate(()=>document.querySelector('#reply-text').value),'Черновик с картой');
  const href=await tab.playwright.getByRole('link',{name:'Показать в 2ГИС',exact:true}).getAttribute('href');
  assert.equal(href,'https://2gis.kz/geo/76.9269795,43.2461819?m=76.9269795,43.2461819/17');
  results.push('PASS: 2GIS receives the same full coordinates; marker and draft survive category changes');
  await tab.playwright.getByRole('heading',{name:'Обращение и история',exact:true}).click();
  await writeFile(`${output}/operator-address-map-desktop.png`,await tab.screenshot({fullPage:false}));

  for(const width of [390,320]) {
    await viewport.set({width,height:900});
    await tab.playwright.locator('.case-panel-nav').waitFor({state:'visible'});
    const screenshot=await tab.screenshot({fullPage:false});
    assert.ok(await tab.playwright.evaluate(()=>document.querySelector('#case-dialog').scrollWidth<=document.querySelector('#case-dialog').clientWidth));
    if(width===390) await writeFile(`${output}/operator-address-map-mobile.png`,screenshot);
  }
  await tab.playwright.locator('[data-panel="support"]').click();
  await tab.playwright.locator('[data-panel="story"]').click();
  assert.equal(await tab.playwright.locator('#case-map .maplibregl-marker').count(),1);
  results.push('PASS: map fits narrow screens and remains selected after switching panels');
  await tab.playwright.getByRole('button',{name:'Закрыть карточку',exact:true}).click();
  await tab.playwright.locator('#case-dialog').waitFor({state:'hidden'});
  await tab.playwright.getByRole('searchbox',{name:'Поиск обращений'}).fill('PULSE-2432');
  await tab.getAXState({emit:false});
  await tab.playwright.locator('.queue-row[data-id="PULSE-2432"]').click();
  await tab.playwright.locator('#case-title').filter({hasText:'PULSE-2432'}).waitFor({state:'visible'});
  assert.equal(await tab.playwright.locator('#case-map').count(),0);
  assert.equal(await tab.playwright.locator('[data-map-search]').count(),0);
  results.push('PASS: an addressless complaint is not assigned an invented district-center pin');
  await viewport.reset();
  return results;
}
