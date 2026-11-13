"""Audit log routes — Phase 4 Bloc G."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.audit_log import AuditLog
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from permissions import AuthContext, Permission, check_permission, require_permission

router = APIRouter(prefix="/workspaces", tags=["audit"])


class AuditLogRead(BaseModel):
    id: UUID
    user_id: UUID | None
    workspace_id: UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    details: dict | None
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogList(BaseModel):
    items: list[AuditLogRead]
    total: int


@router.get("/{workspace_id}/audit", response_model=AuditLogList)
async def list_audit_logs(
    workspace_id: UUID,
    ctx: AuthContext = Depends(require_permission(Permission.AUDIT_VIEW)),
    db: AsyncSession = Depends(get_db),
    action: str | None = Query(None, description="Filter by action (e.g. 'chat.invoke')"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    user_id: UUID | None = Query(None, description="Filter by user"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List audit logs for a workspace (owner/admin only)."""
    # Verify the caller is a member of THIS workspace with AUDIT_VIEW permission
    if not ctx.user:
        raise HTTPException(403, "Authentication required.")
    mem_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == ctx.user.id,
        )
    )
    member = mem_result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Not a member of this workspace.")
    check_permission(member.role, Permission.AUDIT_VIEW)

    base = select(AuditLog).where(AuditLog.workspace_id == workspace_id)

    if action:
        base = base.where(AuditLog.action == action)
    if resource_type:
        base = base.where(AuditLog.resource_type == resource_type)
    if user_id:
        base = base.where(AuditLog.user_id == user_id)

    # Total count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Paginated results
    query = base.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return AuditLogList(items=items, total=total)
