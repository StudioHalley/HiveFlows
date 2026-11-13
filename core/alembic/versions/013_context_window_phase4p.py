"""Add context_window_size to agent_configs (Phase 4 Bloc P)

Revision ID: 013
Revises: 012
Create Date: 2026-03-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column("context_window_size", sa.Integer, nullable=False, server_default="50"),
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "context_window_size")
