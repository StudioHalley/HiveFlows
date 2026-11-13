"""Add provider_credentials table (Phase 5 — Bloc A)

Revision ID: 014
Revises: 013
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("provider", sa.VARCHAR(50), nullable=False),
        sa.Column("display_name", sa.VARCHAR(200), nullable=True),
        sa.Column("credential_data", sa.Text, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP, nullable=False, server_default=sa.text("NOW()")),
    )
    # Index pour accélérer la résolution de credentials par workspace+provider
    op.create_index("ix_provider_credentials_workspace_provider", "provider_credentials", ["workspace_id", "provider"])
    # Contrainte unique: un seul credential `is_default=true` par (workspace, provider)
    op.create_unique_constraint(
        "uq_provider_credentials_workspace_provider_default",
        "provider_credentials",
        ["workspace_id", "provider", "is_default"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_provider_credentials_workspace_provider_default", "provider_credentials")
    op.drop_index("ix_provider_credentials_workspace_provider", "provider_credentials")
    op.drop_table("provider_credentials")
