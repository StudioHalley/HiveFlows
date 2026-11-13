"""Portal enrollment service.

Handles:
  - Device creation (generates slug + enroll token)
  - Enrollment validation (consumes enroll token, sets permanent token)
  - Revocation (nullifies token_hash, sends REVOKE on WS)

Token conventions:
  - Enroll token  : 32 bytes base64url, stored as SHA256 hash in portal_enroll_tokens
  - Permanent token: 32 bytes base64url, stored as SHA256 hash in portal_devices.token_hash
  Both tokens are returned to the caller in plain text exactly once, never persisted in clear.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.portal import PortalAuditSource, PortalDevice, PortalDeviceStatus, PortalEnrollToken


# ── Token helpers ─────────────────────────────────────────────────────────────

ENROLL_TTL_MINUTES = 15


def _gen_token() -> str:
    """Generate a 32-byte base64url-safe token (no padding)."""
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Slug helpers ──────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Convert a device name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-") or "device"
    return slug[:64]


async def unique_device_slug(db: AsyncSession, workspace_id: UUID, base: str) -> str:
    """Return *base* if unique in the workspace, else *base-2*, *base-3*, …"""
    candidate = base
    for n in range(2, 100):
        result = await db.execute(
            select(PortalDevice).where(
                PortalDevice.workspace_id == workspace_id,
                PortalDevice.slug == candidate,
            )
        )
        if not result.scalar_one_or_none():
            return candidate
        candidate = f"{base}-{n}"
    # Extremely unlikely, but fall back to random suffix
    return f"{base}-{secrets.token_hex(4)}"


# ── Device creation ───────────────────────────────────────────────────────────

async def create_device(
    db: AsyncSession,
    workspace_id: UUID,
    name: str,
) -> tuple[PortalDevice, PortalEnrollToken, str]:
    """Create a new portal device (status=pending) and its enroll token.

    Returns (device, enroll_token_row, plain_enroll_token).
    The plain token must be shown to the user exactly once.
    """
    slug = await unique_device_slug(db, workspace_id, _slugify(name))

    device = PortalDevice(
        workspace_id=workspace_id,
        slug=slug,
        name=name,
        token_hash=None,
        services=[],
        status=PortalDeviceStatus.pending,
    )
    db.add(device)
    await db.flush()  # populate device.id

    plain_token = _gen_token()
    token = PortalEnrollToken(
        workspace_id=workspace_id,
        device_name=name,
        token_hash=_hash_token(plain_token),
        expires_at=datetime.utcnow() + timedelta(minutes=ENROLL_TTL_MINUTES),
    )
    db.add(token)
    await db.commit()
    await db.refresh(device)
    await db.refresh(token)

    return device, token, plain_token


# ── Enrollment ────────────────────────────────────────────────────────────────

class EnrollmentError(Exception):
    """Raised when the enroll token is invalid, expired, or already used."""


async def enroll_device(
    db: AsyncSession,
    plain_enroll_token: str,
    device_slug: str,
    workspace_id: UUID,
) -> tuple[PortalDevice, str]:
    """Consume an enroll token and assign a permanent token to the device.

    Returns (device, plain_permanent_token).
    Raises EnrollmentError on any validation failure.
    """
    now = datetime.utcnow()
    token_hash = _hash_token(plain_enroll_token)

    # 1. Lookup enroll token
    tok_result = await db.execute(
        select(PortalEnrollToken).where(
            PortalEnrollToken.workspace_id == workspace_id,
            PortalEnrollToken.token_hash == token_hash,
        )
    )
    enroll_tok = tok_result.scalar_one_or_none()
    if not enroll_tok:
        raise EnrollmentError("Invalid enroll token.")
    if enroll_tok.used_at is not None:
        raise EnrollmentError("Enroll token already used.")
    expires = enroll_tok.expires_at.replace(tzinfo=None) if enroll_tok.expires_at.tzinfo else enroll_tok.expires_at
    if now > expires:
        raise EnrollmentError("Enroll token has expired.")

    # 2. Lookup pending device
    dev_result = await db.execute(
        select(PortalDevice).where(
            PortalDevice.workspace_id == workspace_id,
            PortalDevice.slug == device_slug,
            PortalDevice.status == PortalDeviceStatus.pending,
        )
    )
    device = dev_result.scalar_one_or_none()
    if not device:
        raise EnrollmentError(f"Device '{device_slug}' not found or not in pending state.")

    # 3. Generate permanent token
    plain_perm = _gen_token()

    # 4. Persist
    await db.execute(
        update(PortalDevice)
        .where(PortalDevice.id == device.id)
        .values(token_hash=_hash_token(plain_perm), status=PortalDeviceStatus.disconnected)
    )
    await db.execute(
        update(PortalEnrollToken)
        .where(PortalEnrollToken.id == enroll_tok.id)
        .values(used_at=now)
    )
    await db.commit()
    await db.refresh(device)

    return device, plain_perm


# ── Revocation ────────────────────────────────────────────────────────────────

async def revoke_device(db: AsyncSession, device: PortalDevice) -> None:
    """Set token_hash=None, status=revoked, and send REVOKE on any live WS.

    The caller is responsible for the 204 response; this function only
    handles DB state + registry cleanup.
    """
    from services.portal_registry import portal_registry  # avoid circular at module level

    await db.execute(
        update(PortalDevice)
        .where(PortalDevice.id == device.id)
        .values(token_hash=None, status=PortalDeviceStatus.revoked)
    )
    await db.commit()

    # Fire-and-forget REVOKE on live WS
    await portal_registry.revoke(device.id)
