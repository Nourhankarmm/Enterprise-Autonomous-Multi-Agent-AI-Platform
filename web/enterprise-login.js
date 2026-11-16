'use strict';
async function enterWorkspace(identity){
  el('login-panel').hidden=true;el('workspace').hidden=false;
  if(identity.role==='auditor'){currentIdentity=identity;el('identity').textContent=identity.tenant_id+' · auditor';await initDashboard(identity);return;}
  await loadWorkflowWorkspace(identity);
  if(identity.role!=='approver'){await load();await loadAgentWorkspace();await loadDocumentWorkspace();}
  else{el('identity').textContent=identity.tenant_id+' · approver';el('message').textContent='';}
  if(token)await initDashboard(identity);
}
async function loadAudit(){
  const requestToken=token;el('audit-records').textContent='';
  try{const data=await api('/v1/audit-events');if(token===requestToken)el('audit-records').textContent=JSON.stringify(data.items,null,2);}
  catch(error){if(token===requestToken){el('audit-records').textContent='';el('message').textContent=error.message;}}
}
el('refresh-audit').addEventListener('click',loadAudit);
el('oidc-login').addEventListener('click',()=>window.location.assign('/v1/auth/oidc/start'));
document.addEventListener('DOMContentLoaded',async()=>{
  try{
    const modeResponse=await fetch('/v1/auth/mode');if(!modeResponse.ok)throw new Error('تعذر تحديد طريقة الدخول. أعد تحميل الصفحة.');
    const settings=await modeResponse.json();
    if(settings.mode==='local'){
      el('login-form').hidden=false;el('oidc-login').hidden=true;el('auth-mode-note').textContent='دخول محلي للتجربة. لا يُمثّل دخولًا مؤسسيًا.';return;
    }
    if(settings.mode!=='oidc')throw new Error('طريقة الدخول غير معروفة.');
    el('login-form').hidden=true;el('oidc-login').hidden=false;
    el('auth-mode-note').textContent='الدخول عبر موفّر الحسابات المضبوط على الخادم. الصلاحيات وربط الشركة يحددهما مسؤول التطبيق؛ لا يتم إنشاء حساب تلقائيًا.';
    const response=await fetch('/v1/auth/session');
    if(response.status===401)return;
    if(!response.ok)throw new Error('تعذر استعادة جلسة الدخول.');
    const session=await response.json();cookieSession=true;csrfConfirmation=session.csrf_token;token='cookie-session:'+session.csrf_token;
    await enterWorkspace(session.identity);
  }catch(error){loggedOut();el('auth-mode-note').textContent=error.message;}
});
