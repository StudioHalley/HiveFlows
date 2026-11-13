"""N7 — Slash command parsing and execution tests.

Tests the slash_commands module: parser, registry, and command handlers.

Run with:
    # Unit tests (no server needed):
    pytest tests/test_core/test_slash_commands.py -v

    # In Docker (full integration):
    docker compose exec core python -m pytest /app/tests/test_core/test_slash_commands.py -v
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add core/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "core"))


# ── Unit tests: parser regex (no imports from slash_commands needed) ─────────

# Copy the regex here to test parsing in isolation (no heavy deps)
_SLASH_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9_-]*)\s*(.*)", re.DOTALL)


def _is_slash_command(message: str) -> bool:
    return bool(_SLASH_RE.match(message.strip()))


def _parse_slug(args: str) -> str | None:
    args = args.strip()
    if args.startswith("@"):
        args = args[1:]
    slug = args.split()[0] if args else None
    return slug


class TestSlashParser:
    """Unit tests for slash command detection — no server or deps needed."""

    def test_detects_slash_command(self):
        assert _is_slash_command("/invite @alice") is True

    def test_detects_with_leading_whitespace(self):
        assert _is_slash_command("  /invite @alice") is True

    def test_does_not_detect_normal_message(self):
        assert _is_slash_command("hello world") is False

    def test_does_not_detect_slash_in_middle(self):
        assert _is_slash_command("say /hello") is False

    def test_does_not_detect_bare_slash(self):
        assert _is_slash_command("/") is False

    def test_does_not_detect_slash_number(self):
        assert _is_slash_command("/123") is False

    def test_detects_help(self):
        assert _is_slash_command("/help") is True

    def test_detects_members(self):
        assert _is_slash_command("/members") is True

    def test_detects_part(self):
        assert _is_slash_command("/part") is True

    def test_detects_join(self):
        assert _is_slash_command("/join #workspace") is True

    def test_detects_kick(self):
        assert _is_slash_command("/kick @alice") is True

    def test_parse_slug_with_at(self):
        assert _parse_slug("@alice") == "alice"

    def test_parse_slug_without_at(self):
        assert _parse_slug("alice") == "alice"

    def test_parse_slug_empty(self):
        assert _parse_slug("") is None

    def test_parse_slug_multiple_words(self):
        assert _parse_slug("@alice admin") == "alice"

    def test_regex_captures_args(self):
        m = _SLASH_RE.match("/invite @alice admin")
        assert m.group(1) == "invite"
        assert m.group(2) == "@alice admin"

    def test_regex_no_args(self):
        m = _SLASH_RE.match("/help")
        assert m.group(1) == "help"
        assert m.group(2) == ""

    def test_regex_hyphenated_command(self):
        m = _SLASH_RE.match("/my-command arg1")
        assert m.group(1) == "my-command"


# ── Async tests: command execution (requires sqlalchemy mock chain) ─────────
# These tests mock the DB layer and test the handler logic.

# Try importing slash_commands — if deps are missing, skip async tests
try:
    from slash_commands import (
        execute_slash_command,
        is_slash_command,
    )
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def _make_user(slug="patrick", email="patrick@example.com", display_name="Patrick", user_id=None):
    user = MagicMock()
    user.id = user_id or uuid.uuid4()
    user.slug = slug
    user.email = email
    user.display_name = display_name
    user.is_active = True
    return user


def _make_member(role="owner", workspace_id=None, user_id=None):
    member = MagicMock()
    member.id = uuid.uuid4()
    member.workspace_id = workspace_id or uuid.uuid4()
    member.user_id = user_id or uuid.uuid4()
    member.role = role
    return member


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_DEPS, reason="Requires sqlalchemy/pydantic_settings (run in Docker)")
class TestSlashExecution:
    """Async tests for slash command execution."""

    async def test_unknown_command_returns_error(self):
        user = _make_user()
        member = _make_member()
        db = AsyncMock()

        result = await execute_slash_command("/foobar", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Commande inconnue" in result.system_message
        assert "/foobar" in result.system_message

    async def test_not_a_command_returns_false(self):
        user = _make_user()
        member = _make_member()
        db = AsyncMock()

        result = await execute_slash_command("hello world", user, member.workspace_id, member, db)
        assert result.is_command is False
        assert result.system_message is None

    async def test_help_lists_all_commands(self):
        user = _make_user()
        member = _make_member()
        db = AsyncMock()

        result = await execute_slash_command("/help", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Commandes disponibles" in result.system_message
        assert "/invite" in result.system_message
        assert "/kick" in result.system_message
        assert "/part" in result.system_message
        assert "/join" in result.system_message
        assert "/members" in result.system_message

    async def test_no_workspace_returns_error(self):
        user = _make_user()
        db = AsyncMock()

        result = await execute_slash_command("/invite @alice", user, None, None, db)
        assert result.is_command is True
        assert "workspace actif" in result.system_message

    async def test_invite_no_permission_viewer(self):
        user = _make_user()
        member = _make_member(role="viewer")
        db = AsyncMock()

        result = await execute_slash_command("/invite @alice", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Permission refusée" in result.system_message

    async def test_invite_no_permission_member(self):
        user = _make_user()
        member = _make_member(role="member")
        db = AsyncMock()

        result = await execute_slash_command("/invite @alice", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Permission refusée" in result.system_message

    async def test_invite_no_args(self):
        user = _make_user()
        member = _make_member(role="admin")
        db = AsyncMock()

        result = await execute_slash_command("/invite", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Usage" in result.system_message

    async def test_invite_invalid_role(self):
        user = _make_user()
        member = _make_member(role="admin")
        db = AsyncMock()

        result = await execute_slash_command("/invite @alice superadmin", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Rôle invalide" in result.system_message

    async def test_invite_owner_role_blocked(self):
        user = _make_user()
        member = _make_member(role="owner")
        db = AsyncMock()

        result = await execute_slash_command("/invite @alice owner", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "owner" in result.system_message.lower()

    async def test_kick_no_permission(self):
        user = _make_user()
        member = _make_member(role="member")
        db = AsyncMock()

        result = await execute_slash_command("/kick @alice", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Permission refusée" in result.system_message

    async def test_kick_no_args(self):
        user = _make_user()
        member = _make_member(role="admin")
        db = AsyncMock()

        result = await execute_slash_command("/kick", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Usage" in result.system_message

    async def test_part_owner_blocked(self):
        user = _make_user()
        member = _make_member(role="owner")
        db = AsyncMock()

        result = await execute_slash_command("/part", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "propriétaire" in result.system_message

    async def test_join_no_args(self):
        user = _make_user()
        member = _make_member()
        db = AsyncMock()

        result = await execute_slash_command("/join", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "Usage" in result.system_message

    @patch("slash_commands.audit_log", new_callable=AsyncMock)
    async def test_invite_success(self, mock_audit):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="admin", workspace_id=ws_id, user_id=user.id)

        target = _make_user(slug="alice", email="alice@example.com", display_name="Alice")
        db = AsyncMock()

        call_count = 0
        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(return_value=target)
            else:
                result.scalar_one_or_none = MagicMock(return_value=None)
            return result
        db.execute = mock_execute

        result = await execute_slash_command("/invite @alice", user, ws_id, member, db)
        assert result.is_command is True
        assert "invité" in result.system_message
        assert "Alice" in result.system_message
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    @patch("slash_commands.audit_log", new_callable=AsyncMock)
    async def test_invite_with_role(self, mock_audit):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="owner", workspace_id=ws_id, user_id=user.id)

        target = _make_user(slug="alice", email="alice@example.com", display_name="Alice")
        db = AsyncMock()

        call_count = 0
        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(return_value=target)
            else:
                result.scalar_one_or_none = MagicMock(return_value=None)
            return result
        db.execute = mock_execute

        result = await execute_slash_command("/invite @alice admin", user, ws_id, member, db)
        assert result.is_command is True
        assert "invité" in result.system_message
        assert "admin" in result.system_message

    @patch("slash_commands.audit_log", new_callable=AsyncMock)
    async def test_kick_success(self, mock_audit):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="admin", workspace_id=ws_id, user_id=user.id)

        target = _make_user(slug="bob", email="bob@example.com", display_name="Bob")
        target_member = _make_member(role="member", workspace_id=ws_id, user_id=target.id)

        db = AsyncMock()
        call_count = 0
        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(return_value=target)
            else:
                result.scalar_one_or_none = MagicMock(return_value=target_member)
            return result
        db.execute = mock_execute

        result = await execute_slash_command("/kick @bob", user, ws_id, member, db)
        assert result.is_command is True
        assert "retiré" in result.system_message
        assert "Bob" in result.system_message
        db.delete.assert_awaited_once_with(target_member)
        db.commit.assert_awaited_once()

    @patch("slash_commands.audit_log", new_callable=AsyncMock)
    async def test_part_success(self, mock_audit):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="member", workspace_id=ws_id, user_id=user.id)
        db = AsyncMock()

        result = await execute_slash_command("/part", user, ws_id, member, db)
        assert result.is_command is True
        assert "quitté" in result.system_message
        db.delete.assert_awaited_once_with(member)
        db.commit.assert_awaited_once()

    async def test_kick_cannot_kick_owner(self):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="admin", workspace_id=ws_id, user_id=user.id)

        target = _make_user(slug="boss", email="boss@example.com", display_name="Boss")
        target_member = _make_member(role="owner", workspace_id=ws_id, user_id=target.id)

        db = AsyncMock()
        call_count = 0
        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(return_value=target)
            else:
                result.scalar_one_or_none = MagicMock(return_value=target_member)
            return result
        db.execute = mock_execute

        result = await execute_slash_command("/kick @boss", user, ws_id, member, db)
        assert result.is_command is True
        assert "propriétaire" in result.system_message

    async def test_members_command(self):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="member", workspace_id=ws_id, user_id=user.id)

        u1 = _make_user(slug="alice", display_name="Alice")
        u2 = _make_user(slug="bob", display_name="Bob")
        m1 = _make_member(role="admin", workspace_id=ws_id, user_id=u1.id)
        m2 = _make_member(role="member", workspace_id=ws_id, user_id=u2.id)

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[(m1, u1), (m2, u2)])
        db.execute = AsyncMock(return_value=mock_result)

        result = await execute_slash_command("/members", user, ws_id, member, db)
        assert result.is_command is True
        assert "Membres du workspace" in result.system_message
        assert "@alice" in result.system_message
        assert "@bob" in result.system_message
        assert "admin" in result.system_message

    async def test_invite_user_not_found(self):
        user = _make_user()
        member = _make_member(role="admin")
        db = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=mock_result)

        result = await execute_slash_command("/invite @ghost", user, member.workspace_id, member, db)
        assert result.is_command is True
        assert "introuvable" in result.system_message

    async def test_invite_already_member(self):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="admin", workspace_id=ws_id, user_id=user.id)

        target = _make_user(slug="alice", email="alice@example.com", display_name="Alice")
        existing_member = _make_member(role="member", workspace_id=ws_id, user_id=target.id)

        db = AsyncMock()
        call_count = 0
        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(return_value=target)
            else:
                result.scalar_one_or_none = MagicMock(return_value=existing_member)
            return result
        db.execute = mock_execute

        result = await execute_slash_command("/invite @alice", user, ws_id, member, db)
        assert result.is_command is True
        assert "déjà membre" in result.system_message

    async def test_kick_user_not_member(self):
        user = _make_user()
        ws_id = uuid.uuid4()
        member = _make_member(role="admin", workspace_id=ws_id, user_id=user.id)

        target = _make_user(slug="alice", email="alice@example.com", display_name="Alice")

        db = AsyncMock()
        call_count = 0
        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(return_value=target)
            else:
                result.scalar_one_or_none = MagicMock(return_value=None)
            return result
        db.execute = mock_execute

        result = await execute_slash_command("/kick @alice", user, ws_id, member, db)
        assert result.is_command is True
        assert "pas membre" in result.system_message
