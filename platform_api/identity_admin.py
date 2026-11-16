"""Privileged operator identity linking. No account matching by email or provider roles."""
import uuid
from sqlalchemy import select,insert,delete,update
from .database import users,oidc_identities,sessions
from .oidc import checked_url

def bind_identity(engine,username,issuer,subject,allow_http=False):
    issuer=checked_url(issuer,allow_http)
    if not subject or not 1<=len(subject)<=255:raise ValueError('Explicit provider subject required')
    with engine.begin() as db:
        user=db.execute(select(users).where(users.c.username==username,users.c.active.is_(True))).mappings().first()
        if not user:raise ValueError('Active local user required')
        existing=db.execute(select(oidc_identities).where(oidc_identities.c.user_id==user['id'])).mappings().first()
        claimed=db.execute(select(oidc_identities).where(oidc_identities.c.issuer==issuer,oidc_identities.c.subject==subject)).mappings().first()
        if claimed and claimed['user_id']!=user['id']:raise ValueError('External subject is already bound')
        if existing:
            if existing['issuer']!=issuer or existing['subject']!=subject:raise ValueError('Local user already has a different external identity')
            if existing['active']:return
            db.execute(delete(sessions).where(sessions.c.user_id==user['id']))
            db.execute(update(oidc_identities).where(oidc_identities.c.id==existing['id']).values(active=True))
        else:
            db.execute(delete(sessions).where(sessions.c.user_id==user['id']))
            db.execute(insert(oidc_identities).values(id=str(uuid.uuid4()),issuer=issuer,subject=subject,user_id=user['id'],active=True))

def revoke_sessions(engine,username,disable_identity=False):
    with engine.begin() as db:
        subject=db.execute(select(users.c.id).where(users.c.username==username)).scalar_one_or_none()
        if not subject:raise ValueError('Local user not found')
        db.execute(delete(sessions).where(sessions.c.user_id==subject))
        if disable_identity:db.execute(update(oidc_identities).where(oidc_identities.c.user_id==subject).values(active=False))

def set_user_active(engine,username,active):
    with engine.begin() as db:
        subject=db.execute(select(users.c.id).where(users.c.username==username)).scalar_one_or_none()
        if not subject:raise ValueError('Local user not found')
        db.execute(delete(sessions).where(sessions.c.user_id==subject))
        db.execute(update(users).where(users.c.id==subject).values(active=active))
        if not active:db.execute(update(oidc_identities).where(oidc_identities.c.user_id==subject).values(active=False))
