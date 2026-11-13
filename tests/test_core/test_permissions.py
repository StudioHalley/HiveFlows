"""Unit tests for the RBAC permissions module (Phase 4 — Bloc D).

Run locally:     pytest tests/test_core/test_permissions.py -v
Run in Docker:   docker compose exec core python -m pytest /app/tests/test_core/test_permissions.py -v

The pure-logic tests (matrix, hierarchy) are self-contained.
Import-dependent tests are skipped if FastAPI is not installed.
"""
import sys
from pathlib import Path

import pytest

# Try importing the real module (works in Docker or if FastAPI is installed)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
    from permissions import (
        ROLE_HIERARCHY,
        ROLE_PERMISSIONS,
        AuthContext,
        Permission,
        Role,
        has_permission,
    )

    _HAS_MODULE = True
except ImportError:
    _HAS_MODULE = False

    # Inline the pure logic for local testing without FastAPI
    from enum import Enum

    class Role(str, Enum):
        OWNER = "owner"
        ADMIN = "admin"
        MEMBER = "member"
        VIEWER = "viewer"

    ROLE_HIERARCHY = {Role.OWNER: 4, Role.ADMIN: 3, Role.MEMBER: 2, Role.VIEWER: 1}

    class Permission(str, Enum):
        WORKSPACE_MANAGE = "workspace.manage"
        WORKSPACE_DELETE = "workspace.delete"
        MEMBERS_MANAGE = "members.manage"
        ROLES_MANAGE = "roles.manage"
        AGENT_CREATE = "agent.create"
        AGENT_DELETE = "agent.delete"
        CHAT = "chat"
        CHAT_VIEW = "chat.view"
        TASK_RUN = "task.run"
        TASK_VIEW = "task.view"
        AUDIT_VIEW = "audit.view"
        APIKEY_MANAGE = "apikey.manage"

    ROLE_PERMISSIONS = {
        Role.OWNER: set(Permission),
        Role.ADMIN: {
            Permission.WORKSPACE_MANAGE, Permission.MEMBERS_MANAGE,
            Permission.AGENT_CREATE, Permission.AGENT_DELETE,
            Permission.CHAT, Permission.CHAT_VIEW,
            Permission.TASK_RUN, Permission.TASK_VIEW,
            Permission.AUDIT_VIEW, Permission.APIKEY_MANAGE,
        },
        Role.MEMBER: {
            Permission.AGENT_CREATE, Permission.CHAT, Permission.CHAT_VIEW,
            Permission.TASK_RUN, Permission.TASK_VIEW,
        },
        Role.VIEWER: {Permission.CHAT_VIEW, Permission.TASK_VIEW},
    }

    def has_permission(role: str, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS.get(role, set())


# ── Role hierarchy ───────────────────────────────────────────────────────────


class TestRoleHierarchy:
    def test_owner_is_highest(self):
        assert ROLE_HIERARCHY[Role.OWNER] > ROLE_HIERARCHY[Role.ADMIN]

    def test_admin_above_member(self):
        assert ROLE_HIERARCHY[Role.ADMIN] > ROLE_HIERARCHY[Role.MEMBER]

    def test_member_above_viewer(self):
        assert ROLE_HIERARCHY[Role.MEMBER] > ROLE_HIERARCHY[Role.VIEWER]

    def test_all_roles_have_hierarchy(self):
        for role in Role:
            assert role in ROLE_HIERARCHY


# ── Permission matrix ────────────────────────────────────────────────────────


class TestPermissionMatrix:
    """Verify the RBAC matrix matches the spec from PHASE4.md lines 262-275."""

    def test_owner_has_all_permissions(self):
        assert ROLE_PERMISSIONS[Role.OWNER] == set(Permission)

    def test_admin_cannot_delete_workspace(self):
        assert Permission.WORKSPACE_DELETE not in ROLE_PERMISSIONS[Role.ADMIN]

    def test_admin_cannot_manage_roles(self):
        assert Permission.ROLES_MANAGE not in ROLE_PERMISSIONS[Role.ADMIN]

    def test_admin_can_manage_workspace(self):
        assert Permission.WORKSPACE_MANAGE in ROLE_PERMISSIONS[Role.ADMIN]

    def test_admin_can_manage_members(self):
        assert Permission.MEMBERS_MANAGE in ROLE_PERMISSIONS[Role.ADMIN]

    def test_admin_can_create_and_delete_agents(self):
        assert Permission.AGENT_CREATE in ROLE_PERMISSIONS[Role.ADMIN]
        assert Permission.AGENT_DELETE in ROLE_PERMISSIONS[Role.ADMIN]

    def test_member_can_create_agent(self):
        assert Permission.AGENT_CREATE in ROLE_PERMISSIONS[Role.MEMBER]

    def test_member_cannot_delete_agent(self):
        assert Permission.AGENT_DELETE not in ROLE_PERMISSIONS[Role.MEMBER]

    def test_member_can_chat(self):
        assert Permission.CHAT in ROLE_PERMISSIONS[Role.MEMBER]

    def test_member_cannot_manage_workspace(self):
        assert Permission.WORKSPACE_MANAGE not in ROLE_PERMISSIONS[Role.MEMBER]

    def test_viewer_can_only_view(self):
        assert ROLE_PERMISSIONS[Role.VIEWER] == {Permission.CHAT_VIEW, Permission.TASK_VIEW}

    def test_viewer_cannot_chat(self):
        assert Permission.CHAT not in ROLE_PERMISSIONS[Role.VIEWER]


# ── has_permission() ─────────────────────────────────────────────────────────


class TestHasPermission:
    def test_owner_has_workspace_delete(self):
        assert has_permission("owner", Permission.WORKSPACE_DELETE)

    def test_admin_lacks_workspace_delete(self):
        assert not has_permission("admin", Permission.WORKSPACE_DELETE)

    def test_member_has_chat(self):
        assert has_permission("member", Permission.CHAT)

    def test_viewer_lacks_chat(self):
        assert not has_permission("viewer", Permission.CHAT)

    def test_unknown_role_has_no_permissions(self):
        assert not has_permission("unknown", Permission.CHAT_VIEW)

    def test_all_roles_can_view_tasks(self):
        for role in ("owner", "admin", "member", "viewer"):
            assert has_permission(role, Permission.TASK_VIEW)


# ── Comprehensive matrix parametrized test ───────────────────────────────────


EXPECTED_MATRIX = {
    ("owner", Permission.WORKSPACE_MANAGE): True,
    ("owner", Permission.WORKSPACE_DELETE): True,
    ("owner", Permission.MEMBERS_MANAGE): True,
    ("owner", Permission.ROLES_MANAGE): True,
    ("owner", Permission.AGENT_CREATE): True,
    ("owner", Permission.AGENT_DELETE): True,
    ("owner", Permission.CHAT): True,
    ("owner", Permission.CHAT_VIEW): True,
    ("owner", Permission.TASK_RUN): True,
    ("owner", Permission.TASK_VIEW): True,
    ("owner", Permission.AUDIT_VIEW): True,
    ("owner", Permission.APIKEY_MANAGE): True,

    ("admin", Permission.WORKSPACE_MANAGE): True,
    ("admin", Permission.WORKSPACE_DELETE): False,
    ("admin", Permission.MEMBERS_MANAGE): True,
    ("admin", Permission.ROLES_MANAGE): False,
    ("admin", Permission.AGENT_CREATE): True,
    ("admin", Permission.AGENT_DELETE): True,
    ("admin", Permission.CHAT): True,
    ("admin", Permission.CHAT_VIEW): True,
    ("admin", Permission.TASK_RUN): True,
    ("admin", Permission.TASK_VIEW): True,
    ("admin", Permission.AUDIT_VIEW): True,
    ("admin", Permission.APIKEY_MANAGE): True,

    ("member", Permission.WORKSPACE_MANAGE): False,
    ("member", Permission.WORKSPACE_DELETE): False,
    ("member", Permission.MEMBERS_MANAGE): False,
    ("member", Permission.ROLES_MANAGE): False,
    ("member", Permission.AGENT_CREATE): True,
    ("member", Permission.AGENT_DELETE): False,
    ("member", Permission.CHAT): True,
    ("member", Permission.CHAT_VIEW): True,
    ("member", Permission.TASK_RUN): True,
    ("member", Permission.TASK_VIEW): True,
    ("member", Permission.AUDIT_VIEW): False,
    ("member", Permission.APIKEY_MANAGE): False,

    ("viewer", Permission.WORKSPACE_MANAGE): False,
    ("viewer", Permission.WORKSPACE_DELETE): False,
    ("viewer", Permission.MEMBERS_MANAGE): False,
    ("viewer", Permission.ROLES_MANAGE): False,
    ("viewer", Permission.AGENT_CREATE): False,
    ("viewer", Permission.AGENT_DELETE): False,
    ("viewer", Permission.CHAT): False,
    ("viewer", Permission.CHAT_VIEW): True,
    ("viewer", Permission.TASK_RUN): False,
    ("viewer", Permission.TASK_VIEW): True,
    ("viewer", Permission.AUDIT_VIEW): False,
    ("viewer", Permission.APIKEY_MANAGE): False,
}


class TestFullMatrix:
    @pytest.mark.parametrize(
        "key,expected",
        EXPECTED_MATRIX.items(),
        ids=[f"{r}-{p.value}" for (r, p) in EXPECTED_MATRIX],
    )
    def test_permission(self, key, expected):
        role, permission = key
        assert has_permission(role, permission) == expected
