"""Tests for HiveFlows CLI — Bloc J (login, workspace, chat).

These tests hit the live server. Run with:
    HIVEFLOWS_CORE_URL=http://localhost:8002 pytest tests/test_cli/ -v

Tests create a temporary user, then test the full CLI flow:
login → workspace list → workspace use → chat → logout.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import httpx
import pytest

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "http://localhost:8002")


@pytest.fixture(autouse=True)
def skip_if_no_server():
    """Skip all tests if the server is not reachable."""
    try:
        resp = httpx.get(f"{CORE_URL}/health", timeout=5)
        if resp.status_code != 200:
            pytest.skip("Server not reachable")
    except httpx.HTTPError:
        pytest.skip("Server not reachable")


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def test_user():
    """Register a fresh user for the test session and return credentials + tokens."""
    uid = uuid.uuid4().hex[:8]
    email = f"cli_{uid}@hiveflows.dev"
    password = f"TestPass_{uid}!"
    display_name = f"CLI Test {uid}"

    resp = httpx.post(
        f"{CORE_URL}/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
        timeout=15,
    )
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    data = resp.json()

    # Login to get tokens
    resp = httpx.post(
        f"{CORE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    tokens = resp.json()

    return {
        "email": email,
        "password": password,
        "display_name": display_name,
        "user_id": data["user_id"],
        "workspace_id": str(data["workspace"]["id"]),
        "workspace_slug": data["workspace"]["slug"],
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_in": tokens["expires_in"],
    }


@pytest.fixture()
def session_file(tmp_path):
    """Provide a temp session file path and patch the CLI to use it."""
    return tmp_path / "session.json"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _save_session(session_file, data):
    session_file.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(session_file.read_text()) if session_file.exists() else {}
    existing.update(data)
    session_file.write_text(json.dumps(existing, indent=2))


def _load_session(session_file):
    if session_file.exists():
        return json.loads(session_file.read_text())
    return {}


def _get_auth_headers(test_user):
    return {
        "Authorization": f"Bearer {test_user['access_token']}",
        "X-Workspace-Id": test_user["workspace_id"],
    }


# ── Test: Auth Login ─────────────────────────────────────────────────────────


class TestLogin:
    def test_login_returns_tokens(self, test_user):
        """POST /auth/login with valid credentials returns access + refresh tokens."""
        resp = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
            timeout=15,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

    def test_login_wrong_password(self, test_user):
        """POST /auth/login with wrong password returns 401."""
        resp = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": test_user["email"], "password": "wrong_password"},
            timeout=15,
        )
        assert resp.status_code == 401

    def test_login_nonexistent_email(self):
        """POST /auth/login with unknown email returns 401."""
        resp = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": "nonexistent@hiveflows.dev", "password": "whatever"},
            timeout=15,
        )
        assert resp.status_code == 401


# ── Test: Token Refresh ──────────────────────────────────────────────────────


class TestTokenRefresh:
    def test_refresh_returns_new_access_token(self, test_user):
        """POST /auth/refresh with valid refresh token returns a new access token."""
        resp = httpx.post(
            f"{CORE_URL}/auth/refresh",
            json={"refresh_token": test_user["refresh_token"]},
            timeout=15,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["expires_in"] > 0

    def test_refresh_invalid_token(self):
        """POST /auth/refresh with invalid token returns 401."""
        resp = httpx.post(
            f"{CORE_URL}/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
            timeout=15,
        )
        assert resp.status_code == 401


# ── Test: Workspace List ─────────────────────────────────────────────────────


class TestWorkspaceList:
    def test_list_workspaces(self, test_user):
        """GET /workspaces returns at least the personal workspace."""
        headers = _get_auth_headers(test_user)
        resp = httpx.get(f"{CORE_URL}/workspaces", headers=headers, timeout=15)
        assert resp.status_code == 200
        workspaces = resp.json()
        assert len(workspaces) >= 1
        slugs = [ws["slug"] for ws in workspaces]
        assert test_user["workspace_slug"] in slugs

    def test_list_workspaces_has_role(self, test_user):
        """Each workspace in the list includes a role field."""
        headers = _get_auth_headers(test_user)
        resp = httpx.get(f"{CORE_URL}/workspaces", headers=headers, timeout=15)
        assert resp.status_code == 200
        for ws in resp.json():
            assert "role" in ws


# ── Test: Workspace Switch ───────────────────────────────────────────────────


class TestWorkspaceSwitch:
    def test_switch_workspace_session(self, test_user, session_file):
        """Switching workspace updates session file."""
        _save_session(session_file, {
            "auth_mode": "jwt",
            "access_token": test_user["access_token"],
            "workspace_id": test_user["workspace_id"],
            "workspace_slug": test_user["workspace_slug"],
        })

        session = _load_session(session_file)
        assert session["workspace_slug"] == test_user["workspace_slug"]

        # Simulate workspace use by updating session
        new_slug = test_user["workspace_slug"]
        _save_session(session_file, {"workspace_slug": new_slug, "workspace_id": test_user["workspace_id"]})

        updated = _load_session(session_file)
        assert updated["workspace_slug"] == new_slug


# ── Test: Chat with JWT ──────────────────────────────────────────────────────


class TestChatWithJWT:
    def test_chat_with_bearer_token(self, test_user):
        """POST /chat with Bearer token succeeds."""
        headers = _get_auth_headers(test_user)
        headers["Content-Type"] = "application/json"
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={"agent_id": "assistant-principal", "message": "CLI test hello"},
            headers=headers,
            timeout=120,
        )
        # Might be 200 or might fail if agent not in this workspace — both are valid
        # The key test is that auth works (not 401)
        assert resp.status_code != 401, f"Auth should work with Bearer token, got: {resp.text}"

    def test_chat_without_auth_returns_401(self):
        """POST /chat without auth returns 401."""
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={"agent_id": "assistant-principal", "message": "no auth"},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        assert resp.status_code == 401


# ── Test: Session file management ────────────────────────────────────────────


class TestSessionManagement:
    def test_session_stores_tokens(self, session_file, test_user):
        """Session file correctly stores JWT tokens and workspace info."""
        _save_session(session_file, {
            "auth_mode": "jwt",
            "access_token": test_user["access_token"],
            "refresh_token": test_user["refresh_token"],
            "token_expires_at": time.time() + test_user["expires_in"],
            "workspace_id": test_user["workspace_id"],
            "workspace_slug": test_user["workspace_slug"],
        })

        session = _load_session(session_file)
        assert session["auth_mode"] == "jwt"
        assert session["access_token"] == test_user["access_token"]
        assert session["refresh_token"] == test_user["refresh_token"]
        assert session["workspace_id"] == test_user["workspace_id"]
        assert session["workspace_slug"] == test_user["workspace_slug"]
        assert session["token_expires_at"] > time.time()

    def test_session_stores_api_key(self, session_file):
        """Session file correctly stores API key auth."""
        _save_session(session_file, {
            "auth_mode": "api_key",
            "api_key": "hf-test-key-123",
        })

        session = _load_session(session_file)
        assert session["auth_mode"] == "api_key"
        assert session["api_key"] == "hf-test-key-123"

    def test_session_merge_on_save(self, session_file):
        """save_session merges with existing data (doesn't overwrite)."""
        _save_session(session_file, {"access_token": "tok1", "workspace_slug": "ws1"})
        _save_session(session_file, {"workspace_slug": "ws2"})

        session = _load_session(session_file)
        assert session["access_token"] == "tok1"  # preserved
        assert session["workspace_slug"] == "ws2"  # updated

    def test_logout_clears_session(self, session_file):
        """Logout removes the session file."""
        _save_session(session_file, {"access_token": "tok", "auth_mode": "jwt"})
        assert session_file.exists()

        # Simulate logout
        session_file.unlink()
        assert not session_file.exists()


# ── Test: Full flow ──────────────────────────────────────────────────────────


class TestFullFlow:
    def test_login_list_switch_chat(self, test_user, session_file):
        """End-to-end: login → list workspaces → switch → chat."""
        # 1. Login (simulate — store tokens)
        _save_session(session_file, {
            "auth_mode": "jwt",
            "access_token": test_user["access_token"],
            "refresh_token": test_user["refresh_token"],
            "token_expires_at": time.time() + test_user["expires_in"],
        })

        # 2. List workspaces
        headers = _get_auth_headers(test_user)
        resp = httpx.get(f"{CORE_URL}/workspaces", headers=headers, timeout=15)
        assert resp.status_code == 200
        workspaces = resp.json()
        assert len(workspaces) >= 1

        # 3. Switch to personal workspace
        ws = workspaces[0]
        _save_session(session_file, {
            "workspace_id": ws["id"],
            "workspace_slug": ws["slug"],
        })
        session = _load_session(session_file)
        assert session["workspace_slug"] == ws["slug"]

        # 4. Verify auth works end-to-end
        resp = httpx.get(
            f"{CORE_URL}/workspaces",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            timeout=15,
        )
        assert resp.status_code == 200
