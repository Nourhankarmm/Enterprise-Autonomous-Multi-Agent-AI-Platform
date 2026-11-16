"""Bounded authorization-code OIDC client; no identity/role inference from email or claims."""
import base64,hashlib,hmac,json,math,os,secrets,time,uuid
from dataclasses import dataclass
from urllib.parse import urlsplit,urlunsplit,urlencode
import httpx,jwt
from sqlalchemy import select,insert,delete,func
from .database import oidc_flows,oidc_identities,sessions,users,audit_events
from .auth import token_hash
from .security import secret_value,positive_setting
from .agents.schemas import canonical_hash
from datetime import datetime,timezone

SESSION_COOKIE='platform_session'
FLOW_COOKIE='platform_oidc_flow'
ROLES={'manager','analyst','approver','auditor'}

class OIDCError(Exception):
    def __init__(self,status=401):self.status=status

def origin(url):
    parsed=urlsplit(url)
    host=parsed.hostname.lower() if parsed.hostname else ''
    host='['+host+']' if ':' in host else host
    port=parsed.port
    return parsed.scheme+'://'+host+((':'+str(port)) if port and port!=({'https':443,'http':80}.get(parsed.scheme)) else '')

def checked_url(url,allow_http=False,origin_only=False):
    if not isinstance(url,str):raise RuntimeError('Invalid trusted OIDC URL configuration')
    try:
        parsed=urlsplit(url);port=parsed.port
        if (not url or len(url)>2048 or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(c.isspace() or c=='\\' for c in url)):raise ValueError()
        if parsed.scheme!='https' and not (allow_http and parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','keycloak'}):raise ValueError()
        if origin_only and parsed.path not in {'','/'}:raise ValueError()
    except ValueError:raise RuntimeError('Invalid trusted OIDC URL configuration') from None
    return url.rstrip('/')

@dataclass(frozen=True)
class OIDCConfig:
    issuer:str
    client_id:str
    redirect_uri:str
    allow_http:bool=False
    backchannel_origin:str=''
    required_acr:str=''
    max_auth_age:int=3600
    @property
    def secure_cookie(self):return self.redirect_uri.startswith('https://')
    @property
    def fingerprint(self):return canonical_hash(self.__dict__)
    @classmethod
    def from_environment(cls):
        allow_http=os.getenv('OIDC_ALLOW_HTTP')=='1'
        if allow_http and os.getenv('DEPLOYMENT_MODE','demo')!='demo':raise RuntimeError('HTTP OIDC is restricted to the local demo')
        issuer=checked_url(os.getenv('OIDC_ISSUER',''),allow_http)
        redirect=checked_url(os.getenv('OIDC_REDIRECT_URI',''),allow_http)
        if urlsplit(redirect).path!='/v1/auth/oidc/callback':raise RuntimeError('OIDC redirect URI must use the fixed callback path')
        client=os.getenv('OIDC_CLIENT_ID','')
        if not client or len(client)>200 or any(c.isspace() for c in client):raise RuntimeError('Explicit OIDC client ID required')
        back=os.getenv('OIDC_BACKCHANNEL_ORIGIN','')
        if back:
            if not allow_http:raise RuntimeError('OIDC backchannel override is local-demo only')
            back=checked_url(back,True,True)
            if urlsplit(back).hostname!='keycloak':raise RuntimeError('Local OIDC backchannel must be the Keycloak service')
        acr=os.getenv('OIDC_REQUIRED_ACR','')
        if len(acr)>200 or any(c.isspace() for c in acr):raise RuntimeError('OIDC_REQUIRED_ACR must be one exact assurance value')
        return cls(issuer,client,redirect,allow_http,back,acr,positive_setting('OIDC_AUTH_MAX_AGE_SECONDS',3600))

def csrf_token(session):return token_hash('csrf\0'+session)

class OIDCService:
    def __init__(self,engine,config,http=None,clock=time.time):
        self.engine,self.config,self.http,self.clock=engine,config,http,clock
    def _url(self,url):
        checked_url(url,self.config.allow_http)
        if origin(url)!=origin(self.config.issuer):raise OIDCError(503)
        if self.config.backchannel_origin:
            parsed=urlsplit(url);back=urlsplit(self.config.backchannel_origin)
            return urlunsplit((back.scheme,back.netloc,parsed.path,'',''))
        return url
    def _json(self,method,url,**kwargs):
        try:
            with httpx.Client(timeout=5,follow_redirects=False,trust_env=False,transport=self.http) as client:
                with client.stream(method,self._url(url),**kwargs) as response:
                    if response.status_code!=200:raise OIDCError(503)
                    body=bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body)>262144:raise OIDCError(503)
            result=json.loads(body)
            if not isinstance(result,dict):raise OIDCError(503)
            return result
        except (httpx.HTTPError,ValueError,RuntimeError):raise OIDCError(503) from None
    def metadata(self):
        metadata=self._json('GET',self.config.issuer+'/.well-known/openid-configuration')
        if metadata.get('issuer')!=self.config.issuer:raise OIDCError(503)
        for key in ('authorization_endpoint','token_endpoint','jwks_uri'):
            try:self._url(metadata.get(key,''))
            except RuntimeError:raise OIDCError(503) from None
        for field,required in (('response_types_supported','code'),('id_token_signing_alg_values_supported','RS256'),('code_challenge_methods_supported','S256')):
            if not isinstance(metadata.get(field),list) or required not in metadata[field]:raise OIDCError(503)
        return metadata
    def begin(self):
        metadata=self.metadata();state=secrets.token_urlsafe(32);browser=secrets.token_urlsafe(32)
        nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with self.engine.begin() as db:
            db.execute(delete(oidc_flows).where(oidc_flows.c.expires_at<=self.clock()))
            if db.execute(select(func.count()).select_from(oidc_flows)).scalar_one()>=1000:raise OIDCError(503)
            db.execute(insert(oidc_flows).values(state_hash=token_hash(state),browser_hash=token_hash(browser),nonce_hash=token_hash(nonce),
                pkce_verifier=verifier,configuration_hash=self.config.fingerprint,expires_at=self.clock()+300))
        query={'client_id':self.config.client_id,'redirect_uri':self.config.redirect_uri,'response_type':'code','scope':'openid',
            'state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256','max_age':self.config.max_auth_age}
        if self.config.required_acr:query['acr_values']=self.config.required_acr
        return metadata['authorization_endpoint']+'?'+urlencode(query),browser
    def finish(self,state,browser,code,error=None):
        if not state or not browser or len(state)>200 or len(browser)>200:raise OIDCError()
        with self.engine.begin() as db:
            flow=db.execute(delete(oidc_flows).where(oidc_flows.c.state_hash==token_hash(state),
                oidc_flows.c.browser_hash==token_hash(browser),oidc_flows.c.expires_at>self.clock(),
                oidc_flows.c.configuration_hash==self.config.fingerprint).returning(*oidc_flows.c)).mappings().first()
        if not flow or error is not None or not code or len(code)>4096:raise OIDCError()
        metadata=self.metadata()
        payload={'grant_type':'authorization_code','code':code,'redirect_uri':self.config.redirect_uri,
            'client_id':self.config.client_id,'code_verifier':flow['pkce_verifier']}
        try:secret=secret_value('OIDC_CLIENT_SECRET')
        except RuntimeError:raise OIDCError(503) from None
        auth=httpx.BasicAuth(self.config.client_id,secret) if secret else None
        tokens=self._json('POST',metadata['token_endpoint'],data=payload,auth=auth)
        encoded=tokens.get('id_token')
        if not isinstance(encoded,str) or len(encoded)>32768:raise OIDCError()
        jwks=self._json('GET',metadata['jwks_uri'])
        claims=self.validate_token(encoded,jwks,flow['nonce_hash'])
        with self.engine.begin() as db:
            identity=db.execute(select(oidc_identities,users.c.tenant_id,users.c.role,users.c.active.label('user_active'))
                .join(users,users.c.id==oidc_identities.c.user_id).where(oidc_identities.c.issuer==self.config.issuer,
                    oidc_identities.c.subject==claims['sub'],oidc_identities.c.active.is_(True),users.c.active.is_(True))).mappings().first()
            if not identity or identity['role'] not in ROLES:raise OIDCError()
            session=secrets.token_urlsafe(32);expires=min(self.clock()+3600,claims['exp'])
            db.execute(delete(sessions).where(sessions.c.expires_at<=self.clock()))
            db.execute(insert(sessions).values(token_hash=token_hash(session),user_id=identity['user_id'],expires_at=expires,
                auth_method='oidc',oidc_identity_id=identity['id']))
            db.execute(insert(audit_events).values(id=str(uuid.uuid4()),tenant_id=identity['tenant_id'],actor_id=identity['user_id'],
                action='session.oidc_created',resource='session',occurred_at=datetime.now(timezone.utc).isoformat(),details={'auth_method':'oidc'}))
        return session,max(1,int(expires-self.clock()))
    def validate_token(self,encoded,jwks,nonce_hash):
        try:
            header=jwt.get_unverified_header(encoded)
            if header.get('alg')!='RS256' or not isinstance(header.get('kid'),str) or len(header['kid'])>200 or header.get('crit'):raise ValueError()
            keys=jwks.get('keys',[])
            if not isinstance(keys,list) or len(keys)>50:raise ValueError()
            matches=[k for k in keys if k.get('kid')==header['kid'] and k.get('kty')=='RSA' and k.get('use','sig')=='sig'
                and k.get('alg','RS256')=='RS256' and ('key_ops' not in k or 'verify' in k['key_ops'])]
            if len(matches)!=1:raise ValueError()
            if (not isinstance(matches[0].get('n'),str) or len(matches[0]['n'])>2048 or
                    not isinstance(matches[0].get('e'),str) or len(matches[0]['e'])>16):raise ValueError()
            key=jwt.PyJWK.from_dict(matches[0],algorithm='RS256').key
            if not 2048<=key.key_size<=8192:raise ValueError()
            claims=jwt.decode(encoded,key,algorithms=['RS256'],audience=self.config.client_id,issuer=self.config.issuer,
                options={'require':['iss','sub','aud','exp','iat','nonce','auth_time']},leeway=0)
            now=self.clock()
            for name in ('exp','iat','auth_time'):
                if type(claims[name]) not in (int,float) or not math.isfinite(claims[name]):raise ValueError()
            if claims['iat']>now+30 or claims['auth_time']>now+30 or now-claims['auth_time']>self.config.max_auth_age+30:raise ValueError()
            if claims['exp']<=claims['iat'] or claims['auth_time']>claims['iat']+30:raise ValueError()
            audiences=claims['aud'];multiple=isinstance(audiences,list) and len(audiences)>1
            if ('azp' in claims or multiple) and claims.get('azp')!=self.config.client_id:raise ValueError()
            if not isinstance(claims['sub'],str) or not 1<=len(claims['sub'])<=255:raise ValueError()
            if not isinstance(claims['nonce'],str) or not hmac.compare_digest(token_hash(claims['nonce']),nonce_hash):raise ValueError()
            if self.config.required_acr and claims.get('acr')!=self.config.required_acr:raise ValueError()
            return claims
        except (jwt.PyJWTError,ValueError,KeyError,TypeError,AttributeError,OverflowError):raise OIDCError() from None
