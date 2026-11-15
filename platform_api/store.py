"""Read-only mock ERP connector on migrated relational data."""
import json
from pathlib import Path
from sqlalchemy import select, insert, inspect
from .database import engine_for, database_url, metadata, tenants, suppliers, projects, orders, contracts

FIXTURE = Path(__file__).resolve().parents[1] / 'evals/datasets/procurement.json'

def seed(engine):
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    with engine.begin() as db:
        for table, rows in (
            (tenants, [{'id': t} for t in sorted({o['tenant_id'] for o in fixture['orders']})]),
            (suppliers, [dict(tenant_id=t, id=s) for t, s in sorted({(o['tenant_id'], o['supplier_id']) for o in fixture['orders']})]),
            (projects, [dict(tenant_id=t, id=p) for t, p in sorted({(o['tenant_id'], o['project_id']) for o in fixture['orders']})]),
            (orders, fixture['orders']), (contracts, fixture['contracts'])):
            for row in rows:
                condition = [c == row[c.name] for c in table.primary_key]
                if db.execute(select(table).where(*condition)).first() is None:
                    db.execute(insert(table).values(**row))

def initialize(path):
    engine = engine_for(path)
    try:
        if engine.dialect.name == 'sqlite':
            inspector = inspect(engine)
            if inspector.has_table('purchase_orders') and 'payload' in {c['name'] for c in inspector.get_columns('purchase_orders')}:
                raise RuntimeError('Legacy SQLite database: use a new DATABASE_PATH; old data is preserved.')
            from alembic import command
            from alembic.config import Config
            config=Config(str(FIXTURE.parents[2] / 'alembic.ini'))
            config.attributes['database_url']=database_url(path)
            if inspector.has_table('purchase_orders') and not inspector.has_table('alembic_version'):
                # Adopt only the exact known Phase 1 create_all bootstrap schema.
                from alembic.autogenerate import compare_metadata
                from alembic.migration import MigrationContext
                from sqlalchemy import MetaData
                baseline=MetaData()
                for name in ('tenants','suppliers','projects','purchase_orders','contracts','users','sessions','audit_events'):
                    metadata.tables[name].to_metadata(baseline)
                with engine.connect() as connection:
                    differences=compare_metadata(MigrationContext.configure(connection),baseline)
                if differences:
                    raise RuntimeError('Unversioned database differs from Phase 1 schema; manual migration required')
                command.stamp(config,'0001')
            command.upgrade(config,'head')
        seed(engine)
    finally:
        engine.dispose()

class MockERP:
    def __init__(self, path, tenant_id):
        self.path, self.tenant_id = path, tenant_id

    def _read(self, statement):
        engine = engine_for(self.path)
        try:
            with engine.connect() as db:
                return [dict(row) for row in db.execute(statement).mappings()]
        finally:
            engine.dispose()

    def orders(self):
        return self._read(select(orders).where(orders.c.tenant_id == self.tenant_id).order_by(orders.c.id))

    def contract(self, supplier_id):
        rows = self._read(select(contracts).where(contracts.c.tenant_id == self.tenant_id,
                                                contracts.c.supplier_id == supplier_id))
        return rows[0] if rows else None
