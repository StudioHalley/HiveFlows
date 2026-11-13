"""Add LLM provider columns to agent_configs (Phase 5 — Bloc B)

Revision ID: 015
Revises: 014
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_configs", sa.Column("llm_provider", sa.VARCHAR(50), nullable=True))
    op.add_column("agent_configs", sa.Column("llm_model", sa.VARCHAR(200), nullable=True))
    op.add_column(
        "agent_configs",
        sa.Column("provider_credential_id", UUID(as_uuid=True),
                  sa.ForeignKey("provider_credentials.id", ondelete="SET NULL"),
                  nullable=True),
    )
    op.add_column(
        "agent_configs",
        sa.Column("fallback_llm_config", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "fallback_llm_config")
    op.drop_column("agent_configs", "provider_credential_id")
    op.drop_column("agent_configs", "llm_model")
    op.drop_column("agent_configs", "llm_provider")
