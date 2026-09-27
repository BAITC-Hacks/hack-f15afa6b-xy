import {applyDetectedCity,beginVoiceTurn,finishVoiceTurn,mergeTranscript,speakPrompt,voicePrompts} from './voice.js?v=20260927-17';
import {mountMaps,resetLocationPicker} from './map.js?v=20260927-13';

const form=document.querySelector('#voice-live-form');
const control=document.querySelector('#voice-control');
const reset=document.querySelector('#voice-reset');
const language=document.querySelector('#live-language');
const conversation=document.querySelector('#voice-conversation');
const liveStatus=document.querySelector('#voice-live-status');
const orb=document.querySelector('[data-live-orb]');
const submit=document.querySelector('#voice-submit');
let stage='idle', mode='idle', finishing=false, cities=[], duplicateApproved=false, clarificationAsked=false;

async function api(path,data) {
  let response;
  try {
    response=await fetch(path,{method:data===undefined?'GET':'POST',credentials:'same-origin',headers:data===undefined?{Accept:'application/json'}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
  } catch {throw new Error('Сервер недоступен. Повторите действие.');}
  const result=await response.json().catch(()=>({detail:'Сервис вернул неверный ответ'}));
  if(!response.ok) throw new Error(result.detail||'Не удалось выполнить действие');
  return result;
}

function toast(text) {
  const node=document.querySelector('#voice-toast');node.textContent=text;node.hidden=false;
  clearTimeout(node._timer);node._timer=setTimeout(()=>node.hidden=true,6000);
}

function message(role,text) {
  const node=document.createElement('div');node.className=`voice-message ${role}`;
  const author=document.createElement('small');author.textContent=role==='agent'?'Pulse Voice':'Вы';
  const copy=document.createElement('span');copy.textContent=text;node.append(author,copy);conversation.append(node);
  conversation.scrollTop=conversation.scrollHeight;
}

function showAnalysis(analysis) {
  const node=document.querySelector('#voice-ai-insight');node.hidden=false;
  const urgency=analysis.urgency==='urgent'?' · срочно':' · обычный приоритет';
  const confidence=Number.isFinite(analysis.confidence)?` · ${Math.round(analysis.confidence*100)}%`:'';
  node.replaceChildren();
  const title=document.createElement('strong');title.textContent=`AI ${analysis.needs_clarification?'уточняет':'предполагает'}: ${analysis.category_label}`;
  const detail=document.createElement('span');detail.textContent=`${analysis.ai_active?'Laya':'Резервные правила'}${confidence}${urgency} · оператор проверит результат`;
  node.append(title,detail);
}

function setMode(next,text='') {
  mode=next;const listening=next==='listening';orb.classList.toggle('listening',listening);
  control.classList.toggle('listening',listening);control.disabled=['prompting','transcribing','speaking'].includes(next);
  reset.disabled=control.disabled||listening;language.disabled=control.disabled||listening;
  const labels={idle:'● Начать разговор',prompting:'Агент говорит…',listening:'■ Готово, закончить ответ',transcribing:'Распознаю ответ…',speaking:'Агент говорит…',review:'↻ Перезаписать ответы',retry:'● Повторить ответ'};
  control.textContent=labels[next];liveStatus.textContent=text||({listening:'Слушаю вас · запись завершится через 30 секунд',transcribing:'Перевожу речь в текст…',review:'Проверьте заполненную форму и точку на карте',retry:'Ответ не потерян — можно повторить запись'}[next]||'Агент готов помочь');
}

async function beginTurn(field) {
  stage=field;message('agent',voicePrompts[language.value][field]);
  try {
    await beginVoiceTurn({field,language:language.value,api,onState:state=>setMode(state),onTimeout:finishTurn});
  } catch(error) {setMode('retry',error.message);toast(error.message);}
}

async function finishTurn() {
  if(mode!=='listening'||finishing) return;
  const field=stage;finishing=true;setMode('transcribing');
  try {
    const result=await finishVoiceTurn();if(!result) return;
    const input=document.querySelector(field==='problem'?'#citizen-text':'#citizen-address');
    mergeTranscript(input,result.text);message('citizen',result.text);
    if(field==='address') {await applyDetectedCity(result.detected_city);document.querySelector('[data-address-search]')?.click();}
    if(field==='problem') {
      setMode('transcribing','Laya анализирует описание…');
      let analysis;
      try {
        analysis=await api('/api/voice/analyze',{text:input.value,language:language.value,region_id:form.elements.region_id.value});
        showAnalysis(analysis);message('agent',analysis.assistant_message);
      } catch(error) {toast(`AI-анализ недоступен: ${error.message}`);}
      if(analysis?.needs_clarification&&!clarificationAsked) {
        clarificationAsked=true;await beginTurn('problem');
      } else await beginTurn('address');
    }
    else {
      stage='review';message('agent',result.assistant_message);setMode('speaking');
      await speakPrompt(result.assistant_message,result.language,result.assistant_prompt,api);setMode('review');
    }
  } catch(error) {stage=field;setMode('retry',error.message);toast(error.message);}
  finally {finishing=false;}
}

function resetDialogue(clearFields=true) {
  conversation.replaceChildren();message('agent','Здравствуйте! Я задам два коротких вопроса и заполню обращение вместе с вами.');
  if(clearFields) {
    form.elements.text.value='';form.elements.address.value='';form.elements.media.value='';
    document.querySelector('#voice-ai-insight').hidden=true;
    document.querySelector('[data-geocode-results]').replaceChildren();resetLocationPicker(form);
  }
  stage='idle';duplicateApproved=false;clarificationAsked=false;submit.textContent='Проверить и отправить обращение';setMode('idle');
}

function updateCities() {
  const matches=cities.filter(city=>city.region_id===form.elements.region_id.value).sort((a,b)=>a.name_ru.localeCompare(b.name_ru,'ru'));
  form.elements.city_code.replaceChildren(...matches.map(city=>{
    const option=new Option(city.name_ru,city.code);option.dataset.name=city.name_ru;option.dataset.region=city.region_id;return option;
  }));
  form.elements.city_code.dispatchEvent(new Event('change',{bubbles:true}));
}

function fileData(file) {
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('Не удалось прочитать файл'));reader.readAsDataURL(file);});
}

async function mediaData() {
  const file=form.elements.media.files[0];if(!file) return {photo_data:null,video_data:null};
  const images=['image/jpeg','image/png','image/webp'],videos=['video/mp4','video/webm'];
  if(![...images,...videos].includes(file.type)) throw new Error('Выберите фото JPEG/PNG/WebP или видео MP4/WebM');
  const limit=images.includes(file.type)?4:12;if(file.size>limit*1024*1024) throw new Error(`Файл должен быть не больше ${limit} МБ`);
  const data=await fileData(file);return images.includes(file.type)?{photo_data:data,video_data:null}:{photo_data:null,video_data:data};
}

async function initialize() {
  resetDialogue(false);
  try {
    const [regions,result,health,appHealth]=await Promise.all([api('/api/regions'),api('/api/workspace/cities'),api('/api/voice/health'),api('/api/health')]);
    cities=result.items;
    form.elements.region_id.replaceChildren(...regions.regions.map(region=>new Option(region.name_ru,region.id,false,region.id==='KZ-ALA')));
    updateCities();mountMaps(document);
    const healthNode=document.querySelector('[data-voice-health]'),ready=health.status==='healthy'&&health.tts?.status==='healthy';
    const aiReady=['ok','healthy'].includes(appHealth.laya?.status);
    healthNode.textContent=ready?`${aiReady?'Laya · ':''}OmniVoice · распознавание подключены`:'Доступен резервный голос';healthNode.classList.toggle('offline',!ready);
  } catch(error) {toast(error.message);document.querySelector('[data-voice-health]').textContent='Голосовой сервис недоступен';}
}

control.addEventListener('click',async()=>{
  if(mode==='listening') {await finishTurn();return;}
  if(mode==='review') resetDialogue();
  if(mode==='idle') await beginTurn('problem');
  else if(mode==='retry') await beginTurn(stage);
});
reset.addEventListener('click',()=>resetDialogue());
form.elements.region_id.addEventListener('change',updateCities);
form.addEventListener('input',()=>{duplicateApproved=false;submit.textContent='Проверить и отправить обращение';});
form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try {
    const data=new FormData(form),number=name=>data.get(name)?Number(data.get(name)):null;
    if(!data.get('text').trim()) throw new Error('Сначала расскажите, что произошло');
    if(!data.get('latitude')||!data.get('longitude')) throw new Error('Проверьте адрес и выберите точку на карте');
    const payload={text:data.get('text').trim(),address:data.get('address').trim()||null,region_id:data.get('region_id'),city_code:data.get('city_code'),district:data.get('district')||null,language:data.get('language'),channel:'web',latitude:number('latitude'),longitude:number('longitude'),location_accuracy_m:number('location_accuracy_m'),...await mediaData()};
    const {photo_data,video_data,...probe}=payload;
    if(!duplicateApproved) {
      const similar=await api('/api/workspace/public/similar',probe);
      if(similar.items.length) {duplicateApproved=true;submit.textContent='Отправить как отдельную проблему';throw new Error(`Найдено похожих обращений: ${similar.items.length}. Проверьте карту или нажмите отправить ещё раз.`);}
    }
    const result=await api('/api/workspace/intake',payload);
    document.querySelector('#voice-receipt').innerHTML=`<div class="receipt"><strong>✓ Обращение зарегистрировано</strong><p>${result.id} · номер можно использовать для проверки статуса</p><a href="/#citizen">Открыть кабинет гражданина</a></div>`;
    message('agent',`Готово. Обращение ${result.id} зарегистрировано.`);submit.textContent='Обращение отправлено';
  } catch(error) {toast(error.message);}
  finally {submit.disabled=false;}
});

initialize();
