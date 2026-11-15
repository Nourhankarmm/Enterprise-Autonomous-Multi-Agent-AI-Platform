"""Relational schema with composite tenant ownership constraints."""
from pathlib import Path
from sqlalchemy import (MetaData, Table, Column, String, Integer, Float, Boolean,
                        JSON, Text, LargeBinary, UniqueConstraint, ForeignKeyConstraint, CheckConstraint, create_engine, event)

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


operations = Table('operations', metadata,
    Column('tenant_id', String, primary_key=True), Column('id', String, primary_key=True),
    Column('requester_id', String, nullable=False), Column('goal', String, nullable=False),
    Column('state', String, nullable=False), Column('plan_version', Integer, nullable=False),
    Column('plan', JSON, nullable=False), Column('plan_hash', String, nullable=False),
    Column('planner_provider', String, nullable=False), Column('planner_model', String),
    Column('planner_usage', JSON, nullable=False), Column('replans', Integer, nullable=False),
    Column('result', JSON), Column('error_code', String), Column('lease_token', String),
    Column('lease_until', Float), Column('deadline_at', Float),
    Column('created_at', String, nullable=False), Column('updated_at', String, nullable=False),
    ForeignKeyConstraint(['tenant_id'], ['tenants.id']))
agent_tasks = Table('agent_tasks', metadata,
    Column('tenant_id', String, primary_key=True), Column('operation_id', String, primary_key=True),
    Column('task_id', String, primary_key=True), Column('capability', String, nullable=False),
    Column('state', String, nullable=False), Column('attempts', Integer, nullable=False),
    Column('output', JSON), Column('error_code', String),
    ForeignKeyConstraint(['tenant_id','operation_id'], ['operations.tenant_id','operations.id']))
operation_events = Table('operation_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True), Column('tenant_id', String, nullable=False),
    Column('operation_id', String, nullable=False), Column('task_id', String),
    Column('event_type', String, nullable=False), Column('occurred_at', String, nullable=False),
    Column('details', JSON, nullable=False),
    ForeignKeyConstraint(['tenant_id','operation_id'], ['operations.tenant_id','operations.id']))


document_families = Table('document_families',metadata,
    Column('tenant_id',String,primary_key=True),Column('contract_ref',String,primary_key=True),
    Column('owner_id',String,nullable=False),Column('supplier_id',String,nullable=False),
    UniqueConstraint('tenant_id','contract_ref','owner_id','supplier_id',name='document_family_owner_unique'),
    ForeignKeyConstraint(['tenant_id','supplier_id'],['suppliers.tenant_id','suppliers.id']))
documents = Table('documents', metadata,
    Column('tenant_id',String,primary_key=True),Column('id',String,primary_key=True),
    Column('owner_id',String,nullable=False),Column('supplier_id',String,nullable=False),
    Column('contract_ref',String,nullable=False),Column('version',Integer,nullable=False),
    Column('title',String,nullable=False),Column('checksum',String,nullable=False),
    Column('content',LargeBinary,nullable=False),Column('byte_size',Integer,nullable=False),
    Column('page_count',Integer,nullable=False),Column('state',String,nullable=False),
    Column('revision',Integer,nullable=False),Column('extraction',JSON,nullable=False),
    Column('approved_terms',JSON),Column('order_ids',JSON,nullable=False),
    Column('effective_from',String),Column('effective_to',String),Column('created_at',String,nullable=False),
    UniqueConstraint('tenant_id','contract_ref','version',name='document_version_unique'),
    ForeignKeyConstraint(['tenant_id','contract_ref','owner_id','supplier_id'],['document_families.tenant_id','document_families.contract_ref','document_families.owner_id','document_families.supplier_id']),
    ForeignKeyConstraint(['tenant_id','supplier_id'],['suppliers.tenant_id','suppliers.id']),
    CheckConstraint('version >= 1 AND revision >= 1',name='valid_document_version'))
document_acl = Table('document_acl',metadata,
    Column('tenant_id',String,primary_key=True),Column('document_id',String,primary_key=True),
    Column('subject_id',String,primary_key=True),
    ForeignKeyConstraint(['tenant_id','document_id'],['documents.tenant_id','documents.id']))
document_pages = Table('document_pages',metadata,
    Column('tenant_id',String,primary_key=True),Column('document_id',String,primary_key=True),
    Column('page',Integer,primary_key=True),Column('text',Text,nullable=False),Column('text_hash',String,nullable=False),
    ForeignKeyConstraint(['tenant_id','document_id'],['documents.tenant_id','documents.id']))
document_chunks = Table('document_chunks',metadata,
    Column('tenant_id',String,primary_key=True),Column('document_id',String,primary_key=True),
    Column('chunk_id',String,primary_key=True),Column('page',Integer,nullable=False),
    Column('start_offset',Integer,nullable=False),Column('end_offset',Integer,nullable=False),
    Column('text',Text,nullable=False),Column('text_hash',String,nullable=False),
    ForeignKeyConstraint(['tenant_id','document_id','page'],['document_pages.tenant_id','document_pages.document_id','document_pages.page']))
contract_sources = Table('contract_sources',metadata,
    Column('tenant_id',String,primary_key=True),Column('supplier_id',String,primary_key=True),
    Column('document_id',String,nullable=False),Column('owner_id',String,nullable=False),
    ForeignKeyConstraint(['tenant_id','supplier_id'],['suppliers.tenant_id','suppliers.id']),
    ForeignKeyConstraint(['tenant_id','document_id'],['documents.tenant_id','documents.id']))

def database_url(path):
    if '://' in path:
        return path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return 'sqlite:///' + str(Path(path).resolve()).replace('\\', '/')

def engine_for(path):
    url = database_url(path)
    args = {'connect_timeout': 5, 'options': '-c statement_timeout=5000'} if url.startswith('postgresql') else {'timeout': 5}
    engine = create_engine(url, pool_pre_ping=True, connect_args=args)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
    return engine
