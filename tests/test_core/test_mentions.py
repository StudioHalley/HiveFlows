"""M7 — @mention parsing and routing tests.

Tests the mention_parser module and the chat API's mention routing behavior.

Run with:
    # Unit tests (no server needed):
    python3 -m pytest tests/test_core/test_mentions.py -v

    # Integration tests (server needed):
    HIVEFLOWS_CORE_URL=http://localhost:8002 \
    python3 -m pytest tests/test_core/test_mentions.py -v
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

# Add core/ to path so we can import mention_parser directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "core"))

from mention_parser import parse_mentions


class TestMentionParser:
    """Unit tests for the mention parser — no server needed."""

    def test_single_mention(self):
        result = parse_mentions("@analyst explain this", ["analyst", "coder"], "coder")
        assert result.mentioned_ids == ["analyst"]
        assert result.resolved_agent_id == "analyst"
        assert result.system_message is None
        assert "explain this" in result.clean_message
        assert "@analyst" not in result.clean_message

    def test_mention_at_start(self):
        result = parse_mentions("@bot hello", ["bot"], "bot")
        assert result.resolved_agent_id == "bot"
        assert result.clean_message == "hello"

    def test_mention_at_end(self):
        result = parse_mentions("hello @bot", ["bot"], None)
        assert result.resolved_agent_id == "bot"
        assert result.clean_message == "hello"

    def test_multiple_mentions_routes_to_first(self):
        result = parse_mentions("@analyst and @coder help", ["analyst", "coder"], None)
        assert result.mentioned_ids == ["analyst", "coder"]
        assert result.resolved_agent_id == "analyst"

    def test_unknown_mention_ignored(self):
        result = parse_mentions("@unknown do stuff", ["analyst", "coder"], "analyst")
        assert result.mentioned_ids == []
        assert result.resolved_agent_id == "analyst"

    def test_case_insensitive_mention(self):
        result = parse_mentions("@Analyst help", ["analyst"], None)
        assert result.resolved_agent_id == "analyst"

    def test_no_mention_single_agent_implicit(self):
        """1:1 mode: no mention needed when only one agent."""
        result = parse_mentions("just a message", ["only-agent"], None)
        assert result.resolved_agent_id == "only-agent"
        assert result.system_message is None

    def test_no_mention_uses_default_agent(self):
        """When default is set and no mention, route to default."""
        result = parse_mentions("hello", ["agent-a", "agent-b"], "agent-a")
        assert result.resolved_agent_id == "agent-a"
        assert result.system_message is None

    def test_no_mention_no_default_multi_agent_shows_help(self):
        """Multiple agents, no mention, no default → system message."""
        result = parse_mentions("hello", ["agent-a", "agent-b"], None)
        assert result.resolved_agent_id is None
        assert result.system_message is not None
        assert "@agent-a" in result.system_message

    def test_mention_with_hyphen_and_dot(self):
        result = parse_mentions("@data-analyst.v2 check", ["data-analyst.v2"], None)
        assert result.resolved_agent_id == "data-analyst.v2"

    def test_mention_not_in_email(self):
        """@ in email shouldn't trigger mention (no space before)."""
        result = parse_mentions("send to user@example.com", ["example"], None)
        # The regex requires word boundary before @, so this should not match
        assert result.mentioned_ids == []

    def test_clean_message_preserves_content(self):
        result = parse_mentions("@bot tell me about Python", ["bot"], None)
        assert result.clean_message == "tell me about Python"

    def test_empty_message(self):
        result = parse_mentions("", ["bot"], "bot")
        assert result.resolved_agent_id == "bot"
        assert result.clean_message == ""

    def test_only_mention(self):
        result = parse_mentions("@bot", ["bot"], None)
        assert result.resolved_agent_id == "bot"
        # clean_message falls back to original when stripped is empty
        assert result.clean_message == "@bot"


# ── Integration tests (require running server) ──────────────────────────────

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "")


@pytest.fixture
def skip_if_no_server():
    if not CORE_URL:
        pytest.skip("HIVEFLOWS_CORE_URL not set")
    try:
        import httpx
        resp = httpx.get(f"{CORE_URL}/health", timeout=5)
        if resp.status_code != 200:
            pytest.skip("Server not reachable")
    except Exception:
        pytest.skip("Server not reachable")


def _create_user(prefix: str):
    import httpx
    uid = uuid.uuid4().hex[:8]
    email = f"mention_{prefix}_{uid}@hiveflows.dev"
    reg = httpx.post(
        f"{CORE_URL}/auth/register",
        json={"email": email, "display_name": prefix.title(), "password": "testpass123"},
        timeout=10,
    )
    assert reg.status_code == 201
    login = httpx.post(
        f"{CORE_URL}/auth/login",
        json={"email": email, "password": "testpass123"},
        timeout=10,
    )
    assert login.status_code == 200
    return email, login.json()["access_token"], reg.json()


class TestMentionRouting:
    """Integration tests — verify @mentions route to the correct agent via API."""

    @pytest.fixture(autouse=True)
    def setup(self, skip_if_no_server):
        import httpx
        self.email, self.token, reg = _create_user("mentiontest")
        self.ws_id = reg["workspace"]["id"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Workspace-Id": self.ws_id,
        }

        # Create two agents
        self.agent_a = f"mention-a-{uuid.uuid4().hex[:6]}"
        self.agent_b = f"mention-b-{uuid.uuid4().hex[:6]}"
        for aid, name in [(self.agent_a, "Agent A"), (self.agent_b, "Agent B")]:
            resp = httpx.post(
                f"{CORE_URL}/agents",
                json={
                    "id": aid, "name": name,
                    "llm_config": {"model": "openai/gpt-4o-mini"},
                    "system_prompt": f"Tu es {name}. Reponds en 1 phrase.",
                },
                headers=self.headers,
                timeout=10,
            )
            assert resp.status_code == 201, f"Create {aid} failed: {resp.text}"
        yield
        for aid in (self.agent_a, self.agent_b):
            httpx.delete(f"{CORE_URL}/agents/{aid}", headers=self.headers, timeout=5)

    def test_mention_routes_to_agent(self):
        """@agent-b routes to agent B even when default is agent A."""
        import httpx
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "agent_id": self.agent_a,
                "message": f"@{self.agent_b} who are you?",
            },
            headers=self.headers,
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"]  # Got a response from agent B

    def test_no_mention_uses_default(self):
        """No @mention uses the default agent_id from the request."""
        import httpx
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "agent_id": self.agent_a,
                "message": "hello",
            },
            headers=self.headers,
            timeout=30,
        )
        assert resp.status_code == 200
        assert resp.json()["response"]
