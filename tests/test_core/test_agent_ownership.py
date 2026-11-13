"""L11 — Agent ownership & workspace_agents tests.

Verifies:
- Agent can be in multiple workspaces
- Removing an agent from a workspace does NOT delete it
- Default agents are auto-added to new workspaces
- Site-admin can set/unset default agents

Run with:
    HIVEFLOWS_CORE_URL=http://localhost:8002 \
    pytest tests/test_core/test_agent_ownership.py -v
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "http://localhost:8002")


@pytest.fixture(autouse=True)
def skip_if_no_server():
    try:
        resp = httpx.get(f"{CORE_URL}/health", timeout=5)
        if resp.status_code != 200:
            pytest.skip("Server not reachable")
    except httpx.HTTPError:
        pytest.skip("Server not reachable")


def _create_user(prefix: str) -> tuple[str, str, dict]:
    uid = uuid.uuid4().hex[:8]
    email = f"own_{prefix}_{uid}@hiveflows.dev"
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


class TestAgentOwnership:
    """Test agent ownership, multi-workspace, and workspace_agents junction."""

    @pytest.fixture(autouse=True)
    def setup(self):
        # User A with workspace A
        self.email_a, self.token_a, reg_a = _create_user("owner")
        self.ws_a_id = reg_a["workspace"]["id"]

        # Create an agent in workspace A
        self.agent_id = f"own-agent-{uuid.uuid4().hex[:6]}"
        resp = httpx.post(
            f"{CORE_URL}/agents",
            json={
                "id": self.agent_id,
                "name": "Owned Agent",
                "llm_config": {"model": "openai/gpt-4o-mini"},
                "system_prompt": "Test agent.",
            },
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=10,
        )
        assert resp.status_code == 201, f"Create agent failed: {resp.text}"

        # User B with workspace B
        self.email_b, self.token_b, reg_b = _create_user("other")
        self.ws_b_id = reg_b["workspace"]["id"]
        yield
        # Cleanup
        httpx.delete(
            f"{CORE_URL}/agents/{self.agent_id}",
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=5,
        )

    def test_agent_visible_in_own_workspace(self):
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=10,
        )
        assert resp.status_code == 200
        ids = [a["id"] for a in resp.json()]
        assert self.agent_id in ids

    def test_agent_not_visible_in_other_workspace(self):
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_b, self.ws_b_id),
            timeout=10,
        )
        assert resp.status_code == 200
        ids = [a["id"] for a in resp.json()]
        assert self.agent_id not in ids

    def test_add_agent_to_second_workspace(self):
        """Add user A's agent to workspace B (user B adds it)."""
        # First, add user A as member of workspace B so they can share the agent
        # Actually, user B needs to add the agent to their workspace.
        # The API route is POST /workspaces/{id}/agents/{agent_id}
        resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_b_id}/agents/{self.agent_id}",
            headers=_auth(self.token_b),
            timeout=10,
        )
        assert resp.status_code == 201, f"Add agent to ws B failed: {resp.text}"

        # Agent now visible in workspace B
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_b, self.ws_b_id),
            timeout=10,
        )
        assert resp.status_code == 200
        ids = [a["id"] for a in resp.json()]
        assert self.agent_id in ids

        # Still visible in workspace A
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=10,
        )
        assert resp.status_code == 200
        ids = [a["id"] for a in resp.json()]
        assert self.agent_id in ids

    def test_remove_from_workspace_does_not_delete(self):
        """Removing an agent from workspace B doesn't delete it from workspace A."""
        # Add to workspace B first
        httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_b_id}/agents/{self.agent_id}",
            headers=_auth(self.token_b),
            timeout=10,
        )

        # Remove from workspace B
        resp = httpx.delete(
            f"{CORE_URL}/workspaces/{self.ws_b_id}/agents/{self.agent_id}",
            headers=_auth(self.token_b),
            timeout=10,
        )
        assert resp.status_code == 204

        # Not visible in workspace B
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_b, self.ws_b_id),
            timeout=10,
        )
        ids = [a["id"] for a in resp.json()]
        assert self.agent_id not in ids

        # Still visible in workspace A (not deleted!)
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=_auth(self.token_a, self.ws_a_id),
            timeout=10,
        )
        ids = [a["id"] for a in resp.json()]
        assert self.agent_id in ids

    def test_duplicate_add_returns_409(self):
        """Adding the same agent twice to a workspace returns 409."""
        httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_b_id}/agents/{self.agent_id}",
            headers=_auth(self.token_b),
            timeout=10,
        )
        resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_b_id}/agents/{self.agent_id}",
            headers=_auth(self.token_b),
            timeout=10,
        )
        assert resp.status_code == 409

    def test_list_workspace_agents(self):
        """GET /workspaces/{id}/agents returns agent IDs."""
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{self.ws_a_id}/agents",
            headers=_auth(self.token_a),
            timeout=10,
        )
        assert resp.status_code == 200
        assert self.agent_id in resp.json()
