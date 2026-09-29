const authScreen=document.querySelector('#auth-screen');
const workspaceScreen=document.querySelector('#workspace-screen');
const message=document.querySelector('#auth-message');
const loginForm=document.querySelector('#login-form');
const signupForm=document.querySelector('#signup-form');
const loginTab=document.querySelector('#login-tab');
const signupTab=document.querySelector('#signup-tab');
let startWorkspace=()=>{};
let config={};
const motionPreference=matchMedia('(prefers-reduced-motion: reduce)');
function revealAuth(nodes,stagger=0) {
  if(!globalThis.gsap||motionPreference.matches) return;
  gsap.fromTo(nodes,{opacity:0,y:10},{opacity:1,y:0,duration:.38,stagger,ease:'power2.out',overwrite:true,clearProps:'opacity,transform'});
}
motionPreference.addEventListener('change',()=>{
  if(motionPreference.matches&&globalThis.gsap) gsap.getTweensOf(authScreen.querySelectorAll('[data-auth-reveal],.auth-form')).forEach(tween=>tween.progress(1));
});
const cityMotionButton=document.querySelector('.city-motion-toggle');
let cityMotion=null, cityPaused=false;
function syncCityMotion() {
  if(!globalThis.gsap) return;
  const reduced=motionPreference.matches;
  cityMotionButton.hidden=reduced;
  if(reduced) {
    cityMotion?.revert();cityMotion=null;
    return;
  }
  if(!cityMotion) {
    cityMotion=gsap.timeline({repeat:-1,paused:true})
      .to('.city-signal',{attr:{'stroke-dashoffset':-100},duration:6,ease:'none'},0)
      .fromTo('.city-ring',{attr:{r:25},opacity:.35},{attr:{r:39},opacity:0,duration:2.4,ease:'power1.out'},2)
      .to('.city-windows',{opacity:.9,duration:1.6,repeat:1,yoyo:true,ease:'sine.inOut'},1.2);
  }
  cityMotion.paused(cityPaused||document.hidden||authScreen.hidden);
}
cityMotionButton.addEventListener('click',()=>{
  cityPaused=!cityPaused;
  const label=cityPaused?'Продолжить анимацию':'Приостановить анимацию';
  cityMotionButton.setAttribute('aria-pressed',String(cityPaused));
  cityMotionButton.setAttribute('aria-label',label);cityMotionButton.title=label;
  cityMotionButton.textContent=cityPaused?'▷':'Ⅱ';syncCityMotion();
});
motionPreference.addEventListener('change',syncCityMotion);
document.addEventListener('visibilitychange',syncCityMotion);
const operatorEntry=location.pathname==='/operator';

function csrfToken() {
  const cookie=document.cookie.split('; ').find(item=>item.startsWith('pulse109_csrf='));
  return cookie?decodeURIComponent(cookie.slice(cookie.indexOf('=')+1)):'';
}

function errorText(data,status) {
  if(status===429) return 'Слишком много попыток. Подождите и попробуйте снова.';
  if(status===401) return 'Неверная почта или пароль.';
  if(status===409) return 'Аккаунт с этой почтой уже существует.';
  if(status===403) return 'Регистрация закрыта или код приглашения неверен.';
  if(status===422) return 'Проверьте введённые данные.';
  if(typeof data?.detail==='string') return data.detail;
  return 'Не удалось выполнить действие. Попробуйте ещё раз.';
}

async function authRequest(path,payload) {
  let response;
  try {
    response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  } catch {
    throw new Error('Сервер недоступен. Проверьте подключение и попробуйте снова.');
  }
  const data=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(errorText(data,response.status));
  return data;
}

function showMessage(text,success=false) {
  message.textContent=text;
  message.className=success?'auth-message success':'auth-message';
  message.hidden=!text;
}

function selectMode(mode,focus=true) {
  const signup=mode==='signup'&&!signupTab.hidden;
  loginTab.setAttribute('aria-selected',String(!signup));
  signupTab.setAttribute('aria-selected',String(signup));
  loginTab.tabIndex=signup?-1:0;
  signupTab.tabIndex=signup?0:-1;
  loginForm.hidden=signup;
  signupForm.hidden=!signup;
  document.querySelector('#auth-title').textContent=signup?'Регистрация':'Вход в систему';
  document.querySelector('#auth-intro').textContent=signup?(operatorEntry?'Введите данные и код приглашения оператора.':'После регистрации вы сможете подать обращение и следить за его статусом.'):'Введите почту и пароль.';
  showMessage('');
  if(focus) {revealAuth(signup?signupForm:loginForm);document.querySelector(signup?'#signup-name':'#login-email').focus({preventScroll:true});}
}

function showAuth(mode='login',notice='',success=false) {
  document.querySelectorAll('dialog[open]').forEach(node=>node.close());
  workspaceScreen.hidden=true;
  authScreen.hidden=false;
  syncCityMotion();
  revealAuth(authScreen.querySelectorAll('[data-auth-reveal]'),.055);
  document.querySelector('#skip-link').href='#auth-title';
  selectMode(mode,false);
  if(notice) showMessage(notice,success);
  requestAnimationFrame(()=>document.querySelector(mode==='signup'&&!signupForm.hidden?'#signup-name':'#login-email').focus({preventScroll:true}));
}

function initials(name) {
  return (name||'Оператор').split(/\s+/).filter(Boolean).slice(0,2).map(part=>part[0]).join('').toUpperCase();
}

function showWorkspace(user) {
  authScreen.hidden=true;
  syncCityMotion();
  workspaceScreen.hidden=false;
  document.querySelector('#skip-link').href='#main';
  const name=user.name||user.full_name||'Оператор 109';
  document.querySelector('#profile-name').textContent=name;
  document.querySelector('#profile-email').innerHTML='<i></i> ';
  document.querySelector('#profile-email').append(document.createTextNode(user.email||'Активная смена'));
  document.querySelector('#profile-avatar').textContent=initials(name);
}

function setBusy(form,busy) {
  const submit=form.querySelector('[type="submit"]');
  submit.disabled=busy;
  submit.setAttribute('aria-busy',String(busy));
  if(!submit.dataset.label) submit.dataset.label=submit.textContent;
  submit.textContent=busy?'Подождите…':submit.dataset.label;
}

function applyConfig() {
  signupTab.hidden=operatorEntry?!config.operator_signup_available:!config.signup_available;
  document.querySelector('#invite-field').hidden=!operatorEntry;
  document.querySelector('#signup-invite').required=!!operatorEntry;
}

async function loadConfig() {
  const response=await fetch('/api/auth/config',{credentials:'same-origin'});
  if(response.ok) config=await response.json();
  applyConfig();
}

async function enterWorkspace(user) {
  user=user.user||user;
  const citizen=user.role==='citizen';
  document.body.dataset.citizen=String(citizen);
  document.querySelector('#workspace-context').textContent=citizen?'Личный кабинет':'Кабинет оператора';
  if(location.pathname!=='/') history.replaceState(null,'','/'+location.hash);
  document.title=citizen?'Pulse 109 — Личный кабинет':'Pulse 109 — Кабинет оператора';
  document.querySelectorAll('[data-nav]').forEach(node=>node.hidden=citizen?!['home','requests','citizen','map','help'].includes(node.dataset.nav):['home','requests','citizen','help'].includes(node.dataset.nav));
  document.querySelector('.nav-more').hidden=citizen;
  document.querySelector('.nav-label').textContent=citizen?'ЛИЧНЫЙ КАБИНЕТ':'КАБИНЕТ ОПЕРАТОРА';
  document.querySelector('.disclaimer').hidden=citizen;
  document.querySelector('.demo-badge').hidden=citizen;
  document.querySelectorAll('#workspace-screen .brand').forEach(node=>node.href=citizen?'#home':'#queue');
  showWorkspace(user);
  await startWorkspace(user);
}

export async function authFetch(path,options={}) {
  const headers=new Headers(options.headers||{});
  const method=(options.method||'GET').toUpperCase();
  if(!['GET','HEAD','OPTIONS'].includes(method)) {
    const token=csrfToken();
    if(token) headers.set('X-CSRF-Token',token);
  }
  const response=await fetch(path,{...options,headers,credentials:'same-origin'});
  if(response.status===401&&authScreen.hidden) showAuth('login','Сессия завершена. Войдите снова.');
  return response;
}

export async function bootstrapAuth(start) {
  startWorkspace=start;
  try {
    await loadConfig();
    if(config.user) {
      await enterWorkspace(config.user);
      return;
    }
    showAuth('login');
  } catch {
    showAuth('login','Сервер недоступен. Проверьте подключение и обновите страницу.');
  }
}

loginTab.addEventListener('click',()=>selectMode('login'));
signupTab.addEventListener('click',()=>selectMode('signup'));
document.querySelector('.auth-tabs').addEventListener('keydown',event=>{
  if(!['ArrowLeft','ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  selectMode(loginTab.getAttribute('aria-selected')==='true'?'signup':'login');
});
document.querySelectorAll('.password-toggle').forEach(button=>button.addEventListener('click',()=>{
  const input=document.querySelector(`#${button.getAttribute('aria-controls')}`);
  const show=input.type==='password';
  input.type=show?'text':'password';
  button.textContent=show?'Скрыть':'Показать';
  button.setAttribute('aria-label',show?'Скрыть пароль':'Показать пароль');
  input.focus();
}));

loginForm.addEventListener('submit',async event=>{
  event.preventDefault();
  if(!loginForm.reportValidity()) return;
  setBusy(loginForm,true);showMessage('');
  try {
    const data=new FormData(loginForm);
    const user=await authRequest('/api/auth/login',{email:data.get('email').trim(),password:data.get('password')});
    loginForm.reset();
    await enterWorkspace(user);
  } catch(error) {showMessage(error.message);} finally {setBusy(loginForm,false);}
});

signupForm.addEventListener('submit',async event=>{
  event.preventDefault();
  if(!signupForm.reportValidity()) return;
  setBusy(signupForm,true);showMessage('');
  try {
    const data=new FormData(signupForm);
    const payload={name:data.get('name').trim(),email:data.get('email').trim(),password:data.get('password'),role:operatorEntry?'operator':'citizen'};
    if(data.get('invite_code')) payload.invite_code=data.get('invite_code').trim();
    const user=await authRequest('/api/auth/signup',payload);
    signupForm.reset();
    await loadConfig();
    await enterWorkspace(user);
  } catch(error) {showMessage(error.message);} finally {setBusy(signupForm,false);}
});

document.querySelectorAll('#logout-button,[data-logout]').forEach(node=>node.addEventListener('click',async event=>{
  const button=event.currentTarget;
  button.disabled=true;
  try {
    const response=await authFetch('/api/auth/logout',{method:'POST'});
    if(!response.ok) throw new Error();
    await loadConfig();
    location.replace('/');
  } catch {
    const toast=document.querySelector('#toast');
    toast.textContent='Не удалось выйти из аккаунта. Попробуйте ещё раз.';
    toast.className='error';toast.hidden=false;
    setTimeout(()=>toast.hidden=true,5000);
  } finally {button.disabled=false;}
}));
