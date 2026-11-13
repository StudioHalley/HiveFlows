"""Site-admin routes (Phase 4 Bloc L)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import audit_log, get_client_ip
from auth import get_current_user
from database import get_db
from models.agent_config import AgentConfig
from models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_site_admin(user: User) -> User:
    if not user.is_site_admin:
        raise HTTPException(status_code=403, detail="Site admin required.")
    return user


class SetDefaultAgentRequest(BaseModel):
    agent_id: str
    is_default: bool = True


@router.put("/default-agent")
async def set_default_agent(
    body: SetDefaultAgentRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or unset an agent as the platform default (site-admin only)."""
    _require_site_admin(user)

    config = await db.get(AgentConfig, body.agent_id)
    if not config or not config.is_active:
        raise HTTPException(status_code=404, detail=f"Agent '{body.agent_id}' not found.")

    config.is_default = body.is_default
    await audit_log(
        db, action="admin.set_default_agent", user_id=user.id,
        resource_type="agent", resource_id=body.agent_id,
        details={"is_default": body.is_default},
        ip=get_client_ip(request),
    )
    await db.commit()
    return {"agent_id": body.agent_id, "is_default": config.is_default}


@router.get("/default-agents")
async def list_default_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all default agents (site-admin only)."""
    _require_site_admin(user)
    result = await db.execute(
        select(AgentConfig.id, AgentConfig.name)
        .where(AgentConfig.is_default == True, AgentConfig.is_active == True)
    )
    return [{"agent_id": aid, "name": name} for aid, name in result.all()]
