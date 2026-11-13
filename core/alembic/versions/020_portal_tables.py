"""Portal tables — PortalDevice, PortalEnrollToken, PortalAuditLog (Phase Portal Bloc A)

Revision ID: 020
Revises: 019
Create Date: 2026-04-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

portal_device_status = sa.Enum(
    "pending", "connected", "disconnected", "revoked",
    name="portal_device_status",
)
portal_audit_source = sa.Enum(
    "agent", "app", "dashboard", "unknown",
    name="portal_audit_source",
)


def upgrade() -> None:
    op.create_table(
        "portal_devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.VARCHAR(100), nullable=False),
        sa.Column("name", sa.VARCHAR(200), nullable=False),
        sa.Column("token_hash", sa.VARCHAR(64), nullable=True),
        sa.Column("services", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", portal_device_status, nullable=False, server_default="pending"),
        sa.Column("last_seen_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_portal_device_workspace_slug"),
    )

    op.create_table(
        "portal_enroll_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_name", sa.VARCHAR(200), nullable=False),
        sa.Column("token_hash", sa.VARCHAR(64), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "portal_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("portal_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source", portal_audit_source, nullable=False, server_default="unknown"),
        sa.Column("service_slug", sa.VARCHAR(100), nullable=False),
        sa.Column("method", sa.VARCHAR(10), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("bytes_out", sa.Integer, nullable=True),
        sa.Column("bytes_in", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("ts", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("idx_portal_audit_device_ts", "portal_audit_log", ["device_id", "ts"])
    op.create_index("idx_portal_audit_agent_ts", "portal_audit_log", ["agent_id", "ts"])


def downgrade() -> None:
    op.drop_index("idx_portal_audit_agent_ts", table_name="portal_audit_log")
    op.drop_index("idx_portal_audit_device_ts", table_name="portal_audit_log")
    op.drop_table("portal_audit_log")
    op.drop_table("portal_enroll_tokens")
    op.drop_table("portal_devices")

    portal_audit_source.drop(op.get_bind(), checkfirst=True)
    portal_device_status.drop(op.get_bind(), checkfirst=True)
