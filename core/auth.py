"""Auth dependencies — supports JWT Bearer, DB API Keys, and legacy .env API Keys.

Usage in routes:
    - Depends(get_current_user)   -> returns User object (JWT auth)
    - Depends(verify_api_key)     -> returns api_key string (legacy, rétro-compat)
    - Depends(get_current_user_or_apikey) -> accepts either (transition period)

Phase 4 Bloc E: DB-backed API keys resolve to workspace_id + permissions.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service import decode_token
from config import settings
from database import get_db
from models.api_key import ApiKey, hash_api_key
from models.user import User
from models.workspace import Workspace, WorkspaceMember

# Security schemes
api_key_header = APIKeyHeader(name="X-HF-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


# ── DB API Key resolution ────────────────────────────────────────────────────

async def resolve_db_api_key(db: AsyncSession, raw_key: str) -> ApiKey | None:
    """Look up an API key in the database by SHA256 hash. Returns ApiKey or None."""
    key_hash = hash_api_key(raw_key)
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
    )
    api_key = result.scalar_one_or_none()
    if api_key:
        # Update last_used_at (fire-and-forget, don't block)
        await db.execute(
            update(ApiKey).where(ApiKey.id == api_key.id).values(last_used_at=datetime.now(timezone.utc))
        )
        await db.commit()
    return api_key


# ── Legacy helpers ───────────────────────────────────────────────────────────

async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Legacy API key auth — kept for rétro-compatibility."""
    if not api_key or api_key not in settings.api_keys_list:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """JWT Bearer auth — returns the authenticated User."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header.")

    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")

    return user


async def get_current_user_or_apikey(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    api_key: str | None = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> User | str:
    """Accept either JWT Bearer or API Key (DB or legacy).

    Returns User if JWT, or api_key string if API key auth.
    """
    # Try JWT first
    if credentials:
        payload = decode_token(credentials.credentials)
        if payload and payload.get("type") == "access":
            user_id = payload.get("sub")
            result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
            user = result.scalar_one_or_none()
            if user:
                return user

    # Try DB API key
    if api_key:
        db_key = await resolve_db_api_key(db, api_key)
        if db_key:
            return api_key

    # Fallback to legacy .env API key
    if api_key and api_key in settings.api_keys_list:
        return api_key

    raise HTTPException(status_code=401, detail="Invalid or missing authentication.")


async def get_workspace_id_optional(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    api_key: str | None = Security(api_key_header),
    x_workspace_id: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> UUID | None:
    """Auth + optional workspace extraction.

    - JWT auth: returns workspace_id from header or personal workspace
    - DB API key: returns workspace_id from the key record
    - Legacy API key: returns None (no workspace scoping)
    """
    # Try JWT first
    if credentials:
        payload = decode_token(credentials.credentials)
        if payload and payload.get("type") == "access":
            user_id = payload.get("sub")
            result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
            user = result.scalar_one_or_none()
            if user:
                if x_workspace_id:
                    try:
                        ws_id = UUID(x_workspace_id)
                    except ValueError:
                        raise HTTPException(status_code=400, detail="Invalid X-Workspace-Id header.")
                    membership = await db.execute(
                        select(WorkspaceMember).where(
                            WorkspaceMember.workspace_id == ws_id,
                            WorkspaceMember.user_id == user.id,
                        )
                    )
                    if not membership.scalar_one_or_none():
                        raise HTTPException(status_code=403, detail="Not a member of this workspace.")
                    return ws_id
                # Default: user's personal workspace
                result = await db.execute(
                    select(Workspace.id).where(Workspace.owner_id == user.id, Workspace.is_active == True).limit(1)
                )
                return result.scalar_one_or_none()

    # Try DB API key — returns workspace_id from key record
    if api_key:
        db_key = await resolve_db_api_key(db, api_key)
        if db_key:
            return db_key.workspace_id

    # Fallback to legacy API key — no workspace scoping
    if api_key and api_key in settings.api_keys_list:
        return None

    raise HTTPException(status_code=401, detail="Invalid or missing authentication.")


async def get_workspace_id(
    user: User = Depends(get_current_user),
    x_workspace_id: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Extract workspace_id from X-Workspace-Id header or default to user's personal workspace.

    Validates that the user is a member of the requested workspace.
    """
    if x_workspace_id:
        try:
            ws_id = UUID(x_workspace_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid X-Workspace-Id header.")
        # Verify membership
        result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Not a member of this workspace.")
        return ws_id

    # Default: user's personal workspace (first owned workspace)
    result = await db.execute(
        select(Workspace.id).where(Workspace.owner_id == user.id, Workspace.is_active == True).limit(1)
    )
    ws_id = result.scalar_one_or_none()
    if not ws_id:
        raise HTTPException(status_code=404, detail="No workspace found. Please create one.")
    return ws_id
