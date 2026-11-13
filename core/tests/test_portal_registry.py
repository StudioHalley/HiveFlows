"""Unit tests — PortalRegistry & PortalConnection (Bloc B).

Tests run with standard pytest (no external DB needed).
Async helpers use asyncio.run() so pytest-asyncio is not required.

Coverage:
  - connexion: register + get
  - HELLO invalide: validation helpers
  - disconnect propre: fail_all_pending cancels in-flight Futures
  - corrélation req/resp: send_request resolved by handle_message
  - timeout: send_request raises PortalTimeoutError
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.portal_registry import (
    PortalConnection,
    PortalConnectionLostError,
    PortalRegistry,
    PortalTimeoutError,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_ws() -> MagicMock:
    """Minimal mock WebSocket that records send_text calls."""
    ws = MagicMock()
    ws.sent: list[str] = []

    async def _send(text: str) -> None:
        ws.sent.append(text)

    ws.send_text = _send
    return ws


def _make_conn(ws=None, services=None) -> PortalConnection:
    return PortalConnection(
        ws=ws or _make_ws(),
        device_id=uuid.uuid4(),
        device_slug="test-device",
        workspace_id=uuid.uuid4(),
        services=services or [{"name": "homeassistant", "route": "http://ha:8123"}],
    )


def run(coro):
    return asyncio.run(coro)


# ── connexion ─────────────────────────────────────────────────────────────────

class TestPortalRegistry:
    def test_register_and_get(self):
        registry = PortalRegistry()
        conn = _make_conn()
        registry.register(conn)
        assert registry.get(conn.device_id) is conn

    def test_get_unknown_returns_none(self):
        registry = PortalRegistry()
        assert registry.get(uuid.uuid4()) is None

    def test_is_connected_true_after_register(self):
        registry = PortalRegistry()
        conn = _make_conn()
        registry.register(conn)
        assert registry.is_connected(conn.device_id) is True

    def test_remove_clears_slot(self):
        registry = PortalRegistry()
        conn = _make_conn()
        registry.register(conn)
        registry.remove(conn.device_id)
        assert registry.get(conn.device_id) is None
        assert registry.is_connected(conn.device_id) is False

    def test_remove_unknown_is_noop(self):
        registry = PortalRegistry()
        registry.remove(uuid.uuid4())  # must not raise

    def test_revoke_sends_revoke_message(self):
        registry = PortalRegistry()
        ws = _make_ws()
        conn = _make_conn(ws=ws)
        registry.register(conn)

        run(registry.revoke(conn.device_id))

        assert registry.get(conn.device_id) is None
        assert any(
            json.loads(m).get("type") == "revoke" for m in ws.sent
        ), "REVOKE message not sent"


# ── HELLO invalide ────────────────────────────────────────────────────────────

class TestHelloValidation:
    """Validation logic extracted from the route (tested as pure functions)."""

    def _validate_hello(self, raw: str) -> tuple[bool, str]:
        """Replicate the validation in routes_portal.py."""
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return False, "Invalid JSON"
        if msg.get("type") != "hello":
            return False, "Expected HELLO"
        return True, ""

    def test_valid_hello_accepted(self):
        raw = json.dumps({"type": "hello", "version": "1", "services": []})
        ok, _ = self._validate_hello(raw)
        assert ok is True

    def test_wrong_type_rejected(self):
        raw = json.dumps({"type": "ping"})
        ok, reason = self._validate_hello(raw)
        assert ok is False
        assert "HELLO" in reason

    def test_invalid_json_rejected(self):
        ok, reason = self._validate_hello("not-json{{{")
        assert ok is False
        assert "JSON" in reason

    def test_empty_string_rejected(self):
        ok, _ = self._validate_hello("")
        assert ok is False


# ── corrélation req/resp ──────────────────────────────────────────────────────

class TestRequestResponseCorrelation:
    def test_send_request_resolved_by_handle_message(self):
        ws = _make_ws()
        conn = _make_conn(ws=ws)

        async def _run():
            async def _respond():
                # Yield to let send_request register the Future
                await asyncio.sleep(0)
                req = json.loads(ws.sent[-1])
                assert req["type"] == "request"
                conn.handle_message({
                    "type": "response",
                    "id": req["id"],
                    "status": 200,
                    "headers": {},
                    "body_b64": "aGVsbG8=",
                })

            task = asyncio.create_task(_respond())
            result = await conn.send_request(
                service="homeassistant",
                method="GET",
                path="/api/states",
                headers={},
                body_b64="",
            )
            await task
            return result

        result = run(_run())
        assert result["status"] == 200
        assert result["body_b64"] == "aGVsbG8="

    def test_handle_message_error_type_also_resolves(self):
        ws = _make_ws()
        conn = _make_conn(ws=ws)

        async def _run():
            async def _respond():
                await asyncio.sleep(0)
                req = json.loads(ws.sent[-1])
                conn.handle_message({
                    "type": "error",
                    "id": req["id"],
                    "code": "service_not_allowed",
                    "message": "blocked",
                })

            task = asyncio.create_task(_respond())
            result = await conn.send_request("homeassistant", "GET", "/x", {}, "")
            await task
            return result

        result = run(_run())
        assert result["type"] == "error"
        assert result["code"] == "service_not_allowed"

    def test_unknown_req_id_is_ignored(self):
        conn = _make_conn()
        conn.handle_message({"type": "response", "id": "nonexistent", "status": 200})
        # Must not raise


# ── timeout ───────────────────────────────────────────────────────────────────

class TestTimeout:
    def test_send_request_raises_portal_timeout_error(self):
        ws = _make_ws()
        conn = _make_conn(ws=ws)

        async def _run():
            with pytest.raises(PortalTimeoutError):
                await conn.send_request(
                    "homeassistant", "GET", "/slow", {}, "", timeout=0.01
                )

        run(_run())

    def test_pending_request_cleared_after_timeout(self):
        ws = _make_ws()
        conn = _make_conn(ws=ws)

        async def _run():
            with pytest.raises(PortalTimeoutError):
                await conn.send_request("homeassistant", "GET", "/x", {}, "", timeout=0.01)
            assert len(conn._pending) == 0

        run(_run())


# ── disconnect propre ─────────────────────────────────────────────────────────

class TestDisconnect:
    def test_fail_all_pending_raises_connection_lost(self):
        ws = _make_ws()
        conn = _make_conn(ws=ws)

        async def _run():
            # Start a request but don't resolve it
            send_task = asyncio.create_task(
                conn.send_request("homeassistant", "GET", "/x", {}, "", timeout=10)
            )
            await asyncio.sleep(0)  # let the task register the future
            conn.fail_all_pending()

            with pytest.raises(PortalConnectionLostError):
                await send_task

        run(_run())

    def test_pending_dict_empty_after_fail_all(self):
        conn = _make_conn()
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        conn._pending["fake_id"] = fut
        conn.fail_all_pending()
        assert len(conn._pending) == 0
        loop.close()

    def test_registry_remove_triggers_fail_all(self):
        registry = PortalRegistry()
        ws = _make_ws()
        conn = _make_conn(ws=ws)
        registry.register(conn)

        async def _run():
            send_task = asyncio.create_task(
                conn.send_request("homeassistant", "GET", "/x", {}, "", timeout=10)
            )
            await asyncio.sleep(0)
            registry.remove(conn.device_id)

            with pytest.raises(PortalConnectionLostError):
                await send_task

        run(_run())

    def test_service_names_property(self):
        conn = _make_conn(services=[
            {"name": "ha", "route": "http://ha:8123"},
            {"name": "vlc", "route": "http://vlc:8004"},
        ])
        assert conn.service_names == {"ha", "vlc"}
