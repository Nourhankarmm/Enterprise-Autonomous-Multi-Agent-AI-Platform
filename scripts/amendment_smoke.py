"""Disposable HTTP acceptance: independent amendment plus uncertain project exposure."""
import io,json,os,secrets,uuid,urllib.request
from pathlib import Path
from reportlab.pdfgen import canvas
from compose_smoke import compose,request,BASE_URL,wait_ready

def pdf_bytes(lines):
    output=io.BytesIO();pdf=canvas.Canvas(output,pagesize=(595,842))
    for index,line in enumerate(lines):pdf.drawString(40,780-index*24,line)
    pdf.save();return output.getvalue()

def upload(token,kind,lines):
    metadata=json.dumps({'title':'Synthetic '+kind,'supplier_id':'S1','contract_ref':kind+'-'+uuid.uuid4().hex,
        'version':1,'kind':kind,'order_ids':['PO-A1'] if kind=='amendment' else []})
    boundary='fixture-'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="evidence.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+pdf_bytes(lines)+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(BASE_URL+'/v1/documents',data=body,headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(req,timeout=20) as response:doc=json.load(response)
    return request('/v1/documents/'+doc['id']+'/review',token,payload={'revision':doc['revision'],
        'checksum':doc['checksum'],'extraction_hash':doc['extraction']['extraction_hash']})

def main():
    wait_ready();tenant='amend-'+uuid.uuid4().hex;password=secrets.token_urlsafe(24)
    bootstrap='''
import json,os,sys,uuid
from sqlalchemy import insert
from platform_api.database import engine_for,tenants,suppliers,projects,orders,users
from platform_api.auth import password_hash
c=json.load(sys.stdin);engine=engine_for(os.environ['DATABASE_URL'])
with engine.begin() as db:
    db.execute(insert(tenants).values(id=c['tenant']))
    for supplier in ('S1','S3'):db.execute(insert(suppliers).values(tenant_id=c['tenant'],id=supplier))
    db.execute(insert(projects).values(tenant_id=c['tenant'],id='P1'))
    for ref,supplier,quantity,delivered,due in [('PO-A1','S1',100,20,'2026-11-01'),('PO-A3','S3',40,0,'2026-11-05')]:
        db.execute(insert(orders).values(tenant_id=c['tenant'],id=ref,supplier_id=supplier,project_id='P1',quantity=quantity,delivered_quantity=delivered,due_date=due,critical=True))
    for name,role in [('owner','manager'),('reviewer','approver')]:
        db.execute(insert(users).values(id=str(uuid.uuid4()),tenant_id=c['tenant'],username=name+'-'+c['tenant'],role=role,active=True,password_hash=password_hash(c['password'])))
engine.dispose()
'''
    compose('exec','-T','api','python','-c',bootstrap,input=json.dumps({'tenant':tenant,'password':password}),text=True)
    owner=request('/v1/auth/login',payload={'username':'owner-'+tenant,'password':password})['access_token']
    reviewer=request('/v1/auth/login',payload={'username':'reviewer-'+tenant,'password':password})['access_token']
    reviewer_id=request('/v1/me',reviewer)['subject']
    base=upload(owner,'contract',['Supplier ID: S1','Grace period: 5 calendar days.','Delivery due date: 2026-11-01'])
    amendment=upload(owner,'amendment',['Supplier ID: S1','Delivery due date: 2026-11-20'])
    assert base['active_source'] and not amendment['active_source']
    before=request('/v1/projects/exposure?as_of=2026-11-14&project_id=P1',owner)['items'][0]
    assert before['overdue_order_ids']==['PO-A1','PO-A3'] and before['shared_overdue_dependencies']
    for doc in (base,amendment):request('/v1/documents/'+doc['id']+'/acl',owner,method='PUT',payload={'revision':doc['revision'],'readers':[reviewer_id]})
    row=request('/v1/amendments',owner,payload={'request_id':uuid.uuid4().hex,'order_id':'PO-A1','base_document_id':base['id'],
        'amendment_document_id':amendment['id'],'approver_id':reviewer_id,'effective_from':'2026-11-10'},expected=201)
    assert [r['id'] for r in request('/v1/amendments',reviewer)['items']]==[row['id']]
    decision={'action_hash':row['action_hash'],'decision':'approve','signed_copy_compared':True}
    request('/v1/amendments/'+row['id']+'/decision',owner,payload=decision,expected=403)
    request('/v1/amendments/'+row['id']+'/decision',reviewer,payload=decision|{'signed_copy_compared':False},expected=409)
    assert request('/v1/amendments/'+row['id']+'/decision',reviewer,payload=decision)['state']=='APPROVED'
    after=request('/v1/projects/exposure?as_of=2026-11-14&project_id=P1',owner)['items'][0]
    first=next(o for o in after['orders'] if o['order_id']=='PO-A1')
    assert (first['late_days'],first['beyond_grace_days'],first['risk'])==(0,0,'LOW')
    assert first['erp_due_date']=='2026-11-01' and first['effective_due_date']=='2026-11-20' and first['erp_late_days']==13
    assert after['overdue_order_ids']==['PO-A3'] and after['unresolved_order_ids']==['PO-A3']
    assert all(after[k] is None for k in ('inventory_shortage','project_delay_days','quantity_total'))
    op=request('/v1/operations',owner,payload={'goal':'حلل تأخير طلبات الشراء المفتوحة','as_of':'2026-11-14'},expected=201)
    finished=request('/v1/operations/'+op['id']+'/confirm',owner,payload={'version':op['plan_version'],'plan_hash':op['plan_hash']})
    assert finished['result']['projects'][0]['overdue_order_ids']==['PO-A3']
    request('/v1/auth/logout',owner,payload={},expected=204);request('/v1/auth/logout',reviewer,payload={},expected=204)
    report={'status':'PASS','source_commit':os.getenv('GITHUB_SHA'),'actual_compose_http':True,
        'independent_signed_copy_attestation':True,'self_approval_blocked':True,'missing_attestation_blocked':True,
        'amended_lateness':0,'erp_lateness_preserved':13,'erp_date_unchanged':True,'shared_project_exposure_tested':True,
        'inventory_shortage':None,'project_delay_days':None,'real_emails_sent':0,'legal_signature_verification':False}
    target=Path('.data/phase8-amendment-projects.json');target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
