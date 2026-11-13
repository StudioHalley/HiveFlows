"""Audit logging service — Phase 4 Bloc G.

Usage:
    from audit import audit_log
    await audit_log(db, action="chat.invoke", user_id=user.id, workspace_id=ws_id,
                    resource_type="conversation", resource_id=conv_id,
                    details={"agent_id": "assistant"}, ip=request.client.host)
"""

import logging
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from models.audit_log import AuditLog

logger = logging.getLogger("hiveflows.audit")


async def audit_log(
    db: AsyncSession,
    *,
    action: str,
    user_id: UUID | None = None,
    workspace_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    """Insert an audit log entry."""
    entry = AuditLog(
        user_id=user_id,
        workspace_id=workspace_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=ip,
    )
    db.add(entry)
    await db.flush()
    logger.debug("audit: %s user=%s ws=%s res=%s/%s", action, user_id, workspace_id, resource_type, resource_id)
    return entry


def get_client_ip(request: Request) -> str | None:
    """Extract client IP from the request (supports X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None
