const labels={idle:'Голосовой помощник готов',listening:'Голосовой помощник слушает',processing:'Голосовой помощник обрабатывает ответ',detected:'Язык определён',result:'Ответ готов',error:'Ошибка голосового помощника'};

function draw(canvas,state,time=0) {
  const size=canvas.clientWidth||58,dpr=Math.min(devicePixelRatio||1,2),ctx=canvas.getContext('2d');
  canvas.width=Math.round(size*dpr);canvas.height=Math.round(size*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,size,size);
  const count=size>80?72:32,speed={idle:.00035,listening:.0015,processing:.0011,detected:.0006,result:.00045,error:.00075}[state]||.00035;
  const radius=size*.31*(1+Math.sin(time*.002)*.025),turn=time*speed;
  for(let i=0;i<count;i++) {
    const y=1-(i/(count-1))*2,r=Math.sqrt(1-y*y),angle=i*2.399963+turn;
    let x=Math.cos(angle)*r,z=Math.sin(angle)*r,py=y;
    if(state==='listening') py+=Math.sin(time*.006+i*.72)*.08*(1-Math.abs(y));
    if(state==='processing') {x*=.82;py*=1.08;}
    const depth=(z+1)/2,alpha=.28+depth*.7,dot=(size>80?1.5:1)+depth*(size>80?1.8:1.1);
    ctx.beginPath();ctx.arc(size/2+x*radius,size/2+py*radius,dot,0,Math.PI*2);ctx.fillStyle=`rgba(239,248,255,${alpha})`;ctx.fill();
  }
}

export function mountThinkingOrb(canvas) {
  if(canvas._thinkingOrb) return canvas._thinkingOrb;
  let state=canvas.dataset.orbState||'idle',frame=0;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const paint=time=>{
    draw(canvas,state,time);
    if(!reduced.matches) frame=requestAnimationFrame(paint);
  };
  const controller={setState(next) {
    state=next;cancelAnimationFrame(frame);canvas.dataset.orbState=next;canvas.setAttribute('aria-label',labels[next]||labels.idle);paint(performance.now());
  }};
  reduced.addEventListener('change',()=>controller.setState(state));
  canvas._thinkingOrb=controller;controller.setState(state);return controller;
}

export function mountThinkingOrbs(root=document) {
  root.querySelectorAll('[data-thinking-orb]').forEach(mountThinkingOrb);
}

export function setThinkingOrbState(canvas,state) {
  if(canvas) mountThinkingOrb(canvas).setState(state);
}
