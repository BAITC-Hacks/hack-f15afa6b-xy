import {esc} from './views.js?v=20260928-5';

let remaining=180, timer;

function status(name, ready, detail) {
  return `<div class="demo-health ${ready?'ready':'offline'}"><i></i><span><strong>${esc(name)}</strong><small>${esc(detail)}</small></span></div>`;
}

export function juryDemoView(state) {
  const laya=state.health?.laya, qwen=state.health?.copilot, similarity=state.health?.similarity;
  const voice=state.voiceHealth, voiceReady=voice?.status==='healthy'&&voice?.tts?.status==='healthy';
  return `<div class="demo-hero"><div><p class="eyebrow">РЕЖИМ ПРЕЗЕНТАЦИИ</p><h1>Pulse 109 за три минуты</h1><p>От голоса жителя до решения оператора, массового инцидента и прогноза по 20 регионам.</p></div><div class="demo-timer" aria-live="polite"><small>ОСТАЛОСЬ</small><strong data-demo-clock>03:00</strong><div><button class="button primary" type="button" data-jury-timer="start">Старт</button><button class="button ghost" type="button" data-jury-timer="reset">Сбросить</button></div></div></div>
    <section class="demo-readiness" aria-label="Готовность сервисов">
      ${status('Laya', ['ok','healthy'].includes(laya?.status), state.health?.checkpoint_id?'trained · shadow':'резервный режим')}
      ${status('E5 retrieval', ['ok','healthy'].includes(similarity?.status), similarity?.checkpoint_id?'trained checkpoint':'лексический резерв')}
      ${status('Qwen Copilot', !!qwen?.configured, qwen?.configured?'self-hosted':'резервный ответ')}
      ${status('Voice RU / KK', voiceReady, voiceReady?'STT + OmniVoice':'резервный голос')}
    </section>
    <div class="demo-runbook">
      <article><time>0:00–0:35</time><span>01</span><div><h2>Гражданин говорит</h2><p>Покажите RU/KK распознавание, заполнение адреса, карту и вложение.</p><code>«На Абая 44 с утра нет воды…»</code></div><a class="button primary" href="/voice" target="_blank" rel="noopener">Открыть Voice ↗</a></article>
      <article><time>0:35–1:25</time><span>02</span><div><h2>AI помогает оператору</h2><p>Создайте безопасную синтетическую заявку. В карточке покажите Laya, E5, Qwen и подтверждение человеком.</p></div><button class="button primary" data-action="demo">Создать и открыть</button></article>
      <article><time>1:25–1:55</time><span>03</span><div><h2>Радар видит массовую проблему</h2><p>Шесть сигналов объединяются в инцидент, оригиналы заявок сохраняются.</p></div><button class="button primary" data-action="radar-demo">Запустить всплеск</button></article>
      <article><time>1:55–2:10</time><span>04</span><div><h2>Город видит статус на карте</h2><p>Публичны только проверенные обезличенные записи с согласием.</p></div><a class="button" href="#map">Открыть карту</a></article>
      <article><time>2:10–3:00</time><span>05</span><div><h2>Руководитель видит картину</h2><p>20 регионов, сигналы, прогноз на 1–3 месяца, вопрос к данным, PDF/XLSX и доказательства обучения.</p></div><a class="button" href="#dashboard">Открыть аналитику</a></article>
    </div>
    <p class="demo-boundary"><strong>Честная граница:</strong> национальный набор для показа синтетический; артефакты Laya и E5 действительно обучены на NVIDIA, их текущий статус показан выше, решение всегда подтверждает оператор.</p>`;
}

export function mountJuryDemo(root) {
  const clock=root.querySelector('[data-demo-clock]');
  const draw=()=>{const minutes=Math.floor(remaining/60),seconds=remaining%60;clock.textContent=`${String(minutes).padStart(2,'0')}:${String(seconds).padStart(2,'0')}`;clock.classList.toggle('done',remaining===0);};
  clearInterval(timer);timer=null;draw();
  root.querySelectorAll('[data-jury-timer]').forEach(button=>button.addEventListener('click',()=>{
    if(button.dataset.juryTimer==='reset') {clearInterval(timer);timer=null;remaining=180;button.closest('.demo-timer').querySelector('[data-jury-timer="start"]').textContent='Старт';draw();return;}
    if(timer) {clearInterval(timer);timer=null;button.textContent='Продолжить';return;}
    button.textContent='Пауза';timer=setInterval(()=>{if(!root.isConnected||remaining===0){clearInterval(timer);timer=null;button.textContent='Старт';return;}remaining--;draw();},1000);
  }));
}
