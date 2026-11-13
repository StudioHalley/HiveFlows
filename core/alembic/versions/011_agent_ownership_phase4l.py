"""Agent ownership + workspace_agents junction + user slug/site-admin (Phase 4 Bloc L)

Revision ID: 011
Revises: 010
Create Date: 2026-03-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # L1: Add owner_id + is_default to agent_configs
    op.add_column("agent_configs", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("agent_configs", sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False))
    op.create_foreign_key("fk_agent_configs_owner", "agent_configs", "users", ["owner_id"], ["id"])

    # L2: Create workspace_agents junction table
    op.create_table(
        "workspace_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.VARCHAR(100), sa.ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("added_at", sa.DateTime(), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("workspace_id", "agent_id", name="uq_workspace_agent"),
    )
    op.create_index("idx_wa_workspace", "workspace_agents", ["workspace_id"])
    op.create_index("idx_wa_agent", "workspace_agents", ["agent_id"])

    # L3: Add slug + is_site_admin to users
    op.add_column("users", sa.Column("slug", sa.VARCHAR(100), nullable=True))
    op.add_column("users", sa.Column("is_site_admin", sa.Boolean(), server_default="false", nullable=False))
    op.create_index("idx_users_slug", "users", ["slug"], unique=True)

    # L4: Migrate existing data — populate workspace_agents from agent_configs.workspace_id
    op.execute("""
        INSERT INTO workspace_agents (workspace_id, agent_id, added_by, added_at)
        SELECT ac.workspace_id, ac.id,
               (SELECT wm.user_id FROM workspace_members wm
                WHERE wm.workspace_id = ac.workspace_id AND wm.role = 'owner' LIMIT 1),
               ac.created_at
        FROM agent_configs ac
        WHERE ac.workspace_id IS NOT NULL AND ac.is_active = true
        ON CONFLICT DO NOTHING
    """)

    # L4 continued: Set owner_id from workspace owner for existing agents
    op.execute("""
        UPDATE agent_configs ac
        SET owner_id = (
            SELECT wm.user_id FROM workspace_members wm
            WHERE wm.workspace_id = ac.workspace_id AND wm.role = 'owner' LIMIT 1
        )
        WHERE ac.workspace_id IS NOT NULL AND ac.owner_id IS NULL
    """)

    # L3 continued: Generate slugs for existing users (from email prefix)
    op.execute("""
        UPDATE users
        SET slug = LOWER(REPLACE(REPLACE(SPLIT_PART(email, '@', 1), '.', '-'), ' ', '-'))
        WHERE slug IS NULL
    """)


def downgrade() -> None:
    op.drop_index("idx_users_slug")
    op.drop_column("users", "is_site_admin")
    op.drop_column("users", "slug")
    op.drop_index("idx_wa_agent")
    op.drop_index("idx_wa_workspace")
    op.drop_table("workspace_agents")
    op.drop_constraint("fk_agent_configs_owner", "agent_configs", type_="foreignkey")
    op.drop_column("agent_configs", "is_default")
    op.drop_column("agent_configs", "owner_id")
