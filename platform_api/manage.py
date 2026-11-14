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
    parser.add_argument('command', choices=['seed', 'create-user'])
    parser.add_argument('--username')
    parser.add_argument('--tenant', choices=['alpha', 'beta'])
    parser.add_argument('--role', choices=['manager', 'analyst', 'auditor'], default='analyst')
    args = parser.parse_args()
    path = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH', '.data/phase1.db')
    if args.command == 'seed':
        initialize(path)
        return
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
