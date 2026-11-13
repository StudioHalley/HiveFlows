"""Add workspaces and workspace_members tables + create personal workspace for existing users

Revision ID: 006
Revises: 005
Create Date: 2026-03-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.VARCHAR(200), nullable=False),
        sa.Column("slug", sa.VARCHAR(100), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")),
    )

    op.create_table(
        "workspace_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.VARCHAR(20), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()")),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )

    # Create a personal workspace for each existing user
    conn = op.get_bind()
    users = conn.execute(sa.text("SELECT id, display_name, email FROM users")).fetchall()
    for user in users:
        user_id, display_name, email = user
        # Slug = email prefix, sanitized
        slug = email.split("@")[0].lower().replace(" ", "-").replace(".", "-")
        # Check for slug collision
        existing = conn.execute(sa.text("SELECT 1 FROM workspaces WHERE slug = :slug"), {"slug": slug}).fetchone()
        if existing:
            slug = f"{slug}-{str(user_id)[:8]}"
        conn.execute(
            sa.text(
                "INSERT INTO workspaces (id, name, slug, owner_id) "
                "VALUES (gen_random_uuid(), :name, :slug, :owner_id) "
                "RETURNING id"
            ),
            {"name": f"{display_name}", "slug": slug, "owner_id": user_id},
        )
        ws = conn.execute(sa.text("SELECT id FROM workspaces WHERE slug = :slug"), {"slug": slug}).fetchone()
        conn.execute(
            sa.text(
                "INSERT INTO workspace_members (id, workspace_id, user_id, role) "
                "VALUES (gen_random_uuid(), :ws_id, :user_id, 'owner')"
            ),
            {"ws_id": ws[0], "user_id": user_id},
        )


def downgrade() -> None:
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
