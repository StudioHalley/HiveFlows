"""K4 — Workspace isolation end-to-end tests.

Verifies that agents, conversations, and tasks are properly scoped
per workspace and cannot leak across workspace boundaries.

Run with:
    HIVEFLOWS_CORE_URL=http://localhost:8002 HIVEFLOWS_API_KEY=<key> \
    pytest tests/test_core/test_isolation.py -v
"""
from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "http://localhost:8002")
API_KEY = os.environ.get("HIVEFLOWS_API_KEY", "")


@pytest.fixture(autouse=True)
def skip_if_no_server():
    try:
        resp = httpx.get(f"{CORE_URL}/health", timeout=5)
        if resp.status_code != 200:
            pytest.skip("Server not reachable")
    except httpx.HTTPError:
        pytest.skip("Server not reachable")


def _create_user(prefix: str) -> tuple[str, str, dict]:
    """Register + login, return (email, access_token, register_data)."""
    uid = uuid.uuid4().hex[:8]
    email = f"iso_{prefix}_{uid}@hiveflows.dev"
    reg = httpx.post(
        f"{CORE_URL}/auth/register",
        json={"email": email, "display_name": prefix.title(), "password": "testpass123"},
        timeout=10,
    )
    assert reg.status_code == 201, f"Register failed: {reg.text}"
    login = httpx.post(
        f"{CORE_URL}/auth/login",
        json={"email": email, "password": "testpass123"},
        timeout=10,
    )
    assert login.status_code == 200
    return email, login.json()["access_token"], reg.json()


def _auth(token: str, ws_id: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if ws_id:
        headers["X-Workspace-Id"] = ws_id
    return headers


class TestWorkspaceIsolation:
    """Two users, two workspaces — verify no data leaks between them."""

    @pytest.fixture(autouse=True)
    def setup_two_workspaces(self):
        # User A with workspace A
        self.email_a, self.token_a, reg_a = _create_user("alice")
        self.ws_a_id = reg_a["workspace"]["id"]

        # User B with workspace B
        self.email_b, self.token_b, reg_b = _create_user("bob")
        self.ws_b_id = reg_b["workspace"]["id"]

        # Create an agent in workspace A
        self.agent_a_id = f"iso-agent-a-{uuid.uuid4().hex[:6]}"
        resp = httpx.post(
            f"{CORE_URL}/agents",
            json={
                "id": self.agent_a_id,
                "name": "Alice Agent",
                "llm_config": {"model": "openai/gpt-4o-mini"},
                "system_prompt": "Tu es un agent de test.",
            },
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=10,
        )
        assert resp.status_code == 201, f"Create agent failed: {resp.text}"
        yield
        # Cleanup
        httpx.delete(f"{CORE_URL}/agents/{self.agent_a_id}",
                     headers=_auth(self.token_a, self.ws_a_id), timeout=5)

    def test_agent_not_visible_in_other_workspace(self):
        """Agent created in workspace A is not listed in workspace B."""
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_b, self.ws_b_id),
            timeout=10,
        )
        assert resp.status_code == 200
        agent_ids = [a["id"] for a in resp.json()]
        assert self.agent_a_id not in agent_ids

    def test_agent_visible_in_own_workspace(self):
        """Agent created in workspace A is listed in workspace A."""
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=10,
        )
        assert resp.status_code == 200
        agent_ids = [a["id"] for a in resp.json()]
        assert self.agent_a_id in agent_ids

    def test_user_b_cannot_get_agent_a_details(self):
        """User B cannot GET /agents/{id} for an agent in workspace A."""
        resp = httpx.get(
            f"{CORE_URL}/agents/{self.agent_a_id}",
            headers=_auth(self.token_b, self.ws_b_id),
            timeout=10,
        )
        assert resp.status_code == 404

    def test_user_b_cannot_update_agent_a(self):
        """User B cannot PUT /agents/{id} for an agent in workspace A."""
        resp = httpx.put(
            f"{CORE_URL}/agents/{self.agent_a_id}",
            json={"name": "Hacked"},
            headers=_auth(self.token_b, self.ws_b_id),
            timeout=10,
        )
        assert resp.status_code == 404

    def test_user_b_cannot_delete_agent_a(self):
        """User B cannot DELETE /agents/{id} for an agent in workspace A."""
        resp = httpx.delete(
            f"{CORE_URL}/agents/{self.agent_a_id}",
            headers=_auth(self.token_b, self.ws_b_id),
            timeout=10,
        )
        assert resp.status_code == 404

    def test_user_b_cannot_access_workspace_a(self):
        """User B cannot GET /workspaces/{id} for workspace A."""
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{self.ws_a_id}",
            headers=_auth(self.token_b),
            timeout=10,
        )
        assert resp.status_code == 403

    def test_user_b_cannot_list_workspace_a_members(self):
        """User B cannot GET /workspaces/{id}/members for workspace A."""
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{self.ws_a_id}/members",
            headers=_auth(self.token_b),
            timeout=10,
        )
        assert resp.status_code == 403

    def test_workspaces_list_only_shows_own(self):
        """GET /workspaces only returns workspaces the user is a member of."""
        resp_a = httpx.get(
            f"{CORE_URL}/workspaces",
            headers=_auth(self.token_a),
            timeout=10,
        )
        resp_b = httpx.get(
            f"{CORE_URL}/workspaces",
            headers=_auth(self.token_b),
            timeout=10,
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

        ws_a_ids = {w["id"] for w in resp_a.json()}
        ws_b_ids = {w["id"] for w in resp_b.json()}

        assert self.ws_a_id in ws_a_ids
        assert self.ws_a_id not in ws_b_ids
        assert self.ws_b_id in ws_b_ids
        assert self.ws_b_id not in ws_a_ids

    def test_shared_member_sees_both_workspaces(self):
        """When user B is added to workspace A, they see both workspaces."""
        # Add user B to workspace A as member
        resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_a_id}/members",
            json={"email": self.email_b, "role": "member"},
            headers=_auth(self.token_a),
            timeout=10,
        )
        assert resp.status_code == 201

        # User B now sees both workspaces
        resp = httpx.get(
            f"{CORE_URL}/workspaces",
            headers=_auth(self.token_b),
            timeout=10,
        )
        assert resp.status_code == 200
        ws_ids = {w["id"] for w in resp.json()}
        assert self.ws_a_id in ws_ids
        assert self.ws_b_id in ws_ids

    def test_audit_logs_scoped_by_workspace_id(self):
        """Audit logs returned are filtered by workspace_id parameter."""
        resp_a = httpx.get(
            f"{CORE_URL}/workspaces/{self.ws_a_id}/audit",
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=10,
        )
        assert resp_a.status_code == 200
        data = resp_a.json()
        # All returned logs should belong to workspace A
        for item in data.get("items", []):
            if item.get("workspace_id"):
                assert item["workspace_id"] == self.ws_a_id
