"""Add tools_schema to app_catalog (Phase 7 — App Tools Registry)

Revision ID: 019
Revises: 018
Create Date: 2026-04-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_catalog",
        sa.Column("tools_schema", JSONB, server_default=sa.text("'[]'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("app_catalog", "tools_schema")
