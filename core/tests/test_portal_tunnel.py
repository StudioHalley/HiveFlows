"""Unit tests — tunnel endpoint /tunnel/{ws_slug}/{dev_slug}/{svc_slug}/{path}.

All tests mock the DB and PortalRegistry — no real DB or WS needed.
Uses FastAPI TestClient (sync) with overridden dependencies.

Coverage:
  - succès (happy path GET + POST)
  - device absent → 404
  - service non exposé → 404
  - device non connecté (status != connected) → 503
  - device absent du registry → 503
  - timeout → 504
  - révocation (upstream_unreachable error from device) → 502
  - connection lost → 502
  - body too large → 413
  - auth invalide → 401
  - workspace access denied → 403
"""

import asyncio
import base64
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# We need to bootstrap the test app without the full main.py
from api.routes_tunnel import _auth_tunnel, _TunnelAuth, router as tunnel_router
from models.portal import PortalAuditSource, PortalDevice, PortalDeviceStatus
from models.workspace import Workspace, WorkspaceMember
from services.portal_registry import PortalConnectionLostError, PortalTimeoutError


# ── Fixtures ──────────────────────────────────────────────────────────────────

WS_ID = uuid.uuid4()
DEV_ID = uuid.uuid4()
USER_ID = uuid.uuid4()

_WORKSPACE = MagicMock(spec=Workspace)
_WORKSPACE.id = WS_ID
_WORKSPACE.slug = "acme"
_WORKSPACE.is_active = True

_DEVICE_CONNECTED = MagicMock(spec=PortalDevice)
_DEVICE_CONNECTED.id = DEV_ID
_DEVICE_CONNECTED.slug = "nas-maison"
_DEVICE_CONNECTED.workspace_id = WS_ID
_DEVICE_CONNECTED.status = PortalDeviceStatus.connected
_DEVICE_CONNECTED.services = [
    {"name": "homeassistant", "route": "http://ha:8123"},
    {"name": "vlc", "route": "http://vlc:8004"},
]

_DEVICE_DISCONNECTED = MagicMock(spec=PortalDevice)
_DEVICE_DISCONNECTED.id = DEV_ID
_DEVICE_DISCONNECTED.slug = "nas-maison"
_DEVICE_DISCONNECTED.workspace_id = WS_ID
_DEVICE_DISCONNECTED.status = PortalDeviceStatus.disconnected
_DEVICE_DISCONNECTED.services = [{"name": "homeassistant", "route": "http://ha:8123"}]

_MEMBER = MagicMock(spec=WorkspaceMember)
_MEMBER.workspace_id = WS_ID


def _default_auth() -> _TunnelAuth:
    return _TunnelAuth(
        user=None,
        api_key_obj=None,
        source=PortalAuditSource.agent,
        agent_id=uuid.uuid4(),
        task_id=None,
    )


def _make_app(auth: _TunnelAuth | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the tunnel router and mocked auth."""
    app = FastAPI()
    app.include_router(tunnel_router)

    resolved_auth = auth or _default_auth()
    app.dependency_overrides[_auth_tunnel] = lambda: resolved_auth
    return app


def _make_db_mock(workspace=_WORKSPACE, device=_DEVICE_CONNECTED, member=_MEMBER):
    """Build an async DB mock that returns the given objects in order."""
    db = AsyncMock()

    ws_result = MagicMock()
    ws_result.scalar_one_or_none.return_value = workspace

    dev_result = MagicMock()
    dev_result.scalar_one_or_none.return_value = device

    mem_result = MagicMock()
    mem_result.scalar_one_or_none.return_value = member

    db.execute.side_effect = [ws_result, mem_result, dev_result]
    return db


def _mock_conn(response: dict | None = None, raise_exc: Exception | None = None):
    """Return a PortalConnection mock with controlled send_request behavior."""
    conn = AsyncMock()
    if raise_exc:
        conn.send_request.side_effect = raise_exc
    else:
        conn.send_request.return_value = response or {
            "type": "response",
            "id": "req_1",
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body_b64": base64.b64encode(b'{"ok": true}').decode(),
        }
    return conn


def _url(path: str = "") -> str:
    return f"/tunnel/acme/nas-maison/homeassistant/{path}"


# ── Happy path ────────────────────────────────────────────────────────────────

class TestTunnelSuccess:
    def test_get_returns_200(self):
        app = _make_app()
        db = _make_db_mock()
        conn = _mock_conn()

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("api.routes_tunnel._write_audit", new_callable=AsyncMock), \
             patch("asyncio.create_task"):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get(_url("api/states"))

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_post_with_body_forwarded(self):
        app = _make_app()
        db = _make_db_mock()
        body = b'{"light": "on"}'
        conn = _mock_conn(response={
            "type": "response",
            "id": "req_2",
            "status": 204,
            "headers": {},
            "body_b64": "",
        })

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("api.routes_tunnel._write_audit", new_callable=AsyncMock), \
             patch("asyncio.create_task"):
            client = TestClient(app)
            resp = client.post(_url("api/services/light"), content=body)

        assert resp.status_code == 204
        # Verify body was base64-encoded and passed on
        call_kwargs = conn.send_request.call_args
        assert call_kwargs.kwargs["body_b64"] == base64.b64encode(body).decode()

    def test_response_headers_forwarded(self):
        app = _make_app()
        db = _make_db_mock()
        conn = _mock_conn(response={
            "type": "response",
            "id": "req_3",
            "status": 200,
            "headers": {"X-Custom": "myvalue", "Content-Type": "text/plain"},
            "body_b64": base64.b64encode(b"hello").decode(),
        })

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("api.routes_tunnel._write_audit", new_callable=AsyncMock), \
             patch("asyncio.create_task"):
            client = TestClient(app)
            resp = client.get(_url("hello"))

        assert resp.headers.get("x-custom") == "myvalue"

    def test_hop_by_hop_headers_stripped_from_response(self):
        app = _make_app()
        db = _make_db_mock()
        conn = _mock_conn(response={
            "type": "response",
            "id": "req_4",
            "status": 200,
            "headers": {"Connection": "keep-alive", "Set-Cookie": "sess=abc", "Content-Type": "text/plain"},
            "body_b64": base64.b64encode(b"ok").decode(),
        })

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("api.routes_tunnel._write_audit", new_callable=AsyncMock), \
             patch("asyncio.create_task"):
            client = TestClient(app)
            resp = client.get(_url("x"))

        assert "set-cookie" not in resp.headers
        assert "connection" not in resp.headers


# ── Device absent ─────────────────────────────────────────────────────────────

class TestDeviceNotFound:
    def test_returns_404_when_device_slug_unknown(self):
        app = _make_app()
        db = _make_db_mock(device=None)

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=None):
            client = TestClient(app)
            resp = client.get(_url("api/states"))

        assert resp.status_code == 404
        assert "nas-maison" in resp.json()["detail"]

    def test_returns_404_when_workspace_unknown(self):
        app = _make_app()
        db = _make_db_mock(workspace=None)

        with patch("api.routes_tunnel.get_db", return_value=db):
            client = TestClient(app)
            resp = client.get(_url("x"))

        assert resp.status_code == 404


# ── Service non exposé ────────────────────────────────────────────────────────

class TestServiceNotExposed:
    def test_returns_404_when_service_not_in_device_services(self):
        app = FastAPI()
        app.include_router(tunnel_router)
        app.dependency_overrides[_auth_tunnel] = lambda: _default_auth()

        db = _make_db_mock()
        conn = _mock_conn()

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn):
            client = TestClient(app)
            # "unknown-svc" is not in _DEVICE_CONNECTED.services
            resp = client.get("/tunnel/acme/nas-maison/unknown-svc/path")

        assert resp.status_code == 404
        assert "unknown-svc" in resp.json()["detail"]


# ── Device non connecté ───────────────────────────────────────────────────────

class TestDeviceNotConnected:
    def test_returns_503_when_status_disconnected(self):
        app = _make_app()
        db = _make_db_mock(device=_DEVICE_DISCONNECTED)

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=None):
            client = TestClient(app)
            resp = client.get(_url("x"))

        assert resp.status_code == 503

    def test_returns_503_when_not_in_registry(self):
        app = _make_app()
        db = _make_db_mock()  # device is connected in DB

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=None):
            client = TestClient(app)
            resp = client.get(_url("x"))

        assert resp.status_code == 503


# ── Timeout ───────────────────────────────────────────────────────────────────

class TestTimeout:
    def test_returns_504_on_portal_timeout(self):
        app = _make_app()
        db = _make_db_mock()
        conn = _mock_conn(raise_exc=PortalTimeoutError("timed out"))

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("asyncio.create_task"):
            client = TestClient(app)
            resp = client.get(_url("slow"))

        assert resp.status_code == 504


# ── Révocation / upstream error ───────────────────────────────────────────────

class TestUpstreamError:
    def test_returns_502_on_connection_lost(self):
        app = _make_app()
        db = _make_db_mock()
        conn = _mock_conn(raise_exc=PortalConnectionLostError("ws gone"))

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("asyncio.create_task"):
            client = TestClient(app)
            resp = client.get(_url("x"))

        assert resp.status_code == 502

    def test_returns_502_on_device_error_response(self):
        app = _make_app()
        db = _make_db_mock()
        conn = _mock_conn(response={
            "type": "error",
            "id": "req_e",
            "code": "upstream_unreachable",
            "message": "Connection refused",
        })

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("asyncio.create_task"):
            client = TestClient(app)
            resp = client.get(_url("x"))

        assert resp.status_code == 502
        assert "Connection refused" in resp.json()["detail"]


# ── Body too large ────────────────────────────────────────────────────────────

class TestBodyTooLarge:
    def test_returns_413_when_request_body_exceeds_limit(self):
        app = _make_app()
        db = _make_db_mock()
        conn = _mock_conn()

        big_body = b"x" * (10 * 1024 * 1024 + 1)  # 10 MB + 1 byte

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("api.routes_tunnel.settings") as mock_cfg:
            mock_cfg.portal_max_body_bytes = 10 * 1024 * 1024
            client = TestClient(app)
            resp = client.post(_url("upload"), content=big_body)

        assert resp.status_code == 413

    def test_returns_413_when_response_body_exceeds_limit(self):
        app = _make_app()
        db = _make_db_mock()
        big_b64 = base64.b64encode(b"y" * (10 * 1024 * 1024 + 1)).decode()
        conn = _mock_conn(response={
            "type": "response",
            "id": "req_big",
            "status": 200,
            "headers": {},
            "body_b64": big_b64,
        })

        with patch("api.routes_tunnel.get_db", return_value=db), \
             patch("api.routes_tunnel.portal_registry.get", return_value=conn), \
             patch("api.routes_tunnel.settings") as mock_cfg, \
             patch("asyncio.create_task"):
            mock_cfg.portal_max_body_bytes = 10 * 1024 * 1024
            client = TestClient(app)
            resp = client.get(_url("big"))

        assert resp.status_code == 413


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_returns_401_without_auth(self):
        """Without dependency override the real _auth_tunnel rejects missing credentials."""
        app = FastAPI()
        app.include_router(tunnel_router)
        # No dependency override → real _auth_tunnel runs
        db = AsyncMock()
        db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))

        with patch("api.routes_tunnel.get_db", return_value=db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/tunnel/acme/nas-maison/homeassistant/x")

        assert resp.status_code == 401

    def test_returns_403_when_api_key_workspace_mismatch(self):
        from models.api_key import ApiKey as ApiKeyModel
        api_key_obj = MagicMock(spec=ApiKeyModel)
        api_key_obj.workspace_id = uuid.uuid4()  # different workspace

        auth = _TunnelAuth(
            user=None,
            api_key_obj=api_key_obj,
            source=PortalAuditSource.unknown,
            agent_id=None,
            task_id=None,
        )
        app = _make_app(auth=auth)
        db = _make_db_mock()  # workspace.id == WS_ID, api_key.workspace_id != WS_ID

        with patch("api.routes_tunnel.get_db", return_value=db):
            client = TestClient(app)
            resp = client.get(_url("x"))

        assert resp.status_code == 403


# ── Header filtering ──────────────────────────────────────────────────────────

class TestHeaderFiltering:
    def _run_request_headers(self, headers: dict) -> dict:
        from api.routes_tunnel import _filter_request_headers
        return _filter_request_headers(headers)

    def _run_response_headers(self, headers: dict) -> dict:
        from api.routes_tunnel import _filter_response_headers
        return _filter_response_headers(headers)

    def test_authorization_stripped_from_request(self):
        out = self._run_request_headers({"Authorization": "Bearer secret", "Accept": "application/json"})
        assert "Authorization" not in out
        assert "Accept" in out

    def test_xhiveflows_headers_stripped_from_request(self):
        out = self._run_request_headers({"X-HiveFlows-Agent-ID": "abc", "Content-Type": "application/json"})
        assert "X-HiveFlows-Agent-ID" not in out
        assert "Content-Type" in out

    def test_hop_by_hop_stripped_from_request(self):
        out = self._run_request_headers({"Connection": "keep-alive", "Host": "example.com", "User-Agent": "go"})
        assert "Connection" not in out
        assert "Host" not in out
        assert "User-Agent" in out

    def test_set_cookie_stripped_from_response(self):
        out = self._run_response_headers({"Set-Cookie": "sess=1", "Content-Type": "application/json"})
        assert "Set-Cookie" not in out
        assert "Content-Type" in out

    def test_normal_response_headers_preserved(self):
        out = self._run_response_headers({"X-Custom-Header": "yes", "Content-Length": "42"})
        assert out == {"X-Custom-Header": "yes", "Content-Length": "42"}
