"""add logo placements to presets

Revision ID: f3b2c3d4e5f2
Revises: f2b2c3d4e5f1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f3b2c3d4e5f2"
down_revision: Union[str, None] = "f2b2c3d4e5f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("presets", sa.Column("logo_placements", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("presets", "logo_placements")
