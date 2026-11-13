from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.manager import agent_manager
from api.schemas_agents import AgentConfigCreate, AgentConfigRead, AgentConfigUpdate
from audit import audit_log, get_client_ip
from database import get_db
from models.agent_config import AgentConfig, WorkspaceAgent
from models.workspace import Workspace
from permissions import AuthContext, Permission, require_permission

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentConfigRead])
async def list_agents(
    ctx: AuthContext = Depends(require_permission(Permission.CHAT_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    query = select(AgentConfig).where(AgentConfig.is_active == True)
    if ctx.workspace_id:
        query = query.join(
            WorkspaceAgent,
            (WorkspaceAgent.agent_id == AgentConfig.id)
            & (WorkspaceAgent.workspace_id == ctx.workspace_id),
        )
    result = await db.execute(query)
    return result.scalars().all()


async def _agent_in_workspace(db: AsyncSession, agent_id: str, workspace_id: UUID | None) -> bool:
    """Check if an agent is linked to a workspace via workspace_agents."""
    if not workspace_id:
        return True
    result = await db.execute(
        select(WorkspaceAgent).where(
            WorkspaceAgent.agent_id == agent_id,
            WorkspaceAgent.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none() is not None


@router.post("", response_model=AgentConfigRead, status_code=201)
async def create_agent(
    body: AgentConfigCreate,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.AGENT_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.get(AgentConfig, body.id)
    if existing:
        if not existing.is_active:
            updates = body.model_dump(exclude={"id"})
            for field, value in updates.items():
                setattr(existing, field, value)
            existing.workspace_id = ctx.workspace_id
            existing.owner_id = ctx.user.id if ctx.user else None
            existing.is_active = True
            existing.updated_at = datetime.utcnow()
            # Ensure workspace_agents link
            if ctx.workspace_id and not await _agent_in_workspace(db, body.id, ctx.workspace_id):
                db.add(WorkspaceAgent(
                    workspace_id=ctx.workspace_id, agent_id=body.id,
                    added_by=ctx.user.id if ctx.user else None,
                ))
            await audit_log(db, action="agent.create", user_id=ctx.user.id if ctx.user else None,
                            workspace_id=ctx.workspace_id, resource_type="agent", resource_id=body.id,
                            details={"name": body.name, "reactivated": True}, ip=get_client_ip(request))
            await db.commit()
            await db.refresh(existing)
            if existing.workspace_id:
                ws = await db.get(Workspace, existing.workspace_id)
                if ws:
                    existing._workspace_slug = ws.slug
            agent_manager.register_config(existing)
            return existing
        raise HTTPException(status_code=409, detail=f"Agent '{body.id}' existe déjà.")
    config = AgentConfig(
        **body.model_dump(),
        workspace_id=ctx.workspace_id,
        owner_id=ctx.user.id if ctx.user else None,
    )
    db.add(config)
    await db.flush()
    # Create workspace_agents link
    if ctx.workspace_id:
        db.add(WorkspaceAgent(
            workspace_id=ctx.workspace_id, agent_id=config.id,
            added_by=ctx.user.id if ctx.user else None,
        ))
    await audit_log(db, action="agent.create", user_id=ctx.user.id if ctx.user else None,
                    workspace_id=ctx.workspace_id, resource_type="agent", resource_id=body.id,
                    details={"name": body.name}, ip=get_client_ip(request))
    await db.commit()
    await db.refresh(config)
    if config.workspace_id:
        ws = await db.get(Workspace, config.workspace_id)
        if ws:
            config._workspace_slug = ws.slug
    agent_manager.register_config(config)
    return config


@router.get("/{agent_id}", response_model=AgentConfigRead)
async def get_agent(
    agent_id: str,
    ctx: AuthContext = Depends(require_permission(Permission.CHAT_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    config = await db.get(AgentConfig, agent_id)
    if not config or not await _agent_in_workspace(db, agent_id, ctx.workspace_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' non trouvé.")
    return config


@router.put("/{agent_id}", response_model=AgentConfigRead)
async def update_agent(
    agent_id: str,
    body: AgentConfigUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.AGENT_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    config = await db.get(AgentConfig, agent_id)
    if not config or not await _agent_in_workspace(db, agent_id, ctx.workspace_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' non trouvé.")
    if config.owner_id and (not ctx.user or config.owner_id != ctx.user.id):
        raise HTTPException(status_code=403, detail="Seul le propriétaire de cet agent peut le modifier.")
    updates = body.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(config, field, value)
    config.updated_at = datetime.utcnow()
    await audit_log(db, action="agent.update", user_id=ctx.user.id if ctx.user else None,
                    workspace_id=ctx.workspace_id, resource_type="agent", resource_id=agent_id,
                    details={"fields": list(updates.keys())}, ip=get_client_ip(request))
    await db.commit()
    await db.refresh(config)
    if config.is_active:
        if config.workspace_id:
            ws = await db.get(Workspace, config.workspace_id)
            if ws:
                config._workspace_slug = ws.slug
        agent_manager.register_config(config)
    else:
        agent_manager.deactivate(agent_id, str(ctx.workspace_id) if ctx.workspace_id else None)
    return config


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.AGENT_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    config = await db.get(AgentConfig, agent_id)
    if not config or not await _agent_in_workspace(db, agent_id, ctx.workspace_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' non trouvé.")
    if config.owner_id and (not ctx.user or config.owner_id != ctx.user.id):
        raise HTTPException(status_code=403, detail="Seul le propriétaire de cet agent peut le supprimer.")
    config.is_active = False
    config.updated_at = datetime.utcnow()
    await audit_log(db, action="agent.delete", user_id=ctx.user.id if ctx.user else None,
                    workspace_id=ctx.workspace_id, resource_type="agent", resource_id=agent_id,
                    details={"name": config.name}, ip=get_client_ip(request))
    await db.commit()
    agent_manager.deactivate(agent_id, str(ctx.workspace_id) if ctx.workspace_id else None)
    return {"message": f"Agent '{agent_id}' désactivé."}
