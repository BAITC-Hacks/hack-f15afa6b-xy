let recording=null;

const prompts={
  ru:{problem:'Расскажите, что произошло. Говорите до тридцати секунд.',address:'Назовите адрес или ближайший ориентир.'},
  kk:{problem:'Не болғанын айтып беріңіз. Отыз секундқа дейін сөйлеңіз.',address:'Мекенжайды немесе жақын жердегі нысанды айтыңыз.'},
  mixed:{problem:'Расскажите о проблеме. Мәселе туралы айтып беріңіз.',address:'Назовите адрес. Мекенжайды айтыңыз.'},
};

function speak(text,language) {
  if(!('speechSynthesis' in window)) return Promise.resolve();
  speechSynthesis.cancel();
  return new Promise(resolve=>{
    const utterance=new SpeechSynthesisUtterance(text);
    utterance.lang=language==='kk'?'kk-KZ':'ru-RU';
    utterance.rate=.95;utterance.onend=resolve;utterance.onerror=resolve;
    speechSynthesis.speak(utterance);
  });
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

function setListening(active) {
  document.querySelectorAll('[data-action="voice-record"]').forEach(button=>button.hidden=active);
  const stop=document.querySelector('[data-action="voice-stop"]');if(stop) stop.hidden=!active;
  const status=document.querySelector('[data-voice-status]');if(status) status.textContent=active?'Слушаю… Нажмите «Готово», когда закончите.':'Готов к записи';
}

async function start(field,language,api,toast) {
  if(recording) return;
  if(!navigator.mediaDevices?.getUserMedia) throw new Error('Браузер не поддерживает запись с микрофона');
  await speak(prompts[language][field],language);
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
  const context=new AudioContext(), source=context.createMediaStreamSource(stream);
  const processor=context.createScriptProcessor(4096,1,1), mute=context.createGain(), chunks=[];
  mute.gain.value=0;source.connect(processor);processor.connect(mute);mute.connect(context.destination);
  processor.onaudioprocess=event=>chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  recording={field,language,stream,context,source,processor,mute,chunks,api,toast};
  recording.timer=setTimeout(()=>finish(),30000);setListening(true);
}

async function finish() {
  if(!recording) return;
  const current=recording;recording=null;clearTimeout(current.timer);setListening(false);
  const sampleRate=current.context.sampleRate;
  current.processor.disconnect();current.source.disconnect();current.mute.disconnect();
  current.stream.getTracks().forEach(track=>track.stop());await current.context.close();
  const status=document.querySelector('[data-voice-status]');if(status) status.textContent='Распознаю речь локальной моделью…';
  try {
    const samples=resample(flatten(current.chunks),sampleRate);
    const result=await current.api('/api/voice/transcribe',{audio_data:await dataUrl(wav(samples)),language:current.language,field:current.field});
    const input=document.querySelector(current.field==='problem'?'#citizen-text':'#citizen-address');
    input.value=result.text;input.dispatchEvent(new Event('input',{bubbles:true}));
    if(current.field==='address') document.querySelector('[data-address-search]')?.click();
    if(status) status.textContent=result.assistant_message;
    await speak(result.assistant_message,current.language);
    document.querySelector(`[data-action="voice-record"][data-field="${result.next_field}"]`)?.focus();
  } catch(error) {
    if(status) status.textContent='Не удалось распознать запись. Можно повторить или ввести текст.';
    current.toast(error.message||'Не удалось распознать запись',true);
  }
}

export async function handleVoiceAction(node,api,toast) {
  if(node.dataset.action==='voice-stop') {await finish();return;}
  const language=document.querySelector('#citizen-language')?.value||'mixed';
  await start(node.dataset.field,language,api,toast);
}
