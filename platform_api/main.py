import hashlib
import hmac
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from fastapi import Depends, FastAPI, HTTPException, Response, Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, delete
from .domain import assess_order
from .store import MockERP, initialize
from .database import engine_for, audit_events, sessions, document_families,rate_windows,documents,amendment_proposals,active_amendments,oidc_identities,oidc_flows
from .security import (DatabaseRateLimiter,HardeningMiddleware,RateLimited,LimiterUnavailable,
    positive_setting,validate_deployment,response_headers)
from .auth import login, resolve_session, token_hash
from .oidc import OIDCConfig,SESSION_COOKIE,csrf_token,origin

class Identity(BaseModel):
    tenant_id: str
    role: str
    subject: str = 'development-token'

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


def create_app(db_path: str | None = None, tokens: dict[str, Identity] | None = None, planner=None) -> FastAPI:
    path = db_path or os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH', '.data/phase1.db')
    injected_tokens=tokens is not None
    auth_mode=os.getenv('AUTH_MODE','local')
    if auth_mode not in {'local','oidc'}:raise RuntimeError('AUTH_MODE must be local or oidc')
    oidc_config=OIDCConfig.from_environment() if auth_mode=='oidc' else None
    if oidc_config and (injected_tokens or os.getenv('ENABLE_DEV_TOKENS')=='1'):
        raise RuntimeError('OIDC mode forbids development identity injection')
    # Legacy development tokens are opt-in; explicit test injection remains supported.
    if tokens is None:
        tokens = {}
        if os.getenv('ENABLE_DEV_TOKENS') == '1':
            for tenant, env_name in (('alpha', 'DEV_ALPHA_TOKEN'), ('beta', 'DEV_BETA_TOKEN')):
                token = os.getenv(env_name)
                if token:
                    tokens[token] = Identity(tenant_id=tenant, role='manager')
    token_map = {hashlib.sha256(key.encode()).digest(): value for key, value in tokens.items()}
    engine = engine_for(path)
    validate_deployment(engine,injected_tokens)
    rate_enabled=os.getenv('RATE_LIMIT_ENABLED','0' if injected_tokens else '1')=='1'
    limiter=DatabaseRateLimiter(engine)
    login_peer_limit=positive_setting('LOGIN_PEER_LIMIT',20)
    login_account_limit=positive_setting('LOGIN_ACCOUNT_LIMIT',10)
    login_window=positive_setting('LOGIN_WINDOW_SECONDS',300)
    user_limit=positive_setting('AUTH_USER_LIMIT',180)
    plan_limit=positive_setting('PLANNER_USER_LIMIT',6)
    upload_limit=positive_setting('UPLOAD_USER_LIMIT',6)
    search_limit=positive_setting('SEARCH_USER_LIMIT',30)

    @asynccontextmanager
    async def lifespan(app):
        # Never mutate PostgreSQL schema/fixtures at server startup.
        if engine.dialect.name == 'sqlite':
            initialize(path)
        with engine.connect() as db:
            db.execute(select(audit_events.c.id).limit(1))
            db.execute(select(document_families.c.tenant_id).limit(1))
            db.execute(select(rate_windows.c.scope).limit(1))
            db.execute(select(documents.c.kind).limit(1))
            db.execute(select(amendment_proposals.c.id).limit(1))
            db.execute(select(active_amendments.c.amendment_id).limit(1))
            db.execute(select(oidc_identities.c.id).limit(1))
            db.execute(select(oidc_flows.c.state_hash).limit(1))
            db.execute(select(sessions.c.auth_method).limit(1))
        yield
        engine.dispose()

    app = FastAPI(title='Enterprise AI Operations - Phase 6', lifespan=lifespan,
        docs_url=None if os.getenv('DEPLOYMENT_MODE')=='restricted' else '/docs',
        redoc_url=None if os.getenv('DEPLOYMENT_MODE')=='restricted' else '/redoc',
        openapi_url=None if os.getenv('DEPLOYMENT_MODE')=='restricted' else '/openapi.json')
    app.state.rate_limiter=limiter
    bearer = HTTPBearer(auto_error=False)

    @app.exception_handler(RequestValidationError)
    async def safe_auth_validation(request,error):
        if request.url.path.startswith('/v1/auth/'):
            return JSONResponse(status_code=422,content={'detail':'Invalid authentication request'})
        return await request_validation_exception_handler(request,error)

    @app.exception_handler(RateLimited)
    async def limited(request,error):
        return JSONResponse(status_code=429,content={'detail':'Request rate limit reached'},headers={'Retry-After':str(error.retry_after)})
    @app.exception_handler(LimiterUnavailable)
    async def admission_unavailable(request,error):
        return JSONResponse(status_code=503,content={'detail':'Request admission is temporarily unavailable'})
    @app.exception_handler(Exception)
    async def safe_internal_error(request,error):
        request_id=str(uuid.uuid4())
        return JSONResponse(status_code=500,content={'detail':'Internal service error','request_id':request_id},
            headers=response_headers(request.url.path,request_id))

    def record(actor, action, resource, details=None):
        with engine.begin() as db:
            db.execute(insert(audit_events).values(id=str(uuid.uuid4()), tenant_id=actor.tenant_id,
                actor_id=actor.subject, action=action, resource=resource,
                occurred_at=datetime.now(timezone.utc).isoformat(), details=details or {}))

    def identity(request:Request,credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Identity:
        if not credentials and oidc_config:
            cookie=request.cookies.get(SESSION_COOKIE,'')
            if cookie and len(cookie)<=200:
                if request.method not in {'GET','HEAD','OPTIONS'}:
                    claimed=request.headers.get('X-CSRF-Token','')
                    supplied_origin=request.headers.get('Origin')
                    if (len(claimed)>200 or not hmac.compare_digest(claimed,csrf_token(cookie)) or
                            (supplied_origin is not None and supplied_origin!=origin(oidc_config.redirect_uri))):
                        raise HTTPException(403,'Same-origin session confirmation required')
                credentials=HTTPAuthorizationCredentials(scheme='Bearer',credentials=cookie)
        if credentials:
            digest = hashlib.sha256(credentials.credentials.encode()).digest()
            for candidate, actor in token_map.items():
                if hmac.compare_digest(digest, candidate):
                    if rate_enabled:limiter.consume('user',actor.tenant_id+'/'+actor.subject,user_limit)
                    return actor
            user = resolve_session(engine, credentials.credentials,auth_mode)
            if user:
                if rate_enabled:limiter.consume('user',user['tenant_id']+'/'+user['id'],user_limit)
                return Identity(tenant_id=user['tenant_id'], role=user['role'], subject=user['id'])
        raise HTTPException(401, 'Valid session required', headers={'WWW-Authenticate': 'Bearer'})

    def reader(request:Request,actor: Identity = Depends(identity)) -> Identity:
        if actor.role not in {'manager', 'analyst'}:
            record(actor, 'access.denied', 'procurement')
            raise HTTPException(403, 'Procurement read permission required')
        if rate_enabled:
            key=actor.tenant_id+'/'+actor.subject;route=request.url.path
            if request.method=='POST' and (route=='/v1/operations' or route.endswith('/revise')):
                limiter.consume('planning',key,plan_limit)
            elif request.method=='POST' and (route=='/v1/documents' or route.endswith('/ocr')):limiter.consume('pdf_upload',key,upload_limit)
            elif route=='/v1/documents/search':limiter.consume('search',key,search_limit)
        return actor

    @app.get('/health')
    def health():
        with engine.connect() as db:
            db.execute(select(audit_events.c.id).limit(1))
            db.execute(select(document_families.c.tenant_id).limit(1))
            db.execute(select(amendment_proposals.c.id).limit(1))
        return {'status': 'ok', 'mode': 'synthetic', 'database': engine.dialect.name, 'auth': 'oidc' if oidc_config else 'local-stub'}

    @app.post('/v1/auth/login')
    def sign_in(body: LoginRequest,request:Request):
        if oidc_config:raise HTTPException(403,'Local password login is disabled')
        if rate_enabled:
            limiter.consume('login_peer',request.client.host if request.client else 'unknown',login_peer_limit,login_window)
            limiter.consume('login_account',body.username.casefold(),login_account_limit,login_window)
        result = login(engine, body.username, body.password)
        if not result:
            raise HTTPException(401, 'Invalid username or password')
        token, user = result
        actor = Identity(tenant_id=user['tenant_id'], role=user['role'], subject=user['id'])
        record(actor, 'session.created', 'session')
        return {'access_token': token, 'token_type': 'bearer', 'expires_in': 3600, 'identity': actor}

    @app.post('/v1/auth/logout', status_code=204)
    def sign_out(request:Request,actor: Identity = Depends(identity), credentials=Depends(bearer)):
        session_token=credentials.credentials if credentials else request.cookies.get(SESSION_COOKIE,'')
        with engine.begin() as db:
            db.execute(delete(sessions).where(sessions.c.token_hash == token_hash(session_token)))
        record(actor, 'session.revoked', 'session')
        response=Response(status_code=204)
        if oidc_config:response.delete_cookie(SESSION_COOKIE,path='/',httponly=True,secure=oidc_config.secure_cookie,samesite='lax')
        return response

    from .oidc_routes import register as register_oidc
    register_oidc(app,engine,identity,oidc_config,limiter,rate_enabled,login_peer_limit,login_window)

    @app.get('/v1/me')
    def me(actor: Identity = Depends(identity)):
        return actor

    @app.get('/v1/purchase-orders')
    def orders(actor: Identity = Depends(reader)):
        items = MockERP(path, actor.tenant_id).orders()
        record(actor, 'orders.read', 'purchase_orders', {'count': len(items)})
        return {'items': items}

    @app.get('/v1/purchase-orders/{order_id}')
    def order(order_id: str, actor: Identity = Depends(reader)):
        for item in MockERP(path, actor.tenant_id).orders():
            if item['id'] == order_id:
                record(actor, 'order.read', order_id)
                return item
        record(actor, 'order.not_found', 'purchase_order')
        raise HTTPException(404, 'Purchase order not found')

    @app.get('/v1/procurement/assessment')
    def assessment(as_of: date, actor: Identity = Depends(reader)):
        erp = MockERP(path, actor.tenant_id)
        from .documents.intelligence import assess_with_evidence
        items = [assess_with_evidence(order,app.state.documents.resolve(actor,order['supplier_id'],
            order['id'],as_of,erp.contract(order['supplier_id'])),as_of) for order in erp.orders()]
        record(actor, 'assessment.read', 'procurement', {'as_of': as_of.isoformat()})
        return {'items': items}

    @app.get('/v1/audit-events')
    def audit(actor: Identity = Depends(identity)):
        if actor.role not in {'manager', 'auditor'}:
            raise HTTPException(403, 'Audit permission required')
        with engine.connect() as db:
            rows = db.execute(select(audit_events).where(audit_events.c.tenant_id == actor.tenant_id)
                              .order_by(audit_events.c.occurred_at.desc()).limit(100)).mappings()
            return {'items': [dict(row) for row in rows]}

    from .agents.routes import register_agent_routes
    register_agent_routes(app,engine,path,reader,planner)
    from .documents.routes import register_document_routes
    def document_reader(request:Request,actor: Identity = Depends(identity)):
        if actor.role not in {'manager','analyst','approver'}:raise HTTPException(403,'Evidence read permission required')
        if rate_enabled and request.url.path=='/v1/documents/search':
            limiter.consume('search',actor.tenant_id+'/'+actor.subject,search_limit)
        return actor
    register_document_routes(app,engine,reader,document_reader)
    from .phase_eight_routes import register as register_phase_eight
    register_phase_eight(app,engine,path,reader,document_reader)
    from .workflows.routes import register_workflow_routes
    register_workflow_routes(app,engine,path,identity,reader)
    from .dashboard import register_dashboard_routes
    register_dashboard_routes(app,engine,identity)
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    hosts=[h.strip() for h in os.getenv('ALLOWED_HOSTS','localhost,127.0.0.1,testserver').split(',') if h.strip()]
    if not hosts or any('*' in h or '/' in h for h in hosts):raise RuntimeError('ALLOWED_HOSTS must contain exact host names')
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=hosts,www_redirect=False)
    app.add_middleware(HardeningMiddleware,limiter=limiter,enabled=rate_enabled,require_https=os.getenv('REQUIRE_HTTPS')=='1')

    web = Path(__file__).resolve().parents[1] / 'web'
    app.mount('/assets', StaticFiles(directory=web), name='assets')

    @app.get('/', include_in_schema=False)
    def home():
        return FileResponse(web / 'index.html')

    return app

app = create_app()
