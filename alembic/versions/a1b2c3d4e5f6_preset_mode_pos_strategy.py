"""preset_mode_pos_strategy

Revision ID: a1b2c3d4e5f6
Revises: 15b4c05ba38d
Create Date: 2026-09-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '15b4c05ba38d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'presets',
        sa.Column('mode', sa.String(length=32), nullable=False, server_default='multiple_pdfs'),
    )
    op.add_column(
        'presets',
        sa.Column('pos_strategy', sa.String(length=32), nullable=False, server_default='shared'),
    )


def downgrade() -> None:
    op.drop_column('presets', 'pos_strategy')
    op.drop_column('presets', 'mode')
