"""动态流水线：执行意图表补 created_at + 流水线定义的 Job 同步状态。

revision: c5d8e71a2f93
down_revision: b8e41d62c091

b8e41d62c091 已创建 pipeline_executions（节点编排）；本迁移在其上增量：
- pipeline_executions.created_at（动态路径的意图创建时间）
- pipeline_definitions.job_sync_state / job_synced_hash（动态 Job 同步状态）
"""
from alembic import op
import sqlalchemy as sa

revision = 'c5d8e71a2f93'
down_revision = 'b8e41d62c091'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pipeline_executions',
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()))
    op.add_column('pipeline_definitions',
        sa.Column('job_sync_state', sa.String(16), nullable=False, server_default=''))
    op.add_column('pipeline_definitions',
        sa.Column('job_synced_hash', sa.String(64), nullable=False, server_default=''))


def downgrade():
    op.drop_column('pipeline_definitions', 'job_synced_hash')
    op.drop_column('pipeline_definitions', 'job_sync_state')
    op.drop_column('pipeline_executions', 'created_at')
