"""Real RSA OIDC protocol checks with an isolated provider transport; no live credentials."""
import base64,copy,hashlib,json,os,time,uuid,unittest
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
import httpx,jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import insert,select,update,func
import test_phase_three as phase3
from platform_api.main import create_app
from platform_api.oidc import OIDCConfig,OIDCError,SESSION_COOKIE,FLOW_COOKIE,csrf_token
from platform_api.identity_admin import bind_identity,revoke_sessions
from platform_api.auth import token_hash
from platform_api.database import users,sessions,oidc_flows,oidc_identities,audit_events
from platform_api.agents.planner import DemoPlanner

ISSUER='https://identity.example.invalid/realms/test'
REDIRECT='https://app.example.invalid/v1/auth/oidc/callback'

class PhaseNineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        phase3.PhaseThreeTests.setUpClass.__func__(cls)
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))|{'kid':'test-key','alg':'RS256','use':'sig'}
    tearDownClass=classmethod(phase3.PhaseThreeTests.tearDownClass.__func__)
    def setUp(self):
        phase3.PhaseThreeTests.setUp(self);self.client.__exit__(None,None,None)
        self.environment=patch.dict(os.environ,{'AUTH_MODE':'oidc','OIDC_ISSUER':ISSUER,'OIDC_CLIENT_ID':'test-client',
            'OIDC_REDIRECT_URI':REDIRECT,'OIDC_ALLOW_HTTP':'0','OIDC_BACKCHANNEL_ORIGIN':'','OIDC_CLIENT_SECRET':'',
            'OIDC_CLIENT_SECRET_FILE':'','OIDC_REQUIRED_ACR':'','ENABLE_DEV_TOKENS':'0','RATE_LIMIT_ENABLED':'0',
            'ALLOWED_HOSTS':'app.example.invalid,testserver','DEPLOYMENT_MODE':'demo','REQUIRE_HTTPS':'0'})
        self.environment.start();self.subject='external-'+uuid.uuid4().hex
        bind_identity(self.engine,self.alice,ISSUER,self.subject)
        self.overrides={};self.requests=[];self.login_parameters={};self.token_calls=0
        self.metadata_changes={};self.transport=httpx.MockTransport(self.provider)
        self.app=create_app(self.path,planner=DemoPlanner());self.app.state.oidc.http=self.transport
        self.client=TestClient(self.app,base_url='https://app.example.invalid');self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None);self.environment.stop()
    def provider(self,request):
        self.requests.append(str(request.url))
        if request.url.path.endswith('/.well-known/openid-configuration'):
            return httpx.Response(200,json={'issuer':ISSUER,'authorization_endpoint':ISSUER+'/auth','token_endpoint':ISSUER+'/token',
                'jwks_uri':ISSUER+'/certs','response_types_supported':['code'],'id_token_signing_alg_values_supported':['RS256'],
                'code_challenge_methods_supported':['S256']}|self.metadata_changes)
        if request.url.path.endswith('/certs'):return httpx.Response(200,json={'keys':[self.jwk]})
        if request.url.path.endswith('/token'):
            self.token_calls+=1;self.exchange=parse_qs(request.content.decode())
            return httpx.Response(200,json={'id_token':self.encoded(),'access_token':'provider-access-token-never-returned','refresh_token':'provider-refresh-token-never-stored'})
        return httpx.Response(404,json={'error':'synthetic-provider-secret-not-for-clients'})
    def claims(self):
        now=int(time.time())
        return {'iss':ISSUER,'aud':'test-client','sub':self.subject,'iat':now,'exp':now+1800,'auth_time':now,
            'nonce':self.login_parameters.get('nonce',['synthetic-nonce'])[0],'tenant_id':'beta','role':'super-admin',
            'email':self.alice,'preferred_username':'untrusted-name'}|self.overrides
    def encoded(self,claims=None,**header):return jwt.encode(claims or self.claims(),self.key,algorithm='RS256',headers={'kid':'test-key'}|header)
    def start(self):
        response=self.client.get('/v1/auth/oidc/start',follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        self.login_parameters=parse_qs(urlsplit(response.headers['Location']).query)
        return response
    def finish(self,**params):
        return self.client.get('/v1/auth/oidc/callback',params={'state':self.login_parameters['state'][0],'code':'synthetic-code'}|params,follow_redirects=False)
    def authenticated(self):self.start();r=self.finish();self.assertEqual(r.status_code,303,r.text);return r
    def test_code_pkce_nonce_signed_cookie_session_and_explicit_mapping(self):
        start=self.start();self.assertIn('HttpOnly',start.headers['set-cookie']);self.assertIn('Secure',start.headers['set-cookie'])
        self.assertEqual(self.login_parameters['scope'],['openid']);self.assertEqual(self.login_parameters['code_challenge_method'],['S256'])
        r=self.finish();self.assertEqual(r.status_code,303,r.text);self.assertEqual(r.headers['Location'],'/')
        cookie=r.headers.get_list('set-cookie')[-1]
        for value in ('HttpOnly','Secure','SameSite=lax','Path=/'):self.assertIn(value,cookie)
        self.assertNotIn('Domain=',cookie)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(self.exchange['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge,self.login_parameters['code_challenge'][0])
        identity=self.client.get('/v1/me').json();self.assertEqual((identity['tenant_id'],identity['role'],identity['subject']),(self.alpha,'manager',self.alice))
        self.assertNotIn('access_token',self.client.get('/v1/auth/session').text)
        with self.engine.connect() as db:
            stored=db.execute(select(sessions).where(sessions.c.user_id==self.alice)).mappings().one()
            self.assertEqual(stored['auth_method'],'oidc');self.assertNotEqual(stored['token_hash'],self.client.cookies.get(SESSION_COOKIE))
            self.assertLessEqual(stored['expires_at'],self.claims()['exp'])
    def test_cross_tenant_claims_email_and_provider_roles_cannot_grant_access(self):
        self.authenticated();r=self.client.get('/v1/purchase-orders/PO-B1');self.assertEqual(r.status_code,404)
        self.assertEqual(self.client.get('/v1/me').json()['role'],'manager')
        with self.engine.begin() as db:db.execute(update(users).where(users.c.id==self.alice).values(role='approver'))
        self.assertEqual(self.client.get('/v1/me').json()['role'],'approver');self.assertEqual(self.client.get('/v1/purchase-orders').status_code,403)
    def test_unknown_subject_is_never_auto_provisioned_or_linked_by_email(self):
        self.overrides={'sub':'unmapped-subject','email':self.alice};self.start();response=self.finish()
        self.assertEqual(response.status_code,401);self.assertNotIn('unmapped-subject',response.text)
        with self.engine.connect() as db:self.assertEqual(db.execute(select(func.count()).select_from(sessions).where(sessions.c.user_id==self.alice)).scalar_one(),0)
    def test_state_is_browser_bound_one_use_and_not_consumed_by_wrong_cookie(self):
        self.start();service=self.app.state.oidc
        with self.assertRaises(OIDCError):service.finish(self.login_parameters['state'][0],'wrong-browser','synthetic-code')
        self.assertEqual(self.token_calls,0)
        self.assertEqual(self.finish().status_code,303);self.assertEqual(self.finish().status_code,401);self.assertEqual(self.token_calls,1)
    def test_expired_state_and_changed_configuration_are_rejected_before_exchange(self):
        self.start()
        with self.engine.begin() as db:db.execute(update(oidc_flows).where(oidc_flows.c.state_hash==token_hash(self.login_parameters['state'][0])).values(expires_at=time.time()-1))
        self.assertEqual(self.finish().status_code,401);self.assertEqual(self.token_calls,0)
        self.start()
        with self.engine.begin() as db:db.execute(update(oidc_flows).where(oidc_flows.c.state_hash==token_hash(self.login_parameters['state'][0])).values(configuration_hash='changed'))
        self.assertEqual(self.finish().status_code,401);self.assertEqual(self.token_calls,0)
    def test_nonce_issuer_audience_expiry_authentication_age_and_authorized_party(self):
        now=int(time.time());base=self.claims();nonce_hash=token_hash(base['nonce'])
        bad=[{'nonce':'wrong'},{'iss':'https://other.example.invalid'},{'aud':'other-client'},
            {'aud':['test-client','another-client']},{'azp':'another-client'},{'exp':now-1},{'iat':now+100},
            {'auth_time':now-7200},{'auth_time':now+100},{'exp':True},{'sub':123}]
        for changes in bad:
            with self.subTest(changes=changes),self.assertRaises(OIDCError):
                self.app.state.oidc.validate_token(self.encoded(base|changes),{'keys':[self.jwk]},nonce_hash)
        valid=base|{'aud':['test-client','another-client'],'azp':'test-client'}
        self.assertEqual(self.app.state.oidc.validate_token(self.encoded(valid),{'keys':[self.jwk]},nonce_hash)['sub'],self.subject)
    def test_unsigned_hmac_modified_token_and_untrusted_keys_are_rejected(self):
        base=self.claims();nonce_hash=token_hash(base['nonce'])
        for encoded in (jwt.encode(base,'',algorithm='none',headers={'kid':'test-key'}),
                jwt.encode(base,'synthetic-hmac-secret-at-least-32-bytes',algorithm='HS256',headers={'kid':'test-key'}),self.encoded()[:-10]+'wrongvalue'):
            with self.assertRaises(OIDCError):self.app.state.oidc.validate_token(encoded,{'keys':[self.jwk]},nonce_hash)
        with self.assertRaises(OIDCError):self.app.state.oidc.validate_token(self.encoded(),{'keys':[self.jwk,self.jwk]},nonce_hash)
        claims=self.app.state.oidc.validate_token(self.encoded(jku='https://attacker.example.invalid/key'),{'keys':[self.jwk]},nonce_hash)
        self.assertEqual(claims['sub'],self.subject);self.assertEqual(self.requests,[])
    def test_critical_extensions_missing_claims_and_required_acr_are_enforced(self):
        claims=self.claims();nonce_hash=token_hash(claims['nonce'])
        with self.assertRaises(OIDCError):self.app.state.oidc.validate_token(self.encoded(crit=['unknown']),{'keys':[self.jwk]},nonce_hash)
        for name in ('exp','iat','nonce','auth_time','sub'):
            value=dict(claims);value.pop(name)
            with self.assertRaises(OIDCError):self.app.state.oidc.validate_token(self.encoded(value),{'keys':[self.jwk]},nonce_hash)
        from dataclasses import replace
        self.app.state.oidc.config=replace(self.app.state.oidc.config,required_acr='test-assurance')
        with self.assertRaises(OIDCError):self.app.state.oidc.validate_token(self.encoded(),{'keys':[self.jwk]},nonce_hash)
    def test_http_only_cookie_requires_csrf_and_same_origin_for_unsafe_requests(self):
        self.authenticated();session=self.client.get('/v1/auth/session').json();csrf=session['csrf_token']
        self.assertEqual(self.client.post('/v1/auth/logout').status_code,403)
        self.assertEqual(self.client.post('/v1/auth/logout',headers={'X-CSRF-Token':'wrong'}).status_code,403)
        self.assertEqual(self.client.post('/v1/auth/logout',headers={'X-CSRF-Token':csrf,'Origin':'https://attacker.example.invalid'}).status_code,403)
        self.assertEqual(self.client.get('/v1/me').status_code,200)
        self.assertEqual(self.client.post('/v1/auth/logout',headers={'X-CSRF-Token':csrf,'Origin':'https://app.example.invalid'}).status_code,204)
        self.assertEqual(self.client.get('/v1/me').status_code,401)
    def test_disabled_user_external_binding_and_session_revocation_take_effect_immediately(self):
        self.authenticated();cookie=self.client.cookies.get(SESSION_COOKIE)
        revoke_sessions(self.engine,self.alice,True)
        self.assertEqual(self.client.get('/v1/me').status_code,401)
        self.start();self.assertEqual(self.finish().status_code,401)
        bind_identity(self.engine,self.alice,ISSUER,self.subject)
        self.assertEqual(self.client.get('/v1/me').status_code,401)
        self.authenticated()
        with self.engine.begin() as db:db.execute(update(users).where(users.c.id==self.alice).values(active=False))
        self.assertEqual(self.client.get('/v1/me').status_code,401)
    def test_local_password_old_sessions_and_raw_provider_tokens_are_not_oidc_sessions(self):
        self.assertEqual(self.client.post('/v1/auth/login',json={'username':self.alice,'password':'ignored'}).status_code,403)
        with self.engine.begin() as db:db.execute(insert(sessions).values(token_hash=token_hash('old-local-session'),user_id=self.alice,expires_at=time.time()+3600))
        for value in ('old-local-session',self.encoded()):self.assertEqual(self.client.get('/v1/me',headers={'Authorization':'Bearer '+value}).status_code,401)
    def test_provider_errors_unsafe_discovery_and_outage_never_fall_back_to_password(self):
        self.start();self.assertEqual(self.finish(error='provider-sensitive-error').status_code,401);self.assertEqual(self.token_calls,0)
        for changes in ({'token_endpoint':'https://attacker.example.invalid/token'},{'issuer':'https://wrong.example.invalid'}, {'code_challenge_methods_supported':['plain']}):
            self.metadata_changes=changes;response=self.client.get('/v1/auth/oidc/start',follow_redirects=False)
            self.assertEqual(response.status_code,503);self.assertNotIn('attacker',response.text)
        self.assertTrue(all(url.startswith(ISSUER) for url in self.requests))
        self.assertEqual(self.client.post('/v1/auth/login',json={'username':self.alice,'password':'ignored'}).status_code,403)
    def test_flow_database_and_audit_never_store_codes_provider_tokens_or_raw_browser_state(self):
        self.start()
        with self.engine.connect() as db:flow=db.execute(select(oidc_flows).where(oidc_flows.c.state_hash==token_hash(self.login_parameters['state'][0]))).mappings().one()
        for secret in (self.login_parameters['state'][0],self.login_parameters['nonce'][0],self.client.cookies.get(FLOW_COOKIE)):
            self.assertNotIn(secret,str(dict(flow)))
        self.assertNotIn(flow['pkce_verifier'],str(self.login_parameters))
        self.assertEqual(self.finish().status_code,303)
        with self.engine.connect() as db:
            audit=db.execute(select(audit_events.c.details).where(audit_events.c.tenant_id==self.alpha)).scalars().all()
            self.assertNotIn('provider-access-token',str(audit));self.assertNotIn('synthetic-code',str(audit))
    def test_expired_app_session_and_identity_binding_collision_are_rejected(self):
        self.authenticated()
        with self.engine.begin() as db:db.execute(update(sessions).where(sessions.c.user_id==self.alice).values(expires_at=time.time()-1))
        self.assertEqual(self.client.get('/v1/me').status_code,401)
        with self.assertRaises(ValueError):bind_identity(self.engine,self.bob,ISSUER,self.subject)
        with self.assertRaises(ValueError):bind_identity(self.engine,self.alice,ISSUER,'different-subject')
    def test_url_configuration_is_fail_closed_and_http_is_local_demo_only(self):
        for changes in ({'OIDC_ISSUER':'http://external.example.invalid','OIDC_ALLOW_HTTP':'1'},
            {'OIDC_ISSUER':'https://user:password@example.invalid'}, {'OIDC_REDIRECT_URI':'https://app.example.invalid/other'},
            {'OIDC_BACKCHANNEL_ORIGIN':'https://other.example.invalid'}, {'OIDC_ALLOW_HTTP':'1','DEPLOYMENT_MODE':'restricted'}):
            with self.subTest(changes=changes),patch.dict(os.environ,changes),self.assertRaises(RuntimeError):OIDCConfig.from_environment()
        with self.assertRaises(RuntimeError):create_app(self.path,{'injected':phase3.Identity(tenant_id=self.alpha,role='manager')})
    def test_metadata_response_limit_and_secret_file_failures_are_redacted(self):
        self.app.state.oidc.http=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*300000))
        self.assertEqual(self.client.get('/v1/auth/oidc/start',follow_redirects=False).status_code,503)
        self.app.state.oidc.http=self.transport;self.start()
        with patch.dict(os.environ,{'OIDC_CLIENT_SECRET_FILE':'Z:/private/missing-client-secret'}):
            response=self.finish();self.assertEqual(response.status_code,503);self.assertNotIn('missing-client-secret',response.text)
    def test_offboarding_disables_background_authority_and_requires_explicit_reactivation(self):
        from platform_api.identity_admin import set_user_active
        from platform_api.workflows.service import WorkflowService
        from platform_api.workflows.schemas import WorkflowError
        self.authenticated();set_user_active(self.engine,self.alice,False)
        self.assertEqual(self.client.get('/v1/me').status_code,401)
        with self.assertRaises(WorkflowError):WorkflowService(self.engine,self.path).actor(self.alice,self.alpha,{'manager'})
        set_user_active(self.engine,self.alice,True)
        self.start();self.assertEqual(self.finish().status_code,401)
        bind_identity(self.engine,self.alice,ISSUER,self.subject);self.authenticated()
    def test_login_admission_budget_also_protects_oidc_start(self):
        self.client.__exit__(None,None,None)
        with patch.dict(os.environ,{'RATE_LIMIT_ENABLED':'1','LOGIN_PEER_LIMIT':'1'}):
            self.app=create_app(self.path,planner=DemoPlanner());self.app.state.rate_limiter.clock=lambda:1800000000
            self.app.state.oidc.http=self.transport
            self.client=TestClient(self.app,base_url='https://app.example.invalid');self.client.__enter__()
            self.start();response=self.client.get('/v1/auth/oidc/start',follow_redirects=False)
            self.assertEqual(response.status_code,429);self.assertIn('Retry-After',response.headers)
    def test_invalid_authentication_requests_never_reflect_passwords_codes_or_provider_errors(self):
        secret='synthetic-sensitive-auth-value-'
        response=self.client.post('/v1/auth/login',json={'username':self.alice,'password':secret*100})
        self.assertEqual(response.status_code,422);self.assertNotIn(secret,response.text)
        self.start();response=self.finish(error=secret*100)
        self.assertEqual(response.status_code,401);self.assertNotIn(secret,response.text)
        response=self.finish(state=secret*100,code=secret*200)
        self.assertEqual(response.status_code,401);self.assertNotIn(secret,response.text)
