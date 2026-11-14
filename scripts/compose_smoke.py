"""Acceptance check for an already running disposable Docker Compose stack.

Used by CI after migrations and synthetic seeding finish. Never prints credentials.
"""
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request

BASE_URL = os.getenv('SMOKE_BASE_URL', 'http://127.0.0.1:18000')


def compose(*args, **kwargs):
    return subprocess.run(['docker', 'compose', *args], check=True, timeout=120, **kwargs)


def request(path, token=None, payload=None, method=None, expected=200, extra_headers=None):
    headers = {'Content-Type': 'application/json', **(extra_headers or {})}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raise AssertionError(f'{method or "GET"} {path}: expected {expected}, got {response.status}')
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
    before = request('/v1/audit-events', tokens['alpha'])['items']
    event_ids = {event['id'] for event in before}
    assert event_ids and {event['tenant_id'] for event in before} == {'alpha'}
    compose('restart', 'api')
    wait_ready()
    assert request('/v1/me', tokens['alpha'])['tenant_id'] == 'alpha'
    after = request('/v1/audit-events', tokens['alpha'])['items']
    assert event_ids <= {event['id'] for event in after}
    for token in tokens.values():
        request('/v1/auth/logout', token, method='POST', expected=204)
        request('/v1/me', token, expected=401)
    print('Compose acceptance passed: fresh PostgreSQL bootstrap, login, isolation, assessment, durable audit/session, logout.')


if __name__ == '__main__':
    main()
