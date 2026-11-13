"""Persist chat messages to the conversations/messages tables.

Phase 4 Bloc P — shared workspace conversations.
One conversation per workspace. Messages attributed to users and agents.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models.conversation import Conversation, Message


# ---------------------------------------------------------------------------
# Workspace conversation (shared)
# ---------------------------------------------------------------------------

async def ensure_workspace_conversation(
    db: AsyncSession,
    workspace_id: str,
) -> Conversation:
    """Get or create THE single conversation for a workspace."""
    ws_uuid = uuid.UUID(workspace_id)

    result = await db.execute(
        select(Conversation).where(Conversation.workspace_id == ws_uuid)
    )
    conv = result.scalar_one_or_none()

    if conv:
        conv.updated_at = datetime.utcnow()
        return conv

    conv = Conversation(
        id=uuid.uuid4(),
        agent_id="workspace",
        workspace_id=ws_uuid,
    )
    db.add(conv)
    await db.flush()
    return conv


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------

async def save_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    user_id: str | None = None,
    display_name: str | None = None,
    agent_id: str | None = None,
    metadata: dict | None = None,
) -> Message:
    """Save a single message to the messages table."""
    msg = Message(
        conversation_id=uuid.UUID(conversation_id),
        role=role,
        content=content,
        user_id=uuid.UUID(user_id) if user_id else None,
        display_name=display_name,
        agent_id=agent_id,
        metadata_=metadata or {},
    )
    db.add(msg)
    await db.flush()
    return msg


async def persist_user_message(
    workspace_id: str,
    content: str,
    user_id: str,
    display_name: str,
    target_agent_id: str | None = None,
) -> tuple[str, str]:
    """Save a user message immediately. Returns (conversation_id, message_id).

    Opens its own session — safe to call from anywhere.
    """
    async with async_session() as db:
        conv = await ensure_workspace_conversation(db, workspace_id)
        msg = await save_message(
            db,
            str(conv.id),
            "user",
            content,
            user_id=user_id,
            display_name=display_name,
            agent_id=target_agent_id,
        )
        await db.commit()
        return str(conv.id), str(msg.id)


async def persist_agent_message(
    conversation_id: str,
    agent_id: str,
    content: str,
    tool_events: list[dict] | None = None,
) -> str:
    """Save an agent response. Returns message_id.

    Opens its own session — safe to call from background tasks.
    """
    async with async_session() as db:
        metadata = {"tool_events": tool_events} if tool_events else {}
        msg = await save_message(
            db,
            conversation_id,
            "assistant",
            content,
            agent_id=agent_id,
            metadata=metadata,
        )
        # Touch conversation updated_at
        conv_uuid = uuid.UUID(conversation_id)
        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_uuid)
        )
        conv = result.scalar_one_or_none()
        if conv:
            conv.updated_at = datetime.utcnow()
        await db.commit()
        return str(msg.id)


# ---------------------------------------------------------------------------
# History / pagination
# ---------------------------------------------------------------------------

async def get_recent_messages(
    db: AsyncSession,
    workspace_id: str,
    limit: int = 50,
    before_id: str | None = None,
) -> tuple[list[Message], bool]:
    """Fetch recent messages from the workspace conversation.

    Returns (messages_in_chronological_order, has_more).
    Uses cursor-based pagination with before_id.
    """
    ws_uuid = uuid.UUID(workspace_id)

    # Find workspace conversation
    conv_result = await db.execute(
        select(Conversation.id).where(Conversation.workspace_id == ws_uuid)
    )
    conv_id = conv_result.scalar_one_or_none()
    if conv_id is None:
        return [], False

    query = (
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.desc())
    )

    if before_id:
        # Get the created_at of the cursor message
        cursor_result = await db.execute(
            select(Message.created_at).where(Message.id == uuid.UUID(before_id))
        )
        cursor_ts = cursor_result.scalar_one_or_none()
        if cursor_ts:
            query = query.where(Message.created_at < cursor_ts)

    # Fetch limit+1 to know if there are more
    result = await db.execute(query.limit(limit + 1))
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    messages = rows[:limit]
    messages.reverse()  # chronological order
    return messages, has_more


# ---------------------------------------------------------------------------
# Legacy compat — keep persist_exchange for API routes during transition
# ---------------------------------------------------------------------------

async def persist_exchange(
    conversation_id: str,
    agent_id: str,
    user_message: str,
    agent_response: str,
    workspace_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Legacy: persist a full user->agent exchange. Opens its own session."""
    async with async_session() as db:
        if workspace_id:
            conv = await ensure_workspace_conversation(db, workspace_id)
            conv_id = str(conv.id)
        else:
            # Fallback for non-workspace conversations
            conv_uuid = uuid.UUID(conversation_id)
            result = await db.execute(
                select(Conversation).where(Conversation.id == conv_uuid)
            )
            conv = result.scalar_one_or_none()
            if not conv:
                conv = Conversation(
                    id=conv_uuid,
                    agent_id=agent_id,
                    workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
                )
                db.add(conv)
                await db.flush()
            else:
                conv.updated_at = datetime.utcnow()
            conv_id = str(conv.id)

        await save_message(db, conv_id, "user", user_message,
                           user_id=user_id, agent_id=agent_id)
        if agent_response:
            await save_message(db, conv_id, "assistant", agent_response,
                               agent_id=agent_id)
        await db.commit()
