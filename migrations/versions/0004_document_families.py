"""Enforce immutable owner/supplier identity across a contract's document versions."""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('document_families',sa.Column('tenant_id',sa.String(),primary_key=True),
        sa.Column('contract_ref',sa.String(),primary_key=True),sa.Column('owner_id',sa.String(),nullable=False),
        sa.Column('supplier_id',sa.String(),nullable=False),
        sa.UniqueConstraint('tenant_id','contract_ref','owner_id','supplier_id',name='document_family_owner_unique'),
        sa.ForeignKeyConstraint(['tenant_id','supplier_id'],['suppliers.tenant_id','suppliers.id']))
    db=op.get_bind()
    rows=db.execute(sa.text('SELECT DISTINCT tenant_id, contract_ref, owner_id, supplier_id FROM documents')).mappings().all()
    seen=set()
    for row in rows:
        key=(row['tenant_id'],row['contract_ref'])
        if key in seen:raise RuntimeError('Document family ownership conflict requires manual migration review')
        seen.add(key)
        db.execute(sa.text('INSERT INTO document_families (tenant_id,contract_ref,owner_id,supplier_id) VALUES (:tenant_id,:contract_ref,:owner_id,:supplier_id)'),dict(row))
    with op.batch_alter_table('documents') as batch:
        batch.create_foreign_key('document_family_owner_fk','document_families',
            ['tenant_id','contract_ref','owner_id','supplier_id'],['tenant_id','contract_ref','owner_id','supplier_id'])

def downgrade():
    with op.batch_alter_table('documents') as batch:batch.drop_constraint('document_family_owner_fk',type_='foreignkey')
    op.drop_table('document_families')
