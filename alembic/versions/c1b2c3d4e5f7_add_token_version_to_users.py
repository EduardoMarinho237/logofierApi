"""add token_version to users

Revision ID: c1b2c3d4e5f7
Revises: 35c79ca63f3e
Create Date: 2026-09-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c1b2c3d4e5f7'
down_revision: Union[str, None] = '35c79ca63f3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('token_version', sa.Integer(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    op.drop_column('users', 'token_version')