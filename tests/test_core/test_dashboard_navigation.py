"""Dashboard navigation tests — Phase 4 Bloc I.

Tests that all dashboard pages return 200 for authenticated users.
Requires a running server with at least one user + workspace.

Run with:
    HIVEFLOWS_CORE_URL=http://localhost:8002 pytest tests/test_core/test_dashboard_navigation.py -v

The tests will register a temporary user and use its session cookie.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "http://localhost:8002")

# Unique test user for this run
_TEST_EMAIL = f"navtest_{uuid.uuid4().hex[:8]}@hiveflows.dev"
_TEST_PASSWORD = "testpass123"
_TEST_NAME = "Nav Test User"


@pytest.fixture(scope="module")
def auth_client() -> httpx.Client:
    """Register a user, login via dashboard, return a client with auth cookie."""
    client = httpx.Client(base_url=CORE_URL, timeout=10, follow_redirects=False)

    # Register via API
    resp = client.post(
        "/auth/register",
        json={"email": _TEST_EMAIL, "display_name": _TEST_NAME, "password": _TEST_PASSWORD},
    )
    if resp.status_code not in (201, 409):
        pytest.skip(f"Cannot register test user: {resp.status_code} {resp.text}")

    # Login via dashboard form (sets hf_token cookie)
    resp = client.post(
        "/dashboard/login",
        data={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # Should redirect to /dashboard (303)
    assert resp.status_code == 303, f"Login failed: {resp.status_code}"

    # Cookie should be set
    assert "hf_token" in client.cookies, "No auth cookie after login"

    yield client
    client.close()


@pytest.fixture(scope="module")
def workspace_slug(auth_client: httpx.Client) -> str:
    """Get the default workspace slug from the redirect."""
    resp = auth_client.get("/dashboard", follow_redirects=False)
    # Should redirect to /dashboard/w/{slug}/
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    # Extract slug from /dashboard/w/{slug}/
    parts = location.rstrip("/").split("/")
    slug = parts[-1] if len(parts) >= 4 else None
    assert slug, f"Could not extract slug from redirect: {location}"
    return slug


class TestPublicPages:
    """Pages that should be accessible without auth."""

    def test_login_page(self):
        resp = httpx.get(f"{CORE_URL}/dashboard/login", timeout=5)
        assert resp.status_code == 200
        assert "HiveFlows" in resp.text

    def test_register_page(self):
        resp = httpx.get(f"{CORE_URL}/dashboard/register", timeout=5)
        assert resp.status_code == 200


class TestAuthenticatedPages:
    """Pages that require authentication — all should return 200."""

    def test_dashboard_redirects(self, auth_client, workspace_slug):
        resp = auth_client.get("/dashboard", follow_redirects=True)
        assert resp.status_code == 200

    def test_workspace_index(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/")
        assert resp.status_code == 200

    def test_chat_page(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/chat")
        assert resp.status_code == 200
        assert "chat" in resp.text.lower()

    def test_agents_page(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/agents")
        assert resp.status_code == 200

    def test_tasks_page(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/tasks")
        assert resp.status_code == 200

    def test_audit_page(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/audit")
        assert resp.status_code == 200

    def test_conversations_page(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/conversations")
        assert resp.status_code == 200
        assert "conversation" in resp.text.lower()

    def test_profile_page(self, auth_client, workspace_slug):
        resp = auth_client.get("/dashboard/profile")
        assert resp.status_code == 200
        assert _TEST_EMAIL in resp.text

    def test_workspaces_list(self, auth_client, workspace_slug):
        resp = auth_client.get("/dashboard/workspaces")
        assert resp.status_code == 200


class TestProfileActions:
    """Test profile update actions."""

    def test_update_display_name(self, auth_client, workspace_slug):
        new_name = f"Updated {uuid.uuid4().hex[:4]}"
        resp = auth_client.post(
            "/dashboard/profile",
            data={"display_name": new_name, "email": _TEST_EMAIL},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert new_name in resp.text

    def test_change_password(self, auth_client, workspace_slug):
        resp = auth_client.post(
            "/dashboard/profile/password",
            data={
                "current_password": _TEST_PASSWORD,
                "new_password": "newpass456",
                "confirm_password": "newpass456",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert "mis a jour" in resp.text.lower()

        # Change back so other tests still work
        auth_client.post(
            "/dashboard/profile/password",
            data={
                "current_password": "newpass456",
                "new_password": _TEST_PASSWORD,
                "confirm_password": _TEST_PASSWORD,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def test_password_mismatch_rejected(self, auth_client, workspace_slug):
        resp = auth_client.post(
            "/dashboard/profile/password",
            data={
                "current_password": _TEST_PASSWORD,
                "new_password": "aaaaaa",
                "confirm_password": "bbbbbb",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 400


class TestNavigationElements:
    """Test that navigation elements are present on pages."""

    def test_sidebar_has_all_nav_items(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/")
        html = resp.text
        assert "/chat" in html
        assert "/conversations" in html
        assert "/agents" in html
        assert "/tasks" in html
        assert "/audit" in html
        assert "/workspaces" in html

    def test_breadcrumbs_present(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/chat")
        assert "breadcrumb" in resp.text

    def test_workspace_selector_present(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/")
        assert "ws-selector" in resp.text

    def test_profile_link_in_sidebar(self, auth_client, workspace_slug):
        resp = auth_client.get(f"/dashboard/w/{workspace_slug}/")
        assert "/dashboard/profile" in resp.text


class TestUnauthenticatedRedirects:
    """Pages that should redirect to login without auth."""

    def test_dashboard_redirects_to_login(self):
        resp = httpx.get(f"{CORE_URL}/dashboard", timeout=5, follow_redirects=False)
        assert resp.status_code == 307
        assert "/login" in resp.headers.get("location", "")

    def test_profile_redirects_to_login(self):
        resp = httpx.get(f"{CORE_URL}/dashboard/profile", timeout=5, follow_redirects=False)
        assert resp.status_code == 307
        assert "/login" in resp.headers.get("location", "")
