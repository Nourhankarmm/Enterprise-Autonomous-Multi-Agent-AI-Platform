"""Immutable document versions, ACL-filtered pages/chunks and explicit active sources."""
from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('documents',sa.Column('tenant_id',sa.String(),primary_key=True),sa.Column('id',sa.String(),primary_key=True),
        sa.Column('owner_id',sa.String(),nullable=False),sa.Column('supplier_id',sa.String(),nullable=False),
        sa.Column('contract_ref',sa.String(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('title',sa.String(),nullable=False),sa.Column('checksum',sa.String(),nullable=False),
        sa.Column('content',sa.LargeBinary(),nullable=False),sa.Column('byte_size',sa.Integer(),nullable=False),
        sa.Column('page_count',sa.Integer(),nullable=False),sa.Column('state',sa.String(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),sa.Column('extraction',sa.JSON(),nullable=False),
        sa.Column('approved_terms',sa.JSON()),sa.Column('order_ids',sa.JSON(),nullable=False),
        sa.Column('effective_from',sa.String()),sa.Column('effective_to',sa.String()),sa.Column('created_at',sa.String(),nullable=False),
        sa.UniqueConstraint('tenant_id','contract_ref','version',name='document_version_unique'),
        sa.ForeignKeyConstraint(['tenant_id','supplier_id'],['suppliers.tenant_id','suppliers.id']),
        sa.CheckConstraint('version >= 1 AND revision >= 1',name='valid_document_version'))
    op.create_table('document_acl',sa.Column('tenant_id',sa.String(),primary_key=True),
        sa.Column('document_id',sa.String(),primary_key=True),sa.Column('subject_id',sa.String(),primary_key=True),
        sa.ForeignKeyConstraint(['tenant_id','document_id'],['documents.tenant_id','documents.id']))
    op.create_table('document_pages',sa.Column('tenant_id',sa.String(),primary_key=True),
        sa.Column('document_id',sa.String(),primary_key=True),sa.Column('page',sa.Integer(),primary_key=True),
        sa.Column('text',sa.Text(),nullable=False),sa.Column('text_hash',sa.String(),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','document_id'],['documents.tenant_id','documents.id']))
    op.create_table('document_chunks',sa.Column('tenant_id',sa.String(),primary_key=True),
        sa.Column('document_id',sa.String(),primary_key=True),sa.Column('chunk_id',sa.String(),primary_key=True),
        sa.Column('page',sa.Integer(),nullable=False),sa.Column('start_offset',sa.Integer(),nullable=False),
        sa.Column('end_offset',sa.Integer(),nullable=False),sa.Column('text',sa.Text(),nullable=False),sa.Column('text_hash',sa.String(),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','document_id','page'],['document_pages.tenant_id','document_pages.document_id','document_pages.page']))
    op.create_table('contract_sources',sa.Column('tenant_id',sa.String(),primary_key=True),sa.Column('supplier_id',sa.String(),primary_key=True),
        sa.Column('document_id',sa.String(),nullable=False),sa.Column('owner_id',sa.String(),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','supplier_id'],['suppliers.tenant_id','suppliers.id']),
        sa.ForeignKeyConstraint(['tenant_id','document_id'],['documents.tenant_id','documents.id']))

def downgrade():
    for name in ('contract_sources','document_chunks','document_pages','document_acl','documents'):op.drop_table(name)
