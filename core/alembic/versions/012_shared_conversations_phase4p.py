"""Shared workspace conversations — add user_id/display_name/agent_id to messages,
unique workspace conversation constraint (Phase 4 Bloc P)

Revision ID: 012
Revises: 011
Create Date: 2026-03-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P1: Add user attribution columns to messages
    op.add_column("messages", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("messages", sa.Column("display_name", sa.VARCHAR(200), nullable=True))
    op.add_column("messages", sa.Column("agent_id", sa.VARCHAR(100), nullable=True))
    op.create_foreign_key("fk_messages_user", "messages", "users", ["user_id"], ["id"])
    op.create_index("idx_messages_user_id", "messages", ["user_id"])

    # P2: Backfill agent_id on existing messages from their conversation
    op.execute("""
        UPDATE messages m
        SET agent_id = c.agent_id
        FROM conversations c
        WHERE m.conversation_id = c.id AND m.agent_id IS NULL
    """)

    # P3: Backfill user_id on existing messages from their conversation (for user messages)
    op.execute("""
        UPDATE messages m
        SET user_id = c.user_id
        FROM conversations c
        WHERE m.conversation_id = c.id AND m.role = 'user' AND m.user_id IS NULL AND c.user_id IS NOT NULL
    """)

    # P4: Merge duplicate conversations per workspace — keep the oldest, move messages
    # First, tag messages with their source conversation's agent_id before merging
    op.execute("""
        WITH ranked AS (
            SELECT id, workspace_id,
                   ROW_NUMBER() OVER (PARTITION BY workspace_id ORDER BY created_at ASC) AS rn
            FROM conversations
            WHERE workspace_id IS NOT NULL
        ),
        keeper AS (
            SELECT workspace_id, id AS keep_id FROM ranked WHERE rn = 1
        ),
        duplicate AS (
            SELECT r.id AS dup_id, k.keep_id
            FROM ranked r
            JOIN keeper k ON r.workspace_id = k.workspace_id
            WHERE r.rn > 1
        )
        UPDATE messages m
        SET conversation_id = d.keep_id
        FROM duplicate d
        WHERE m.conversation_id = d.dup_id
    """)

    # Delete the now-empty duplicate conversations
    op.execute("""
        WITH ranked AS (
            SELECT id, workspace_id,
                   ROW_NUMBER() OVER (PARTITION BY workspace_id ORDER BY created_at ASC) AS rn
            FROM conversations
            WHERE workspace_id IS NOT NULL
        )
        DELETE FROM conversations
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    """)

    # P5: Set agent_id to 'workspace' on remaining workspace conversations
    op.execute("""
        UPDATE conversations SET agent_id = 'workspace' WHERE workspace_id IS NOT NULL
    """)

    # P6: Make Conversation.agent_id nullable (now 'workspace' for shared convos)
    op.alter_column("conversations", "agent_id", nullable=True)

    # P7: Unique partial index — one conversation per workspace
    op.execute("""
        CREATE UNIQUE INDEX uq_conversation_workspace
        ON conversations (workspace_id)
        WHERE workspace_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_conversation_workspace")
    op.alter_column("conversations", "agent_id", nullable=False)
    op.drop_index("idx_messages_user_id", "messages")
    op.drop_constraint("fk_messages_user", "messages", type_="foreignkey")
    op.drop_column("messages", "agent_id")
    op.drop_column("messages", "display_name")
    op.drop_column("messages", "user_id")
