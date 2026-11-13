"""Create app_instances table (Phase 6 — Bloc F)

Revision ID: 018
Revises: 017
Create Date: 2026-04-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_instances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("app_id", UUID(as_uuid=True),
                  sa.ForeignKey("app_catalog.id"),
                  nullable=False),
        sa.Column("container_name", sa.VARCHAR(200), nullable=False),
        # pulling | starting | running | stopped | crashed | error
        sa.Column("status", sa.VARCHAR(20), server_default="stopped", nullable=False),
        sa.Column("env_values", sa.Text, nullable=True),   # JSON chiffré Fernet
        sa.Column("custom_mounts", JSONB, server_default=sa.text("'[]'")),
        sa.Column("container_ip", sa.VARCHAR(50), nullable=True),
        sa.Column("installed_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id"),
                  nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("stopped_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("workspace_id", "app_id", name="uq_app_instances_workspace_app"),
    )
    op.create_index("ix_app_instances_workspace_id", "app_instances", ["workspace_id"])
    op.create_index("ix_app_instances_status", "app_instances", ["status"])


def downgrade() -> None:
    op.drop_index("ix_app_instances_status", table_name="app_instances")
    op.drop_index("ix_app_instances_workspace_id", table_name="app_instances")
    op.drop_table("app_instances")
