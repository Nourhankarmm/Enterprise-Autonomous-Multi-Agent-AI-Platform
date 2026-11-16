"""Request admission, secret isolation, deployment guards and safe error regressions."""
import copy,os,tempfile,uuid,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient
import test_phase_four as phase4
from platform_api.main import create_app
from platform_api.database import rate_windows
from platform_api.security import DatabaseRateLimiter,RateLimited,secret_value,validate_deployment
from platform_api.agents.planner import DemoPlanner,GeminiPlanner,PlannerUnavailable
from platform_api.agents.schemas import DEMO_GOAL,validate_proposal,PlanRejected
from datetime import date

class PhaseSixTests(unittest.TestCase):
    setUpClass=classmethod(phase4.PhaseFourTests.setUpClass.__func__)
    tearDownClass=classmethod(phase4.PhaseFourTests.tearDownClass.__func__)
    headers=phase4.PhaseFourTests.headers
    def setUp(self):
        phase4.PhaseFourTests.setUp(self);self.settings=None
    def tearDown(self):
        phase4.PhaseFourTests.tearDown(self)
        if self.settings:self.settings.stop()
    def configure(self,**settings):
        self.client.__exit__(None,None,None)
        self.settings=patch.dict(os.environ,{'RATE_LIMIT_ENABLED':'1'}|{k:str(v) for k,v in settings.items()});self.settings.start()
        self.app=create_app(self.path,self.tokens,DemoPlanner())
        # Keep multi-request assertions in one fixed window; expiry is tested with an explicit advancing clock below.
        self.app.state.rate_limiter.clock=lambda:1800000000
        self.client=TestClient(self.app,raise_server_exceptions=False);self.client.__enter__()
        # Isolate peer budgets across tests sharing a PostgreSQL database.
        with self.engine.begin() as db:db.execute(rate_windows.delete())

    def test_security_headers_and_server_generated_request_id(self):
        self.configure()
        r=self.client.get('/health',headers={'X-Request-ID':'untrusted-caller-value'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers['X-Frame-Options'],'DENY');self.assertEqual(r.headers['Referrer-Policy'],'no-referrer')
        self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        uuid.UUID(r.headers['X-Request-ID']);self.assertNotIn('untrusted-caller-value',str(r.headers))

    def test_login_account_budget_and_retry_after(self):
        self.configure(LOGIN_ACCOUNT_LIMIT=2,LOGIN_PEER_LIMIT=100)
        for _ in range(2):self.assertEqual(self.client.post('/v1/auth/login',json={'username':'unknown-account','password':'wrong'}).status_code,401)
        r=self.client.post('/v1/auth/login',json={'username':'UNKNOWN-ACCOUNT','password':'wrong'})
        self.assertEqual(r.status_code,429);self.assertGreater(int(r.headers['Retry-After']),0)
        self.assertEqual(r.headers['X-Content-Type-Options'],'nosniff')

    def test_login_peer_budget_cannot_be_bypassed_with_forwarded_headers(self):
        self.configure(LOGIN_PEER_LIMIT=2,LOGIN_ACCOUNT_LIMIT=100)
        for i in range(2):self.assertEqual(self.client.post('/v1/auth/login',json={'username':'unknown-'+str(i),'password':'wrong'},headers={'X-Forwarded-For':'198.51.100.'+str(i)}).status_code,401)
        r=self.client.post('/v1/auth/login',json={'username':'another-unknown','password':'wrong'},headers={'X-Forwarded-For':'203.0.113.44'})
        self.assertEqual(r.status_code,429)

    def test_authenticated_budget_is_shared_across_sessions_for_same_subject(self):
        self.configure(AUTH_USER_LIMIT=2)
        for _ in range(2):self.assertEqual(self.client.get('/v1/me',headers=self.headers()).status_code,200)
        self.assertEqual(self.client.get('/v1/me',headers=self.headers()).status_code,429)
        self.assertEqual(self.client.get('/v1/me',headers=self.headers('bob')).status_code,200)

    def test_planning_budget_prevents_excess_model_calls(self):
        self.configure(PLANNER_USER_LIMIT=2)
        body={'goal':DEMO_GOAL,'as_of':'2026-11-14'}
        for _ in range(2):self.assertEqual(self.client.post('/v1/operations',json=body,headers=self.headers()).status_code,201)
        with patch.object(self.app.state.operations.planner,'propose',side_effect=AssertionError('must not call provider')):
            self.assertEqual(self.client.post('/v1/operations',json=body,headers=self.headers()).status_code,429)

    def test_upload_and_search_have_separate_bounded_budgets(self):
        self.configure(UPLOAD_USER_LIMIT=1,SEARCH_USER_LIMIT=1)
        for _ in range(1):self.assertEqual(self.client.post('/v1/documents',headers=self.headers(),data={'metadata':'{}'},files={'file':('bad.pdf',b'%PDF-bad','application/pdf')}).status_code,422)
        self.assertEqual(self.client.post('/v1/documents',headers=self.headers(),data={'metadata':'{}'},files={'file':('bad.pdf',b'%PDF-bad','application/pdf')}).status_code,429)
        self.assertEqual(self.client.get('/v1/documents/search?q=Grace',headers=self.headers()).status_code,200)
        self.assertEqual(self.client.get('/v1/documents/search?q=Grace',headers=self.headers()).status_code,429)

    def test_global_peer_budget_is_not_bypassed_by_forged_bearer_tokens(self):
        self.configure(API_PEER_LIMIT=2)
        for token in ('invalid-1','invalid-2'):self.assertEqual(self.client.get('/v1/me',headers=self.headers(token)).status_code,401)
        self.assertEqual(self.client.get('/v1/me',headers=self.headers('invalid-3')).status_code,429)
        self.assertEqual(self.client.get('/health').status_code,200)

    def test_database_windows_survive_new_limiter_instance(self):
        scope='test-'+uuid.uuid4().hex;first=DatabaseRateLimiter(self.engine,clock=lambda:2000000000)
        first.consume(scope,'subject',1)
        with self.assertRaises(RateLimited):DatabaseRateLimiter(self.engine,clock=lambda:2000000000).consume(scope,'subject',1)

    def test_concurrent_admission_never_exceeds_database_limit(self):
        limiter=DatabaseRateLimiter(self.engine,clock=lambda:2200000000);scope='test-'+uuid.uuid4().hex
        def attempt(_):
            try:limiter.consume(scope,'subject',5);return True
            except RateLimited:return False
        with ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(attempt,range(20)))
        self.assertEqual(sum(results),5)

    def test_window_expiry_and_cleanup_do_not_reset_current_budget(self):
        clock=[2000000000];limiter=DatabaseRateLimiter(self.engine,clock=lambda:clock[0]);scope='test-'+uuid.uuid4().hex
        limiter.consume(scope,'subject',1)
        with self.assertRaises(RateLimited):limiter.consume(scope,'subject',1)
        clock[0]+=61;limiter.consume(scope,'subject',1)
        with self.assertRaises(RateLimited):limiter.consume(scope,'subject',1)
        clock[0]+=4000;limiter.consume(scope,'subject',1)
        with self.engine.connect() as db:
            rows=db.execute(select(rate_windows).where(rate_windows.c.scope==scope)).mappings().all()
        self.assertEqual(len(rows),1)

    def test_rate_storage_has_no_raw_peer_account_or_token(self):
        self.configure()
        self.client.post('/v1/auth/login',json={'username':'sensitive-example-name','password':'never-store-password'})
        with self.engine.connect() as db:rows=db.execute(select(rate_windows)).mappings().all()
        text=str([dict(r) for r in rows]);self.assertNotIn('sensitive-example-name',text);self.assertNotIn('never-store-password',text)
        self.assertTrue(all(len(r['key_hash'])==64 for r in rows))

    def test_limiter_backend_failure_fails_closed_without_credentials(self):
        self.configure()
        with patch.object(self.app.state.rate_limiter.engine,'begin',side_effect=OperationalError('private-sql',{},Exception('secret-password'))):
            r=self.client.get('/v1/me',headers=self.headers())
        self.assertEqual(r.status_code,503);self.assertNotIn('secret-password',r.text);self.assertNotIn('private-sql',r.text)

    def test_json_size_limit_applies_before_login_and_ignores_claimed_length(self):
        self.configure()
        r=self.client.post('/v1/auth/login',content=b'x'*70000,headers={'Content-Type':'application/json','Content-Length':'1'})
        self.assertEqual(r.status_code,413);self.assertIn('X-Request-ID',r.headers)

    def test_streamed_body_is_bounded_without_content_length(self):
        self.configure()
        r=self.client.post('/v1/operations',headers=self.headers()|{'Content-Type':'application/json'},content=iter([b'x'*40000,b'y'*40000]))
        self.assertEqual(r.status_code,413)

    def test_host_and_https_enforcement_ignore_untrusted_forwarded_proto(self):
        self.configure(REQUIRE_HTTPS=1)
        self.assertEqual(self.client.get('/health',headers={'X-Forwarded-Proto':'https'}).status_code,403)
        self.assertEqual(self.client.get('https://testserver/health').status_code,200)
        self.assertEqual(self.client.get('https://testserver/health',headers={'Host':'testserver.evil.invalid'}).status_code,400)

    def test_unexpected_service_errors_are_generic_and_correlated(self):
        self.configure()
        with patch('platform_api.main.MockERP.orders',side_effect=RuntimeError('credential-internal-should-not-escape')):
            r=self.client.get('/v1/purchase-orders',headers=self.headers())
        self.assertEqual(r.status_code,500);self.assertNotIn('credential-internal',r.text)
        self.assertEqual(r.json()['request_id'],r.headers['X-Request-ID'])

    def test_restricted_mode_is_fail_closed_and_disables_unsafe_configuration(self):
        with patch.dict(os.environ,{'DEPLOYMENT_MODE':'restricted','REQUIRE_HTTPS':'0'}):
            with self.assertRaises(RuntimeError):validate_deployment(self.engine,False)
        with patch.dict(os.environ,{'DEPLOYMENT_MODE':'restricted','REQUIRE_HTTPS':'1','ALLOWED_HOSTS':'pilot.example.invalid','RATE_LIMIT_ENABLED':'1','ENABLE_DEV_TOKENS':'0'}):
            postgres=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
            self.assertIsNone(validate_deployment(postgres,False))
            with self.assertRaises(RuntimeError):validate_deployment(postgres,True)

    def test_host_wildcard_and_invalid_limit_settings_are_rejected(self):
        with patch.dict(os.environ,{'ALLOWED_HOSTS':'*'}):
            with self.assertRaises(RuntimeError):create_app(self.path,self.tokens)
        with patch.dict(os.environ,{'AUTH_USER_LIMIT':'not-a-limit'}):
            with self.assertRaises(RuntimeError):create_app(self.path,self.tokens)

    def test_secret_file_input_is_bounded_and_never_inherited_as_an_environment_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fake-key.txt';path.write_text('synthetic-file-key',encoding='utf-8')
            with patch.dict(os.environ,{'GEMINI_API_KEY':'','GEMINI_API_KEY_FILE':str(path)}):
                self.assertEqual(secret_value('GEMINI_API_KEY'),'synthetic-file-key')
                self.assertEqual(GeminiPlanner().api_key,'synthetic-file-key')
                self.assertEqual(os.environ['GEMINI_API_KEY'],'')
            path.write_bytes(b'x'*4097)
            with patch.dict(os.environ,{'GEMINI_API_KEY':'','GEMINI_API_KEY_FILE':str(path)}):
                with self.assertRaises(PlannerUnavailable) as caught:GeminiPlanner()
                self.assertNotIn(str(path),str(caught.exception))

    def test_secret_file_failure_is_redacted_and_explicit_env_takes_precedence(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'','GEMINI_API_KEY_FILE':'Z:/missing-secret-location/private.key'}):
            with self.assertRaises(PlannerUnavailable) as caught:GeminiPlanner()
            self.assertNotIn('private.key',str(caught.exception))
        with patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic-env-key','GEMINI_API_KEY_FILE':'Z:/missing/private.key'}):
            self.assertEqual(secret_value('GEMINI_API_KEY'),'synthetic-env-key')

    def test_seeded_adversarial_plan_mutations_cannot_expand_tools_or_budgets(self):
        baseline=DemoPlanner().propose(DEMO_GOAL,date(2026,11,14)).proposal.model_dump()
        mutations=[]
        for capability in ('email.send','supplier.bank_account.update','shell.exec','contracts.delete'):
            value=copy.deepcopy(baseline);value['tasks'][0]['capability']=capability;mutations.append(value)
        for amount in (-1,0,4,1000000):
            value=copy.deepcopy(baseline);value['tasks'][0]['max_attempts']=amount;mutations.append(value)
        value=copy.deepcopy(baseline);value['tasks'][0]['depends_on']=['report'];mutations.append(value)
        value=copy.deepcopy(baseline);value['tasks'][0]['filters']['tenant_id']='forged';mutations.append(value)
        from pydantic import ValidationError
        for value in mutations:
            with self.subTest(value=value),self.assertRaises((PlanRejected,ValidationError)):validate_proposal(value)
