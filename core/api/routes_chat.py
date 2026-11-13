import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.manager import agent_manager
from audit import audit_log, get_client_ip
from chat_persistence import persist_exchange
from database import get_db
from mention_parser import parse_mentions
from models.agent_config import AgentConfig, WorkspaceAgent
from permissions import AuthContext, Permission, require_permission
from slash_commands import execute_slash_command, is_slash_command

logger = logging.getLogger("hiveflows.chat")

router = APIRouter(tags=["chat"])


async def _workspace_agent_ids(db: AsyncSession, workspace_id: UUID | None) -> list[str]:
    """Get active agent IDs linked to a workspace."""
    if not workspace_id:
        return []
    result = await db.execute(
        select(WorkspaceAgent.agent_id)
        .join(AgentConfig, AgentConfig.id == WorkspaceAgent.agent_id)
        .where(WorkspaceAgent.workspace_id == workspace_id, AgentConfig.is_active == True)
    )
    return [row[0] for row in result.all()]


class ChatRequest(BaseModel):
    agent_id: str = "assistant-principal"
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    response: str
    tool_calls: list[dict] = []
    usage: dict | None = None


class ResumeRequest(BaseModel):
    agent_id: str = "assistant-principal"
    decisions: list[dict]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.CHAT)),
    db: AsyncSession = Depends(get_db),
):
    ws_id = str(ctx.workspace_id) if ctx.workspace_id else None

    # N1: Detect and execute slash commands before sending to LLM
    if is_slash_command(req.message) and ctx.user:
        slash = await execute_slash_command(
            req.message, ctx.user, ctx.workspace_id, ctx.member, db,
        )
        if slash.is_command:
            return ChatResponse(
                conversation_id=req.conversation_id or "",
                message_id="system",
                response=slash.system_message or "",
            )

    # M1-M4: Parse @mentions and resolve routing
    available = await _workspace_agent_ids(db, ctx.workspace_id)
    mention = parse_mentions(req.message, available, req.agent_id)

    if mention.system_message:
        return ChatResponse(
            conversation_id=req.conversation_id or "",
            message_id="system",
            response=mention.system_message,
        )

    agent_id = mention.resolved_agent_id or req.agent_id
    message_for_agent = mention.clean_message

    result = await agent_manager.invoke(
        agent_id=agent_id,
        message=message_for_agent,
        conversation_id=req.conversation_id,
        workspace_id=ws_id,
        allowed_skills=ctx.allowed_skills,
    )
    await audit_log(db, action="chat.invoke", user_id=ctx.user.id if ctx.user else None,
                    workspace_id=ctx.workspace_id, resource_type="conversation",
                    resource_id=result.get("conversation_id"),
                    details={"agent_id": agent_id, "message_preview": req.message[:100],
                             "mentions": mention.mentioned_ids},
                    ip=get_client_ip(request))
    await db.commit()

    # Persist conversation + messages
    try:
        await persist_exchange(
            conversation_id=result["conversation_id"],
            agent_id=agent_id,
            user_message=req.message,
            agent_response=result.get("response", ""),
            workspace_id=ws_id,
            user_id=str(ctx.user.id) if ctx.user else None,
        )
    except Exception as e:
        logger.warning(f"Failed to persist chat exchange: {e}")

    return ChatResponse(**result)


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.CHAT)),
    db: AsyncSession = Depends(get_db),
):
    """Streaming SSE — émet des events LangGraph en temps réel."""
    ws_id = str(ctx.workspace_id) if ctx.workspace_id else None
    user_id = str(ctx.user.id) if ctx.user else None

    # N1: Detect and execute slash commands before sending to LLM
    if is_slash_command(req.message) and ctx.user:
        slash = await execute_slash_command(
            req.message, ctx.user, ctx.workspace_id, ctx.member, db,
        )
        if slash.is_command:
            async def slash_msg():
                yield f"event: message_start\ndata: {json.dumps({'conversation_id': req.conversation_id or '', 'agent_id': 'system'})}\n\n"
                yield f"event: token\ndata: {json.dumps({'content': slash.system_message or ''})}\n\n"
                yield f"event: message_end\ndata: {json.dumps({'conversation_id': req.conversation_id or ''})}\n\n"
            return StreamingResponse(slash_msg(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    # M1-M4: Parse @mentions
    available = await _workspace_agent_ids(db, ctx.workspace_id)
    mention = parse_mentions(req.message, available, req.agent_id)

    if mention.system_message:
        async def system_msg():
            yield f"event: message_start\ndata: {json.dumps({'conversation_id': req.conversation_id or '', 'agent_id': 'system'})}\n\n"
            yield f"event: token\ndata: {json.dumps({'content': mention.system_message})}\n\n"
            yield f"event: message_end\ndata: {json.dumps({'conversation_id': req.conversation_id or ''})}\n\n"
        return StreamingResponse(system_msg(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    agent_id = mention.resolved_agent_id or req.agent_id
    message_for_agent = mention.clean_message

    # Log at invocation start (not per token)
    await audit_log(db, action="chat.stream", user_id=ctx.user.id if ctx.user else None,
                    workspace_id=ctx.workspace_id, resource_type="conversation",
                    resource_id=req.conversation_id,
                    details={"agent_id": agent_id, "message_preview": req.message[:100],
                             "mentions": mention.mentioned_ids},
                    ip=get_client_ip(request))
    await db.commit()

    async def event_generator():
        collected_text = []
        final_conversation_id = req.conversation_id

        try:
            async for sse in agent_manager.astream(
                agent_id=agent_id,
                message=message_for_agent,
                conversation_id=req.conversation_id,
                workspace_id=ws_id,
                allowed_skills=ctx.allowed_skills,
            ):
                event_type = sse["event"]
                data = sse["data"]

                if data.get("conversation_id"):
                    final_conversation_id = data["conversation_id"]
                if event_type == "token" and data.get("content"):
                    collected_text.append(data["content"])

                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

        # Persist conversation + messages
        if final_conversation_id:
            try:
                await persist_exchange(
                    conversation_id=final_conversation_id,
                    agent_id=agent_id,
                    user_message=req.message,
                    agent_response="".join(collected_text),
                    workspace_id=ws_id,
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning(f"Failed to persist chat exchange: {e}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/{conversation_id}/resume", response_model=ChatResponse)
async def resume_chat(
    conversation_id: str,
    req: ResumeRequest,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.CHAT)),
    db: AsyncSession = Depends(get_db),
):
    """Resume a HITL-interrupted conversation."""
    ws_id = str(ctx.workspace_id) if ctx.workspace_id else None
    result = await agent_manager.resume(
        agent_id=req.agent_id,
        conversation_id=conversation_id,
        decisions=req.decisions,
        workspace_id=ws_id,
        allowed_skills=ctx.allowed_skills,
    )
    await audit_log(db, action="chat.resume", user_id=ctx.user.id if ctx.user else None,
                    workspace_id=ctx.workspace_id, resource_type="conversation",
                    resource_id=conversation_id,
                    details={"agent_id": req.agent_id, "decisions_count": len(req.decisions)},
                    ip=get_client_ip(request))
    await db.commit()

    # Persist resume response
    try:
        await persist_exchange(
            conversation_id=conversation_id,
            agent_id=req.agent_id,
            user_message=f"[HITL resume: {req.decisions}]",
            agent_response=result.get("response", ""),
            workspace_id=ws_id,
            user_id=str(ctx.user.id) if ctx.user else None,
        )
    except Exception as e:
        logger.warning(f"Failed to persist resume exchange: {e}")

    return ChatResponse(**result)
