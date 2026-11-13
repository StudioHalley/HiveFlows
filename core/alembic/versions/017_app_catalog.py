"""Create app_catalog table (Phase 6 — Bloc F)

Revision ID: 017
Revises: 016
Create Date: 2026-04-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_catalog",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.VARCHAR(100), nullable=False),
        sa.Column("slug", sa.VARCHAR(100), unique=True, nullable=False),
        sa.Column("version", sa.VARCHAR(20), nullable=False, server_default="1.0.0"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("image", sa.VARCHAR(500), nullable=False),
        sa.Column("app_type", sa.VARCHAR(20), nullable=False),       # persistent | backend
        sa.Column("ingress_mode", sa.VARCHAR(20), server_default="iframe"),  # iframe | link | none
        sa.Column("default_port", sa.Integer, nullable=True),
        sa.Column("env_schema", JSONB, server_default=sa.text("'[]'")),
        sa.Column("default_mounts", JSONB, server_default=sa.text("'[]'")),
        sa.Column("resources", JSONB, server_default=sa.text("'{}'")),
        sa.Column("icon", sa.VARCHAR(100), nullable=True),
        sa.Column("readme_md", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_app_catalog_slug", "app_catalog", ["slug"])
    op.create_index("ix_app_catalog_is_active", "app_catalog", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_app_catalog_is_active", table_name="app_catalog")
    op.drop_index("ix_app_catalog_slug", table_name="app_catalog")
    op.drop_table("app_catalog")
