"""Durable Jenkins orchestration and idempotent run requests."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b8e41d62c091'
down_revision = 'a3f2c9d41b57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pipeline_executions',
        sa.Column('run_id', sa.String(64), sa.ForeignKey('pipeline_runs.id'), primary_key=True),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('request_key', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('nodes', postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint('actor_id', 'request_key', name='uq_execution_request'))


def downgrade():
    op.drop_table('pipeline_executions')
