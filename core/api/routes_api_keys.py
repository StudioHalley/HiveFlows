"""API Key CRUD routes — workspace-scoped (Phase 4 Bloc E)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import audit_log, get_client_ip
from auth import get_current_user
from database import get_db
from models.api_key import ApiKey, generate_api_key, hash_api_key
from models.user import User
from models.workspace import WorkspaceMember
from permissions import Permission, check_permission

router = APIRouter(prefix="/workspaces", tags=["api-keys"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    name: str
    permissions: list[str] = ["chat", "tasks", "agents"]


class ApiKeyRead(BaseModel):
    id: UUID
    key_prefix: str
    name: str
    workspace_id: UUID
    created_by: UUID
    permissions: list[str]
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(BaseModel):
    """Returned only once at creation — includes full key."""
    id: UUID
    key: str
    key_prefix: str
    name: str
    permissions: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_membership_checked(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, permission: Permission,
) -> WorkspaceMember:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this workspace.")
    check_permission(member.role, permission)
    return member


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("/{workspace_id}/api-keys", response_model=list[ApiKeyRead])
async def list_api_keys(
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_membership_checked(db, workspace_id, user.id, Permission.APIKEY_MANAGE)
    result = await db.execute(
        select(ApiKey).where(ApiKey.workspace_id == workspace_id, ApiKey.is_active == True)
    )
    return result.scalars().all()


@router.post("/{workspace_id}/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    workspace_id: UUID,
    body: ApiKeyCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_membership_checked(db, workspace_id, user.id, Permission.APIKEY_MANAGE)

    full_key, prefix = generate_api_key()
    key_hash = hash_api_key(full_key)

    api_key = ApiKey(
        key_hash=key_hash,
        key_prefix=prefix,
        name=body.name.strip(),
        workspace_id=workspace_id,
        created_by=user.id,
        permissions=body.permissions,
    )
    db.add(api_key)
    await audit_log(db, action="apikey.create", user_id=user.id, workspace_id=workspace_id,
                    resource_type="api_key", resource_id=prefix,
                    details={"name": body.name, "permissions": body.permissions},
                    ip=get_client_ip(request))
    await db.commit()
    await db.refresh(api_key)

    return ApiKeyCreated(
        id=api_key.id,
        key=full_key,
        key_prefix=prefix,
        name=api_key.name,
        permissions=api_key.permissions,
    )


@router.delete("/{workspace_id}/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    workspace_id: UUID,
    key_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_membership_checked(db, workspace_id, user.id, Permission.APIKEY_MANAGE)

    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.workspace_id == workspace_id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found.")

    api_key.is_active = False
    await audit_log(db, action="apikey.revoke", user_id=user.id, workspace_id=workspace_id,
                    resource_type="api_key", resource_id=str(key_id),
                    details={"name": api_key.name, "prefix": api_key.key_prefix},
                    ip=get_client_ip(request))
    await db.commit()
