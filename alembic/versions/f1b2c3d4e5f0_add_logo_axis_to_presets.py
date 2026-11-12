"""add logo_axis to presets

Revision ID: f1b2c3d4e5f0
Revises: e1b2c3d4e5f9
Create Date: 2026-11-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f1b2c3d4e5f0'
down_revision: Union[str, None] = 'e1b2c3d4e5f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'presets',
        sa.Column('logo_axis', sa.String(length=32), nullable=False, server_default='top_left'),
    )


def downgrade() -> None:
    op.drop_column('presets', 'logo_axis')
