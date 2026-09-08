"""remove_seeded_admin

Remove the hardcoded admin account ("dev@logofier.com.br") that older
migrations seeded with an empty password. This is a security cleanup: only the
empty-password variant is deleted so a legitimately-provisioned admin with a
real password is never touched.

Revision ID: 35c79ca63f3e
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = '35c79ca63f3e'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM users
        WHERE email = 'dev@logofier.com.br'
          AND hashed_password = ''
        """
    )


def downgrade() -> None:
    # Intentionally irreversible: re-seeding a backdoor would be a security hole.
    pass