"""Relational schema with composite tenant ownership constraints."""
from pathlib import Path
from sqlalchemy import (MetaData, Table, Column, String, Integer, Float, Boolean,
                        JSON, ForeignKeyConstraint, CheckConstraint, create_engine, event)

metadata = MetaData()
tenants = Table('tenants', metadata, Column('id', String, primary_key=True))
suppliers = Table('suppliers', metadata, Column('tenant_id', String, primary_key=True),
                  Column('id', String, primary_key=True),
                  ForeignKeyConstraint(['tenant_id'], ['tenants.id']))
projects = Table('projects', metadata, Column('tenant_id', String, primary_key=True),
                 Column('id', String, primary_key=True),
                 ForeignKeyConstraint(['tenant_id'], ['tenants.id']))
orders = Table('purchase_orders', metadata,
    Column('tenant_id', String, primary_key=True), Column('id', String, primary_key=True),
    Column('supplier_id', String, nullable=False), Column('project_id', String, nullable=False),
    Column('quantity', Float, nullable=False), Column('delivered_quantity', Float, nullable=False),
    Column('due_date', String, nullable=False), Column('critical', Boolean, nullable=False),
    CheckConstraint('quantity >= 0 AND delivered_quantity >= 0', name='nonnegative_quantities'),
    ForeignKeyConstraint(['tenant_id', 'supplier_id'], ['suppliers.tenant_id', 'suppliers.id']),
    ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id']))
contracts = Table('contracts', metadata,
    Column('tenant_id', String, primary_key=True), Column('supplier_id', String, primary_key=True),
    Column('id', String, nullable=False), Column('grace_days', Integer, nullable=False),
    Column('version', Integer, nullable=False), Column('location', String, nullable=False),
    CheckConstraint('grace_days >= 0 AND version >= 1', name='valid_contract_terms'),
    ForeignKeyConstraint(['tenant_id', 'supplier_id'], ['suppliers.tenant_id', 'suppliers.id']))
users = Table('users', metadata,
    Column('id', String, primary_key=True), Column('username', String, unique=True, nullable=False),
    Column('password_hash', String, nullable=False), Column('tenant_id', String, nullable=False),
    Column('role', String, nullable=False), Column('active', Boolean, nullable=False),
    ForeignKeyConstraint(['tenant_id'], ['tenants.id']))
sessions = Table('sessions', metadata, Column('token_hash', String, primary_key=True),
    Column('user_id', String, nullable=False), Column('expires_at', Float, nullable=False),
    ForeignKeyConstraint(['user_id'], ['users.id']))
audit_events = Table('audit_events', metadata,
    Column('id', String, primary_key=True), Column('tenant_id', String, nullable=False),
    Column('actor_id', String, nullable=False), Column('action', String, nullable=False),
    Column('resource', String, nullable=False), Column('occurred_at', String, nullable=False),
    Column('details', JSON, nullable=False),
    ForeignKeyConstraint(['tenant_id'], ['tenants.id']))

def database_url(path):
    if '://' in path:
        return path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return 'sqlite:///' + str(Path(path).resolve()).replace('\\', '/')

def engine_for(path):
    engine = create_engine(database_url(path), pool_pre_ping=True)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
    return engine
