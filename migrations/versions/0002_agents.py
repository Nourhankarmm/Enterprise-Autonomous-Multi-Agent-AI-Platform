"""Persist validated operation plans, bounded task results and public event traces."""
from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('operations',
        sa.Column('tenant_id',sa.String(),primary_key=True),sa.Column('id',sa.String(),primary_key=True),
        sa.Column('requester_id',sa.String(),nullable=False),sa.Column('goal',sa.String(),nullable=False),
        sa.Column('state',sa.String(),nullable=False),sa.Column('plan_version',sa.Integer(),nullable=False),
        sa.Column('plan',sa.JSON(),nullable=False),sa.Column('plan_hash',sa.String(),nullable=False),
        sa.Column('planner_provider',sa.String(),nullable=False),sa.Column('planner_model',sa.String()),
        sa.Column('planner_usage',sa.JSON(),nullable=False),sa.Column('replans',sa.Integer(),nullable=False),
        sa.Column('result',sa.JSON()),sa.Column('error_code',sa.String()),sa.Column('lease_token',sa.String()),
        sa.Column('lease_until',sa.Float()),sa.Column('deadline_at',sa.Float()),
        sa.Column('created_at',sa.String(),nullable=False),sa.Column('updated_at',sa.String(),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'],['tenants.id']))
    op.create_table('agent_tasks',
        sa.Column('tenant_id',sa.String(),primary_key=True),sa.Column('operation_id',sa.String(),primary_key=True),
        sa.Column('task_id',sa.String(),primary_key=True),sa.Column('capability',sa.String(),nullable=False),
        sa.Column('state',sa.String(),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),
        sa.Column('output',sa.JSON()),sa.Column('error_code',sa.String()),
        sa.ForeignKeyConstraint(['tenant_id','operation_id'],['operations.tenant_id','operations.id']))
    op.create_table('operation_events',sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('tenant_id',sa.String(),nullable=False),sa.Column('operation_id',sa.String(),nullable=False),
        sa.Column('task_id',sa.String()),sa.Column('event_type',sa.String(),nullable=False),
        sa.Column('occurred_at',sa.String(),nullable=False),sa.Column('details',sa.JSON(),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','operation_id'],['operations.tenant_id','operations.id']))

def downgrade():
    for name in ('operation_events','agent_tasks','operations'):op.drop_table(name)
