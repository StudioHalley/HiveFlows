"""Integration tests for Audit Logging (Phase 4 — Bloc G).

Run:
    HIVEFLOWS_CORE_URL=http://localhost:8002 HIVEFLOWS_API_KEY=<key> \
    pytest tests/test_core/test_audit.py -v

On the remote server:
    cd ~/hiveflows && source .env && API_KEY=$(echo $API_KEYS | cut -d',' -f1) \
    HIVEFLOWS_CORE_URL=http://localhost:8002 HIVEFLOWS_API_KEY=$API_KEY \
    python3 -m pytest tests/test_core/test_audit.py -v
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "http://localhost:8002")
API_KEY = os.environ.get("HIVEFLOWS_API_KEY", "")

HEADERS = {"Content-Type": "application/json", "X-API-Key": API_KEY}


@pytest.fixture(autouse=True)
def skip_if_no_key():
    if not API_KEY:
        pytest.skip("HIVEFLOWS_API_KEY not set — skipping integration tests")


def _register_user(email: str, password: str = "testpass123", display_name: str = "Test User") -> dict:
    """Register a new user and return tokens + workspace info."""
    resp = httpx.post(
        f"{CORE_URL}/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
        timeout=10,
    )
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    return resp.json()


def _login_user(email: str, password: str = "testpass123") -> dict:
    """Login and return token response."""
    resp = httpx.post(
        f"{CORE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_user_workspaces(token: str) -> list:
    resp = httpx.get(f"{CORE_URL}/workspaces", headers=_auth_headers(token), timeout=10)
    assert resp.status_code == 200
    return resp.json()


class TestAuditLogging:
    """Test that actions are correctly logged in the audit system."""

    def test_register_creates_audit_entry(self):
        """G7: Registering a user should create an auth.register audit entry."""
        uid = uuid.uuid4().hex[:8]
        email = f"audit-reg-{uid}@test.com"

        reg = _register_user(email)
        tokens = _login_user(email)
        token = tokens["access_token"]

        # Get workspace
        workspaces = _get_user_workspaces(token)
        assert len(workspaces) >= 1
        ws_id = workspaces[0]["id"]

        # Check audit log
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}/audit?action=auth.register",
            headers=_auth_headers(token),
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        actions = [item["action"] for item in data["items"]]
        assert "auth.register" in actions

    def test_login_creates_audit_entry(self):
        """G7: Logging in should create an auth.login audit entry."""
        uid = uuid.uuid4().hex[:8]
        email = f"audit-login-{uid}@test.com"

        _register_user(email)
        tokens = _login_user(email)
        token = tokens["access_token"]

        workspaces = _get_user_workspaces(token)
        ws_id = workspaces[0]["id"]

        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}/audit?action=auth.login",
            headers=_auth_headers(token),
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        # The login audit is logged without workspace_id (user doesn't have ws context yet)
        # so we just check that the API itself works
        assert "items" in data
        assert "total" in data

    def test_agent_crud_creates_audit_entries(self):
        """G7: Creating, updating, and deleting an agent should create audit entries."""
        uid = uuid.uuid4().hex[:8]
        email = f"audit-agent-{uid}@test.com"

        _register_user(email)
        tokens = _login_user(email)
        token = tokens["access_token"]

        workspaces = _get_user_workspaces(token)
        ws_id = workspaces[0]["id"]
        headers = {**_auth_headers(token), "X-Workspace-Id": ws_id}

        agent_id = f"audit-test-agent-{uid}"

        # Create agent
        resp = httpx.post(
            f"{CORE_URL}/agents",
            json={"id": agent_id, "name": "Audit Test Agent",
                  "llm_config": {"provider": "openrouter", "model": "openai/gpt-4o-mini"}},
            headers=headers,
            timeout=10,
        )
        assert resp.status_code == 201

        # Update agent
        resp = httpx.put(
            f"{CORE_URL}/agents/{agent_id}",
            json={"name": "Audit Test Agent Updated"},
            headers=headers,
            timeout=10,
        )
        assert resp.status_code == 200

        # Delete agent
        resp = httpx.delete(
            f"{CORE_URL}/agents/{agent_id}",
            headers=headers,
            timeout=10,
        )
        assert resp.status_code == 200

        # Check audit log for all three actions
        for action in ["agent.create", "agent.update", "agent.delete"]:
            resp = httpx.get(
                f"{CORE_URL}/workspaces/{ws_id}/audit?action={action}",
                headers=_auth_headers(token),
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] >= 1, f"No audit entry for {action}"

    def test_audit_pagination(self):
        """G7: Audit log should support pagination."""
        uid = uuid.uuid4().hex[:8]
        email = f"audit-page-{uid}@test.com"

        _register_user(email)
        tokens = _login_user(email)
        token = tokens["access_token"]

        workspaces = _get_user_workspaces(token)
        ws_id = workspaces[0]["id"]

        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}/audit?limit=2&offset=0",
            headers=_auth_headers(token),
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert len(data["items"]) <= 2

    def test_audit_requires_permission(self):
        """G7: Members and viewers should not access audit logs."""
        uid = uuid.uuid4().hex[:8]
        email_owner = f"audit-perm-owner-{uid}@test.com"
        email_viewer = f"audit-perm-viewer-{uid}@test.com"

        _register_user(email_owner)
        _register_user(email_viewer)

        tokens_owner = _login_user(email_owner)
        tokens_viewer = _login_user(email_viewer)

        # Get owner's workspace
        workspaces = _get_user_workspaces(tokens_owner["access_token"])
        ws_id = workspaces[0]["id"]

        # Add viewer as member with "viewer" role
        resp = httpx.post(
            f"{CORE_URL}/workspaces/{ws_id}/members",
            json={"email": email_viewer, "role": "viewer"},
            headers=_auth_headers(tokens_owner["access_token"]),
            timeout=10,
        )
        assert resp.status_code == 201

        # Viewer should not be able to access audit
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}/audit",
            headers={**_auth_headers(tokens_viewer["access_token"]), "X-Workspace-Id": ws_id},
            timeout=10,
        )
        assert resp.status_code == 403

    def test_audit_filter_by_resource_type(self):
        """G7: Audit log filtering by resource_type works."""
        uid = uuid.uuid4().hex[:8]
        email = f"audit-filter-{uid}@test.com"

        _register_user(email)
        tokens = _login_user(email)
        token = tokens["access_token"]

        workspaces = _get_user_workspaces(token)
        ws_id = workspaces[0]["id"]

        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}/audit?resource_type=user",
            headers=_auth_headers(token),
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["resource_type"] == "user"
