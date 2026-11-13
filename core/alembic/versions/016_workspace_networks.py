"""Create workspace_networks table (Phase 6 — Bloc B)

Revision ID: 016
Revises: 015
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_networks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("network_name", sa.VARCHAR(200), nullable=False),
        sa.Column("subnet", sa.VARCHAR(50), nullable=False),
        sa.Column("gateway", sa.VARCHAR(50), nullable=False),
        sa.Column("docker_network_id", sa.VARCHAR(100), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_workspace_networks_workspace_id", "workspace_networks", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_networks_workspace_id", table_name="workspace_networks")
    op.drop_table("workspace_networks")
