'use strict';
let dashboardData=null,workspaceView='overview',durableEnabled=false,pollTimer=null,pollEpoch=0,pollBusy=false;
const panelIds={audit:'audit-panel',amendments:'amendments-panel',projects:'projects-panel',overview:'overview-panel',analysis:'analysis-panel',workflows:'durable-area',documents:'documents-panel',usage:'usage-panel',procurement:'procurement-panel'};
const displayState=value=>workflowLabels[value]??value;
const formatTime=value=>value?new Date(value).toLocaleString('ar-EG'):'غير مسجّل';
function clearDashboard(){el('audit-records').textContent='';clearPhaseEight();pollEpoch++;clearTimeout(pollTimer);dashboardData=null;for(const id of ['dashboard-rows','dashboard-metrics','usage-rows','usage-metrics','agent-trace','workflow-timeline','operation-history','operation-events','operation-findings','plan-tasks'])el(id).replaceChildren();el('usage-coverage').textContent='';el('last-sync').textContent='';el('approval-status').textContent='';el('goal').value='';for(const id of ['operation-question','operation-summary','plan-metadata'])el(id).textContent='';}
function showWorkspaceView(view,focus=false){
  if(!token||!panelIds[view])return;
  if(currentIdentity?.role==='auditor'&&view!=='audit')view='audit';
  if(currentIdentity?.role!=='auditor'&&view==='audit')view='overview';
  if(currentIdentity?.role==='approver'&&['analysis','usage','procurement','projects'].includes(view))view='overview';
  workspaceView=view;for(const [name,id] of Object.entries(panelIds))el(id).hidden=name!==view;
  for(const button of el('workspace-nav').querySelectorAll('button'))button.setAttribute('aria-pressed',String(button.dataset.view===view));
  if(focus)el(panelIds[view]).querySelector('h2')?.focus();
  if(view==='amendments')loadAmendments().catch(error=>el('message').textContent=error.message);
  if(view==='projects'&&!el('project-as-of').value)el('project-as-of').value=el('analysis-date').value;
  if(view==='usage')loadUsage().catch(error=>el('message').textContent=error.message);
}
function metric(target,label,value,note=''){const card=document.createElement('div');card.className='metric';const strong=document.createElement('strong');strong.textContent=value;const span=document.createElement('span');span.textContent=label;card.append(strong,span);if(note){const small=document.createElement('small');small.textContent=note;card.append(small);}target.append(card);}
async function initDashboard(identity){
  currentIdentity=identity;const approver=identity.role==='approver';
  for(const button of el('workspace-nav').querySelectorAll('button'))if(button.dataset.view==='audit')button.hidden=identity.role!=='auditor';
  if(identity.role==='auditor'){for(const button of el('workspace-nav').querySelectorAll('button'))button.hidden=button.dataset.view!=='audit';showWorkspaceView('audit');await loadAudit();return;}
  for(const button of el('workspace-nav').querySelectorAll('button'))button.hidden=button.dataset.view==='audit'||(approver&&['analysis','usage','procurement','projects'].includes(button.dataset.view));
  el('dashboard-new-goal').hidden=approver;el('document-form').hidden=approver;el('sample-controls').hidden=approver;document.querySelector('#documents-panel > p').textContent=approver?'تصفّح الأدلة التي شاركها مالك المستند معك، وافتح الصفحة قبل الموافقة على المسودة.':'ارفع PDF نصّيًا، راجع البنود والصفحات، ثم اعتمده مصدرًا للتحليل. المستند خاص بك افتراضيًا، والنص لا يُرسل إلى مزوّد AI.';
  if(approver)await loadDocumentWorkspace();
  if(!el('goal').value)el('goal').value='حلل تأخير طلبات الشراء المفتوحة';
  el('analysis-date').value=el('as-of').value;el('start-durable').hidden=true;
  showWorkspaceView('overview');await refreshDashboard();schedulePoll();
}
function dashboardRows(){
  if(!dashboardData)return;
  const kind=el('dashboard-kind').value,state=el('dashboard-state').value,query=el('dashboard-query').value.trim().toLocaleLowerCase();
  const records=[...dashboardData.analyses.map(r=>({...r,kind:'analysis'})),...dashboardData.workflows.map(r=>({...r,kind:'workflow'}))]
    .filter(r=>(kind==='all'||r.kind===kind)&&(state==='all'||r.state===state)&&((r.goal??'')+' '+r.id).toLocaleLowerCase().includes(query))
    .sort((a,b)=>b.updated_at.localeCompare(a.updated_at));
  el('dashboard-rows').replaceChildren();el('dashboard-empty').hidden=records.length>0;
  for(const item of records){const tr=document.createElement('tr');cell(tr,item.kind==='analysis'?'تحليل':'متابعة');cell(tr,item.goal??item.id.slice(0,8));const status=cell(tr,'');const badge=document.createElement('span');badge.className='badge';badge.textContent=displayState(item.state);status.append(badge);cell(tr,formatTime(item.updated_at));const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='فتح التفاصيل';button.addEventListener('click',()=>openDashboardRecord(item));cell(tr,'').append(button);el('dashboard-rows').append(tr);}
}
async function refreshDashboard(){
  const activeToken=token,epoch=pollEpoch;const data=await api('/v1/dashboard');if(activeToken!==token||epoch!==pollEpoch)return;
  dashboardData=data;el('dashboard-metrics').replaceChildren();
  const counts=data.workflow_states;metric(el('dashboard-metrics'),'تحليلات محفوظة',data.analyses.length,'تشمل لقطات إعادة التحليل');metric(el('dashboard-metrics'),'متابعات تنتظر قرارًا',counts.WAITING_APPROVAL??0);metric(el('dashboard-metrics'),'تحتاج أدلة أو توضيحًا',(counts.WAITING_DOCUMENT??0)+(counts.WAITING_CLARIFICATION??0));metric(el('dashboard-metrics'),'قيد التنفيذ أو التحقق',(counts.RUNNING??0)+(counts.RECONCILING??0)+(counts.CANCEL_REQUESTED??0)+(counts.START_PENDING??0)+(counts.RETRY_SEND??0));
  el('dashboard-scope').textContent='آخر '+data.window_limit+' تحليل ومتابعة مسموحة لك؛ الأرقام تخص هذه النافذة فقط، وليست إجماليات الشركة.'+(data.limited?' النافذة محدودة؛ بعض السجلات الأقدم خارجها.':'');
  const old=el('dashboard-state').value;el('dashboard-state').replaceChildren();const all=document.createElement('option');all.value='all';all.textContent='كل الحالات';el('dashboard-state').append(all);for(const state of [...new Set([...data.analyses,...data.workflows].map(r=>r.state))].sort()){const option=document.createElement('option');option.value=state;option.textContent=displayState(state);el('dashboard-state').append(option);}if([...el('dashboard-state').options].some(o=>o.value===old))el('dashboard-state').value=old;
  dashboardRows();el('last-sync').textContent='آخر مزامنة: '+formatTime(data.generated_at);
}
async function openDashboardRecord(item){if(item.kind==='analysis')await operationAction(()=>api('/v1/operations/'+item.id),'جارٍ تحميل التحليل…');else await workflowAction(()=>api('/v1/workflows/'+item.id));}
async function loadUsage(){
  if(currentIdentity?.role==='approver')return;const activeToken=token,epoch=pollEpoch;const data=await api('/v1/telemetry/planner');if(activeToken!==token||epoch!==pollEpoch)return;
  const s=data.summary;el('usage-metrics').replaceChildren();metric(el('usage-metrics'),'استدعاءات مسجّلة',s.calls);metric(el('usage-metrics'),'توكنز إدخال معلومة',s.input_tokens);metric(el('usage-metrics'),'توكنز إخراج معلومة',s.output_tokens);metric(el('usage-metrics'),'إجمالي التقدير USD',s.total_estimate_usd??'غير متاح');
  el('usage-scope').textContent='آخر '+data.window_days+' يومًا، حتى '+data.window_limit+' استدعاء مسموح منذ تفعيل القياس. لا تُحسب استدعاءات العمليات القديمة أو تكلفة البنية التحتية.'+(data.limited?' نافذة القياس محدودة.':'');
  el('usage-coverage').textContent=!s.calls?'لا توجد قياسات مسجّلة ضمن هذه النافذة؛ لا نفترض تكلفة إجمالية صفرًا.':s.unpriced_calls?`${s.unpriced_calls} استدعاء بدون تقدير موثوق. المجموع المعروف فقط: ${s.known_estimate_usd} USD؛ الاستهلاك غير المعلوم: ${s.unknown_usage_calls}.`:'كل الاستدعاءات المعروضة لها تقدير محفوظ. الوضع التجريبي المحلي تكلفته النموذجية صفر.';
  el('usage-rows').replaceChildren();el('usage-empty').hidden=data.items.length>0;
  for(const call of data.items){const tr=document.createElement('tr');for(const value of [formatTime(call.occurred_at),call.provider+' / '+(call.model??'محلي'),{SUCCESS:'نجح',FAILED:'فشل',REJECTED:'مقترح مرفوض'}[call.status]??call.status,call.usage_known?call.usage.input_tokens:'غير معلوم',call.usage_known?call.usage.output_tokens:'غير معلوم',call.duration_ms+' ms',call.estimated_cost_usd??'غير متاح'])cell(tr,value);const source=cell(tr,'');if(call.pricing?.source?.startsWith('https://')){const link=document.createElement('a');link.href=call.pricing.source;link.textContent=call.pricing.as_of;link.target='_blank';link.rel='noopener noreferrer';source.append(link);}else source.textContent=call.pricing?.source==='offline-demo'?'تجربة محلية':'لا يوجد سعر محفوظ';el('usage-rows').append(tr);}
}
async function loadTrace(operationId){
  const activeToken=token;const data=await api('/v1/operations/'+operationId+'/trace');if(activeToken!==token||currentOperation?.id!==operationId)return;
  const taskNames=new Map(data.tasks.map(t=>[t.task_id,capabilityLabels[t.capability]??t.capability]));el('agent-trace').replaceChildren();for(const task of data.tasks){const card=document.createElement('article');card.className='trace-card';const h=document.createElement('h4');h.textContent=capabilityLabels[task.capability]??task.capability;const p=document.createElement('p');p.textContent=displayState(task.state)+' · '+task.attempts+'/'+task.max_attempts+' محاولات'+(task.error_code?' · '+task.error_code:'');card.append(h,p);const meta=document.createElement('small');meta.textContent='تعتمد على: '+(task.depends_on.map(id=>taskNames.get(id)??id).join('، ')||'بداية الخطة')+' · أحجام المخرجات: '+(Object.entries(task.output_counts).map(([k,v])=>({items:'سجلات',findings:'نتائج',unresolved_order_ids:'طلبات بلا دليل مكتمل'}[k]??k)+': '+v).join('، ')||'لم تتوفر');card.append(meta);for(const attempt of task.timings){const small=document.createElement('small');small.textContent='محاولة '+attempt.attempt+' · '+(attempt.duration_ms===null?'لم ينته القياس':attempt.duration_ms+' ms')+' · '+({succeeded:'نجحت',failed:'فشلت',retry_scheduled:'إعادة محاولة',RUNNING:'قيد التنفيذ'}[attempt.outcome]??attempt.outcome);card.append(small);}el('agent-trace').append(card);}if(data.limited){const p=document.createElement('p');p.textContent='سجل التتبّع محدود بأحدث نطاق مدعوم.';el('agent-trace').append(p);}
}
async function loadWorkflowTimeline(runId){
  const activeToken=token;const data=await api('/v1/workflows/'+runId+'/timeline');if(activeToken!==token||currentWorkflow?.id!==runId)return;
  el('workflow-timeline').replaceChildren();const names={'workflow.requested':'طُلب التشغيل الدائم','workflow.state':'تغيّرت حالة المتابعة','workflow.refresh_requested':'طُلب تحديث الأدلة','workflow.cancel_requested':'طُلب الإلغاء','action.proposed':'جُمّدت مسودة للمراجعة','approval.approve':'وافق المراجع المكلف','approval.reject':'رفض المراجع المكلف','action.receipt_recorded':'سُجّل إيصال المزوّد'};
  for(const event of data.items){const li=document.createElement('li');li.textContent=formatTime(event.occurred_at)+' · '+(names[event.event_type]??event.event_type)+(event.details.state?' · '+displayState(event.details.state):'')+(event.details.revision?' · إصدار '+event.details.revision:'');el('workflow-timeline').append(li);}if(data.limited){const li=document.createElement('li');li.textContent='السجل المعروض محدود.';el('workflow-timeline').append(li);}
}
function updateApprovalStatus(row){
  const decisions=row.approvals.filter(d=>row.action&&d.action_id===row.action.id).sort((a,b)=>b.decided_at-a.decided_at);const latest=decisions[0];
  el('approval-status').textContent=latest?(latest.decision==='reject'?'رفض المراجع هذه المسودة.':(latest.expires_at*1000<=Date.now()?'انتهت صلاحية الموافقة؛ يلزم قرار جديد.':'الموافقة صالحة حتى '+formatTime(latest.expires_at*1000))):'لا توجد موافقة على الإصدار الحالي.';
  el('workflow-analysis').hidden=currentIdentity?.subject!==row.requester_id||!row.snapshot_id;
  for(const id of ['approve-action','reject-action'])el(id).disabled=false;
}
function schedulePoll(){clearTimeout(pollTimer);const epoch=pollEpoch;pollTimer=setTimeout(async()=>{if(!token||epoch!==pollEpoch)return;if(!document.hidden&&el('live-refresh').checked&&!pollBusy){pollBusy=true;try{await refreshDashboard();if(workspaceView==='workflows')await loadWorkflows();if(workspaceView==='analysis'&&currentOperation){const id=currentOperation.id,activeToken=token;const row=await api('/v1/operations/'+id);if(token===activeToken&&currentOperation?.id===id){renderOperation(row);await loadOperationEvents(id);await loadTrace(id);}}if(workspaceView==='workflows'&&currentWorkflow){const id=currentWorkflow.id,activeToken=token;const row=await api('/v1/workflows/'+id);if(token===activeToken&&currentWorkflow?.id===id){if(row.action?.id!==currentWorkflow.action?.id||row.action?.action_hash!==currentWorkflow.action?.action_hash){el('workflow-note').textContent='تغيّرت المسودة أو الأدلة. حدّث التفاصيل يدويًا وراجع الإصدار الجديد قبل اتخاذ قرار.';el('approve-action').disabled=true;el('reject-action').disabled=true;}else{renderWorkflow(row);await loadWorkflowTimeline(id);}}}}catch(error){if(token)el('last-sync').textContent='تعذر التحديث التلقائي: '+error.message;}finally{pollBusy=false;}}if(token&&epoch===pollEpoch)schedulePoll();},5000);}
for(const button of el('workspace-nav').querySelectorAll('button'))button.addEventListener('click',()=>showWorkspaceView(button.dataset.view,true));
for(const id of ['dashboard-kind','dashboard-state','dashboard-query'])el(id).addEventListener(id==='dashboard-query'?'input':'change',dashboardRows);
el('dashboard-new-goal').addEventListener('click',()=>{currentOperation=null;el('operation-detail').hidden=true;showWorkspaceView('analysis',true);el('goal').focus();});
el('dashboard-refresh').addEventListener('click',()=>refreshDashboard().catch(error=>el('message').textContent=error.message));
el('usage-refresh').addEventListener('click',()=>loadUsage().catch(error=>el('message').textContent=error.message));
el('open-linked-workflow').addEventListener('click',()=>workflowAction(()=>api('/v1/workflows/'+currentOperation.workflow_id)));
el('workflow-analysis').addEventListener('click',()=>operationAction(()=>api('/v1/operations/'+currentWorkflow.snapshot_id),'جارٍ تحميل لقطة الأدلة…'));
