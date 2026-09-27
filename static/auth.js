const authScreen=document.querySelector('#auth-screen');
const workspaceScreen=document.querySelector('#workspace-screen');
const message=document.querySelector('#auth-message');
const loginForm=document.querySelector('#login-form');
const signupForm=document.querySelector('#signup-form');
const loginTab=document.querySelector('#login-tab');
const signupTab=document.querySelector('#signup-tab');
let startWorkspace=()=>{};
let config={};

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
  document.querySelector('#auth-title').textContent=signup?'Создайте аккаунт':'Добро пожаловать';
  document.querySelector('#auth-intro').textContent=signup?(config.first_account?'Создайте первый аккаунт администратора.':'Введите данные и код приглашения.'):'Войдите, чтобы работать с обращениями.';
  showMessage('');
  if(focus) document.querySelector(signup?'#signup-name':'#login-email').focus();
}

function showAuth(mode='login',notice='',success=false) {
  document.querySelectorAll('dialog[open]').forEach(node=>node.close());
  workspaceScreen.hidden=true;
  authScreen.hidden=false;
  document.querySelector('#skip-link').href='#auth-title';
  selectMode(mode,false);
  if(notice) showMessage(notice,success);
  requestAnimationFrame(()=>document.querySelector(mode==='signup'&&!signupForm.hidden?'#signup-name':'#login-email').focus());
}

function initials(name) {
  return (name||'Оператор').split(/\s+/).filter(Boolean).slice(0,2).map(part=>part[0]).join('').toUpperCase();
}

function showWorkspace(user) {
  authScreen.hidden=true;
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
  signupTab.hidden=!config.signup_available;
  document.querySelector('#invite-field').hidden=!config.invite_required;
  document.querySelector('#signup-invite').required=!!config.invite_required;
}

async function loadConfig() {
  const response=await fetch('/api/auth/config',{credentials:'same-origin'});
  if(response.ok) config=await response.json();
  applyConfig();
}

async function enterWorkspace(user) {
  showWorkspace(user.user||user);
  await startWorkspace();
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
    showAuth(config.first_account?'signup':'login');
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
    const payload={name:data.get('name').trim(),email:data.get('email').trim(),password:data.get('password')};
    if(data.get('invite_code')) payload.invite_code=data.get('invite_code').trim();
    const user=await authRequest('/api/auth/signup',payload);
    signupForm.reset();
    await loadConfig();
    await enterWorkspace(user);
  } catch(error) {showMessage(error.message);} finally {setBusy(signupForm,false);}
});

document.querySelector('#logout-button').addEventListener('click',async event=>{
  const button=event.currentTarget;
  button.disabled=true;
  try {
    const response=await authFetch('/api/auth/logout',{method:'POST'});
    if(!response.ok) throw new Error();
    await loadConfig();
    showAuth('login','Вы вышли из аккаунта.',true);
  } catch {
    const toast=document.querySelector('#toast');
    toast.textContent='Не удалось завершить сессию. Попробуйте ещё раз.';
    toast.className='error';toast.hidden=false;
    setTimeout(()=>toast.hidden=true,5000);
  } finally {button.disabled=false;}
});
