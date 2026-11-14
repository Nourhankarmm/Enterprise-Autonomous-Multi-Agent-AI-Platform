import hashlib
import hmac
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, delete
from .domain import assess_order
from .store import MockERP, initialize
from .database import engine_for, audit_events, sessions
from .auth import login, resolve_session, token_hash

class Identity(BaseModel):
    tenant_id: str
    role: str
    subject: str = 'development-token'

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


def create_app(db_path: str | None = None, tokens: dict[str, Identity] | None = None, planner=None) -> FastAPI:
    path = db_path or os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH', '.data/phase1.db')
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

    @asynccontextmanager
    async def lifespan(app):
        # Never mutate PostgreSQL schema/fixtures at server startup.
        if engine.dialect.name == 'sqlite':
            initialize(path)
        with engine.connect() as db:
            db.execute(select(audit_events.c.id).limit(1))
        yield
        engine.dispose()

    app = FastAPI(title='Enterprise AI Operations - Phase 2', lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware('http')
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        if request.url.path in {'/docs', '/redoc', '/docs/oauth2-redirect'}:
            # FastAPI's bundled documentation HTML uses CDN assets and inline initialization.
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    def record(actor, action, resource, details=None):
        with engine.begin() as db:
            db.execute(insert(audit_events).values(id=str(uuid.uuid4()), tenant_id=actor.tenant_id,
                actor_id=actor.subject, action=action, resource=resource,
                occurred_at=datetime.now(timezone.utc).isoformat(), details=details or {}))

    def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Identity:
        if credentials:
            digest = hashlib.sha256(credentials.credentials.encode()).digest()
            for candidate, actor in token_map.items():
                if hmac.compare_digest(digest, candidate):
                    return actor
            user = resolve_session(engine, credentials.credentials)
            if user:
                return Identity(tenant_id=user['tenant_id'], role=user['role'], subject=user['id'])
        raise HTTPException(401, 'Valid session required', headers={'WWW-Authenticate': 'Bearer'})

    def reader(actor: Identity = Depends(identity)) -> Identity:
        if actor.role not in {'manager', 'analyst'}:
            record(actor, 'access.denied', 'procurement')
            raise HTTPException(403, 'Procurement read permission required')
        return actor

    @app.get('/health')
    def health():
        with engine.connect() as db:
            db.execute(select(audit_events.c.id).limit(1))
        return {'status': 'ok', 'mode': 'synthetic', 'database': engine.dialect.name, 'auth': 'local-stub'}

    @app.post('/v1/auth/login')
    def sign_in(body: LoginRequest):
        result = login(engine, body.username, body.password)
        if not result:
            raise HTTPException(401, 'Invalid username or password')
        token, user = result
        actor = Identity(tenant_id=user['tenant_id'], role=user['role'], subject=user['id'])
        record(actor, 'session.created', 'session')
        return {'access_token': token, 'token_type': 'bearer', 'expires_in': 3600, 'identity': actor}

    @app.post('/v1/auth/logout', status_code=204)
    def sign_out(actor: Identity = Depends(identity), credentials=Depends(bearer)):
        with engine.begin() as db:
            db.execute(delete(sessions).where(sessions.c.token_hash == token_hash(credentials.credentials)))
        record(actor, 'session.revoked', 'session')
        return Response(status_code=204)

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
        items = [assess_order(order, erp.contract(order['supplier_id']), as_of) for order in erp.orders()]
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

    web = Path(__file__).resolve().parents[1] / 'web'
    app.mount('/assets', StaticFiles(directory=web), name='assets')

    @app.get('/', include_in_schema=False)
    def home():
        return FileResponse(web / 'index.html')

    return app

app = create_app()
