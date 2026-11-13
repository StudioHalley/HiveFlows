"""Tunnel endpoint — HTTP-over-WebSocket portal forwarding.

Route: ANY /tunnel/{workspace_slug}/{device_slug}/{service_slug}/{path:path}

Auth multi-source (JWT Bearer or API key):
  - X-HiveFlows-Agent-ID header present → source = agent
  - X-HiveFlows-App-ID header present   → source = app
  - JWT Bearer / session only           → source = dashboard
  - API key, no extra headers           → source = unknown

Errors:
  401  auth missing/invalid
  403  caller not in workspace
  404  workspace, device, or service not found
  413  body > PORTAL_MAX_BODY_BYTES
  502  WS lost during request, or device replied ERROR upstream_unreachable
  503  device not connected (DB status or not in registry)
  504  device did not reply within 30 s
"""

import asyncio
import base64
import logging
import time
import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Security
from fastapi.responses import Response
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service import decode_token
from config import settings
from database import async_session, get_db
from models.api_key import ApiKey, hash_api_key
from models.portal import PortalAuditLog, PortalAuditSource, PortalDevice, PortalDeviceStatus
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from services.portal_registry import PortalConnectionLostError, PortalTimeoutError, portal_registry

logger = logging.getLogger("hiveflows.tunnel")

router = APIRouter(tags=["portal"])

# ── Header filter sets ────────────────────────────────────────────────────────

_HOP_BY_HOP = frozenset({
    "host", "connection", "upgrade", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "keep-alive",
})

# Strip these from the request before forwarding to the device.
# Note: "authorization" is only stripped when the caller authenticated via JWT Bearer
# (to avoid leaking HiveFlows credentials upstream). When using X-HF-API-Key, the
# Authorization header is passed through so upstream services can use it.
_REQUEST_STRIP = _HOP_BY_HOP

# Strip these from the device response before returning to the caller
_RESPONSE_STRIP = _HOP_BY_HOP | {"set-cookie"}


def _filter_request_headers(headers, strip_auth: bool = False) -> dict[str, str]:
    """Return only safe headers, removing hop-by-hop and X-HiveFlows-* headers.

    strip_auth=True removes Authorization (used when caller authenticated via JWT
    Bearer, to avoid forwarding HiveFlows credentials to the upstream service).
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        lo = k.lower()
        if lo in _REQUEST_STRIP:
            continue
        if strip_auth and lo == "authorization":
            continue
        if lo.startswith("x-hiveflows-"):
            continue
        out[k] = v
    return out


def _filter_response_headers(headers: dict) -> dict[str, str]:
    """Strip hop-by-hop and Set-Cookie from device response headers."""
    return {k: v for k, v in headers.items() if k.lower() not in _RESPONSE_STRIP}


# ── Auth dependency ───────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-HF-API-Key", auto_error=False)


class _TunnelAuth:
    """Carries resolved identity for a tunnel request."""
    def __init__(
        self,
        user: User | None,
        api_key_obj: ApiKey | None,
        source: PortalAuditSource,
        agent_id: UUID | None,
        task_id: UUID | None,
    ):
        self.user = user
        self.api_key_obj = api_key_obj
        self.source = source
        self.agent_id = agent_id
        self.task_id = task_id

    @property
    def workspace_id(self) -> UUID | None:
        if self.api_key_obj:
            return self.api_key_obj.workspace_id
        return None


async def _auth_tunnel(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    api_key: str | None = Security(_api_key_header),
    db: AsyncSession = Depends(get_db),
) -> _TunnelAuth:
    """Accept JWT Bearer or API key, determine audit source from headers."""
    # -- Parse audit metadata from headers ------------------------------------
    raw_agent_id = request.headers.get("X-HiveFlows-Agent-ID")
    raw_task_id = request.headers.get("X-HiveFlows-Task-ID")
    raw_app_id = request.headers.get("X-HiveFlows-App-ID")

    agent_id: UUID | None = None
    task_id: UUID | None = None
    try:
        if raw_agent_id:
            agent_id = UUID(raw_agent_id)
    except ValueError:
        pass
    try:
        if raw_task_id:
            task_id = UUID(raw_task_id)
    except ValueError:
        pass

    # -- JWT Bearer -----------------------------------------------------------
    if credentials:
        payload = decode_token(credentials.credentials)
        if payload and payload.get("type") == "access":
            result = await db.execute(
                select(User).where(User.id == payload.get("sub"), User.is_active == True)
            )
            user = result.scalar_one_or_none()
            if user:
                source = PortalAuditSource.agent if agent_id else PortalAuditSource.dashboard
                return _TunnelAuth(user=user, api_key_obj=None, source=source, agent_id=agent_id, task_id=task_id)

    # -- DB API key -----------------------------------------------------------
    if api_key:
        key_hash = hash_api_key(api_key)
        result = await db.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
        )
        db_key = result.scalar_one_or_none()
        if db_key:
            if agent_id:
                source = PortalAuditSource.agent
            elif raw_app_id:
                source = PortalAuditSource.app
            else:
                source = PortalAuditSource.unknown
            return _TunnelAuth(user=None, api_key_obj=db_key, source=source, agent_id=agent_id, task_id=task_id)

    # -- Legacy .env API key --------------------------------------------------
    if api_key and api_key in settings.api_keys_list:
        source = PortalAuditSource.agent if agent_id else PortalAuditSource.unknown
        return _TunnelAuth(user=None, api_key_obj=None, source=source, agent_id=agent_id, task_id=task_id)

    raise HTTPException(status_code=401, detail="Invalid or missing authentication.")


# ── Audit log (fire-and-forget) ───────────────────────────────────────────────

async def _write_audit(
    device_id: UUID,
    auth: _TunnelAuth,
    service_slug: str,
    method: str,
    path: str,
    status_code: int | None,
    duration_ms: int,
    bytes_out: int,
    bytes_in: int,
    error: str | None,
) -> None:
    try:
        async with async_session() as db:
            entry = PortalAuditLog(
                device_id=device_id,
                agent_id=auth.agent_id,
                task_id=auth.task_id,
                source=auth.source,
                service_slug=service_slug,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                bytes_out=bytes_out,
                bytes_in=bytes_in,
                error=error,
                ts=datetime.now(timezone.utc),
            )
            db.add(entry)
            await db.commit()
    except Exception:
        logger.exception("portal audit log write failed")


# ── Tunnel handler ────────────────────────────────────────────────────────────

ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@router.api_route(
    "/tunnel/{workspace_slug}/{device_slug}/{service_slug}/{path:path}",
    methods=ALL_METHODS,
)
async def tunnel(
    workspace_slug: str,
    device_slug: str,
    service_slug: str,
    path: str,
    request: Request,
    auth: _TunnelAuth = Depends(_auth_tunnel),
    db: AsyncSession = Depends(get_db),
) -> Response:
    t_start = time.monotonic()

    # 1. Resolve workspace by slug ------------------------------------------
    ws_result = await db.execute(
        select(Workspace).where(Workspace.slug == workspace_slug, Workspace.is_active == True)
    )
    workspace = ws_result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_slug}' not found.")

    # 2. Verify caller has access to the workspace ---------------------------
    if auth.user:
        mem = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == auth.user.id,
            )
        )
        if not mem.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Not a member of this workspace.")
    elif auth.api_key_obj and auth.api_key_obj.workspace_id != workspace.id:
        raise HTTPException(status_code=403, detail="API key does not belong to this workspace.")
    # Legacy / legacy-key has no workspace scoping — allow through

    # 3. Load device ---------------------------------------------------------
    dev_result = await db.execute(
        select(PortalDevice).where(
            PortalDevice.workspace_id == workspace.id,
            PortalDevice.slug == device_slug,
        )
    )
    device = dev_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{device_slug}' not found.")

    # 4. Check device status -------------------------------------------------
    if device.status != PortalDeviceStatus.connected:
        raise HTTPException(status_code=503, detail=f"Device '{device_slug}' is not connected.")

    # 5. Check service is exposed by device ----------------------------------
    services = device.services or []
    service_names = {s["name"] for s in services if isinstance(s, dict) and "name" in s}
    if service_slug not in service_names:
        raise HTTPException(status_code=404, detail=f"Service '{service_slug}' not exposed by device.")

    # 6. Get live connection from registry -----------------------------------
    conn = portal_registry.get(device.id)
    if conn is None:
        raise HTTPException(status_code=503, detail=f"Device '{device_slug}' has no active connection.")

    # 7. Read + validate body ------------------------------------------------
    body_bytes = await request.body()
    if len(body_bytes) > settings.portal_max_body_bytes:
        raise HTTPException(status_code=413, detail="Request body exceeds 10 MB limit.")

    body_b64 = base64.b64encode(body_bytes).decode()
    bytes_out = len(body_bytes)

    # 8. Filter outbound headers ---------------------------------------------
    # Strip Authorization only if the caller used it to auth to the tunnel
    # (JWT Bearer), to avoid leaking HiveFlows credentials upstream.
    # API-key callers may carry Authorization for their upstream service.
    filtered_headers = _filter_request_headers(request.headers, strip_auth=auth.user is not None)

    # 9. Forward via WebSocket -----------------------------------------------
    forward_path = "/" + path if path else "/"
    method = request.method

    error_msg: str | None = None
    status_code: int | None = None
    response_body = b""
    response_headers: dict[str, str] = {}

    try:
        resp_msg = await conn.send_request(
            service=service_slug,
            method=method,
            path=forward_path,
            headers=filtered_headers,
            body_b64=body_b64,
        )
    except PortalTimeoutError as exc:
        error_msg = str(exc)
        duration_ms = int((time.monotonic() - t_start) * 1000)
        asyncio.create_task(_write_audit(
            device.id, auth, service_slug, method, forward_path,
            None, duration_ms, bytes_out, 0, error_msg,
        ))
        raise HTTPException(status_code=504, detail="Device did not respond in time.")
    except PortalConnectionLostError as exc:
        error_msg = str(exc)
        duration_ms = int((time.monotonic() - t_start) * 1000)
        asyncio.create_task(_write_audit(
            device.id, auth, service_slug, method, forward_path,
            None, duration_ms, bytes_out, 0, error_msg,
        ))
        raise HTTPException(status_code=502, detail="Device connection lost.")

    # 10. Parse RESPONSE / ERROR ---------------------------------------------
    if resp_msg.get("type") == "error":
        code = resp_msg.get("code", "")
        error_msg = resp_msg.get("message", code)
        duration_ms = int((time.monotonic() - t_start) * 1000)
        asyncio.create_task(_write_audit(
            device.id, auth, service_slug, method, forward_path,
            502, duration_ms, bytes_out, 0, error_msg,
        ))
        raise HTTPException(status_code=502, detail=f"Device error: {error_msg}")

    # type == "response"
    status_code = int(resp_msg.get("status", 200))
    raw_body_b64: str = resp_msg.get("body_b64") or ""
    response_body = base64.b64decode(raw_body_b64) if raw_body_b64 else b""
    bytes_in = len(response_body)

    if bytes_in > settings.portal_max_body_bytes:
        error_msg = "Response body exceeds 10 MB limit"
        duration_ms = int((time.monotonic() - t_start) * 1000)
        asyncio.create_task(_write_audit(
            device.id, auth, service_slug, method, forward_path,
            413, duration_ms, bytes_out, bytes_in, error_msg,
        ))
        raise HTTPException(status_code=413, detail="Response body exceeds 10 MB limit.")

    raw_resp_headers: dict = resp_msg.get("headers") or {}
    response_headers = _filter_response_headers(raw_resp_headers)

    # 11. Audit log (fire-and-forget) ----------------------------------------
    duration_ms = int((time.monotonic() - t_start) * 1000)
    asyncio.create_task(_write_audit(
        device.id, auth, service_slug, method, forward_path,
        status_code, duration_ms, bytes_out, bytes_in, None,
    ))

    # 12. Return response ----------------------------------------------------
    return Response(
        content=response_body,
        status_code=status_code,
        headers=response_headers,
    )
