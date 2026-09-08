"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "uploading", "processing", "done", "failed", name="jobstatus"),
            nullable=False,
        ),
        sa.Column("logo_key", sa.String(500), nullable=False),
        sa.Column("config", sqlite.JSON(), nullable=False),
        sa.Column("source_keys", sqlite.JSON(), nullable=True),
        sa.Column("total_files", sa.Integer, server_default="0"),
        sa.Column("processed_files", sa.Integer, server_default="0"),
        sa.Column("output_zip_key", sa.String(500), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS jobstatus")
