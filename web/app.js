'use strict';
let token = null;
const el = id => document.getElementById(id);
const labels = {HIGH:'عالية', MEDIUM:'متوسطة', LOW:'منخفضة'};
const errorMessages={
  'Current permission required':'تغيّرت صلاحية الحساب. سجّل الدخول بحساب مخوّل.',
  'Evidence access changed; this result is unavailable':'تغيّرت صلاحية الدليل؛ هذه النتيجة لم تعد متاحة.',
  'Temporal owns this operation; use its workflow controls':'افتح المتابعة المرتبطة للتحكم في هذه العملية.',
  'Choose an independent active approver from your company':'اختر موافقًا مستقلًا ومفعّلًا من شركتك قبل تجميد المسودة.',
  'Complete supported findings are required':'استكمل الأدلة وحدّث التحليل قبل تجهيز رسالة المتابعة.',
  'Proposal is stale or no longer waiting':'تغيّرت المسودة أو حالة المتابعة. حدّث التفاصيل وراجع الإصدار الجديد.',
  'Workflow is terminal':'انتهت هذه المتابعة؛ ابدأ عملية جديدة لإجراء متابعة أخرى.',
  'Source data changed; refresh evidence and request new approval':'تغيّرت بيانات المصدر. حدّث الأدلة وجهّز مسودة لموافقة جديدة.',
  'Start the Temporal development stack first':'التشغيل المستمر غير متاح الآن؛ شغّل خدمة Temporal والعامل.',
  'Wait for the requested evidence refresh before drafting':'انتظر اكتمال تحديث الأدلة قبل تجهيز مسودة جديدة.'
};
el('as-of').value = new Date().toLocaleDateString('en-CA');
function clearWorkspace() { el('rows').replaceChildren(); el('metrics').replaceChildren(); el('identity').textContent=''; }
function loggedOut() { clearDashboard(); clearWorkflowWorkspace();currentIdentity=null; clearDocumentWorkspace();currentOperation=null;el('operation-detail').hidden=true;el('operation-history').replaceChildren();token=null; clearWorkspace(); el('workspace').hidden=true; el('login-panel').hidden=false; }
async function api(path, options={}) {
  const requestToken=token;
  const response=await fetch(path, {...options, headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(options.headers??{}), ...(requestToken?{Authorization:`Bearer ${requestToken}`}:{})}});
  if(requestToken!==token&&path!=='/v1/auth/login')throw new Error('تغيرت الجلسة؛ تجاهلنا الرد السابق.');
  if (!response.ok) {
    if(response.status===401 && token) loggedOut();
    if(response.status===403 || (response.status===404 && path.startsWith('/v1/documents/'))){clearDashboard();clearWorkflowWorkspace();clearDocumentWorkspace();currentOperation=null;el('operation-detail').hidden=true;}
    const errorBody=await response.json().catch(()=>({}));
    if(response.status!==401 && typeof errorBody.detail==='string') throw new Error(errorMessages[errorBody.detail]??errorBody.detail);
    throw new Error(response.status===401?'بيانات الدخول غير صحيحة أو انتهت الجلسة.':response.status===403?'حسابك لا يملك صلاحية عرض المشتريات.':'تعذر تحميل البيانات. حاول مرة أخرى.');
  }
  const data=response.status===204?null:await response.json();
  if(requestToken!==token&&path!=='/v1/auth/login')throw new Error('تغيرت الجلسة؛ تجاهلنا الرد السابق.');
  return data;
}
function cell(row,text) {const td=document.createElement('td'); td.textContent=text;row.append(td);return td;}
async function load() {
  const requestToken=token;
  el('message').textContent='جارٍ تحميل التحليل…'; el('refresh').disabled=true; clearWorkspace();
  try {
    const me=await api('/v1/me');if(requestToken!==token)return;el('identity').textContent=`الشركة: ${me.tenant_id} · الدور: ${me.role}`;
    if(!el('as-of').value) throw new Error('اختر تاريخ التحليل.');
    const {items}=await api(`/v1/procurement/assessment?as_of=${encodeURIComponent(el('as-of').value)}`);
    if(requestToken!==token)return;
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
  try {const result=await api('/v1/auth/login',{method:'POST',body:JSON.stringify({username:el('username').value,password:el('password').value})});token=result.access_token;el('password').value='';el('login-panel').hidden=true;el('workspace').hidden=false;await loadWorkflowWorkspace(result.identity);if(result.identity.role!=='approver'){await load();await loadAgentWorkspace();await loadDocumentWorkspace();}else{el('identity').textContent=result.identity.tenant_id+' · approver';el('message').textContent='';}if(token)await initDashboard(result.identity);}
  catch(error){el('message').textContent=error.message;}
  finally{el('login-button').disabled=false;}
});
el('refresh').addEventListener('click',load);
el('logout').addEventListener('click',async()=>{try{await api('/v1/auth/logout',{method:'POST'});loggedOut();el('message').textContent='تم تسجيل الخروج.';}catch(error){el('message').textContent=error.message;}});

let currentOperation=null;
const capabilityLabels={'purchase_orders.read':'قراءة طلبات الشراء','contracts.read':'قراءة شروط العقود المسموحة','procurement.assess':'حساب التأخير والأولوية','findings.verify':'التحقق المستقل من النتائج والأدلة','report.compose':'تجميع التقرير'};
const stateLabels={PLANNED:'تنتظر مراجعة الخطة',WAITING_CLARIFICATION:'تحتاج توضيحًا',RUNNING:'قيد التنفيذ',WAITING_DOCUMENT:'مستند ناقص',COMPLETED:'مكتملة',FAILED:'فشلت',CANCELLED:'ملغاة',CANCEL_REQUESTED:'الإلغاء مطلوب',PENDING:'لم تبدأ',BLOCKED:'محجوبة',SUCCEEDED:'نجحت'};
const eventLabels={'plan.created':'تم تجهيز الخطة','plan.revised':'تم تعديل الخطة','plan.confirmed':'تم تأكيد الخطة','operation.resumed':'تم استئناف التنفيذ','operation.finished':'انتهى التنفيذ','operation.cancel_requested':'طُلب الإلغاء','operation.cancelled':'أُلغيت العملية','task.started':'بدأت المهمة','task.succeeded':'نجحت المهمة','task.failed':'فشلت المهمة','task.retry_scheduled':'تمت جدولة إعادة محاولة'};
async function loadAgentWorkspace(){
  try{
    const info=await api('/v1/agent-capabilities');
    el('planner-mode').textContent=info.planner_provider==='demo'?'مخطط تجريبي محلي':info.planner_provider==='gemini'?'مخطط Gemini':'مخطط OpenAI';
    el('planner-help').textContent=info.planner_provider==='demo'?'وضع تجربة بخطة ثابتة للهدف المعروض، بدون استدعاء نموذج AI. لفهم أهداف أخرى فعّل مخطط AI متصل على الخادم.':'سيُرسل نص الهدف فقط إلى مزوّد AI المختار لتجهيز مقترح. الأدوات والصلاحيات والحسابات تُفرض داخل الخادم.';
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
  el('operation-state').textContent=stateLabels[op.state]??op.state;el('operation-summary').textContent=op.plan.proposal.summary;el('plan-metadata').textContent='تاريخ التحليل: '+op.plan.as_of+' · المخطط: '+op.planner_provider+' / '+(op.planner_model??'محلي')+' · بصمة الخطة: '+op.plan_hash;el('open-linked-workflow').hidden=!op.workflow_id;
  el('operation-question').textContent=op.plan.proposal.clarification??'';el('plan-tasks').replaceChildren();
  const names=new Map(op.plan.proposal.tasks.map(t=>[t.task_id,capabilityLabels[t.capability]]));
  for(const task of op.plan.proposal.tasks){const li=document.createElement('li');const saved=op.tasks.find(t=>t.task_id===task.task_id);li.textContent=`${capabilityLabels[task.capability]} · ${stateLabels[saved?.state]??'لم تبدأ'} · المحاولات: ${saved?.attempts??0}/${task.max_attempts}`;const detail=document.createElement('small');detail.textContent=task.depends_on.length?'بعد: '+task.depends_on.map(id=>names.get(id)).join('، '):'بداية التنفيذ';li.append(detail);if(task.filters){const filter=document.createElement('small');filter.textContent=`النطاق: ${task.filters.outstanding_only?'طلبات مفتوحة':'كل الطلبات'} · المورد: ${task.filters.supplier_id??'الكل'} · المشروع: ${task.filters.project_id??'الكل'} · الطلبات: ${task.filters.order_ids.join('، ')||'الكل'}`;li.append(filter);}el('plan-tasks').append(li);}
  el('start-durable').disabled=Boolean(op.workflow_id)||!['PLANNED','COMPLETED','WAITING_DOCUMENT','WAITING_CLARIFICATION'].includes(op.state);el('confirm-operation').disabled=Boolean(op.workflow_id)||op.state!=='PLANNED';el('revise-operation').disabled=Boolean(op.workflow_id)||!['PLANNED','WAITING_CLARIFICATION'].includes(op.state)||op.replans>=2;
  el('cancel-operation').disabled=Boolean(op.workflow_id)||!['PLANNED','WAITING_CLARIFICATION','WAITING_DOCUMENT','RUNNING','CANCEL_REQUESTED'].includes(op.state);
  el('resume-operation').hidden=Boolean(op.workflow_id)||op.state!=='RUNNING';el('resume-operation').disabled=false;el('refresh-operation').disabled=false;
  el('operation-result').hidden=!op.result;el('operation-findings').replaceChildren();
  if(op.result){el('result-note').textContent=op.result.unresolved_order_ids.length?(op.result.conflicting_order_ids?.length?'مصادر متعارضة؛ الأولوية التعاقدية غير معلومة للطلبات: ':'تحليل جزئي موثّق؛ دليل غير مكتمل للطلبات: ')+op.result.unresolved_order_ids.join('، '):'تم التحقق من النتائج. لا توجد إجراءات إرسال أو كتابة خارجية.';for(const finding of op.result.findings){const row=document.createElement('tr');for(const value of [finding.order_id,finding.late_days,labels[finding.risk]??'غير معلومة'])cell(row,value);const refs=cell(row,'');const seenPages=new Set();for(const citation of finding.evidence){if(citation.document_id){const pageKey=`${citation.document_id}:${citation.version}:${citation.page}`;if(seenPages.has(pageKey))continue;seenPages.add(pageKey);const button=document.createElement('button');button.className='secondary';button.textContent=`${citation.source_id} · ص ${citation.page} · إصدار ${citation.version}`;button.addEventListener('click',()=>showEvidence(citation).catch(error=>el('message').textContent=error.message));refs.append(button);}else{const span=document.createElement('span');span.textContent=citation.source_id+' ';refs.append(span);}}el('operation-findings').append(row);}}
  if(op.error_code)el('operation-question').textContent='توقف التنفيذ: '+op.error_code;
}
async function loadOperationEvents(id){
  const requestToken=token;const {items}=await api(`/v1/operations/${id}/events?limit=200`);if(token!==requestToken)return;el('operation-events').replaceChildren();
  for(const event of items){const li=document.createElement('li');li.textContent=`${eventLabels[event.event_type]??event.event_type}${event.task_id?' · '+event.task_id:''}${event.details.attempt?' · محاولة '+event.details.attempt:''}`;el('operation-events').append(li);}
}
async function operationAction(action,message){
  const requestToken=token;
  document.querySelectorAll('.operation-action').forEach(button=>button.disabled=true);el('message').textContent=message;
  try{const op=await action();if(token!==requestToken)return;renderOperation(op);showWorkspaceView('analysis');await loadOperationEvents(op.id);await loadTrace(op.id);await loadOperations();await refreshDashboard();el('message').textContent='';}
  catch(error){el('message').textContent=error.message;}
  finally{el('create-operation').disabled=false;if(currentOperation)renderOperation(currentOperation);}
}
el('goal-form').addEventListener('submit',event=>{event.preventDefault();el('as-of').value=el('analysis-date').value;operationAction(()=>api('/v1/operations',{method:'POST',body:JSON.stringify({goal:el('goal').value,as_of:el('as-of').value})}),'جارٍ تجهيز الخطة للمراجعة…');});
el('confirm-operation').addEventListener('click',()=>{const op=currentOperation;if(durableEnabled){workflowAction(()=>api('/v1/operations/'+op.id+'/durable',{method:'POST',body:JSON.stringify({version:op.plan_version,plan_hash:op.plan_hash})}));return;}operationAction(()=>api(`/v1/operations/${op.id}/confirm`,{method:'POST',body:JSON.stringify({version:op.plan_version,plan_hash:op.plan_hash})}),'جارٍ تنفيذ الخطة المؤكدة…');});
el('revise-operation').addEventListener('click',()=>{el('as-of').value=el('analysis-date').value;const op=currentOperation;operationAction(()=>api(`/v1/operations/${op.id}/revise`,{method:'POST',body:JSON.stringify({goal:el('goal').value,as_of:el('as-of').value,version:op.plan_version,plan_hash:op.plan_hash})}),'جارٍ تعديل الخطة؛ يلزم تأكيد النسخة الجديدة…');});
for(const action of ['cancel','resume'])el(`${action}-operation`).addEventListener('click',()=>{const id=currentOperation.id;operationAction(()=>api(`/v1/operations/${id}/${action}`,{method:'POST'}),'جارٍ تحديث العملية…');});
el('refresh-operation').addEventListener('click',()=>{const id=currentOperation.id;operationAction(()=>api(`/v1/operations/${id}`),'جارٍ تحديث الحالة…');});
el('reload-operations').addEventListener('click',()=>loadOperations().catch(error=>el('message').textContent=error.message));

let currentDocument=null;
const documentStateLabels={EXTRACTED:'بنود مستخرجة تنتظر المراجعة',READY:'مصدر مُراجع',CONFLICT:'تعارض بين البنود',NEEDS_REVIEW:'يلزم توضيح البنود',NEEDS_OCR:'يلزم OCR؛ الصورة لا تكفي للتحليل'};
const documentReasons={OCR_LOW_CONFIDENCE:'ثقة القراءة منخفضة أو توجد صفحة بلا نص. لا يمكن اعتماد هذا الإصدار؛ ارفع صورة أوضح أو نسخة نصية مصححة.',NO_EXTRACTABLE_TEXT:'صفحة واحدة أو أكثر لا تحتوي نصًا قابلًا للاستخراج. يمكن إنشاء إصدار OCR محلي إذا كان المحرك مضبوطًا على الخادم.',SUPPLIER_LINK_CONFLICT:'رقم المورد داخل الملف لا يطابق المورد المحدد.',CONTRADICTORY_CLAUSES:'المستند يحتوي قيمًا متعارضة. ارفع إصدارًا موضحًا قبل اعتماده.',INVALID_DATE:'تاريخ التسليم داخل المستند غير صالح.',GRACE_NOT_EXPLICIT:'لم نجد بند سماح صريحًا بالصيغة المدعومة. لا نفترض أن السماح صفر.',BUSINESS_CALENDAR_REQUIRED:'البند يستخدم أيام عمل؛ يلزم تقويم شركة غير متاح حاليًا.',GRACE_OUTSIDE_DEMO_POLICY:'قيمة السماح خارج حدود التجربة وتحتاج مراجعة.',EXPLICIT_CLAUSES_PENDING_REVIEW:'راجع الاقتباسات وربط المورد ونطاق الطلبات قبل اعتماد هذا المصدر.'};
function clearDocumentWorkspace(){for(const id of ['document-facts','document-readers'])el(id).replaceChildren();for(const id of ['document-name','document-explanation','document-scope','evidence-title'])el(id).textContent='';currentDocument=null;el('document-detail').hidden=true;el('evidence-viewer').hidden=true;el('evidence-text').textContent='';el('document-list').replaceChildren();el('document-search-results').replaceChildren();}
async function loadDocumentWorkspace(){try{await loadDocuments();}catch(error){el('message').textContent=error.message;}}
async function loadDocuments(){const requestToken=token;const {items}=await api('/v1/documents');if(token!==requestToken)return;el('document-list').replaceChildren();if(!items.length){el('document-list').textContent='لا توجد مستندات مسموحة بعد.';return;}for(const doc of items){const button=document.createElement('button');button.className='secondary';button.textContent=`${doc.title} · ${doc.supplier_id} · إصدار ${doc.version} · ${documentStateLabels[doc.state]??doc.state}`;button.addEventListener('click',()=>documentAction(()=>api(`/v1/documents/${doc.id}`),'جارٍ تحميل المستند…'));el('document-list').append(button);}}
function evidenceCard(citation,label){const card=document.createElement('div');card.className='evidence-card';const heading=document.createElement('strong');heading.textContent=label;const quote=document.createElement('blockquote');quote.dir='auto';quote.textContent=citation.quote;const button=document.createElement('button');button.className='secondary';button.textContent=`عرض الصفحة ${citation.page}`;button.addEventListener('click',()=>showEvidence(citation).catch(error=>el('message').textContent=error.message));card.append(heading,quote,button);return card;}
async function showEvidence(citation){const requestToken=token;const page=await api(`/v1/documents/${encodeURIComponent(citation.document_id)}/pages/${Number(citation.page)}`);if(token!==requestToken)return;el('evidence-title').textContent=`${page.title} · صفحة ${page.page} · إصدار ${page.version} · بصمة ${page.checksum.slice(0,12)}`;el('evidence-text').textContent=page.text;el('evidence-viewer').hidden=false;showWorkspaceView('documents');el('evidence-viewer').scrollIntoView({behavior:'smooth',block:'start'});}
function renderDocument(doc){currentDocument=doc;el('document-detail').hidden=false;el('document-name').textContent=doc.title;el('document-state').textContent=(documentStateLabels[doc.state]??doc.state)+(doc.active_source?' · المصدر النشط':'');el('document-explanation').textContent=documentReasons[doc.extraction.reason]??'';el('document-scope').textContent=`المورد: ${doc.supplier_id} · الطلبات: ${doc.order_ids.join('، ')||'كل طلبات المورد'} · الإصدار: ${doc.version} · البصمة: ${doc.checksum.slice(0,16)}`;el('document-facts').replaceChildren();for(const fact of doc.extraction.facts){const field={grace_days:'فترة السماح',due_date:'تاريخ التسليم',supplier_id:'رقم المورد'}[fact.field]??fact.field;const label=`${field}: ${fact.value??'غير معلوم'}${fact.day_basis?' · '+(fact.day_basis==='calendar'?'أيام تقويم':'أيام عمل'):''}`;el('document-facts').append(evidenceCard({...fact,document_id:doc.id},label));}el('review-document').disabled=currentIdentity?.role==='approver'||!doc.can_manage||doc.state!=='EXTRACTED';el('document-sharing').hidden=currentIdentity?.role==='approver'||!doc.can_manage;el('save-document-acl').disabled=false;el('download-document').disabled=false;el('refresh-document').disabled=false;renderOCRControls(doc);}
async function loadDocumentReaders(doc){if(!doc.can_manage)return;const requestToken=token;const {items}=await api('/v1/document-readers');if(token!==requestToken||currentDocument?.id!==doc.id)return;el('document-readers').replaceChildren();if(!items.length)el('document-readers').textContent='لا يوجد مستخدمون آخرون مؤهلون للمشاركة.';for(const user of items){const label=document.createElement('label');const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=user.id;checkbox.checked=(doc.readers??[]).includes(user.id);label.append(checkbox,document.createTextNode(' '+user.username));el('document-readers').append(label);}}
async function documentAction(action,message){const requestToken=token;document.querySelectorAll('.document-action').forEach(button=>button.disabled=true);el('message').textContent=message;try{const doc=await action();if(token!==requestToken)return;renderDocument(doc);showWorkspaceView('documents');await loadDocumentReaders(doc);await loadDocuments();el('message').textContent='';}catch(error){el('message').textContent=error.message;}finally{el('upload-document').disabled=false;el('upload-sample').disabled=false;if(currentDocument&&token===requestToken)renderDocument(currentDocument);}}
el('document-form').addEventListener('submit',event=>{event.preventDefault();const file=el('doc-file').files[0];if(!file)return;if(file.size>2*1024*1024){el('message').textContent='الملف أكبر من 2 MiB.';return;}const metadata={title:el('doc-title').value,supplier_id:el('doc-supplier').value.trim(),contract_ref:el('doc-reference').value.trim(),version:Number(el('doc-version').value),order_ids:el('doc-orders').value.split(/[,،]/).map(s=>s.trim()).filter(Boolean)};const form=new FormData();form.append('metadata',JSON.stringify(metadata));form.append('file',file);documentAction(()=>api('/v1/documents',{method:'POST',body:form}),'جارٍ استخراج الصفحات والبنود…');});
el('review-document').addEventListener('click',()=>{const doc=currentDocument;documentAction(()=>api(`/v1/documents/${doc.id}/review`,{method:'POST',body:JSON.stringify({revision:doc.revision,checksum:doc.checksum,extraction_hash:doc.extraction.extraction_hash,ocr_compared_to_original:el('ocr-comparison').checked})}),'جارٍ اعتماد المصدر الذي راجعته…');});
el('save-document-acl').addEventListener('click',()=>{const doc=currentDocument;const readers=[...el('document-readers').querySelectorAll('input:checked')].map(i=>i.value);documentAction(()=>api(`/v1/documents/${doc.id}/acl`,{method:'PUT',body:JSON.stringify({revision:doc.revision,readers})}),'جارٍ تحديث صلاحيات القراءة…');});
el('refresh-document').addEventListener('click',()=>{const id=currentDocument.id;documentAction(()=>api(`/v1/documents/${id}`),'جارٍ تحديث المستند…');});
el('reload-documents').addEventListener('click',()=>loadDocuments().catch(error=>el('message').textContent=error.message));
el('document-search-form').addEventListener('submit',async event=>{event.preventDefault();const requestToken=token;el('message').textContent='جارٍ البحث في الأدلة المسموحة…';try{const result=await api('/v1/documents/search?q='+encodeURIComponent(el('document-query').value));if(token!==requestToken)return;el('document-search-results').replaceChildren();for(const hit of result.items)el('document-search-results').append(evidenceCard(hit,`${hit.title} · إصدار ${hit.version}`));if(!result.items.length)el('document-search-results').textContent='لا توجد نتائج مسموحة مطابقة.';el('message').textContent=result.corpus_limited?'النتائج من نطاق بحث محدود؛ ضيّق المستندات أو الاستعلام.':'';}catch(error){el('message').textContent=error.message;}});
el('download-document').addEventListener('click',async()=>{const doc=currentDocument;const requestToken=token;if(!doc)return;try{const response=await fetch(`/v1/documents/${doc.id}/file`,{headers:{Authorization:`Bearer ${token}`}});if(!response.ok){clearDocumentWorkspace();throw new Error('تعذر تحميل المستند؛ تحقق من الصلاحية.');}const blob=await response.blob();if(token!==requestToken)return;const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=doc.id+'.pdf';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){el('message').textContent=error.message;}});

el('upload-sample').addEventListener('click',()=>{const sample=el('document-sample').value;const requestToken=token;const metadata={title:el('doc-title').value||'عقد تجريبي',supplier_id:sample==='supplier-s3'?'S3':'S1',contract_ref:el('doc-reference').value.trim()||'SAMPLE-'+crypto.randomUUID(),version:Number(el('doc-version').value),order_ids:[]};el('doc-supplier').value=metadata.supplier_id;el('doc-reference').value=metadata.contract_ref;documentAction(async()=>{const response=await fetch('/v1/document-samples/'+encodeURIComponent(sample),{headers:{Authorization:`Bearer ${requestToken}`}});if(!response.ok)throw new Error('تعذر تحميل العقد التجريبي.');const blob=await response.blob();if(token!==requestToken)throw new Error('الجلسة تغيرت.');const form=new FormData();form.append('metadata',JSON.stringify(metadata));form.append('file',new File([blob],sample+'.pdf',{type:'application/pdf'}));return api('/v1/documents',{method:'POST',body:form});},'جارٍ رفع نسخة PDF تجريبية خاصة بك…');});

function renderOCRControls(doc){
  const owner=doc.can_manage&&['manager','analyst'].includes(currentIdentity?.role);
  el('ocr-controls').hidden=!(owner&&doc.state==='NEEDS_OCR');
  el('ocr-version').value=doc.version+1;el('run-ocr').disabled=!owner||doc.state!=='NEEDS_OCR';
  el('ocr-comparison').checked=false;
  el('ocr-comparison-label').hidden=!(owner&&doc.extraction.ocr&&doc.state==='EXTRACTED');
  if(doc.extraction.ocr){
    el('document-explanation').textContent+=' النص مقروء آليًا من صورة، وليس نص PDF أصليًا؛ قارن البنود بالملف الأصلي قبل الاعتماد.';
    if(doc.state==='EXTRACTED')el('review-document').disabled=true;
  }
}
el('ocr-comparison').addEventListener('change',()=>{
  const doc=currentDocument;
  el('review-document').disabled=!doc||!doc.can_manage||doc.state!=='EXTRACTED'||!['manager','analyst'].includes(currentIdentity?.role)||(!!doc.extraction.ocr&&!el('ocr-comparison').checked);
});
el('run-ocr').addEventListener('click',()=>{
  const doc=currentDocument;const version=Number(el('ocr-version').value);
  if(!doc||!Number.isInteger(version)||version<=doc.version||version>10000){el('message').textContent='اختر رقم إصدار جديدًا أكبر من الإصدار الحالي.';return;}
  documentAction(()=>api(`/v1/documents/${doc.id}/ocr`,{method:'POST',body:JSON.stringify({revision:doc.revision,version})}),'جارٍ قراءة الصور محليًا وإنشاء إصدار جديد؛ قد يستغرق نحو دقيقة…');
});
