"""Bounded ingress and shared database rate windows; not an edge/WAF replacement."""
import asyncio,hashlib,math,os,threading,time,uuid
from pathlib import Path
from fastapi.responses import JSONResponse
from sqlalchemy import delete,select
from sqlalchemy.exc import OperationalError
from .database import rate_windows

class RateLimited(Exception):
    def __init__(self,retry_after):self.retry_after=retry_after
class LimiterUnavailable(Exception):pass

def positive_setting(name,default):
    try:value=int(os.getenv(name,str(default)))
    except ValueError:raise RuntimeError('Invalid positive integer setting: '+name) from None
    if not 1<=value<=100000:raise RuntimeError('Invalid positive integer setting: '+name)
    return value

def secret_value(name):
    value=os.getenv(name)
    if value:return value.strip()
    path=os.getenv(name+'_FILE')
    if not path:return ''
    try:
        with Path(path).open('rb') as stream:data=stream.read(4097)
        if len(data)>4096:raise ValueError()
        return data.decode('utf-8').strip()
    except (OSError,ValueError):raise RuntimeError('Provider secret file is unavailable or invalid') from None

def validate_deployment(engine,injected_tokens):
    mode=os.getenv('DEPLOYMENT_MODE','demo')
    if mode not in {'demo','restricted'}:raise RuntimeError('DEPLOYMENT_MODE must be demo or restricted')
    if mode=='restricted':
        requirements={
            'PostgreSQL':engine.dialect.name=='postgresql',
            'REQUIRE_HTTPS=1':os.getenv('REQUIRE_HTTPS')=='1',
            'explicit ALLOWED_HOSTS':bool(os.getenv('ALLOWED_HOSTS')),
            'rate limiting enabled':os.getenv('RATE_LIMIT_ENABLED','1')=='1',
            'development tokens disabled':not injected_tokens and os.getenv('ENABLE_DEV_TOKENS')!='1'}
        requirements['enterprise OIDC enabled']=os.getenv('AUTH_MODE')=='oidc'
        requirements['OIDC secret configured']=bool(secret_value('OIDC_CLIENT_SECRET'))
        missing=[k for k,v in requirements.items() if not v]
        if missing:raise RuntimeError('Restricted deployment requirements missing: '+', '.join(missing))

class DatabaseRateLimiter:
    def __init__(self,engine,clock=time.time):
        self.engine,self.clock=engine,clock;self.cleanup_lock=threading.Lock();self.last_cleanup=0
    def consume(self,scope,key,limit,window=60):
        now=self.clock();bucket=int(now//window)*window
        digest=hashlib.sha256((scope+'\0'+str(key)).encode()).hexdigest()
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy.dialects.postgresql import insert as postgres_insert
        factory=postgres_insert if self.engine.dialect.name=='postgresql' else sqlite_insert
        statement=factory(rate_windows).values(scope=scope,key_hash=digest,bucket=bucket,hits=1)
        statement=statement.on_conflict_do_update(index_elements=['scope','key_hash','bucket'],
            set_={'hits':rate_windows.c.hits+1},where=rate_windows.c.hits<limit).returning(rate_windows.c.hits)
        try:
            with self.engine.begin() as db:
                hit=db.execute(statement).scalar_one_or_none()
                if now-self.last_cleanup>=60 and self.cleanup_lock.acquire(blocking=False):
                    try:
                        db.execute(delete(rate_windows).where(rate_windows.c.bucket<int(now)-3600))
                        self.last_cleanup=now
                    finally:self.cleanup_lock.release()
        except OperationalError:raise LimiterUnavailable() from None
        if hit is None:raise RateLimited(max(1,math.ceil(bucket+window-now)))
        return limit-hit

def response_headers(path,request_id):
    csp="default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
    if path in {'/docs','/redoc','/docs/oauth2-redirect'}:
        csp="default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
    return {'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
        'Permissions-Policy':'camera=(), microphone=(), geolocation=()','Content-Security-Policy':csp,
        'Cache-Control':'no-store','X-Request-ID':request_id}

class HardeningMiddleware:
    def __init__(self,app,limiter,enabled=True,require_https=False):
        self.app,self.limiter,self.enabled,self.require_https=app,limiter,enabled,require_https
        self.peer_limit=positive_setting('API_PEER_LIMIT',600)
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        request_id=str(uuid.uuid4());path=scope.get('path','')
        async def secured_send(message):
            if message['type']=='http.response.start':
                fixed={k.lower():v for k,v in response_headers(path,request_id).items()}
                headers=[(k,v) for k,v in message.get('headers',[]) if k.decode().lower() not in fixed]
                headers.extend((k.encode(),v.encode()) for k,v in fixed.items())
                message={**message,'headers':headers}
            await send(message)
        async def reject(status,detail,headers=None):
            await JSONResponse(status_code=status,content={'detail':detail,'request_id':request_id},headers=headers)(scope,receive,secured_send)
        if self.require_https and scope.get('scheme')!='https':return await reject(403,'HTTPS is required')
        if self.enabled and path.startswith('/v1/'):
            peer=(scope.get('client') or ('unknown',0))[0]
            try:await asyncio.to_thread(self.limiter.consume,'peer',peer,self.peer_limit)
            except RateLimited as error:return await reject(429,'Request rate limit reached',{'Retry-After':str(error.retry_after)})
            except LimiterUnavailable:return await reject(503,'Request admission is temporarily unavailable')
        # Multipart PDF has its own 2 MiB bounded body middleware. Other bodies are limited here.
        if scope.get('method') in {'POST','PUT','PATCH'} and path!='/v1/documents':
            body=bytearray()
            while True:
                message=await receive()
                if message['type']=='http.disconnect':return
                body.extend(message.get('body',b''))
                if len(body)>65536:return await reject(413,'Request body exceeds the 64 KiB limit')
                if not message.get('more_body',False):break
            sent=False
            async def replay():
                nonlocal sent
                if not sent:
                    sent=True;return {'type':'http.request','body':bytes(body),'more_body':False}
                return await receive()
            return await self.app(scope,replay,secured_send)
        return await self.app(scope,receive,secured_send)
