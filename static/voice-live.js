import {applyDetectedCity,applyLanguageResult,beginVoiceTurn,clearLanguageResult,discardSavedVoiceTurn,finishVoiceTurn,mergeTranscript,previewTranscript,retrySavedVoiceTurn,selectedIntakeLanguage,selectedVoiceLanguage,settleTranscriptPreview,speakPrompt,voicePrompts} from './voice.js?v=20260930-voice2';
import {mountMaps,resetLocationPicker} from './map.js?v=20260929-brand';
import {mountThinkingOrb} from './thinking-orb.js?v=20260929-brand';

const form=document.querySelector('#voice-live-form');
const control=document.querySelector('#voice-control');
const reset=document.querySelector('#voice-reset');
const language=document.querySelector('#live-language');
const conversation=document.querySelector('#voice-conversation');
const liveStatus=document.querySelector('#voice-live-status');
const orb=document.querySelector('[data-live-orb]');
const thinkingOrb=mountThinkingOrb(orb);
const submit=document.querySelector('#voice-submit');
let stage='idle', mode='idle', finishing=false, cities=[], duplicateApproved=false;
let realtimeAvailable=false,liveDebounceMs=2500,liveAnalysisTimer=null,liveAnalysisRevision=0,liveAnalysisController=null;

async function api(path,data,signal) {
  let response;
  const timeout=AbortSignal.timeout(20000);
  signal=signal?AbortSignal.any([signal,timeout]):timeout;
  const csrf=document.cookie.split('; ').find(value=>value.startsWith('pulse109_csrf='))?.split('=')[1]||'';
  try {
    response=await fetch(path,{method:data===undefined?'GET':'POST',credentials:'same-origin',headers:data===undefined?{Accept:'application/json'}:{'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(csrf)},body:data===undefined?undefined:JSON.stringify(data),signal});
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
  node.replaceChildren();
  const title=document.createElement('strong');title.textContent=`ИИ ${analysis.needs_clarification?'просит уточнить':'предлагает категорию'}: ${analysis.category_label}`;
  const detail=document.createElement('span');detail.textContent=`Рекомендация ИИ${urgency} · оператор проверит результат`;
  node.append(title,detail);
}

function scheduleLiveAnalysis(text,result) {
  clearTimeout(liveAnalysisTimer);liveAnalysisController?.abort();const revision=++liveAnalysisRevision;
  liveAnalysisTimer=setTimeout(async()=>{
    if(stage!=='problem'||revision!==liveAnalysisRevision||text.trim().length<8) return;
    liveAnalysisController=new AbortController();
    try {
      const analysis=await api('/api/voice/analyze',{text,language:result.language||language.value,region_id:form.elements.region_id.value},liveAnalysisController.signal);
      if(revision===liveAnalysisRevision&&stage==='problem') showAnalysis(analysis);
    } catch { /* The final transcript and manual flow remain available. */ }
  },liveDebounceMs);
}

function setMode(next,text='') {
  mode=next;const listening=next==='listening';orb.classList.toggle('listening',listening);
  thinkingOrb.setState(listening?'listening':['prompting','transcribing','speaking'].includes(next)?'processing':next==='retry'?'error':next==='review'?'result':'idle');
  control.classList.toggle('listening',listening);control.disabled=['prompting','transcribing','speaking'].includes(next);
  reset.disabled=control.disabled||listening;language.disabled=control.disabled||listening;
  const labels={idle:'● Начать разговор',prompting:'Помощник задаёт вопрос…',listening:'■ Готово, закончить ответ',transcribing:'Распознаю ответ…',speaking:'Помощник задаёт вопрос…',review:'↻ Перезаписать ответы',retry:'● Повторить ответ'};
  control.textContent=labels[next];liveStatus.textContent=text||({listening:'Слушаю вас · закончу запись после короткой паузы',transcribing:'Перевожу речь в текст…',review:'Проверьте заполненную форму и точку на карте',retry:'Повторите запись или исправьте текст в форме'}[next]||'Нажмите «Начать разговор»');
}

async function beginTurn(field,promptText=null) {
  const selected=selectedVoiceLanguage(language),promptLanguage=selected.language==='auto'?(selected.hintLanguage||'mixed'):selected.language;
  stage=field;message('agent',promptText||voicePrompts[promptLanguage][field]);
  try {
    await beginVoiceTurn({field,...selected,promptText,api,realtime:realtimeAvailable,onState:state=>setMode(state,state==='listening'&&language.value==='auto'?'Определяем язык…':''),onSpeech:()=>setMode('listening','Слышу вас · определяем язык'),onPartial:(text,result)=>{
      previewTranscript(document.querySelector(field==='problem'?'#citizen-text':'#citizen-address'),text);
      liveStatus.textContent=applyLanguageResult(language,result)||'Заполняем черновик…';
      scheduleLiveAnalysis(text,result);
    },onSilence:finishTurn,onTimeout:finishTurn});
  } catch(error) {setMode('retry',error.message);toast(error.message);}
}

async function useTranscript(field,result) {
    clearTimeout(liveAnalysisTimer);liveAnalysisController?.abort();liveAnalysisRevision++;
    const input=document.querySelector(field==='problem'?'#citizen-text':'#citizen-address');
    settleTranscriptPreview(input);
    mergeTranscript(input,result.text);message('citizen',result.text);
    const languageStatus=applyLanguageResult(language,result);
    if(languageStatus) liveStatus.textContent=languageStatus;
    if(field==='problem') {
      await beginTurn('address');
      return;
    }
    await applyDetectedCity(result.detected_city);
    document.querySelector('[data-address-search]')?.click();
    const replyLanguage=result.response_language||selectedVoiceLanguage(language).hintLanguage||'mixed';
    stage='review';message('agent',voicePrompts[replyLanguage].review);setMode('speaking');
    await speakPrompt(voicePrompts[replyLanguage].review,replyLanguage,`${replyLanguage}_review`,api);
    setMode('review');
}

async function finishTurn() {
  if(mode!=='listening'||finishing) return;
  const field=stage;finishing=true;setMode('transcribing');
  try {
    const result=await finishVoiceTurn();if(result) await useTranscript(field,result);
  } catch(error) {
    settleTranscriptPreview(document.querySelector(field==='problem'?'#citizen-text':'#citizen-address'),true);
    stage=field;setMode('retry',error.message);toast(error.message);
  }
  finally {finishing=false;}
}

function resetDialogue(clearFields=true) {
  clearTimeout(liveAnalysisTimer);liveAnalysisController?.abort();liveAnalysisRevision++;
  conversation.replaceChildren();message('agent','Здравствуйте! Сәлеметсіз бе! Говорите на русском или казахском — я определю язык автоматически.');
  if(clearFields) {
    settleTranscriptPreview(form.elements.text);settleTranscriptPreview(form.elements.address);
    form.elements.text.value='';form.elements.address.value='';form.elements.media.value='';form.elements.public_consent.checked=false;
    document.querySelector('#voice-ai-insight').hidden=true;
    document.querySelector('[data-geocode-results]').replaceChildren();resetLocationPicker(form);
  }
  clearLanguageResult(language);discardSavedVoiceTurn();
  stage='idle';duplicateApproved=false;submit.textContent='Проверить и отправить обращение';setMode('idle');
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
    const [regions,result,health]=await Promise.all([api('/api/regions'),api('/api/workspace/cities'),api('/api/voice/health')]);
    cities=result.items;
    form.elements.region_id.replaceChildren(...regions.regions.map(region=>new Option(region.name_ru,region.id,false,region.id==='KZ-ALA')));
    updateCities();mountMaps(document);
    realtimeAvailable=health.realtime?.status==='configured';
    liveDebounceMs=health.realtime?.live_copilot_debounce_ms||2500;
    const healthNode=document.querySelector('[data-voice-health]'),ready=realtimeAvailable||health.status==='healthy';
    healthNode.textContent=ready?'Голосовое распознавание подключено':'Голос временно недоступен · можно продолжить вручную';healthNode.classList.toggle('offline',!ready);
  } catch(error) {toast(error.message);document.querySelector('[data-voice-health]').textContent='Голосовой сервис недоступен';}
}

control.addEventListener('click',async()=>{
  if(mode==='listening') {await finishTurn();return;}
  if(mode==='review') resetDialogue();
  if(mode==='idle') await beginTurn('problem');
  else if(mode==='retry') {
    finishing=true;
    try {
      const result=await retrySavedVoiceTurn(state=>setMode(state));
      if(result) await useTranscript(stage,result); else await beginTurn(stage);
    } catch(error) {setMode('retry',error.message);toast(error.message);}
    finally {finishing=false;}
  }
});
reset.addEventListener('click',()=>resetDialogue());
language.addEventListener('change',()=>{clearLanguageResult(language);liveStatus.textContent=language.value==='auto'?'Язык: определять автоматически':`Язык выбран вручную: ${language.value==='kk'?'Қазақша':'Русский'}`;});
form.elements.region_id.addEventListener('change',updateCities);
form.addEventListener('input',()=>{duplicateApproved=false;submit.textContent='Проверить и отправить обращение';});
form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try {
    const data=new FormData(form),number=name=>data.get(name)?Number(data.get(name)):null;
    if(!data.get('text').trim()) throw new Error('Сначала расскажите, что произошло');
    if(!data.get('latitude')||!data.get('longitude')) throw new Error('Проверьте адрес и выберите точку на карте');
    const payload={text:data.get('text').trim(),address:data.get('address').trim()||null,region_id:data.get('region_id'),city_code:data.get('city_code'),district:data.get('district')||null,language:selectedIntakeLanguage(language),channel:'web',latitude:number('latitude'),longitude:number('longitude'),location_accuracy_m:number('location_accuracy_m'),public_consent:data.get('public_consent')==='on',...await mediaData()};
    const {photo_data,video_data,...probe}=payload;
    if(!duplicateApproved) {
      const similar=await api('/api/workspace/public/similar',probe);
      if(similar.items.length) {duplicateApproved=true;submit.textContent='Отправить как отдельную проблему';throw new Error(`Найдено похожих обращений: ${similar.items.length}. Проверьте карту или нажмите отправить ещё раз.`);}
    }
    const result=await api('/api/workspace/intake',payload);
    const synthetic=result.data_origin==='synthetic';
    const account=await api('/api/auth/config');
    document.querySelector('#voice-receipt').innerHTML=`<div class="receipt"><strong>✓ Обращение зарегистрировано</strong><p>${result.id} · ${synthetic?'номер можно использовать для проверки статуса':'приватное обращение; публикация возможна только после согласия и проверки оператором'}</p>${account.user?.role==='citizen'?'<a href="/citizen">Открыть мои обращения</a>':'<small>Обращение отправлено без личного кабинета.</small>'}</div>`;
    message('agent',`Готово. Обращение ${result.id} зарегистрировано.`);submit.textContent='Обращение отправлено';
  } catch(error) {toast(error.message);}
  finally {submit.disabled=false;}
});

initialize();
