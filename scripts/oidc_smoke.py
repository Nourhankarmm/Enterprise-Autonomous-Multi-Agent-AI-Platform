"""Disposable actual-Keycloak OIDC flow, session, tenant and revocation acceptance."""
import argparse,json,os
from pathlib import Path
from identity_demo_support import LocalIdentityDemo,compose_identity,safe_http

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--disposable',action='store_true');args=parser.parse_args()
    if not args.disposable:parser.error('Use only against an explicitly disposable local Compose stack with --disposable')
    password=os.environ.get('KEYCLOAK_ADMIN_PASSWORD')
    if not password:raise RuntimeError('Local Keycloak admin password configuration required')
    demo=LocalIdentityDemo(password,os.getenv('KEYCLOAK_BASE_URL','http://localhost:18080'),os.getenv('SMOKE_BASE_URL','http://127.0.0.1:18000'))
    demo.wait();demo.admin();username,user_password=demo.provision()
    browser,response,callback=demo.browser_login(username,user_password)
    assert response.status_code==303 and response.headers['Location']=='/'
    identity=browser.get(demo.app+'/v1/me',timeout=10).json()
    assert identity['tenant_id']=='alpha' and identity['role']=='analyst'
    assert browser.get(demo.app+'/v1/purchase-orders/PO-B1',timeout=10).status_code==404
    assert browser.get(demo.app+'/v1/audit-events',timeout=10).status_code==403
    assert safe_http(browser,'GET',callback,timeout=10,allow_redirects=False).status_code==401
    assert browser.get(demo.app+'/v1/me',timeout=10).status_code==200
    session=browser.get(demo.app+'/v1/auth/session',timeout=10).json()
    assert 'csrf_token' in session and 'access_token' not in session
    assert browser.post(demo.app+'/v1/auth/logout',timeout=10).status_code==403
    assert browser.post(demo.app+'/v1/auth/logout',headers={'X-CSRF-Token':session['csrf_token'],'Origin':demo.app},timeout=10).status_code==204
    assert browser.get(demo.app+'/v1/me',timeout=10).status_code==401
    browser,response,_=demo.browser_login(username,user_password);assert response.status_code==303
    compose_identity('exec','-T','api','python','-m','platform_api.manage','revoke-sessions','--username',username)
    assert browser.get(demo.app+'/v1/me',timeout=10).status_code==401
    unknown,unknown_password=demo.provision(link=False)
    _,response,_=demo.browser_login(unknown,unknown_password);assert response.status_code==401
    compose_identity('exec','-T','api','python','-m','platform_api.manage','disable-user','--username',username)
    _,response,_=demo.browser_login(username,user_password);assert response.status_code==401
    compose_identity('stop','keycloak')
    assert browser.get(demo.app+'/v1/auth/oidc/start',timeout=15,allow_redirects=False).status_code==503
    assert browser.post(demo.app+'/v1/auth/login',json={'username':username,'password':user_password},timeout=10).status_code==403
    report={'status':'PASS','source_commit':os.getenv('GITHUB_SHA'),'actual_keycloak_authorization_code_flow':True,
        'keycloak_image':'quay.io/keycloak/keycloak:26.8.2','transport':'loopback HTTP demo; production TLS not tested',
        'server_identity_mapping_and_role_enforced':True,'cross_tenant_access_denied':True,'unmapped_subject_denied':True,
        'callback_replay_denied':True,'csrf_guard_tested':True,'application_logout_and_operator_revocation_tested':True,
        'disabled_user_denied':True,'provider_outage_fails_closed':True,'provider_tokens_returned_to_javascript':False,
        'actual_company_identity_provider_configured':False,'production_ready':False}
    target=Path('.data/phase9-oidc-keycloak.json');target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
