"""add independent rest logo placements to presets

Revision ID: f4b2c3d4e5f3
Revises: f3b2c3d4e5f2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4b2c3d4e5f3"
down_revision: Union[str, None] = "f3b2c3d4e5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("presets", sa.Column("logo_placements_rest", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("presets", "logo_placements_rest")
