'use strict';
let currentAmendment=null,amendmentBusy=false;
function clearPhaseEight(){
  currentAmendment=null;
  for(const id of ['amendment-list','amendment-summary','amendment-hash','amendment-evidence','project-results','amend-base','amend-document','amend-order','amend-reviewer'])el(id).replaceChildren();
  el('amendment-detail').hidden=true;el('amend-attestation').checked=false;
  el('approve-amendment').disabled=true;el('reject-amendment').disabled=true;
  el('project-filter').value='';el('project-as-of').value='';el('amend-effective').value='';
}
function fillAmendOptions(id,rows,label){
  const select=el(id);select.replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='اختر…';select.append(empty);
  for(const row of rows){const option=document.createElement('option');option.value=row.id;option.textContent=label(row);select.append(option);}
}
async function loadAmendments(){
  const requestToken=token;el('amendment-list').replaceChildren();el('amendment-detail').hidden=true;currentAmendment=null;el('amend-attestation').checked=false;
  const writer=['manager','analyst'].includes(currentIdentity?.role);el('amendment-form').hidden=!writer;
  try{
    const list=await api('/v1/amendments');if(token!==requestToken)return;
    for(const row of list.items){const button=document.createElement('button');button.className='secondary';button.textContent=`${row.order_id} · ${displayState(row.state)} · ${row.effective_from}`;button.addEventListener('click',()=>openAmendment(row.id));el('amendment-list').append(button);}
    if(!list.items.length)el('amendment-list').textContent='لا توجد تعديلات مسموحة لك.';
    if(writer){
      const [docs,people,orders]=await Promise.all([api('/v1/documents'),api('/v1/document-readers'),api('/v1/purchase-orders')]);
      if(token!==requestToken)return;
      fillAmendOptions('amend-base',docs.items.filter(d=>d.kind==='contract'&&d.state==='READY'),d=>`${d.title} · ${d.supplier_id} · ${d.contract_ref} · إصدار ${d.version}`);
      fillAmendOptions('amend-document',docs.items.filter(d=>d.kind==='amendment'&&d.state==='READY'),d=>`${d.title} · ${d.supplier_id} · ${d.contract_ref} · إصدار ${d.version}`);
      fillAmendOptions('amend-reviewer',people.items.filter(p=>p.role==='approver'),p=>p.username);
      fillAmendOptions('amend-order',orders.items,o=>`${o.id} · ${o.supplier_id} · ${o.project_id}`);
    }
  }catch(error){if(token===requestToken)clearPhaseEight();throw error;}
}
function renderAmendment(row){
  currentAmendment=row;el('amendment-detail').hidden=false;el('amend-attestation').checked=false;
  const payload=row.payload;const terms=payload.amendment.approved_terms;
  el('amendment-summary').textContent=`${row.order_id} · ${displayState(row.state)} · موعد ERP: ${payload.order.due_date} · موعد التعديل: ${terms.due_date} · فترة السماح: ${terms.grace_days??'تُحفظ من العقد الأساسي'} · يسري من ${row.effective_from}`;
  el('amendment-hash').textContent=`بصمة التعديل: ${row.action_hash} · يحل محل: ${row.supersedes_id??'لا يوجد تعديل سابق'} · قارن المستندين بالأصل قبل القرار.`;
  el('amendment-evidence').replaceChildren();
  for(const [label,doc] of [['العقد الأساسي',payload.base],['مستند التعديل',payload.amendment]]){
    const button=document.createElement('button');button.className='secondary';button.textContent=`فتح ${label} · إصدار ${doc.version}`;
    button.addEventListener('click',()=>documentAction(()=>api('/v1/documents/'+doc.id),'جارٍ فتح المستند للمراجعة…'));el('amendment-evidence').append(button);
    for(const citation of doc.approved_terms.citations)el('amendment-evidence').append(evidenceCard(citation,label));
  }
  const canDecide=currentIdentity?.subject===row.approver_id&&row.state==='WAITING_APPROVAL';
  el('amend-attestation-label').hidden=!canDecide;el('approve-amendment').disabled=true;el('reject-amendment').disabled=!canDecide||amendmentBusy;
}
async function openAmendment(id){
  const requestToken=token;el('amendment-detail').hidden=true;currentAmendment=null;
  try{const row=await api('/v1/amendments/'+id);if(token===requestToken)renderAmendment(row);}
  catch(error){if(token===requestToken){clearPhaseEight();el('message').textContent=error.message;}}
}
el('amend-attestation').addEventListener('change',()=>{el('approve-amendment').disabled=amendmentBusy||!el('amend-attestation').checked||!currentAmendment||currentAmendment.state!=='WAITING_APPROVAL'||currentAmendment.approver_id!==currentIdentity?.subject;});
el('reload-amendments').addEventListener('click',()=>loadAmendments().catch(error=>el('message').textContent=error.message));
el('amendment-form').addEventListener('submit',async event=>{
  event.preventDefault();if(amendmentBusy)return;const requestToken=token;amendmentBusy=true;el('propose-amendment').disabled=true;
  try{
    const row=await api('/v1/amendments',{method:'POST',body:JSON.stringify({request_id:crypto.randomUUID(),order_id:el('amend-order').value,base_document_id:el('amend-base').value,amendment_document_id:el('amend-document').value,approver_id:el('amend-reviewer').value,effective_from:el('amend-effective').value})});
    if(token!==requestToken)return;await loadAmendments();if(token===requestToken)renderAmendment(row);el('message').textContent='التعديل ينتظر موافقة مستقلة؛ لم يتغير موعد ERP.';
  }catch(error){if(token===requestToken)el('message').textContent=error.message;}
  finally{amendmentBusy=false;el('propose-amendment').disabled=false;}
});
async function decideAmendment(decision){
  const row=currentAmendment,requestToken=token;if(!row||amendmentBusy)return;
  const compared=el('amend-attestation').checked;amendmentBusy=true;el('approve-amendment').disabled=true;el('reject-amendment').disabled=true;
  try{
    const result=await api('/v1/amendments/'+row.id+'/decision',{method:'POST',body:JSON.stringify({action_hash:row.action_hash,decision,signed_copy_compared:compared})});
    if(token!==requestToken)return;await loadAmendments();if(token===requestToken)renderAmendment(result);el('message').textContent='تم تسجيل القرار على النسخة المعروضة.';
  }catch(error){if(token===requestToken){el('message').textContent=error.message;el('amend-attestation').checked=false;}}
  finally{amendmentBusy=false;}
}
el('approve-amendment').addEventListener('click',()=>decideAmendment('approve'));
el('reject-amendment').addEventListener('click',()=>decideAmendment('reject'));
el('project-form').addEventListener('submit',async event=>{
  event.preventDefault();const requestToken=token;el('project-results').replaceChildren();el('load-projects').disabled=true;
  try{
    const query=new URLSearchParams({as_of:el('project-as-of').value});if(el('project-filter').value.trim())query.set('project_id',el('project-filter').value.trim());
    const data=await api('/v1/projects/exposure?'+query);if(token!==requestToken)return;
    if(!data.items.length)el('project-results').textContent='لا توجد طلبات ضمن النطاق المسموح.';
    for(const project of data.items){
      const card=document.createElement('section');card.className='evidence-card';const title=document.createElement('h3');title.textContent=`${project.project_id} · طلبات متأخرة مفتوحة: ${project.overdue_order_ids.length}`;card.append(title);
      const note=document.createElement('p');note.textContent=`طلبات عالية المخاطر: ${project.high_risk_order_ids.join('، ')||'لا يوجد'} · أدلة غير مكتملة: ${project.unresolved_order_ids.join('، ')||'لا يوجد'} · عجز المخزون وتأخير المشروع: غير معلومين`;card.append(note);
      for(const finding of project.orders){
        const text=document.createElement('p');text.textContent=`${finding.order_id} · متبقٍ: ${finding.remaining_quantity} · تأخير التوريد: ${finding.late_days} يوم · المخاطر: ${labels[finding.risk]??'غير معلومة'}`+(finding.amendment?` · موعد ERP: ${finding.erp_due_date} · الموعد المُراجع: ${finding.effective_due_date}`:'');card.append(text);
        for(const citation of finding.evidence.filter(e=>e.source_type==='contract_pdf'))card.append(evidenceCard(citation,finding.order_id));
      }
      el('project-results').append(card);
    }
  }catch(error){if(token===requestToken){el('project-results').replaceChildren();el('message').textContent=error.message;}}
  finally{el('load-projects').disabled=false;}
});
