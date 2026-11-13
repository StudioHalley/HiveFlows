"""Portal device management & enrollment routes.

Management (session auth required):
  POST   /api/portal/devices
  GET    /api/portal/devices
  GET    /api/portal/devices/{device_id}
  DELETE /api/portal/devices/{device_id}
  GET    /api/portal/devices/{device_id}/audit

Enrollment (no session — called by the portal-client Go binary):
  POST   /portal/enroll
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from models.portal import PortalAuditLog, PortalDevice, PortalDeviceStatus
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from services.portal_enrollment import EnrollmentError, create_device, enroll_device, revoke_device

logger = logging.getLogger("hiveflows.portal.devices")

router = APIRouter(tags=["portal"])


# ── Auth helper ───────────────────────────────────────────────────────────────

async def _get_workspace_member(
    workspace_id: UUID,
    user: User,
    db: AsyncSession,
) -> Workspace:
    """Verify user is a member of the workspace. Returns the workspace."""
    ws_r = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.is_active == True)
    )
    ws = ws_r.scalar_one_or_none()
    if not ws:
        raise HTTPException(404, "Workspace not found.")

    mem_r = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if not mem_r.scalar_one_or_none():
        raise HTTPException(403, "Not a member of this workspace.")
    return ws


async def _get_device(db: AsyncSession, workspace_id: UUID, device_id: UUID) -> PortalDevice:
    r = await db.execute(
        select(PortalDevice).where(
            PortalDevice.id == device_id,
            PortalDevice.workspace_id == workspace_id,
        )
    )
    dev = r.scalar_one_or_none()
    if not dev:
        raise HTTPException(404, "Device not found.")
    return dev


# ── Schemas ───────────────────────────────────────────────────────────────────

class DeviceCreateRequest(BaseModel):
    workspace_id: UUID
    name: str = Field(..., min_length=1, max_length=200)


class DeviceCreateResponse(BaseModel):
    device_id: UUID
    slug: str
    name: str
    enroll_token: str
    expires_at: datetime
    init_command: str


class DeviceSummary(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str
    last_seen_at: datetime | None
    services_count: int
    created_at: datetime


class DeviceDetail(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str
    last_seen_at: datetime | None
    services: list[dict]
    created_at: datetime


class AuditEntry(BaseModel):
    id: int
    source: str
    service_slug: str
    method: str
    path: str
    status_code: int | None
    duration_ms: int | None
    bytes_out: int | None
    bytes_in: int | None
    error: str | None
    ts: datetime


class EnrollRequest(BaseModel):
    enroll_token: str
    device_slug: str
    workspace_slug: str
    device_info: dict = Field(default_factory=dict)


class EnrollResponse(BaseModel):
    permanent_token: str
    workspace_slug: str


# ── Management routes ─────────────────────────────────────────────────────────

@router.post("/api/portal/devices", response_model=DeviceCreateResponse, status_code=201)
async def create_portal_device(
    body: DeviceCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeviceCreateResponse:
    ws = await _get_workspace_member(body.workspace_id, user, db)

    device, enroll_tok, plain_token = await create_device(db, ws.id, body.name)

    init_cmd = (
        f"portal-client init "
        f"--server wss://hiveflows/portal/ws "
        f"--token {plain_token} "
        f"--device-slug {device.slug}"
    )

    return DeviceCreateResponse(
        device_id=device.id,
        slug=device.slug,
        name=device.name,
        enroll_token=plain_token,
        expires_at=enroll_tok.expires_at,
        init_command=init_cmd,
    )


@router.get("/api/portal/devices", response_model=list[DeviceSummary])
async def list_portal_devices(
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DeviceSummary]:
    await _get_workspace_member(workspace_id, user, db)

    result = await db.execute(
        select(PortalDevice)
        .where(PortalDevice.workspace_id == workspace_id)
        .order_by(PortalDevice.created_at.desc())
    )
    devices = result.scalars().all()

    return [
        DeviceSummary(
            id=d.id,
            slug=d.slug,
            name=d.name,
            status=d.status.value,
            last_seen_at=d.last_seen_at,
            services_count=len(d.services or []),
            created_at=d.created_at,
        )
        for d in devices
    ]


@router.get("/api/portal/devices/{device_id}", response_model=DeviceDetail)
async def get_portal_device(
    device_id: UUID,
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeviceDetail:
    await _get_workspace_member(workspace_id, user, db)
    device = await _get_device(db, workspace_id, device_id)

    return DeviceDetail(
        id=device.id,
        slug=device.slug,
        name=device.name,
        status=device.status.value,
        last_seen_at=device.last_seen_at,
        services=device.services or [],
        created_at=device.created_at,
    )


@router.delete("/api/portal/devices/{device_id}", status_code=204)
async def delete_portal_device(
    device_id: UUID,
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_workspace_member(workspace_id, user, db)
    device = await _get_device(db, workspace_id, device_id)

    await revoke_device(db, device)


@router.get("/api/portal/devices/{device_id}/audit", response_model=list[AuditEntry])
async def get_device_audit(
    device_id: UUID,
    workspace_id: UUID,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuditEntry]:
    await _get_workspace_member(workspace_id, user, db)
    await _get_device(db, workspace_id, device_id)

    limit = min(max(limit, 1), 500)
    result = await db.execute(
        select(PortalAuditLog)
        .where(PortalAuditLog.device_id == device_id)
        .order_by(PortalAuditLog.ts.desc())
        .limit(limit)
    )
    logs = result.scalars().all()

    return [
        AuditEntry(
            id=entry.id,
            source=entry.source.value,
            service_slug=entry.service_slug,
            method=entry.method,
            path=entry.path,
            status_code=entry.status_code,
            duration_ms=entry.duration_ms,
            bytes_out=entry.bytes_out,
            bytes_in=entry.bytes_in,
            error=entry.error,
            ts=entry.ts,
        )
        for entry in logs
    ]


# ── Enrollment (unauthenticated — called by portal-client binary) ─────────────

@router.post("/portal/enroll", response_model=EnrollResponse)
async def portal_enroll(
    body: EnrollRequest,
    db: AsyncSession = Depends(get_db),
) -> EnrollResponse:
    # Resolve workspace by slug
    ws_r = await db.execute(
        select(Workspace).where(Workspace.slug == body.workspace_slug, Workspace.is_active == True)
    )
    workspace = ws_r.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, f"Workspace '{body.workspace_slug}' not found.")

    try:
        _device, plain_perm = await enroll_device(
            db=db,
            plain_enroll_token=body.enroll_token,
            device_slug=body.device_slug,
            workspace_id=workspace.id,
        )
    except EnrollmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return EnrollResponse(
        permanent_token=plain_perm,
        workspace_slug=workspace.slug,
    )
