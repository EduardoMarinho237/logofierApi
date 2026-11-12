"""add logo_axis_rest to presets

Revision ID: f2b2c3d4e5f1
Revises: f1b2c3d4e5f0
Create Date: 2026-11-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2b2c3d4e5f1'
down_revision: Union[str, None] = 'f1b2c3d4e5f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'presets',
        sa.Column('logo_axis_rest', sa.String(length=32), nullable=False, server_default='top_left'),
    )


def downgrade() -> None:
    op.drop_column('presets', 'logo_axis_rest')
