"""Auth routes — register, login, refresh, me (Phase 4)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

limiter = Limiter(key_func=get_remote_address)

from api.schemas_auth import (
    LoginRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserRead,
    WorkspaceShort,
)
from audit import audit_log, get_client_ip
from auth import get_current_user
from auth_service import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from config import settings
from database import get_db
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from workspace_fs import ensure_workspace_dir

router = APIRouter(prefix="/auth", tags=["auth"])


async def _add_default_agents(db: _AsyncSession, workspace_id, user_id=None) -> None:
    """Link all is_default agents to a workspace."""
    from models.agent_config import AgentConfig, WorkspaceAgent
    result = await db.execute(
        select(AgentConfig.id).where(AgentConfig.is_default == True, AgentConfig.is_active == True)
    )
    for (agent_id,) in result.all():
        db.add(WorkspaceAgent(workspace_id=workspace_id, agent_id=agent_id, added_by=user_id))
    await db.flush()


async def create_personal_workspace(db: _AsyncSession, user: User) -> Workspace:
    """Create a personal workspace + owner membership for a new user."""
    slug = user.email.split("@")[0].lower().replace(" ", "-").replace(".", "-")
    # Check slug collision
    existing = await db.execute(select(Workspace).where(Workspace.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{str(user.id)[:8]}"
    ws = Workspace(name=user.display_name, slug=slug, owner_id=user.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await db.flush()
    await _add_default_agents(db, ws.id, user.id)
    ensure_workspace_dir(ws.slug)
    return ws


@router.post("/register", response_model=RegisterResponse, status_code=201)
@limiter.limit("100/minute")
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Check duplicate email
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered.")

    # Generate user slug from email prefix
    user_slug = body.email.split("@")[0].lower().replace(" ", "-").replace(".", "-")
    slug_exists = await db.execute(select(User).where(User.slug == user_slug))
    if slug_exists.scalar_one_or_none():
        user_slug = f"{user_slug}-{__import__('uuid').uuid4().hex[:8]}"

    user = User(
        email=body.email,
        slug=user_slug,
        display_name=body.display_name,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()

    ws = await create_personal_workspace(db, user)

    await audit_log(db, action="auth.register", user_id=user.id, workspace_id=ws.id,
                    resource_type="user", resource_id=str(user.id),
                    details={"email": user.email}, ip=get_client_ip(request))

    await db.commit()
    await db.refresh(user)
    await db.refresh(ws)

    return RegisterResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        workspace=WorkspaceShort(id=ws.id, slug=ws.slug, name=ws.name),
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("100/minute")
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated.")

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    await audit_log(db, action="auth.login", user_id=user.id,
                    resource_type="user", resource_id=str(user.id),
                    details={"email": user.email}, ip=get_client_ip(request))
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token.")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated.")

    access_token = create_access_token(user.id)
    return RefreshResponse(
        access_token=access_token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)):
    return user
