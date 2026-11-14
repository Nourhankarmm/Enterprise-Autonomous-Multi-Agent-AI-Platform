'use strict';
let token = null;
const el = id => document.getElementById(id);
const labels = {HIGH:'عالية', MEDIUM:'متوسطة', LOW:'منخفضة'};
el('as-of').value = new Date().toLocaleDateString('en-CA');
function clearWorkspace() { el('rows').replaceChildren(); el('metrics').replaceChildren(); el('identity').textContent=''; }
function loggedOut() { currentOperation=null;el('operation-detail').hidden=true;el('operation-history').replaceChildren();token=null; clearWorkspace(); el('workspace').hidden=true; el('login-panel').hidden=false; }
async function api(path, options={}) {
  const response=await fetch(path, {...options, headers:{'Content-Type':'application/json', ...(token?{Authorization:`Bearer ${token}`}:{})}});
  if (!response.ok) {
    if(response.status===401 && token) loggedOut();
    const errorBody=await response.json().catch(()=>({}));
    if(response.status!==401 && typeof errorBody.detail==='string') throw new Error(errorBody.detail);
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
  try {const result=await api('/v1/auth/login',{method:'POST',body:JSON.stringify({username:el('username').value,password:el('password').value})});token=result.access_token;el('password').value='';el('login-panel').hidden=true;el('workspace').hidden=false;await load();await loadAgentWorkspace();}
  catch(error){el('message').textContent=error.message;}
  finally{el('login-button').disabled=false;}
});
el('refresh').addEventListener('click',load);
el('logout').addEventListener('click',async()=>{try{await api('/v1/auth/logout',{method:'POST'});loggedOut();el('message').textContent='تم تسجيل الخروج.';}catch(error){el('message').textContent=error.message;}});

let currentOperation=null;
const capabilityLabels={'purchase_orders.read':'قراءة طلبات الشراء','contracts.read':'قراءة شروط العقود التجريبية','procurement.assess':'حساب التأخير والأولوية','findings.verify':'التحقق المستقل من النتائج والأدلة','report.compose':'تجميع التقرير'};
const stateLabels={PLANNED:'تنتظر مراجعة الخطة',WAITING_CLARIFICATION:'تحتاج توضيحًا',RUNNING:'قيد التنفيذ',WAITING_DOCUMENT:'مستند ناقص',COMPLETED:'مكتملة',FAILED:'فشلت',CANCELLED:'ملغاة',CANCEL_REQUESTED:'الإلغاء مطلوب',PENDING:'لم تبدأ',BLOCKED:'محجوبة',SUCCEEDED:'نجحت'};
const eventLabels={'plan.created':'تم تجهيز الخطة','plan.revised':'تم تعديل الخطة','plan.confirmed':'تم تأكيد الخطة','operation.resumed':'تم استئناف التنفيذ','operation.finished':'انتهى التنفيذ','operation.cancel_requested':'طُلب الإلغاء','operation.cancelled':'أُلغيت العملية','task.started':'بدأت المهمة','task.succeeded':'نجحت المهمة','task.failed':'فشلت المهمة','task.retry_scheduled':'تمت جدولة إعادة محاولة'};
async function loadAgentWorkspace(){
  try{
    const info=await api('/v1/agent-capabilities');
    el('planner-mode').textContent=info.planner_provider==='demo'?'مخطط تجريبي محلي':'مخطط OpenAI';
    el('planner-help').textContent=info.planner_provider==='demo'?'وضع تجربة بخطة ثابتة للهدف المعروض، بدون استدعاء نموذج AI. لفهم أهداف أخرى فعّل مخطط OpenAI على الخادم.':'سيُرسل نص الهدف فقط إلى نموذج OpenAI لتجهيز مقترح. الأدوات والصلاحيات والحسابات تُفرض داخل الخادم.';
    await loadOperations();
  }catch(error){el('message').textContent=error.message;}
}
async function loadOperations(){
  const requestToken=token;const {items}=await api('/v1/operations');if(token!==requestToken)return;el('operation-history').replaceChildren();
  if(!items.length){el('operation-history').textContent='لا توجد عمليات محفوظة بعد.';return;}
  for(const item of items){const button=document.createElement('button');button.className='secondary';button.textContent=`${item.goal} · ${stateLabels[item.state]??item.state}`;button.addEventListener('click',()=>operationAction(()=>api(`/v1/operations/${item.id}`),'جارٍ تحميل العملية…'));el('operation-history').append(button);}
}
function renderOperation(op){
  currentOperation=op;el('operation-detail').hidden=false;el('operation-title').textContent=`خطة الإصدار ${op.plan_version}`;
  el('operation-state').textContent=stateLabels[op.state]??op.state;el('operation-summary').textContent=op.plan.proposal.summary;
  el('operation-question').textContent=op.plan.proposal.clarification??'';el('plan-tasks').replaceChildren();
  const names=new Map(op.plan.proposal.tasks.map(t=>[t.task_id,capabilityLabels[t.capability]]));
  for(const task of op.plan.proposal.tasks){const li=document.createElement('li');const saved=op.tasks.find(t=>t.task_id===task.task_id);li.textContent=`${capabilityLabels[task.capability]} · ${stateLabels[saved?.state]??'لم تبدأ'} · المحاولات: ${saved?.attempts??0}/${task.max_attempts}`;const detail=document.createElement('small');detail.textContent=task.depends_on.length?'بعد: '+task.depends_on.map(id=>names.get(id)).join('، '):'بداية التنفيذ';li.append(detail);if(task.filters){const filter=document.createElement('small');filter.textContent=`النطاق: ${task.filters.outstanding_only?'طلبات مفتوحة':'كل الطلبات'} · المورد: ${task.filters.supplier_id??'الكل'} · المشروع: ${task.filters.project_id??'الكل'} · الطلبات: ${task.filters.order_ids.join('، ')||'الكل'}`;li.append(filter);}el('plan-tasks').append(li);}
  el('confirm-operation').disabled=op.state!=='PLANNED';el('revise-operation').disabled=!['PLANNED','WAITING_CLARIFICATION'].includes(op.state)||op.replans>=2;
  el('cancel-operation').disabled=!['PLANNED','WAITING_CLARIFICATION','WAITING_DOCUMENT','RUNNING','CANCEL_REQUESTED'].includes(op.state);
  el('resume-operation').hidden=op.state!=='RUNNING';el('resume-operation').disabled=false;el('refresh-operation').disabled=false;
  el('operation-result').hidden=!op.result;el('operation-findings').replaceChildren();
  if(op.result){el('result-note').textContent=op.result.unresolved_order_ids.length?'تحليل جزئي موثّق؛ عقود ناقصة للطلبات: '+op.result.unresolved_order_ids.join('، '):'تم التحقق من النتائج. لا توجد إجراءات إرسال أو كتابة خارجية.';for(const finding of op.result.findings){const row=document.createElement('tr');for(const value of [finding.order_id,finding.late_days,labels[finding.risk]??'غير معلومة',finding.evidence.map(e=>e.source_id).join('، ')])cell(row,value);el('operation-findings').append(row);}}
  if(op.error_code)el('operation-question').textContent='توقف التنفيذ: '+op.error_code;
}
async function loadOperationEvents(id){
  const requestToken=token;const {items}=await api(`/v1/operations/${id}/events?limit=200`);if(token!==requestToken)return;el('operation-events').replaceChildren();
  for(const event of items){const li=document.createElement('li');li.textContent=`${eventLabels[event.event_type]??event.event_type}${event.task_id?' · '+event.task_id:''}${event.details.attempt?' · محاولة '+event.details.attempt:''}`;el('operation-events').append(li);}
}
async function operationAction(action,message){
  const requestToken=token;
  document.querySelectorAll('.operation-action').forEach(button=>button.disabled=true);el('message').textContent=message;
  try{const op=await action();if(token!==requestToken)return;renderOperation(op);await loadOperationEvents(op.id);await loadOperations();el('message').textContent='';}
  catch(error){el('message').textContent=error.message;}
  finally{el('create-operation').disabled=false;if(currentOperation)renderOperation(currentOperation);}
}
el('goal-form').addEventListener('submit',event=>{event.preventDefault();operationAction(()=>api('/v1/operations',{method:'POST',body:JSON.stringify({goal:el('goal').value,as_of:el('as-of').value})}),'جارٍ تجهيز الخطة للمراجعة…');});
el('confirm-operation').addEventListener('click',()=>{const op=currentOperation;operationAction(()=>api(`/v1/operations/${op.id}/confirm`,{method:'POST',body:JSON.stringify({version:op.plan_version,plan_hash:op.plan_hash})}),'جارٍ تنفيذ الخطة المؤكدة…');});
el('revise-operation').addEventListener('click',()=>{const op=currentOperation;operationAction(()=>api(`/v1/operations/${op.id}/revise`,{method:'POST',body:JSON.stringify({goal:el('goal').value,as_of:el('as-of').value,version:op.plan_version,plan_hash:op.plan_hash})}),'جارٍ تعديل الخطة؛ يلزم تأكيد النسخة الجديدة…');});
for(const action of ['cancel','resume'])el(`${action}-operation`).addEventListener('click',()=>{const id=currentOperation.id;operationAction(()=>api(`/v1/operations/${id}/${action}`,{method:'POST'}),'جارٍ تحديث العملية…');});
el('refresh-operation').addEventListener('click',()=>{const id=currentOperation.id;operationAction(()=>api(`/v1/operations/${id}`),'جارٍ تحديث الحالة…');});
el('reload-operations').addEventListener('click',()=>loadOperations().catch(error=>el('message').textContent=error.message));
