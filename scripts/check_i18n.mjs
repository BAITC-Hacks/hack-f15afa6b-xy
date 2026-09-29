import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../static/i18n.js',import.meta.url),'utf8');
for(const file of ['workspace.html','public-map.html','voice-live.html','index.html']) {
  const html=fs.readFileSync(new URL(`../static/${file}`,import.meta.url),'utf8');
  assert.ok(html.includes('data-language-switch'),`${file} has no language switch`);
  assert.ok(html.indexOf('/static/i18n.js')<html.lastIndexOf('<script'),`${file} loads localization before app code`);
}

function translator(language) {
  const context={
    localStorage:{getItem:()=>language,setItem(){}},navigator:{language},location:{reload(){}},
    document:{documentElement:{lang:''},title:'Pulse 109 — Вход',readyState:'loading',addEventListener(){},querySelectorAll(){return[]}},
    MutationObserver:class {observe(){}},Node:{TEXT_NODE:3,ELEMENT_NODE:1},console,
  };
  vm.runInNewContext(source,context);
  return context.pulseI18n.translate;
}

const en=translator('en');
assert.equal(en('Очередь обращений'),'Request queue');
assert.equal(en('Все города · обращений: 45'),'All cities · requests: 45');
assert.equal(en('Просрочено 2 ч 16 мин'),'Overdue by 2h 16m');

const kk=translator('kk');
assert.equal(kk('Очередь обращений'),'Өтініштер кезегі');
assert.equal(kk('2. Проверить всплеск: Отопление'),'2. Өсімді тексеру: Жылыту');
assert.equal(kk('Открыть PULSE-109'),'Ашу: PULSE-109');
assert.equal(kk('Просрочено на 2 ч 16 мин'),'Мерзімі өтті: 2 сағ 16 мин');

console.log('i18n checks passed: ru, kk, en');
