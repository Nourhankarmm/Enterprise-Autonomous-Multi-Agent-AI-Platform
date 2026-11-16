"""Local authentication stub, not enterprise OIDC/SSO."""
import hashlib
import hmac
import secrets
import time
from sqlalchemy import select, insert, delete
from .database import users, sessions,oidc_identities

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000)
    return salt + ':' + digest.hex()

def verify_password(password, stored):
    return hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

DUMMY_HASH = password_hash('invalid', '00' * 16)

def login(engine, username, password):
    with engine.begin() as db:
        user = db.execute(select(users).where(users.c.username == username)).mappings().first()
        valid = verify_password(password, user['password_hash'] if user else DUMMY_HASH)
        if not user or not valid or not user['active']:
            return None
        token = secrets.token_urlsafe(32)
        db.execute(delete(sessions).where(sessions.c.expires_at <= time.time()))
        db.execute(insert(sessions).values(token_hash=token_hash(token), user_id=user['id'], expires_at=time.time()+3600))
        return token, dict(user)

def resolve_session(engine, token,auth_method='local'):
    with engine.connect() as db:
        query=select(users).join(sessions,sessions.c.user_id==users.c.id)
        if auth_method=='oidc':
            query=query.join(oidc_identities,sessions.c.oidc_identity_id==oidc_identities.c.id).where(
                oidc_identities.c.active.is_(True),oidc_identities.c.user_id==users.c.id)
        row = db.execute(query.where(
            sessions.c.token_hash == token_hash(token), sessions.c.expires_at > time.time(),
            sessions.c.auth_method==auth_method,users.c.active.is_(True))).mappings().first()
        return dict(row) if row else None
