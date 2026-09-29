import {setThinkingOrbState} from './thinking-orb.js?v=20260929-brand';

let recording=null, playback=null, processing=false, failedTurn=null;
const voiceThreshold=.008, silenceMs=1400, previewMs=4500;

export const voicePrompts={
  ru:{problem:'Расскажите, что произошло. Говорите до тридцати секунд.',address:'Теперь назовите адрес или ближайший ориентир.',review:'Проверьте текст, точку на карте и приложите фото или видео.'},
  kk:{problem:'Не болғанын айтып беріңіз. Отыз секундқа дейін сөйлеңіз.',address:'Енді мекенжайды немесе жақын жердегі нысанды айтыңыз.',review:'Мәтінді, картадағы орынды тексеріп, фото немесе видео тіркеңіз.'},
  mixed:{problem:'Расскажите о проблеме. Мәселе туралы айтып беріңіз.',address:'Теперь назовите адрес. Енді мекенжайды айтыңыз.',review:'Проверьте данные. Мәліметтерді тексеріңіз.'},
};

function browserSpeak(text,language) {
  if(!('speechSynthesis' in window)) return Promise.resolve();
  speechSynthesis.cancel();
  return new Promise(resolve=>{
    const utterance=new SpeechSynthesisUtterance(text);
    utterance.lang=language==='kk'?'kk-KZ':'ru-RU';
    utterance.rate=.95;utterance.onend=resolve;utterance.onerror=resolve;
    speechSynthesis.speak(utterance);
  });
}

export async function speakPrompt(text,language,prompt,api) {
  if(playback) {playback.pause();playback=null;}
  try {
    const result=await api('/api/voice/speak',{prompt});
    if(!result.audio_data?.startsWith('data:audio/wav;base64,')) throw new Error('Invalid audio');
    const audio=new Audio(result.audio_data);playback=audio;
    await new Promise((resolve,reject)=>{
      audio.onended=resolve;audio.onerror=()=>reject(new Error('Audio playback failed'));
      audio.play().catch(reject);
    });
    playback=null;
  } catch {
    playback=null;
    await browserSpeak(text,language);
  }
}

function flatten(chunks) {
  const length=chunks.reduce((sum,chunk)=>sum+chunk.length,0), output=new Float32Array(length);
  let offset=0;for(const chunk of chunks) {output.set(chunk,offset);offset+=chunk.length;}
  return output;
}

function resample(input,inputRate,outputRate=16000) {
  if(inputRate===outputRate) return input;
  const output=new Float32Array(Math.round(input.length*outputRate/inputRate));
  const scale=inputRate/outputRate;
  if(scale>1) {
    for(let i=0;i<output.length;i++) {
      const start=Math.floor(i*scale), end=Math.max(start+1,Math.min(input.length,Math.floor((i+1)*scale)));
      let sum=0;for(let j=start;j<end;j++) sum+=input[j];output[i]=sum/(end-start);
    }
    return output;
  }
  for(let i=0;i<output.length;i++) {
    const position=i*scale, left=Math.floor(position), right=Math.min(left+1,input.length-1);
    output[i]=input[left]+(input[right]-input[left])*(position-left);
  }
  return output;
}

export function prepareSpeech(input,inputRate) {
  const samples=resample(input,inputRate), mean=samples.reduce((sum,value)=>sum+value,0)/samples.length;
  let peak=0, energy=0;
  for(let i=0;i<samples.length;i++) {samples[i]-=mean;peak=Math.max(peak,Math.abs(samples[i]));energy+=samples[i]**2;}
  const rms=Math.sqrt(energy/samples.length);
  if(!samples.length||peak<.006||rms<.0015) throw new Error('Микрофон записал слишком тихо. Говорите ближе и повторите ответ.');
  const gain=Math.min(4,.92/peak,.08/rms);
  if(Math.abs(gain-1)>.05) for(let i=0;i<samples.length;i++) samples[i]*=gain;
  return samples;
}

function wav(samples) {
  const buffer=new ArrayBuffer(44+samples.length*2), view=new DataView(buffer);
  const text=(offset,value)=>[...value].forEach((char,index)=>view.setUint8(offset+index,char.charCodeAt(0)));
  text(0,'RIFF');view.setUint32(4,36+samples.length*2,true);text(8,'WAVE');text(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);
  view.setUint16(34,16,true);text(36,'data');view.setUint32(40,samples.length*2,true);
  samples.forEach((sample,index)=>view.setInt16(44+index*2,Math.max(-1,Math.min(1,sample))*0x7fff,true));
  return new Blob([buffer],{type:'audio/wav'});
}

function dataUrl(blob) {
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});
}

export function voiceActivity(samples,state,now) {
  let energy=0;for(const sample of samples) energy+=sample*sample;
  const rms=Math.sqrt(energy/samples.length),speaking=rms>=voiceThreshold;
  const heard=state.heard||speaking,lastVoiceAt=speaking?now:state.lastVoiceAt;
  return {rms,speaking,heard,lastVoiceAt,shouldStop:heard&&!speaking&&now-lastVoiceAt>=silenceMs};
}

async function previewVoice(current) {
  if(recording!==current||current.previewPromise||!current.activity.heard) return;
  const chunks=current.chunks.slice(),sampleCount=chunks.reduce((sum,chunk)=>sum+chunk.length,0);
  if(sampleCount<current.context.sampleRate*2) return;
  current.previewPromise=(async()=>{
    try {
      const samples=prepareSpeech(flatten(chunks),current.context.sampleRate);
      const result=await current.api('/api/voice/transcribe',{audio_data:await dataUrl(wav(samples)),language:current.language,hint_language:current.hintLanguage,field:current.field});
      if(recording===current&&result.text) {
        if(current.language==='auto'&&result.response_language) current.hintLanguage=result.response_language;
        current.onPartial(result.text,result);
      }
    } catch { /* A partial transcript is optional; the final pass still runs. */ }
  })();
  await current.previewPromise;
  if(recording===current) current.previewPromise=null;
}

export async function beginVoiceTurn({field,language,hintLanguage=null,api,onState=()=>{},onTimeout=()=>{},onPartial=()=>{},onSpeech=()=>{},onSilence=()=>{}}) {
  if(recording||processing) throw new Error('Дождитесь завершения текущего ответа');
  if(!navigator.mediaDevices?.getUserMedia) throw new Error('Браузер не поддерживает запись с микрофона');
  const promptLanguage=language==='auto'?(hintLanguage||'mixed'):language;
  onState('prompting');
  await speakPrompt(voicePrompts[promptLanguage][field],promptLanguage,`${promptLanguage}_${field}`,api);
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
  const context=new AudioContext(), source=context.createMediaStreamSource(stream);
  const processor=context.createScriptProcessor(4096,1,1), mute=context.createGain(), chunks=[];
  const current={field,language,hintLanguage,stream,context,source,processor,mute,chunks,api,onPartial,activity:{heard:false,lastVoiceAt:0},silenceTriggered:false,previewPromise:null};
  failedTurn=null;
  recording=current;
  mute.gain.value=0;source.connect(processor);processor.connect(mute);mute.connect(context.destination);
  processor.onaudioprocess=event=>{
    const samples=new Float32Array(event.inputBuffer.getChannelData(0));chunks.push(samples);
    const previous=current.activity,currentState=voiceActivity(samples,previous,performance.now());current.activity=currentState;
    if(currentState.speaking&&!previous.heard) onSpeech();
    if(currentState.shouldStop&&!current.silenceTriggered) {current.silenceTriggered=true;queueMicrotask(onSilence);}
  };
  current.timer=setTimeout(onTimeout,30000);
  current.previewTimer=setInterval(()=>previewVoice(current),previewMs);onState('listening');
}

export async function finishVoiceTurn(onState=()=>{}) {
  if(!recording||processing) return null;
  const current=recording;recording=null;processing=true;clearTimeout(current.timer);clearInterval(current.previewTimer);onState('transcribing');
  const sampleRate=current.context.sampleRate;
  current.processor.onaudioprocess=null;current.processor.disconnect();current.source.disconnect();current.mute.disconnect();
  current.stream.getTracks().forEach(track=>track.stop());await current.context.close();
  try {
    if(current.previewPromise) await current.previewPromise;
    const samples=prepareSpeech(flatten(current.chunks),sampleRate);
    const payload={audio_data:await dataUrl(wav(samples)),language:current.language,hint_language:current.hintLanguage,field:current.field};
    failedTurn={api:current.api,payload};
    const result=await current.api('/api/voice/transcribe',payload);failedTurn=null;return result;
  } finally {processing=false;}
}

export async function retrySavedVoiceTurn(onState=()=>{}) {
  if(!failedTurn||processing) return null;
  processing=true;onState('transcribing');
  try {
    const result=await failedTurn.api('/api/voice/transcribe',failedTurn.payload);
    failedTurn=null;return result;
  } finally {processing=false;}
}

export function discardSavedVoiceTurn() {failedTurn=null;}

export function languageStatusText(result) {
  if(result?.needs_language_choice) return 'Не удалось определить язык · выберите вручную или повторите фразу';
  if(result?.language==='kk') return 'Язык определён автоматически: Қазақша';
  if(result?.language==='ru') return 'Язык определён автоматически: Русский';
  if(result?.language==='mixed') return `Смешанная речь · отвечаем ${result.response_language==='kk'?'на казахском':'на русском'}`;
  return 'Определяем язык…';
}

export function applyLanguageResult(select,result) {
  if(!select||select.value!=='auto') return '';
  if(result?.needs_language_choice) {
    delete select.dataset.detectedLanguage;delete select.dataset.responseLanguage;
  } else if(result?.language) {
    select.dataset.detectedLanguage=result.language;
    if(result.response_language) select.dataset.responseLanguage=result.response_language;
  }
  return languageStatusText(result);
}

export function clearLanguageResult(select) {
  if(!select) return;
  delete select.dataset.detectedLanguage;delete select.dataset.responseLanguage;
}

export function selectedVoiceLanguage(select) {
  const language=select?.value||'auto';
  return {language,hintLanguage:language==='auto'?select.dataset.responseLanguage||null:null};
}

export function selectedIntakeLanguage(select) {
  if(!select||select.value!=='auto') return select?.value||'unknown';
  return select.dataset.detectedLanguage||'auto';
}

export function previewTranscript(input,text) {
  if(!input||!text) return false;
  if(!('voiceOriginal' in input.dataset)) input.dataset.voiceOriginal=input.value;
  const original=input.dataset.voiceOriginal.trim();input.value=original?`${original} ${text}`:text;
  input.dataset.voicePreview='true';input.dispatchEvent(new Event('input',{bubbles:true}));return true;
}

export function settleTranscriptPreview(input,keep=false) {
  if(!input?.dataset.voicePreview) return false;
  if(!keep) input.value=input.dataset.voiceOriginal;
  delete input.dataset.voiceOriginal;delete input.dataset.voicePreview;
  input.dispatchEvent(new Event('input',{bubbles:true}));return true;
}

export function mergeTranscript(input,text) {
  if(!input) return false;
  const before=input.value.trim();
  input.value=!before?text:before===text||before.endsWith(text)?before:`${before} ${text}`;
  input.dispatchEvent(new Event('input',{bubbles:true}));
  input.dispatchEvent(new Event('change',{bubbles:true}));
  return true;
}

export async function applyDetectedCity(city) {
  if(!city) return false;
  const region=document.querySelector('#citizen-region'),select=document.querySelector('#citizen-city');
  if(!region||!select) return false;
  const form=select.closest('form'),map=form?.querySelector('[data-map-mode="picker"][data-mounted]');
  let timer,listener;
  const ready=map?new Promise(resolve=>{
    const finish=()=>{clearTimeout(timer);form.removeEventListener('pulse-city-ready',listener);resolve();};
    listener=event=>{if(event.detail?.code===city.code) finish();};
    form.addEventListener('pulse-city-ready',listener);timer=setTimeout(finish,2000);
  }):Promise.resolve();
  if(region.value!==city.region_id) {region.value=city.region_id;region.dispatchEvent(new Event('change',{bubbles:true}));}
  select.value=city.code;
  if(select.value!==city.code) return false;
  select.dispatchEvent(new Event('change',{bubbles:true}));
  await ready;return true;
}

function setControls(mode) {
  document.querySelectorAll('[data-action="voice-record"]').forEach(button=>{
    button.hidden=mode==='listening';button.disabled=mode!=='idle';
  });
  const stop=document.querySelector('[data-action="voice-stop"]');
  if(stop) {stop.hidden=mode!=='listening';stop.disabled=false;}
}

function status(text,state) {
  const node=document.querySelector('[data-voice-status]');if(node) node.textContent=text;
  const agent=node?.closest('.voice-agent');if(agent&&state) {agent.dataset.voiceState=state;setThinkingOrbState(agent.querySelector('[data-thinking-orb]'),state);}
}

function target(field) {
  return document.querySelector(field==='problem'?'#citizen-text':'#citizen-address');
}

function restoreDrafts() {
  for(const field of ['problem','address']) {
    try {
      const text=sessionStorage.getItem(`pulse109-voice-${field}`);
      if(text&&mergeTranscript(target(field),text)) sessionStorage.removeItem(`pulse109-voice-${field}`);
    } catch { /* The form still works without session storage. */ }
  }
}

async function start(field,language,api,toast) {
  restoreDrafts();setControls('busy');status('Готовлю голосовой ввод…','processing');
  try {
    const select=document.querySelector('#citizen-language'),hintLanguage=select?.dataset.responseLanguage||null;
    await beginVoiceTurn({field,language,hintLanguage,api,onState:mode=>{
      status(mode==='prompting'?'Помощник задаёт вопрос…':language==='auto'?'Определяем язык…':'Слушаю… Нажмите «Готово», когда закончите.',mode==='listening'?'listening':'processing');
      if(mode==='listening') setControls('listening');
    },onSpeech:()=>status('Слышу вас… Остановлю запись после паузы.','listening'),onPartial:(text,result)=>{
      previewTranscript(target(field),text);status(applyLanguageResult(select,result)||'Черновик обновляется во время разговора…',result?.language?'detected':'listening');
    },onSilence:()=>finish(api,toast),onTimeout:()=>finish(api,toast)});
  } catch(error) {setControls('idle');status('Не удалось начать запись. Можно ввести текст вручную.','error');throw error;}
}

async function finish(api,toast) {
  setControls('busy');status('Распознаю речь локальной моделью…','processing');
  try {
    const result=await finishVoiceTurn();if(!result) return;
    const input=target(result.field);
    settleTranscriptPreview(input);
    if(!mergeTranscript(input,result.text)) {
      try {sessionStorage.setItem(`pulse109-voice-${result.field}`,result.text);} catch { /* no-op */ }
      throw new Error('Расшифровка сохранена. Вернитесь к форме, чтобы вставить её.');
    }
    try {sessionStorage.removeItem(`pulse109-voice-${result.field}`);} catch { /* no-op */ }
    const select=document.querySelector('#citizen-language');status(applyLanguageResult(select,result),result.needs_language_choice?'error':'detected');
    if(result.needs_language_choice) {
      await speakPrompt(result.assistant_message,'mixed',result.assistant_prompt,api);return;
    }
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches) await new Promise(resolve=>setTimeout(resolve,160));
    if(result.field==='address') {await applyDetectedCity(result.detected_city);document.querySelector('[data-address-search]')?.click();}
    status(result.assistant_message,'result');
    await speakPrompt(result.assistant_message,result.language,result.assistant_prompt,api);
    document.querySelector(`[data-action="voice-record"][data-field="${result.next_field}"]`)?.focus();
  } catch(error) {
    for(const field of ['problem','address']) settleTranscriptPreview(target(field),true);
    status('Не удалось распознать запись. Можно повторить или ввести текст.','error');
    toast(error.message||'Не удалось распознать запись',true);
  } finally {setControls('idle');}
}

export async function handleVoiceAction(node,api,toast) {
  const language=document.querySelector('#citizen-language')?.value||'auto';
  if(node.dataset.action==='voice-stop') {await finish(api,toast);return;}
  await start(node.dataset.field,language,api,toast);
}
