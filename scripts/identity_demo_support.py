"""Synthetic local Keycloak setup and HTTP browser-flow support; never prints credentials."""
import json,os,secrets,subprocess,time,uuid
from html.parser import HTMLParser
from urllib.parse import urlsplit
import requests

FILES=['docker','compose','-f','compose.yaml','-f','compose.oidc.yaml']
def compose_identity(*args,**kwargs):return subprocess.run([*FILES,*args],check=True,timeout=240,**kwargs)

def safe_http(client,method,url,**kwargs):
    try:return client.request(method,url,**kwargs)
    except requests.RequestException:raise RuntimeError('Local identity HTTP request failed') from None

def local_browser_cookies(browser,url):
    # Browsers treat localhost as a secure context; requests' cookie jar does not.
    # Emulate that exception only for this explicit loopback test provider. Never alter cookie flags.
    parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname!='localhost':raise RuntimeError('Loopback cookie fixture requires localhost')
    pairs=[]
    for cookie in browser.cookies:
        if cookie.domain.lstrip('.') in {'localhost','localhost.local'} and parsed.path.startswith(cookie.path or '/'):
            pairs.append(cookie.name+'='+cookie.value)
    return '; '.join(pairs)

class LoginForm(HTMLParser):
    def __init__(self):super().__init__();self.action=None;self.values={};self.selected=False
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if tag=='form' and attributes.get('id')=='kc-form-login':self.action=attributes.get('action');self.selected=True
        if tag=='input' and self.selected and attributes.get('type')=='hidden' and attributes.get('name'):
            self.values[attributes['name']]=attributes.get('value','')
    def handle_endtag(self,tag):
        if tag=='form':self.selected=False

class LocalIdentityDemo:
    def __init__(self,admin_password,base='http://localhost:18080',app='http://127.0.0.1:18000'):
        self.base=base.rstrip('/');self.app=app.rstrip('/');self.issuer=self.base+'/realms/platform';self.admin_password=admin_password
        self.setup=requests.Session();self.setup.trust_env=False
        for url in (self.base,self.app):
            parsed=urlsplit(url)
            if parsed.scheme!='http' or parsed.hostname not in {'localhost','127.0.0.1'} or parsed.username or parsed.password:
                raise RuntimeError('Identity demo is restricted to loopback HTTP fixtures')
    def wait(self):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            try:
                response=self.setup.get(self.issuer+'/.well-known/openid-configuration',timeout=3,allow_redirects=False)
                if response.status_code==200:return
            except requests.RequestException:pass
            time.sleep(1)
        raise RuntimeError('Local identity provider did not become ready')
    def admin(self):
        response=self.setup.post(self.base+'/realms/master/protocol/openid-connect/token',data={
            'client_id':'admin-cli','grant_type':'password','username':'demo-admin','password':self.admin_password},timeout=10,allow_redirects=False)
        if response.status_code!=200:raise RuntimeError('Local identity demo admin setup failed')
        self.admin_headers={'Authorization':'Bearer '+response.json()['access_token']}
    def provision(self,password=None,link=True,role='analyst'):
        username='demo-'+uuid.uuid4().hex[:12];password=password or secrets.token_urlsafe(32)
        response=self.setup.post(self.base+'/admin/realms/platform/users',headers=self.admin_headers,json={
            'username':username,'enabled':True,'firstName':'Synthetic','lastName':'Demo','email':username+'@example.invalid',
            'emailVerified':True,'requiredActions':[],'credentials':[{'type':'password','value':password,'temporary':False}]},timeout=10,allow_redirects=False)
        if response.status_code!=201:raise RuntimeError('Synthetic identity creation failed')
        location=response.headers.get('Location','')
        if not location.startswith(self.base+'/admin/realms/platform/users/'):raise RuntimeError('Unexpected identity location')
        subject=urlsplit(location).path.rsplit('/',1)[-1]
        if link:
            bootstrap='''
import json,os,sys,secrets,uuid
from sqlalchemy import insert
from platform_api.database import engine_for,users
from platform_api.auth import password_hash
from platform_api.identity_admin import bind_identity
c=json.load(sys.stdin);engine=engine_for(os.environ['DATABASE_URL'])
with engine.begin() as db:
    db.execute(insert(users).values(id=str(uuid.uuid4()),username=c['username'],tenant_id='alpha',role=c['role'],active=True,password_hash=password_hash(secrets.token_urlsafe(48))))
bind_identity(engine,c['username'],c['issuer'],c['subject'],True)
engine.dispose()
'''
            compose_identity('exec','-T','api','python','-c',bootstrap,input=json.dumps({'username':username,
                'role':role,'issuer':self.issuer,'subject':subject}),text=True)
        return username,password
    def browser_login(self,username,password):
        browser=requests.Session();browser.trust_env=False
        start=browser.get(self.app+'/v1/auth/oidc/start',allow_redirects=False,timeout=15)
        if start.status_code!=303:raise RuntimeError('Application OIDC start failed')
        location=start.headers.get('Location','')
        if not location.startswith(self.issuer+'/'):raise RuntimeError('Unexpected authorization endpoint')
        response=browser.get(location,timeout=15,allow_redirects=False)
        if response.status_code!=200 or len(response.content)>262144:raise RuntimeError('Synthetic IdP login form unavailable')
        form=LoginForm();form.feed(response.text)
        if not form.action or not form.action.startswith(self.issuer+'/'):raise RuntimeError('Unexpected IdP login action')
        headers={'Cookie':local_browser_cookies(browser,form.action)} if urlsplit(form.action).hostname=='localhost' else {}
        result=safe_http(browser,'POST',form.action,data=form.values|{'username':username,'password':password},headers=headers,timeout=15,allow_redirects=False)
        if result.status_code not in {302,303}:raise RuntimeError('Synthetic IdP authentication did not finish')
        callback=result.headers.get('Location','')
        if not callback.startswith(self.app+'/v1/auth/oidc/callback?'):raise RuntimeError('Unexpected OIDC callback')
        completed=safe_http(browser,'GET',callback,timeout=20,allow_redirects=False)
        return browser,completed,callback
