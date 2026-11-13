"""RBAC permissions — Phase 4 Bloc D.

Defines roles, permissions, role→permission matrix, and FastAPI dependencies.
"""

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service import decode_token
from config import settings
from database import get_db
from models.api_key import ApiKey, hash_api_key
from models.user import User
from models.workspace import Workspace, WorkspaceMember


# ── Enums ────────────────────────────────────────────────────────────────────


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


ROLE_HIERARCHY: dict[str, int] = {
    Role.OWNER: 4,
    Role.ADMIN: 3,
    Role.MEMBER: 2,
    Role.VIEWER: 1,
}


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


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    Role.OWNER: set(Permission),  # all permissions
    Role.ADMIN: {
        Permission.WORKSPACE_MANAGE,
        Permission.MEMBERS_MANAGE,
        Permission.AGENT_CREATE,
        Permission.AGENT_DELETE,
        Permission.CHAT,
        Permission.CHAT_VIEW,
        Permission.TASK_RUN,
        Permission.TASK_VIEW,
        Permission.AUDIT_VIEW,
        Permission.APIKEY_MANAGE,
    },
    Role.MEMBER: {
        Permission.AGENT_CREATE,
        Permission.CHAT,
        Permission.CHAT_VIEW,
        Permission.TASK_RUN,
        Permission.TASK_VIEW,
    },
    Role.VIEWER: {
        Permission.CHAT_VIEW,
        Permission.TASK_VIEW,
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def has_permission(role: str, permission: Permission) -> bool:
    """Check if a role has a given permission."""
    return permission in ROLE_PERMISSIONS.get(role, set())


def check_permission(role: str, permission: Permission) -> None:
    """Raise 403 if the role lacks the permission. For use in dashboard routes."""
    if not has_permission(role, permission):
        raise HTTPException(status_code=403, detail="Permission denied.")


# ── FastAPI dependency ───────────────────────────────────────────────────────


@dataclass
class AuthContext:
    """Result of require_permission() — carries user, workspace, and membership info."""

    user: User | None
    workspace_id: UUID | None
    member: WorkspaceMember | None

    @property
    def allowed_skills(self) -> list[str] | None:
        """Shortcut — returns member's allowed_skills (None = all)."""
        if self.member is None:
            return None
        return self.member.allowed_skills


_bearer = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-HF-API-Key", auto_error=False)


def require_permission(permission: Permission):
    """Return a FastAPI dependency that checks the given permission.

    Supports dual auth: JWT Bearer (checks permission via membership)
    and legacy API key (bypasses permission — no workspace scoping).
    """

    async def _checker(
        credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
        api_key: str | None = Security(_api_key_header),
        x_workspace_id: str | None = Header(None),
        db: AsyncSession = Depends(get_db),
    ) -> AuthContext:
        # ── JWT auth ─────────────────────────────────────────────────
        if credentials:
            payload = decode_token(credentials.credentials)
            if payload and payload.get("type") == "access":
                user_id = payload.get("sub")
                result = await db.execute(
                    select(User).where(User.id == user_id, User.is_active == True)
                )
                user = result.scalar_one_or_none()
                if user:
                    # Resolve workspace_id
                    ws_id: UUID | None = None
                    if x_workspace_id:
                        try:
                            ws_id = UUID(x_workspace_id)
                        except ValueError:
                            raise HTTPException(400, "Invalid X-Workspace-Id header.")
                    else:
                        ws_result = await db.execute(
                            select(Workspace.id)
                            .where(Workspace.owner_id == user.id, Workspace.is_active == True)
                            .limit(1)
                        )
                        ws_id = ws_result.scalar_one_or_none()

                    if ws_id:
                        mem_result = await db.execute(
                            select(WorkspaceMember).where(
                                WorkspaceMember.workspace_id == ws_id,
                                WorkspaceMember.user_id == user.id,
                            )
                        )
                        member = mem_result.scalar_one_or_none()
                        if not member:
                            raise HTTPException(403, "Not a member of this workspace.")
                        if not has_permission(member.role, permission):
                            raise HTTPException(403, "Permission denied.")
                        return AuthContext(user=user, workspace_id=ws_id, member=member)

                    # Authenticated but no workspace
                    return AuthContext(user=user, workspace_id=None, member=None)

        # ── DB API key ────────────────────────────────────────────────
        if api_key:
            key_hash = hash_api_key(api_key)
            result = await db.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
            )
            db_key = result.scalar_one_or_none()
            if db_key:
                # Check that the requested permission is in the key's permissions
                perm_value = permission.value.split(".")[0]  # "chat.view" → "chat"
                if perm_value not in db_key.permissions and permission.value not in db_key.permissions:
                    raise HTTPException(403, "API key does not have this permission.")
                return AuthContext(user=None, workspace_id=db_key.workspace_id, member=None)

        # ── Legacy .env API key fallback (no permission checks) ──────
        if api_key and api_key in settings.api_keys_list:
            return AuthContext(user=None, workspace_id=None, member=None)

        raise HTTPException(401, "Invalid or missing authentication.")

    return _checker
