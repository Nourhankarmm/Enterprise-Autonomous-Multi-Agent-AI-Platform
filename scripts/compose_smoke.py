"""Acceptance check for an already running disposable Docker Compose stack.

Used by CI after migrations and synthetic seeding finish. Never prints credentials.
"""
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
import urllib.error
import urllib.request

BASE_URL = os.getenv('SMOKE_BASE_URL', 'http://127.0.0.1:18000')


def compose(*args, **kwargs):
    return subprocess.run(['docker', 'compose', *args], check=True, timeout=120, **kwargs)


def request(path, token=None, payload=None, method=None, expected=200, extra_headers=None, timeout=10):
    headers = {'Content-Type': 'application/json', **(extra_headers or {})}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raw=response.read()
            try:detail=json.loads(raw).get('detail','')
            except (ValueError,AttributeError):detail=''
            suffix=': '+detail[:300] if isinstance(detail,str) else ''
            actual_method=method or ('POST' if payload is not None else 'GET')
            raise AssertionError(f'{actual_method} {path}: expected {expected}, got {response.status}{suffix}')
        raw = response.read()
        if not raw:
            return None
        if 'application/json' in response.headers.get('Content-Type', ''):
            return json.loads(raw)
        return raw.decode()


def wait_ready():
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            health = request('/health')
            if health['status'] == 'ok' and health['database'] == 'postgresql':
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError, AssertionError):
            pass
        time.sleep(1)
    raise AssertionError('PostgreSQL-backed API did not become ready within 60 seconds')


def main():
    wait_ready()
    suffix = secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    bootstrap = '''
import json, os, sys, uuid
from sqlalchemy import insert
from platform_api.database import engine_for, users
from platform_api.auth import password_hash
config=json.load(sys.stdin)
engine=engine_for(os.environ['DATABASE_URL'])
try:
    with engine.begin() as db:
        for tenant, role in [('alpha','manager'),('beta','analyst')]:
            db.execute(insert(users).values(id=str(uuid.uuid4()),username=tenant+'-'+config['suffix'],
                tenant_id=tenant,role=role,active=True,password_hash=password_hash(config['password'])))
finally:
    engine.dispose()
'''
    compose('exec', '-T', 'api', 'python', '-c', bootstrap,
            input=json.dumps({'suffix': suffix, 'password': password}), text=True)
    assert 'login-form' in request('/')
    tokens = {}
    for tenant, expected_ids in [('alpha', {'PO-A1', 'PO-A2', 'PO-A3'}), ('beta', {'PO-B1'})]:
        session = request('/v1/auth/login', payload={'username':tenant+'-'+suffix, 'password':password})
        token = tokens[tenant] = session['access_token']
        items = request('/v1/purchase-orders?tenant_id=other', token,
                        extra_headers={'X-Tenant-ID':'other'})['items']
        assert {item['id'] for item in items} == expected_ids
        assert {item['tenant_id'] for item in items} == {tenant}
    request('/v1/purchase-orders/PO-B1', tokens['alpha'], expected=404)
    request('/v1/purchase-orders/PO-A1', tokens['beta'], expected=404)
    request('/v1/me', 'forged-session', expected=401)
    results = request('/v1/procurement/assessment?as_of=2026-11-14', tokens['alpha'])['items']
    assert {r['order_id']:r['risk'] for r in results} == {'PO-A1':'HIGH','PO-A2':'LOW','PO-A3':None}
    operation=request('/v1/operations',tokens['alpha'],payload={
        'goal':'حلل تأخير طلبات الشراء المفتوحة','as_of':'2026-11-14'},expected=201)
    assert operation['state']=='PLANNED' and all(t['attempts']==0 for t in operation['tasks'])
    confirm_path='/v1/operations/'+operation['id']+'/confirm'
    request(confirm_path,tokens['alpha'],payload={'version':99,'plan_hash':operation['plan_hash']},expected=409)
    request('/v1/operations/'+operation['id']+'/events',tokens['beta'],expected=404)
    finished=request(confirm_path,tokens['alpha'],payload={
        'version':operation['plan_version'],'plan_hash':operation['plan_hash']})
    assert finished['state']=='WAITING_DOCUMENT'
    assert all(t['state']=='SUCCEEDED' and t['attempts']==1 for t in finished['tasks'])
    assert finished['result']['unresolved_order_ids']==['PO-A3']
    events=request('/v1/operations/'+operation['id']+'/events',tokens['alpha'])['items']
    assert sum(e['event_type']=='task.succeeded' for e in events)==5
    # Ingest a real PDF with multipart, inspect exact page citations, then publish explicitly.
    import uuid
    pdf=(Path(__file__).resolve().parents[1]/'evals/datasets/contracts/supplier-s1.pdf').read_bytes()
    boundary='fixture-'+uuid.uuid4().hex
    metadata=json.dumps({'title':'CI PDF contract','supplier_id':'S1','contract_ref':'CI-'+suffix,'version':1})
    multipart=(f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="contract.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+pdf+f'\r\n--{boundary}--\r\n'.encode()
    upload=urllib.request.Request(BASE_URL+'/v1/documents',data=multipart,headers={
        'Authorization':'Bearer '+tokens['alpha'],'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(upload,timeout=20) as response:
        assert response.status==201
        document=json.load(response)
    assert document['state']=='EXTRACTED' and document['page_count']==2
    request('/v1/documents/'+document['id']+'/pages/2',tokens['beta'],expected=404)
    request('/v1/documents/'+document['id']+'/review',tokens['alpha'],payload={
        'revision':document['revision'],'checksum':document['checksum'],
        'extraction_hash':document['extraction']['extraction_hash']})
    evidence=request('/v1/procurement/assessment?as_of=2026-11-14',tokens['alpha'])['items']
    cited=next(f for f in evidence if f['order_id']=='PO-A1')
    assert cited['source_mode']=='reviewed_pdf' and cited['beyond_grace_days']==8
    assert cited['evidence'][1]['document_id']==document['id'] and cited['evidence'][1]['page']==2
    page=request(cited['evidence'][1]['location'],tokens['alpha'])
    assert page['text'][cited['evidence'][1]['start']:cited['evidence'][1]['end']]==cited['evidence'][1]['quote']
    hits=request('/v1/documents/search?q=Grace',tokens['alpha'])['items']
    assert hits and hits[0]['document_id']==document['id']
    before = request('/v1/audit-events', tokens['alpha'])['items']
    event_ids = {event['id'] for event in before}
    assert event_ids and {event['tenant_id'] for event in before} == {'alpha'}
    compose('restart', 'api')
    wait_ready()
    assert request('/v1/me', tokens['alpha'])['tenant_id'] == 'alpha'
    assert request('/v1/documents/'+document['id'],tokens['alpha'])['state']=='READY'
    assert request('/v1/documents/'+document['id']+'/pages/2',tokens['alpha'])['checksum']==document['checksum']
    restored=request('/v1/operations/'+operation['id'],tokens['alpha'])
    assert restored['state']=='WAITING_DOCUMENT' and restored['result']==finished['result']
    request(confirm_path,tokens['alpha'],payload={'version':operation['plan_version'],'plan_hash':operation['plan_hash']},expected=409)

    after = request('/v1/audit-events', tokens['alpha'])['items']
    assert event_ids <= {event['id'] for event in after}
    for token in tokens.values():
        request('/v1/auth/logout', token, method='POST', expected=204)
        request('/v1/me', token, expected=401)
    print('Compose acceptance passed: fresh PostgreSQL bootstrap, login, isolation, assessment, durable audit/session, reviewed LangGraph execution/results, PDF ingestion/citations/ACL, logout.')


if __name__ == '__main__':
    main()
