"""Add workspace_id/user_id to agent_configs, conversations, tasks — migrate existing data

Revision ID: 007
Revises: 006
Create Date: 2026-03-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agent_configs
    op.add_column("agent_configs", sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=True))

    # conversations
    op.add_column("conversations", sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("conversations", sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=True))

    # tasks
    op.add_column("tasks", sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("tasks", sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=True))

    # Migrate existing data: attach to first available workspace + user
    conn = op.get_bind()
    first_ws = conn.execute(sa.text("SELECT id FROM workspaces LIMIT 1")).fetchone()
    first_user = conn.execute(sa.text("SELECT id FROM users LIMIT 1")).fetchone()

    if first_ws:
        ws_id = first_ws[0]
        conn.execute(sa.text("UPDATE agent_configs SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {"ws_id": ws_id})
        conn.execute(sa.text("UPDATE conversations SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {"ws_id": ws_id})
        conn.execute(sa.text("UPDATE tasks SET workspace_id = :ws_id WHERE workspace_id IS NULL"), {"ws_id": ws_id})

    if first_user:
        user_id = first_user[0]
        conn.execute(sa.text("UPDATE conversations SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})
        conn.execute(sa.text("UPDATE tasks SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})


def downgrade() -> None:
    op.drop_column("tasks", "workspace_id")
    op.drop_column("tasks", "user_id")
    op.drop_column("conversations", "workspace_id")
    op.drop_column("conversations", "user_id")
    op.drop_column("agent_configs", "workspace_id")
