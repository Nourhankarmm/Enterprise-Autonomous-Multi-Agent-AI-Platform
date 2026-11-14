'use strict';
let token = null;
const el = id => document.getElementById(id);
const labels = {HIGH:'عالية', MEDIUM:'متوسطة', LOW:'منخفضة'};
el('as-of').value = new Date().toLocaleDateString('en-CA');
function clearWorkspace() { el('rows').replaceChildren(); el('metrics').replaceChildren(); el('identity').textContent=''; }
function loggedOut() { token=null; clearWorkspace(); el('workspace').hidden=true; el('login-panel').hidden=false; }
async function api(path, options={}) {
  const response=await fetch(path, {...options, headers:{'Content-Type':'application/json', ...(token?{Authorization:`Bearer ${token}`}:{})}});
  if (!response.ok) {
    if(response.status===401 && token) loggedOut();
    throw new Error(response.status===401?'بيانات الدخول غير صحيحة أو انتهت الجلسة.':response.status===403?'حسابك لا يملك صلاحية عرض المشتريات.':'تعذر تحميل البيانات. حاول مرة أخرى.');
  }
  return response.status===204?null:response.json();
}
function cell(row,text) {const td=document.createElement('td'); td.textContent=text;row.append(td);return td;}
async function load() {
  el('message').textContent='جارٍ تحميل التحليل…'; el('refresh').disabled=true; clearWorkspace();
  try {
    const me=await api('/v1/me');el('identity').textContent=`الشركة: ${me.tenant_id} · الدور: ${me.role}`;
    if(!el('as-of').value) throw new Error('اختر تاريخ التحليل.');
    const {items}=await api(`/v1/procurement/assessment?as_of=${encodeURIComponent(el('as-of').value)}`);
    for(const item of items) {
      const row=document.createElement('tr');
      for(const value of [item.order_id,item.supplier_id,item.project_id,item.remaining_quantity,item.late_days,item.beyond_grace_days??'غير معلوم']) cell(row,value);
      const badge=document.createElement('span');badge.className=`badge ${item.risk??''}`;badge.textContent=labels[item.risk]??'أدلة ناقصة';cell(row,'').append(badge);el('rows').append(row);
    }
    for(const [label,value] of [['طلبات الشراء',items.length],['أولوية عالية',items.filter(i=>i.risk==='HIGH').length],['أدلة ناقصة',items.filter(i=>i.risk===null).length]]) {
      const box=document.createElement('div');box.className='metric';const strong=document.createElement('strong');strong.textContent=value;const span=document.createElement('span');span.textContent=label;box.append(strong,span);el('metrics').append(box);
    }
    el('empty').hidden=items.length>0;el('message').textContent='';
  } catch(error) {el('message').textContent=error.message;}
  finally {el('refresh').disabled=false;}
}
el('login-form').addEventListener('submit',async event=>{
  event.preventDefault();el('login-button').disabled=true;el('message').textContent='جارٍ تسجيل الدخول…';
  try {const result=await api('/v1/auth/login',{method:'POST',body:JSON.stringify({username:el('username').value,password:el('password').value})});token=result.access_token;el('password').value='';el('login-panel').hidden=true;el('workspace').hidden=false;await load();}
  catch(error){el('message').textContent=error.message;}
  finally{el('login-button').disabled=false;}
});
el('refresh').addEventListener('click',load);
el('logout').addEventListener('click',async()=>{try{await api('/v1/auth/logout',{method:'POST'});loggedOut();el('message').textContent='تم تسجيل الخروج.';}catch(error){el('message').textContent=error.message;}});
