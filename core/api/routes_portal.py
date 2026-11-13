"""WebSocket endpoint /portal/ws — portal device connection handler.

Auth flow:
  1. Parse Authorization: Bearer <token> from WS handshake headers.
  2. SHA256-hash the token, lookup portal_devices in DB.
  3. Reject (close 1008) if token unknown or device is revoked.
  4. Accept WS, wait for HELLO.
  5. Validate HELLO; close 1008 on invalid.
  6. Register PortalConnection, update DB status=connected.
  7. Read loop: route PING→PONG, RESPONSE/ERROR→future resolution.
  8. On disconnect: set DB status=disconnected, remove from registry.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update

from database import async_session
from models.portal import PortalDevice, PortalDeviceStatus
from services.portal_registry import PortalConnection, portal_registry

logger = logging.getLogger("hiveflows.portal")

router = APIRouter(tags=["portal"])


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.websocket("/portal/ws")
async def portal_ws(websocket: WebSocket) -> None:
    # ── 1. Token from Authorization header ───────────────────────────────────
    auth_header = websocket.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        await websocket.close(code=1008, reason="Missing Bearer token")
        return

    raw_token = auth_header[len("Bearer "):].strip()
    if not raw_token:
        await websocket.close(code=1008, reason="Empty token")
        return

    token_hash = _hash_token(raw_token)

    # ── 2. DB lookup ──────────────────────────────────────────────────────────
    async with async_session() as db:
        result = await db.execute(
            select(PortalDevice).where(PortalDevice.token_hash == token_hash)
        )
        device = result.scalar_one_or_none()

    if not device or device.status == PortalDeviceStatus.revoked or device.token_hash is None:
        await websocket.close(code=1008, reason="Invalid or revoked token")
        return

    # ── 3. Accept WS ──────────────────────────────────────────────────────────
    await websocket.accept()

    # ── 4. Receive HELLO ─────────────────────────────────────────────────────
    try:
        raw = await websocket.receive_text()
    except WebSocketDisconnect:
        return

    try:
        hello = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        await websocket.close(code=1008, reason="Invalid JSON in HELLO")
        return

    if hello.get("type") != "hello":
        await websocket.close(code=1008, reason="Expected HELLO as first message")
        return

    services: list[dict] = hello.get("services") or []

    # ── 5. Register + update DB ───────────────────────────────────────────────
    conn = PortalConnection(
        ws=websocket,
        device_id=device.id,
        device_slug=device.slug,
        workspace_id=device.workspace_id,
        services=services,
    )
    portal_registry.register(conn)

    async with async_session() as db:
        await db.execute(
            update(PortalDevice)
            .where(PortalDevice.id == device.id)
            .values(
                status=PortalDeviceStatus.connected,
                last_seen_at=datetime.utcnow(),
                services=services,
            )
        )
        await db.commit()

    logger.info("Portal device connected: %s (id=%s)", device.slug, device.id)

    # ── 6. Read loop ──────────────────────────────────────────────────────────
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                async with async_session() as db:
                    await db.execute(
                        update(PortalDevice)
                        .where(PortalDevice.id == device.id)
                        .values(last_seen_at=datetime.utcnow())
                    )
                    await db.commit()

            elif msg_type in ("response", "error"):
                conn.handle_message(msg)

    except WebSocketDisconnect:
        logger.info("Portal device disconnected: %s", device.slug)

    finally:
        portal_registry.remove(device.id)
        async with async_session() as db:
            await db.execute(
                update(PortalDevice)
                .where(PortalDevice.id == device.id)
                .values(status=PortalDeviceStatus.disconnected)
            )
            await db.commit()
