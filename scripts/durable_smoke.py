"""Real Compose acceptance: SIGKILL worker, server/API restart, queued commands, one send."""
import json,secrets,time,uuid,urllib.request
from pathlib import Path
from compose_smoke import compose,request,wait_ready,BASE_URL

def main():
    wait_ready();suffix=secrets.token_hex(6);tenant='durable-'+suffix;password=secrets.token_urlsafe(24)
    bootstrap='''
import json,os,sys,uuid
from sqlalchemy import insert
from platform_api.database import engine_for,tenants,suppliers,projects,orders,users
from platform_api.auth import password_hash
c=json.load(sys.stdin);e=engine_for(os.environ['DATABASE_URL']);t=c['tenant']
with e.begin() as db:
 db.execute(insert(tenants).values(id=t))
 db.execute(insert(suppliers).values(tenant_id=t,id='S3'))
 db.execute(insert(projects).values(tenant_id=t,id='P2'))
 db.execute(insert(orders).values(tenant_id=t,id='QA-PO',supplier_id='S3',project_id='P2',quantity=10,delivered_quantity=0,due_date='2026-11-05',critical=True))
 for name,role in [('owner','manager'),('reviewer','approver')]:
  db.execute(insert(users).values(id=str(uuid.uuid4()),username=name+'-'+t,tenant_id=t,role=role,active=True,password_hash=password_hash(c['password'])))
e.dispose()
'''
    compose('exec','-T','api','python','-c',bootstrap,input=json.dumps({'tenant':tenant,'password':password}),text=True)
    tokens={name:request('/v1/auth/login',payload={'username':name+'-'+tenant,'password':password})['access_token'] for name in ('owner','reviewer')}
    owner,reviewer=tokens['owner'],tokens['reviewer']
    op=request('/v1/operations',owner,payload={'goal':'حلل تأخير طلبات الشراء المفتوحة','as_of':'2026-11-14'},expected=201)
    start={'version':op['plan_version'],'plan_hash':op['plan_hash']}
    row=request('/v1/operations/'+op['id']+'/durable',owner,payload=start,expected=202)
    run_id=row['id'];path='/v1/workflows/'+run_id
    def wait(state):
        deadline=time.monotonic()+80
        while time.monotonic()<deadline:
            row=request(path,owner)
            if row['state']==state:return row
            if row['state']=='FAILED':raise AssertionError('Durable workflow failed: '+str(row['error_code']))
            time.sleep(1)
        raise AssertionError('Timed out waiting for '+state)
    wait('WAITING_DOCUMENT')
    assert request('/v1/operations/'+op['id']+'/durable',owner,payload=start,expected=202)['id']==run_id
    compose('kill','-s','SIGKILL','worker')
    pdf=(Path(__file__).resolve().parents[1]/'evals/datasets/contracts/supplier-s3.pdf').read_bytes()
    boundary='fixture-'+uuid.uuid4().hex;metadata=json.dumps({'title':'Durable S3 evidence','supplier_id':'S3','contract_ref':'S3-PDF','version':1})
    data=(f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="s3.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+pdf+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(BASE_URL+'/v1/documents',data=data,headers={'Authorization':'Bearer '+owner,'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(req,timeout=20) as response:doc=json.load(response)
    request('/v1/documents/'+doc['id']+'/review',owner,payload={'revision':doc['revision'],'checksum':doc['checksum'],'extraction_hash':doc['extraction']['extraction_hash']})
    readers=request('/v1/document-readers',owner)['items'];reviewer_id=next(r['id'] for r in readers if r['username']=='reviewer-'+tenant)
    doc=request('/v1/documents/'+doc['id'],owner)
    request('/v1/documents/'+doc['id']+'/acl',owner,payload={'revision':doc['revision'],'readers':[reviewer_id]},method='PUT')
    request(path+'/refresh',owner,payload={'request_id':uuid.uuid4().hex},expected=202)
    compose('restart','temporal');compose('start','worker')
    wait('WAITING_APPROVAL')
    action=request(path+'/draft',owner,payload={'request_id':uuid.uuid4().hex,'supplier_id':'S3','subject':'Supplier follow-up','body':'Please confirm delivery timing.'},expected=201)
    assert [r['id'] for r in request('/v1/dashboard',reviewer)['workflows']]==[run_id]
    assert request('/v1/dashboard',reviewer)['analyses']==[]
    request('/v1/telemetry/planner',reviewer,expected=403)
    decision={'request_id':uuid.uuid4().hex,'action_hash':action['action_hash'],'decision':'approve'}
    request('/v1/approvals/'+action['id']+'/decision',owner,payload=decision,expected=403)
    compose('kill','-s','SIGKILL','worker')
    first=request('/v1/approvals/'+action['id']+'/decision',reviewer,payload=decision)
    assert request('/v1/approvals/'+action['id']+'/decision',reviewer,payload=decision)==first
    compose('restart','api','temporal');wait_ready();compose('start','worker')
    completed=wait('COMPLETED');receipt=completed['execution']['receipt']
    assert receipt['accepted'] and completed['result']['external_actions']==1
    assert completed['action']['action_hash']==action['action_hash']
    timeline=request(path+'/timeline',reviewer)
    assert {'action.proposed','approval.approve','action.receipt_recorded'}<={e['event_type'] for e in timeline['items']}
    assert request('/v1/telemetry/planner',owner)['summary']['calls']==1
    compose('restart','worker');time.sleep(2)
    assert request(path,owner)['execution']['receipt']==receipt
    count='''
import json,os,sys
from sqlalchemy import select,func
from platform_api.database import engine_for,sandbox_messages
e=engine_for(os.environ['DATABASE_URL']);t=json.load(sys.stdin)['tenant']
with e.connect() as db:assert db.execute(select(func.count()).select_from(sandbox_messages).where(sandbox_messages.c.tenant_id==t)).scalar_one()==1
e.dispose()
'''
    compose('exec','-T','api','python','-c',count,input=json.dumps({'tenant':tenant}),text=True)
    for token in tokens.values():request('/v1/auth/logout',token,method='POST',expected=204)
    print('Durable acceptance passed: missing PDF, SIGKILL/restart, persisted Temporal history, independent exact approval, queued decisions, one sandbox receipt and no duplicate delivery.')

if __name__=='__main__':main()
