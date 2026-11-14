"""Phase 1 acceptance: real login, tenant isolation, constraints and durable audit."""
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update, inspect
from sqlalchemy.exc import IntegrityError
from platform_api.main import create_app
from platform_api.auth import password_hash, token_hash
from platform_api.database import engine_for, database_url, users, sessions, orders, audit_events
from platform_api.store import seed

class PhaseOneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = os.getenv('TEST_DATABASE_URL') or str(Path(cls.temp.name)/'phase1.db')
        cls.engine = engine_for(cls.path)
        config = Config('alembic.ini')
        with patch.dict(os.environ, {'DATABASE_URL':database_url(cls.path)}):
            command.upgrade(config, 'head')
            command.upgrade(config, 'head')
            command.check(config)
        seed(cls.engine)
        cls.password = 'test-only-password-123'
        cls.names = {}
        with cls.engine.begin() as db:
            for tenant, role in [('alpha','manager'),('beta','analyst'),('alpha','auditor')]:
                name = str(uuid.uuid4())
                cls.names[(tenant,role)] = name
                db.execute(insert(users).values(id=name,username=name,password_hash=password_hash(cls.password),
                    tenant_id=tenant,role=role,active=True))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp.cleanup()

    def setUp(self):
        self.client = TestClient(create_app(self.path, {}))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)

    def sign_in(self, tenant='alpha', role='manager'):
        response = self.client.post('/v1/auth/login',json={'username':self.names[(tenant,role)],'password':self.password})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['access_token']

    def headers(self, token):
        return {'Authorization':f'Bearer {token}'}

    def test_login_and_tenant_scoped_reads(self):
        for tenant, role, expected in [('alpha','manager',{'PO-A1','PO-A2','PO-A3'}),('beta','analyst',{'PO-B1'})]:
            token=self.sign_in(tenant,role)
            headers=self.headers(token)|{'X-Tenant-ID':'beta' if tenant=='alpha' else 'alpha'}
            result=self.client.get('/v1/purchase-orders?tenant_id=other',headers=headers)
            self.assertEqual({r['id'] for r in result.json()['items']},expected)
            self.assertEqual({r['tenant_id'] for r in result.json()['items']},{tenant})
        self.assertEqual(self.client.get('/v1/purchase-orders/PO-A1',headers=self.headers(token)).status_code,404)

    def test_invalid_and_forged_credentials(self):
        for password in ['wrong',self.password]:
            username=self.names[('alpha','manager')] if password=='wrong' else 'missing'
            self.assertEqual(self.client.post('/v1/auth/login',json={'username':username,'password':password}).status_code,401)
        self.assertEqual(self.client.get('/v1/me',headers=self.headers('forged')).status_code,401)
        self.assertEqual(self.client.get('/v1/me').status_code,401)

    def test_logout_revokes_session(self):
        token=self.sign_in();headers=self.headers(token)
        self.assertEqual(self.client.post('/v1/auth/logout',headers=headers).status_code,204)
        self.assertEqual(self.client.get('/v1/me',headers=headers).status_code,401)

    def test_expired_session(self):
        token=self.sign_in()
        with self.engine.begin() as db:
            db.execute(update(sessions).where(sessions.c.token_hash==token_hash(token)).values(expires_at=time.time()-1))
        self.assertEqual(self.client.get('/v1/me',headers=self.headers(token)).status_code,401)

    def test_role_revocation_and_disabled_user(self):
        name=self.names[('beta','analyst')];token=self.sign_in('beta','analyst')
        try:
            with self.engine.begin() as db:
                db.execute(update(users).where(users.c.id==name).values(role='auditor'))
            self.assertEqual(self.client.get('/v1/purchase-orders',headers=self.headers(token)).status_code,403)
            with self.engine.begin() as db:
                db.execute(update(users).where(users.c.id==name).values(active=False))
            self.assertEqual(self.client.get('/v1/me',headers=self.headers(token)).status_code,401)
        finally:
            with self.engine.begin() as db:
                db.execute(update(users).where(users.c.id==name).values(role='analyst',active=True))

    def test_relational_constraints(self):
        base=dict(tenant_id='beta',id=str(uuid.uuid4()),supplier_id='S2',project_id='P9',
                  quantity=1,delivered_quantity=0,due_date='2026-11-14',critical=True)
        with self.assertRaises(IntegrityError), self.engine.begin() as db:
            db.execute(insert(orders).values(**base)) # S2 belongs only to alpha.
        with self.assertRaises(IntegrityError), self.engine.begin() as db:
            db.execute(insert(orders).values(**(base|{'supplier_id':'S1','quantity':-1})))
        self.assertIn('alembic_version',inspect(self.engine).get_table_names())

    def test_audit_is_persistent_scoped_and_redacted(self):
        token=self.sign_in();headers=self.headers(token)
        self.client.get('/v1/purchase-orders',headers=headers)
        with TestClient(create_app(self.path,{})) as restarted:
            response=restarted.get('/v1/audit-events',headers=headers)
            self.assertEqual(response.status_code,200)
            items=response.json()['items']
            self.assertTrue(any(r['action']=='orders.read' for r in items))
            self.assertEqual({r['tenant_id'] for r in items},{'alpha'})
            self.assertNotIn(token,response.text)
            self.assertNotIn(self.password,response.text)
        with self.engine.connect() as db:
            self.assertIsNotNone(db.execute(select(sessions).where(sessions.c.token_hash==token_hash(token))).first())
            self.assertIsNone(db.execute(select(sessions).where(sessions.c.token_hash==token)).first())
        token=self.sign_in('beta','analyst')
        self.assertEqual(self.client.get('/v1/audit-events',headers=self.headers(token)).status_code,403)

    def test_auditor_reads_audit_not_procurement(self):
        headers=self.headers(self.sign_in('alpha','auditor'))
        self.assertEqual(self.client.get('/v1/purchase-orders',headers=headers).status_code,403)
        self.assertEqual(self.client.get('/v1/audit-events',headers=headers).status_code,200)

    def test_web_shell_and_health(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn('login-form',response.text)
        self.assertIn("frame-ancestors 'none'",response.headers['content-security-policy'])
        self.assertEqual(self.client.get('/assets/app.js').status_code,200)
        self.assertEqual(self.client.get('/health').json()['status'],'ok')

if __name__ == '__main__':
    unittest.main()
