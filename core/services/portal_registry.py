"""PortalRegistry — in-memory registry of active portal device connections.

Each connected portal-client device holds a PortalConnection slot.
The tunnel endpoint uses send_request() to forward HTTP traffic through
the WebSocket and awaits the RESPONSE with an asyncio.Future.

Correlation: each REQUEST gets a unique req_id; the read loop in the WS
route calls handle_message() to resolve the matching Future.
"""

import asyncio
import json
import logging
import secrets
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger("hiveflows.portal")


class PortalTimeoutError(Exception):
    """Device did not respond within the allowed window."""


class PortalConnectionLostError(Exception):
    """Device disconnected while a request was in flight."""


class PortalConnection:
    """Represents one connected portal-client device.

    Thread-safety: all methods must be called from the same event loop.
    """

    def __init__(
        self,
        ws: WebSocket,
        device_id: UUID,
        device_slug: str,
        workspace_id: UUID,
        services: list[dict],
    ):
        self.ws = ws
        self.device_id = device_id
        self.device_slug = device_slug
        self.workspace_id = workspace_id
        self.services = services  # [{name, route}, ...]
        self._pending: dict[str, asyncio.Future] = {}

    @property
    def service_names(self) -> set[str]:
        return {s["name"] for s in self.services if isinstance(s, dict)}

    async def send_request(
        self,
        service: str,
        method: str,
        path: str,
        headers: dict,
        body_b64: str,
        timeout: float = 30.0,
    ) -> dict:
        """Forward an HTTP request to the device and return the RESPONSE message.

        Raises:
            PortalTimeoutError: device did not reply within *timeout* seconds.
            PortalConnectionLostError: WS closed before/during the request.
        """
        req_id = secrets.token_urlsafe(16)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut

        msg = json.dumps({
            "type": "request",
            "id": req_id,
            "service": service,
            "method": method,
            "path": path,
            "headers": headers,
            "body_b64": body_b64,
        })
        try:
            await self.ws.send_text(msg)
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise PortalConnectionLostError(str(exc)) from exc

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise PortalTimeoutError(f"No response from device '{self.device_slug}' within {timeout}s")

    def handle_message(self, msg: dict) -> None:
        """Resolve the Future matching msg['id'] (called from the read loop)."""
        req_id = msg.get("id")
        if not req_id:
            return
        fut = self._pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(msg)

    async def send_revoke(self) -> None:
        """Send a REVOKE message to the device (best-effort, ignores errors)."""
        try:
            await self.ws.send_text(json.dumps({"type": "revoke"}))
        except Exception:
            pass

    def fail_all_pending(self) -> None:
        """Fail every in-flight request with PortalConnectionLostError."""
        exc = PortalConnectionLostError(f"Device '{self.device_slug}' disconnected")
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()


class PortalRegistry:
    """Singleton in-memory map: device_id → PortalConnection."""

    def __init__(self) -> None:
        self._connections: dict[UUID, PortalConnection] = {}

    def register(self, conn: PortalConnection) -> None:
        self._connections[conn.device_id] = conn

    def get(self, device_id: UUID) -> PortalConnection | None:
        return self._connections.get(device_id)

    def remove(self, device_id: UUID) -> None:
        """Remove connection and fail all pending requests."""
        conn = self._connections.pop(device_id, None)
        if conn:
            conn.fail_all_pending()

    async def revoke(self, device_id: UUID) -> None:
        """Send REVOKE, remove, and fail pending requests."""
        conn = self._connections.pop(device_id, None)
        if conn:
            await conn.send_revoke()
            conn.fail_all_pending()

    def is_connected(self, device_id: UUID) -> bool:
        return device_id in self._connections


# Module-level singleton — imported by routes and services
portal_registry = PortalRegistry()
