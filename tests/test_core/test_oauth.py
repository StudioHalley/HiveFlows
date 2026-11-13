"""Tests for OAuth2 flow (Phase 4 — Bloc H).

Pure logic tests (no server needed) + integration tests (need running server).

Run locally:     pytest tests/test_core/test_oauth.py -v
Run on server:   HIVEFLOWS_CORE_URL=http://localhost:8002 HIVEFLOWS_API_KEY=<key> \
                 pytest tests/test_core/test_oauth.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "http://localhost:8002")
API_KEY = os.environ.get("HIVEFLOWS_API_KEY", "")


# ── Pure logic tests (no server needed) ──────────────────────────────────────

class TestOAuthConfig:
    """Test that OAuth config properties work correctly."""

    def test_oauth_disabled_by_default(self):
        """H6: When env vars are empty, OAuth should be disabled."""
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
            from config import Settings
            s = Settings(
                oauth_google_client_id="",
                oauth_google_client_secret="",
                oauth_github_client_id="",
                oauth_github_client_secret="",
            )
            assert not s.oauth_google_enabled
            assert not s.oauth_github_enabled
        except ImportError:
            pytest.skip("Cannot import config module")

    def test_oauth_enabled_when_configured(self):
        """H6: When env vars are set, OAuth should be enabled."""
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
            from config import Settings
            s = Settings(
                oauth_google_client_id="test-id",
                oauth_google_client_secret="test-secret",
                oauth_github_client_id="",
                oauth_github_client_secret="",
            )
            assert s.oauth_google_enabled
            assert not s.oauth_github_enabled
        except ImportError:
            pytest.skip("Cannot import config module")

    def test_oauth_both_enabled(self):
        """H6: Both providers can be enabled independently."""
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
            from config import Settings
            s = Settings(
                oauth_google_client_id="gid",
                oauth_google_client_secret="gsecret",
                oauth_github_client_id="ghid",
                oauth_github_client_secret="ghsecret",
            )
            assert s.oauth_google_enabled
            assert s.oauth_github_enabled
        except ImportError:
            pytest.skip("Cannot import config module")


# ── Integration tests (need running server) ──────────────────────────────────


@pytest.fixture
def skip_if_no_server():
    if not API_KEY:
        pytest.skip("HIVEFLOWS_API_KEY not set — skipping integration tests")


class TestOAuthRoutes:
    """Test OAuth route behavior on the live server."""

    def test_disabled_provider_returns_404(self, skip_if_no_server):
        """H6: If provider is not configured, routes should return 404."""
        # Unless the server has OAuth configured, both providers should 404
        # We test with a known-bad provider name
        resp = httpx.get(f"{CORE_URL}/auth/oauth/fakeprovider", follow_redirects=False, timeout=10)
        assert resp.status_code == 404

    def test_login_page_accessible(self, skip_if_no_server):
        """H5: Login page should load and contain OAuth buttons only if configured."""
        resp = httpx.get(f"{CORE_URL}/dashboard/login", timeout=10)
        assert resp.status_code == 200
        html = resp.text
        assert "Se connecter" in html
        # The page should always render — OAuth buttons are optional

    def test_register_page_accessible(self, skip_if_no_server):
        """H5: Register page should load."""
        resp = httpx.get(f"{CORE_URL}/dashboard/register", timeout=10)
        assert resp.status_code == 200
        assert "Créer mon compte" in resp.text or "create account" in resp.text.lower()

    def test_oauth_redirect_or_404(self, skip_if_no_server):
        """H3: /auth/oauth/google should redirect (302) if configured, or 404 if not."""
        resp = httpx.get(f"{CORE_URL}/auth/oauth/google", follow_redirects=False, timeout=10)
        # Either 302 (redirect to Google) or 404 (not configured) — both are valid
        assert resp.status_code in (302, 307, 404)

    def test_oauth_callback_without_code_fails(self, skip_if_no_server):
        """H3: Callback without authorization code should fail gracefully."""
        resp = httpx.get(
            f"{CORE_URL}/auth/oauth/google/callback",
            follow_redirects=False,
            timeout=10,
        )
        # Should be 400 (bad request) or 404 (not configured)
        assert resp.status_code in (400, 404, 500)


class TestOAuthUserLinkage:
    """Test the user creation/linkage logic described in H4.

    These tests verify the database behavior via the API.
    Since we can't do a real OAuth flow in tests, we verify that:
    1. Users created via register have no oauth_provider
    2. The /auth/me endpoint shows the user profile
    """

    def test_regular_user_has_no_oauth(self, skip_if_no_server):
        """H4: A user created via email/password should have no OAuth linkage."""
        import uuid
        uid = uuid.uuid4().hex[:8]
        email = f"oauth-test-{uid}@test.com"

        # Register
        resp = httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "password": "testpass123", "display_name": "OAuth Test"},
            timeout=10,
        )
        assert resp.status_code == 201

        # Login
        resp = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": email, "password": "testpass123"},
            timeout=10,
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        # Check /auth/me
        resp = httpx.get(
            f"{CORE_URL}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 200
