"""Create audit_logs table (Phase 4 Bloc G — Audit Logging)

Revision ID: 010
Revises: 009
Create Date: 2026-03-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.VARCHAR(50), nullable=False),
        sa.Column("resource_type", sa.VARCHAR(50), nullable=True),
        sa.Column("resource_id", sa.VARCHAR(255), nullable=True),
        sa.Column("details", postgresql.JSONB(), server_default="{}"),
        sa.Column("ip_address", sa.VARCHAR(45), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_audit_workspace", "audit_logs", ["workspace_id", sa.text("created_at DESC")])
    op.create_index("idx_audit_user", "audit_logs", ["user_id", sa.text("created_at DESC")])
    op.create_index("idx_audit_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_index("idx_audit_action")
    op.drop_index("idx_audit_user")
    op.drop_index("idx_audit_workspace")
    op.drop_table("audit_logs")
