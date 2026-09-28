const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.argv[2]||'http://127.0.0.1:8769';

(async()=>{
  let fixture='ru';
  const browser=await chromium.launch({headless:true,channel:process.env.P109_BROWSER||'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{
    const node=()=>({connect(){},disconnect(){}});
    class AudioContextMock {
      constructor(){this.sampleRate=16000;}
      createMediaStreamSource(){return node();}
      createScriptProcessor(){return globalThis.__voiceProcessor={...node(),onaudioprocess:null};}
      createGain(){return {...node(),gain:{value:1}};}
      close(){return Promise.resolve();}
      get destination(){return {};}
    }
    Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}});
    globalThis.AudioContext=AudioContextMock;
    Object.defineProperty(globalThis,'speechSynthesis',{value:{cancel(){},speak(value){queueMicrotask(()=>value.onend?.());}}});
    Object.defineProperty(globalThis,'SpeechSynthesisUtterance',{value:class {constructor(text){this.text=text;}}});
    globalThis.__feedVoice=()=>{
      const samples=Float32Array.from({length:4096},(_,index)=>.04*Math.sin(index/8));
      globalThis.__voiceProcessor.onaudioprocess({inputBuffer:{getChannelData:()=>samples}});
    };
  });
  await page.route('**/api/voice/**',async route=>{
    const url=new URL(route.request().url()), path=url.pathname;
    if(path.endsWith('/health')) return route.fulfill({json:{status:'healthy',tts:{status:'healthy'}}});
    if(path.endsWith('/speak')) return route.fulfill({status:503,json:{detail:'Озвучивание временно недоступно'}});
    if(path.endsWith('/transcribe')) {
      if(fixture==='unavailable') return route.fulfill({status:503,json:{detail:'Распознавание речи временно недоступно'}});
      const body=route.request().postDataJSON(), requested=body.language;
      const detected=requested==='auto'?fixture:requested;
      const unknown=detected==='unknown';
      const language=unknown?'unknown':detected;
      const responseLanguage=unknown?null:language;
      const text=unknown?'Абай 44':language==='kk'?'Абай көшесінде таңертең су жоқ':'На Абая с утра нет воды';
      const next=body.field==='problem'?'address':'review';
      const prompt=responseLanguage?`${responseLanguage}_${next}`:'mixed_language_retry';
      return route.fulfill({json:{text,field:body.field,next_field:next,assistant_message:unknown?'Не удалось определить язык. Тілді таңдаңыз немесе фразаны қайталаңыз.':responseLanguage==='kk'?'Енді мекенжайды айтыңыз.':'Теперь назовите адрес.',assistant_prompt:prompt,detected_city:null,language,response_language:responseLanguage,source:requested==='auto'?'text':'manual',needs_language_choice:unknown,audio_stored:false}});
    }
    if(path.endsWith('/analyze')) {
      const body=route.request().postDataJSON(), kk=body.language==='kk';
      return route.fulfill({json:{category:'water_supply',category_label:kk?'Сумен жабдықтау':'Водоснабжение',confidence:.9,urgency:'normal',needs_clarification:false,spam_suspected:false,ai_active:true,assistant_message:kk?'Түсіндім: «Сумен жабдықтау». Енді оқиға орнын нақтылайық.':'Похоже, это «Водоснабжение». Теперь уточним место.',language:body.language,response_language:body.language,source:'text',needs_language_choice:false}});
    }
    return route.continue();
  });
  const record=async()=>{
    const button=page.getByRole('button',{name:/Начать разговор|Повторить ответ/});
    await button.waitFor();await button.click();
    try {await page.waitForFunction(()=>globalThis.__voiceProcessor?.onaudioprocess);}
    catch(error) {throw new Error(`${error.message}; status=${await page.locator('#voice-live-status').innerText()}; browser=${errors.join(' | ')}`);}
    await page.evaluate(()=>globalThis.__feedVoice());
    await page.getByRole('button',{name:/Готово, закончить ответ/}).click();
  };
  try {
    await page.goto(base+'/voice');
    const language=page.getByLabel('Язык',{exact:true});
    assert.equal(await language.inputValue(),'auto');
    assert.deepEqual(await language.locator('option').allTextContents(),['Определять автоматически','Қазақша','Русский']);
    await record();
    await page.getByLabel('Что произошло?',{exact:true}).waitFor({state:'visible'});
    await page.waitForFunction(()=>document.querySelector('#citizen-text').value.includes('нет воды'));
    assert.equal(await language.getAttribute('data-detected-language'),'ru');
    assert.match(await page.locator('#voice-conversation').innerText(),/Похоже, это «Водоснабжение»/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    console.log('PASS VOICE UI 1: desktop auto-detects Russian and continues in Russian');

    await page.setViewportSize({width:390,height:844});fixture='kk';await page.reload();
    await record();
    await page.waitForFunction(()=>document.querySelector('#citizen-text').value.includes('су жоқ'));
    assert.equal(await language.getAttribute('data-detected-language'),'kk');
    assert.match(await page.locator('#voice-conversation').innerText(),/Түсіндім/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    console.log('PASS VOICE UI 2: mobile auto-detects Kazakh and continues in Kazakh');

    fixture='kk';await page.reload();await language.selectOption('ru');
    await record();
    await page.waitForFunction(()=>document.querySelector('#citizen-text').value.includes('нет воды'));
    assert.match(await page.locator('#voice-conversation').innerText(),/Похоже, это «Водоснабжение»/);
    console.log('PASS VOICE UI 3: manual Russian overrides automatic detection');

    fixture='unknown';await page.reload();await language.selectOption('auto');
    await record();
    await page.waitForFunction(()=>document.querySelector('#voice-live-status').textContent.includes('выберите вручную'));
    assert.equal(await page.getByLabel('Что произошло?',{exact:true}).inputValue(),'Абай 44');
    assert.equal(await language.isEnabled(),true);
    console.log('PASS VOICE UI 4: uncertain language preserves text and asks for a manual choice');

    fixture='unavailable';await page.reload();
    await record();
    await page.waitForFunction(()=>document.querySelector('#voice-live-status').textContent.includes('временно недоступно'));
    await page.getByLabel('Что произошло?',{exact:true}).fill('Ввожу обращение вручную');
    assert.equal(await page.getByLabel('Что произошло?',{exact:true}).inputValue(),'Ввожу обращение вручную');
    assert.deepEqual(errors,[]);
    console.log('PASS VOICE UI 5: STT failure keeps manual input usable');
  } finally {await browser.close();}
  console.log('ALL 5 VOICE UI CHECKS PASSED');
})().catch(error=>{console.error(error);process.exitCode=1;});
