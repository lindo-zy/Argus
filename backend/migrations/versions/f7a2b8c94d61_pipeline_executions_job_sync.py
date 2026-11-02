"""动态流水线编排：执行意图表 + 流水线定义的 Job 同步状态。

revision: f7a2b8c94d61
down_revision: a3f2c9d41b57
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f7a2b8c94d61'
down_revision = 'a3f2c9d41b57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pipeline_executions',
        sa.Column('run_id', sa.String(64), sa.ForeignKey('pipeline_runs.id'), primary_key=True),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('request_key', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('nodes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('actor_id', 'request_key', name='uq_execution_request'))
    op.add_column('pipeline_definitions',
        sa.Column('job_sync_state', sa.String(16), nullable=False, server_default=''))
    op.add_column('pipeline_definitions',
        sa.Column('job_synced_hash', sa.String(64), nullable=False, server_default=''))


def downgrade():
    op.drop_column('pipeline_definitions', 'job_synced_hash')
    op.drop_column('pipeline_definitions', 'job_sync_state')
    op.drop_table('pipeline_executions')
