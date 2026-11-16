"""Explicit fixture import and local user provisioning."""
import argparse
import getpass
import os
import uuid
from sqlalchemy import insert
from .database import engine_for, users
from .auth import password_hash
from .store import initialize

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['seed', 'create-user','bind-oidc','disable-oidc','revoke-sessions','disable-user','activate-user'])
    parser.add_argument('--username')
    parser.add_argument('--tenant')
    parser.add_argument('--issuer')
    parser.add_argument('--subject')
    parser.add_argument('--role', choices=['manager', 'analyst', 'approver', 'auditor'], default='analyst')
    args = parser.parse_args()
    path = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH', '.data/phase1.db')
    if args.command == 'seed':
        initialize(path)
        return
    if args.command in {'bind-oidc','disable-oidc','revoke-sessions','disable-user','activate-user'}:
        if not args.username:parser.error('--username is required')
        engine=engine_for(path)
        try:
            from .identity_admin import bind_identity,revoke_sessions,set_user_active
            if args.command=='bind-oidc':
                if not args.issuer or not args.subject:parser.error('--issuer and --subject are required')
                bind_identity(engine,args.username,args.issuer,args.subject,os.getenv('OIDC_ALLOW_HTTP')=='1' and os.getenv('DEPLOYMENT_MODE','demo')=='demo')
            elif args.command in {'disable-user','activate-user'}:set_user_active(engine,args.username,args.command=='activate-user')
            else:revoke_sessions(engine,args.username,args.command=='disable-oidc')
        finally:engine.dispose()
        print('Identity/session operation completed.');return
    if not args.username or not args.tenant:
        parser.error('--username and --tenant are required')
    password = getpass.getpass('New password (12+ characters): ')
    if len(password) < 12:
        parser.error('Password must contain at least 12 characters')
    engine = engine_for(path)
    try:
        with engine.begin() as db:
            db.execute(insert(users).values(id=str(uuid.uuid4()), username=args.username,
                tenant_id=args.tenant, role=args.role, password_hash=password_hash(password), active=True))
    finally:
        engine.dispose()
    print('User created.')

if __name__ == '__main__':
    main()
