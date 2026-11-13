"""Add users table for Phase 4 auth

Revision ID: 005
Revises: 004
Create Date: 2026-03-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.VARCHAR(255), unique=True, nullable=False),
        sa.Column("display_name", sa.VARCHAR(200), nullable=False),
        sa.Column("hashed_password", sa.VARCHAR(255), nullable=True),
        sa.Column("oauth_provider", sa.VARCHAR(50), nullable=True),
        sa.Column("oauth_id", sa.VARCHAR(255), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("users")
