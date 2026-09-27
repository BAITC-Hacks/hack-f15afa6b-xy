let recording=null, playback=null, processing=false;

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
  for(let i=0;i<output.length;i++) {
    const position=i*scale, left=Math.floor(position), right=Math.min(left+1,input.length-1);
    output[i]=input[left]+(input[right]-input[left])*(position-left);
  }
  return output;
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

export async function beginVoiceTurn({field,language,api,onState=()=>{},onTimeout=()=>{}}) {
  if(recording||processing) throw new Error('Дождитесь завершения текущего ответа');
  if(!navigator.mediaDevices?.getUserMedia) throw new Error('Браузер не поддерживает запись с микрофона');
  onState('prompting');
  await speakPrompt(voicePrompts[language][field],language,`${language}_${field}`,api);
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
  const context=new AudioContext(), source=context.createMediaStreamSource(stream);
  const processor=context.createScriptProcessor(4096,1,1), mute=context.createGain(), chunks=[];
  mute.gain.value=0;source.connect(processor);processor.connect(mute);mute.connect(context.destination);
  processor.onaudioprocess=event=>chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  recording={field,language,stream,context,source,processor,mute,chunks,api};
  recording.timer=setTimeout(onTimeout,30000);onState('listening');
}

export async function finishVoiceTurn(onState=()=>{}) {
  if(!recording||processing) return null;
  const current=recording;recording=null;processing=true;clearTimeout(current.timer);onState('transcribing');
  const sampleRate=current.context.sampleRate;
  current.processor.disconnect();current.source.disconnect();current.mute.disconnect();
  current.stream.getTracks().forEach(track=>track.stop());await current.context.close();
  try {
    const samples=resample(flatten(current.chunks),sampleRate);
    return await current.api('/api/voice/transcribe',{audio_data:await dataUrl(wav(samples)),language:current.language,field:current.field});
  } finally {processing=false;}
}

export function mergeTranscript(input,text) {
  if(!input) return false;
  const before=input.value.trim();
  input.value=!before?text:before===text||before.endsWith(text)?before:`${before} ${text}`;
  input.dispatchEvent(new Event('input',{bubbles:true}));
  input.dispatchEvent(new Event('change',{bubbles:true}));
  return true;
}

function setControls(mode) {
  document.querySelectorAll('[data-action="voice-record"]').forEach(button=>{
    button.hidden=mode==='listening';button.disabled=mode!=='idle';
  });
  const stop=document.querySelector('[data-action="voice-stop"]');
  if(stop) {stop.hidden=mode!=='listening';stop.disabled=false;}
}

function status(text) {
  const node=document.querySelector('[data-voice-status]');if(node) node.textContent=text;
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
  restoreDrafts();setControls('busy');
  try {
    await beginVoiceTurn({field,language,api,onState:mode=>{
      status(mode==='prompting'?'Агент задаёт вопрос…':'Слушаю… Нажмите «Готово», когда закончите.');
      if(mode==='listening') setControls('listening');
    },onTimeout:()=>finish(api,toast)});
  } catch(error) {setControls('idle');status('Готов к записи');throw error;}
}

async function finish(api,toast) {
  setControls('busy');status('Распознаю речь локальной моделью…');
  try {
    const result=await finishVoiceTurn();if(!result) return;
    const input=target(result.field);
    if(!mergeTranscript(input,result.text)) {
      try {sessionStorage.setItem(`pulse109-voice-${result.field}`,result.text);} catch { /* no-op */ }
      throw new Error('Расшифровка сохранена. Вернитесь к форме, чтобы вставить её.');
    }
    try {sessionStorage.removeItem(`pulse109-voice-${result.field}`);} catch { /* no-op */ }
    if(result.field==='address') document.querySelector('[data-address-search]')?.click();
    status(result.assistant_message);
    await speakPrompt(result.assistant_message,result.language,result.assistant_prompt,api);
    document.querySelector(`[data-action="voice-record"][data-field="${result.next_field}"]`)?.focus();
  } catch(error) {
    status('Не удалось распознать запись. Можно повторить или ввести текст.');
    toast(error.message||'Не удалось распознать запись',true);
  } finally {setControls('idle');}
}

export async function handleVoiceAction(node,api,toast) {
  const language=document.querySelector('#citizen-language')?.value||'mixed';
  if(node.dataset.action==='voice-stop') {await finish(api,toast);return;}
  await start(node.dataset.field,language,api,toast);
}
