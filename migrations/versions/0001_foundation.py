"""Phase 1 relational foundation, frozen schema revision."""
from alembic import op
import sqlalchemy as sa
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('tenants', sa.Column('id', sa.String(), primary_key=True))
    for name in ('suppliers', 'projects'):
        op.create_table(name, sa.Column('tenant_id', sa.String(), primary_key=True),
            sa.Column('id', sa.String(), primary_key=True),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']))
    op.create_table('purchase_orders',
        sa.Column('tenant_id', sa.String(), primary_key=True), sa.Column('id', sa.String(), primary_key=True),
        sa.Column('supplier_id', sa.String(), nullable=False), sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False), sa.Column('delivered_quantity', sa.Float(), nullable=False),
        sa.Column('due_date', sa.String(), nullable=False), sa.Column('critical', sa.Boolean(), nullable=False),
        sa.CheckConstraint('quantity >= 0 AND delivered_quantity >= 0', name='nonnegative_quantities'),
        sa.ForeignKeyConstraint(['tenant_id', 'supplier_id'], ['suppliers.tenant_id', 'suppliers.id']),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id']))
    op.create_table('contracts',
        sa.Column('tenant_id', sa.String(), primary_key=True), sa.Column('supplier_id', sa.String(), primary_key=True),
        sa.Column('id', sa.String(), nullable=False), sa.Column('grace_days', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('location', sa.String(), nullable=False),
        sa.CheckConstraint('grace_days >= 0 AND version >= 1', name='valid_contract_terms'),
        sa.ForeignKeyConstraint(['tenant_id', 'supplier_id'], ['suppliers.tenant_id', 'suppliers.id']))
    op.create_table('users', sa.Column('id', sa.String(), primary_key=True),
        sa.Column('username', sa.String(), unique=True, nullable=False), sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False), sa.Column('role', sa.String(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False), sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']))
    op.create_table('sessions', sa.Column('token_hash', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False), sa.Column('expires_at', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']))
    op.create_table('audit_events', sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), nullable=False), sa.Column('actor_id', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False), sa.Column('resource', sa.String(), nullable=False),
        sa.Column('occurred_at', sa.String(), nullable=False), sa.Column('details', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']))

def downgrade():
    for name in ('audit_events', 'sessions', 'users', 'contracts', 'purchase_orders', 'projects', 'suppliers', 'tenants'):
        op.drop_table(name)
