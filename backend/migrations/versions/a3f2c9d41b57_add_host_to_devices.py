"""add host to devices

Revision ID: a3f2c9d41b57
Revises: 7d8cb583176f
Create Date: 2026-11-01 14:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = 'a3f2c9d41b57'
down_revision = '7d8cb583176f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('host', sa.String(length=100), nullable=False,
                                       server_default='', comment='虚拟机所在宿主机，物理机留空'))


def downgrade() -> None:
    op.drop_column('devices', 'host')
