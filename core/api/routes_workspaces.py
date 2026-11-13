"""Workspace & member routes (Phase 4 — Bloc B + D)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas_workspaces import (
    MemberAdd,
    MemberRead,
    MemberUpdateRole,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
    WorkspaceWithRole,
)
from audit import audit_log, get_client_ip
from auth import get_current_user
from workspace_fs import archive_workspace_dir, ensure_workspace_dir
from database import get_db
from models.agent_config import AgentConfig, WorkspaceAgent
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from permissions import Permission, check_permission

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_workspace(db: AsyncSession, slug: str) -> Workspace:
    result = await db.execute(select(Workspace).where(Workspace.slug == slug, Workspace.is_active == True))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return ws


async def _get_membership(db: AsyncSession, workspace_id, user_id) -> WorkspaceMember | None:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def _require_member(member: WorkspaceMember | None) -> WorkspaceMember:
    """Ensure user is a member of the workspace."""
    if not member:
        raise HTTPException(status_code=403, detail="Permission denied.")
    return member


# ── Workspace CRUD ───────────────────────────────────────────────────────────

@router.get("", response_model=list[WorkspaceWithRole])
async def list_workspaces(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List all workspaces the current user is a member of."""
    result = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.is_active == True)
    )
    rows = result.all()
    return [
        WorkspaceWithRole(
            id=ws.id, name=ws.name, slug=ws.slug, description=ws.description,
            owner_id=ws.owner_id, is_active=ws.is_active, role=role,
            created_at=ws.created_at,
        )
        for ws, role in rows
    ]


@router.post("", response_model=WorkspaceRead, status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check slug uniqueness
    existing = await db.execute(select(Workspace).where(Workspace.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug already taken.")

    ws = Workspace(name=body.name.strip(), slug=body.slug, description=body.description, owner_id=user.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await db.flush()
    # Create workspace filesystem directory (Bloc O)
    ensure_workspace_dir(ws.slug)
    # Auto-add default agents
    from api.routes_auth import _add_default_agents
    await _add_default_agents(db, ws.id, user.id)
    await audit_log(db, action="workspace.create", user_id=user.id, workspace_id=ws.id,
                    resource_type="workspace", resource_id=str(ws.id),
                    details={"name": ws.name, "slug": ws.slug}, ip=get_client_ip(request))
    await db.commit()
    await db.refresh(ws)
    return ws


@router.get("/{workspace_slug}", response_model=WorkspaceRead)
async def get_workspace(
    workspace_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(db, workspace_slug)
    member = await _get_membership(db, ws.id, user.id)
    _require_member(member)
    check_permission(member.role, Permission.CHAT_VIEW)
    return ws


@router.put("/{workspace_slug}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_slug: str,
    body: WorkspaceUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(db, workspace_slug)
    member = await _get_membership(db, ws.id, user.id)
    _require_member(member)
    check_permission(member.role, Permission.WORKSPACE_MANAGE)

    if body.name is not None:
        ws.name = body.name.strip()
    if body.description is not None:
        ws.description = body.description
    await audit_log(db, action="workspace.update", user_id=user.id, workspace_id=ws.id,
                    resource_type="workspace", resource_id=str(ws.id),
                    details={"fields": [k for k, v in body.model_dump().items() if v is not None]},
                    ip=get_client_ip(request))
    await db.commit()
    await db.refresh(ws)
    return ws


@router.delete("/{workspace_slug}", status_code=204)
async def delete_workspace(
    workspace_slug: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(db, workspace_slug)
    member = await _get_membership(db, ws.id, user.id)
    _require_member(member)
    check_permission(member.role, Permission.WORKSPACE_DELETE)

    ws.is_active = False
    # Archive workspace filesystem directory (Bloc O)
    archive_workspace_dir(ws.slug)
    await audit_log(db, action="workspace.delete", user_id=user.id, workspace_id=ws.id,
                    resource_type="workspace", resource_id=str(ws.id),
                    details={"name": ws.name}, ip=get_client_ip(request))
    await db.commit()


# ── Members ──────────────────────────────────────────────────────────────────

@router.get("/{workspace_slug}/members", response_model=list[MemberRead])
async def list_members(
    workspace_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(db, workspace_slug)
    member = await _get_membership(db, ws.id, user.id)
    _require_member(member)
    check_permission(member.role, Permission.CHAT_VIEW)

    result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == ws.id)
    )
    return [
        MemberRead(
            id=wm.id, user_id=wm.user_id, email=u.email,
            display_name=u.display_name, role=wm.role, created_at=wm.created_at,
        )
        for wm, u in result.all()
    ]


@router.post("/{workspace_slug}/members", response_model=MemberRead, status_code=201)
async def add_member(
    workspace_slug: str,
    body: MemberAdd,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a member by email. The user must already exist."""
    ws = await _get_workspace(db, workspace_slug)
    caller = await _get_membership(db, ws.id, user.id)
    _require_member(caller)
    check_permission(caller.role, Permission.MEMBERS_MANAGE)

    # Find target user
    result = await db.execute(select(User).where(User.email == body.email))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found with that email.")

    # Check not already member
    existing = await _get_membership(db, ws.id, target_user.id)
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member.")

    wm = WorkspaceMember(workspace_id=ws.id, user_id=target_user.id, role=body.role)
    db.add(wm)
    await audit_log(db, action="member.add", user_id=user.id, workspace_id=ws.id,
                    resource_type="member", resource_id=str(target_user.id),
                    details={"email": body.email, "role": body.role}, ip=get_client_ip(request))
    await db.commit()
    await db.refresh(wm)
    return MemberRead(
        id=wm.id, user_id=target_user.id, email=target_user.email,
        display_name=target_user.display_name, role=wm.role, created_at=wm.created_at,
    )


@router.put("/{workspace_slug}/members/{member_user_id}", response_model=MemberRead)
async def update_member_role(
    workspace_slug: str,
    member_user_id: str,
    body: MemberUpdateRole,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(db, workspace_slug)
    caller = await _get_membership(db, ws.id, user.id)
    _require_member(caller)
    check_permission(caller.role, Permission.ROLES_MANAGE)

    target = await _get_membership(db, ws.id, member_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found.")
    if target.role == "owner":
        raise HTTPException(status_code=400, detail="Cannot change the owner's role.")

    old_role = target.role
    target.role = body.role
    await audit_log(db, action="member.role_change", user_id=user.id, workspace_id=ws.id,
                    resource_type="member", resource_id=str(member_user_id),
                    details={"old_role": old_role, "new_role": body.role}, ip=get_client_ip(request))
    await db.commit()
    await db.refresh(target)

    result = await db.execute(select(User).where(User.id == member_user_id))
    target_user = result.scalar_one()
    return MemberRead(
        id=target.id, user_id=target_user.id, email=target_user.email,
        display_name=target_user.display_name, role=target.role, created_at=target.created_at,
    )


@router.delete("/{workspace_slug}/members/{member_user_id}", status_code=204)
async def remove_member(
    workspace_slug: str,
    member_user_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(db, workspace_slug)
    caller = await _get_membership(db, ws.id, user.id)
    _require_member(caller)
    check_permission(caller.role, Permission.MEMBERS_MANAGE)

    target = await _get_membership(db, ws.id, member_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found.")
    if target.role == "owner":
        raise HTTPException(status_code=400, detail="Cannot remove the workspace owner.")

    await audit_log(db, action="member.remove", user_id=user.id, workspace_id=ws.id,
                    resource_type="member", resource_id=str(member_user_id),
                    details={"role": target.role}, ip=get_client_ip(request))
    await db.delete(target)
    await db.commit()


# ── Workspace Agents ─────────────────────────────────────────────────────────

@router.get("/{workspace_slug}/agents", response_model=list[str])
async def list_workspace_agents(
    workspace_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List agent IDs linked to this workspace."""
    ws = await _get_workspace(db, workspace_slug)
    member = await _get_membership(db, ws.id, user.id)
    _require_member(member)
    check_permission(member.role, Permission.CHAT_VIEW)
    result = await db.execute(
        select(WorkspaceAgent.agent_id)
        .join(AgentConfig, AgentConfig.id == WorkspaceAgent.agent_id)
        .where(WorkspaceAgent.workspace_id == ws.id, AgentConfig.is_active == True)
    )
    return [row[0] for row in result.all()]


@router.post("/{workspace_slug}/agents/{agent_id}", status_code=201)
async def add_agent_to_workspace(
    workspace_slug: str,
    agent_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an existing agent to this workspace."""
    ws = await _get_workspace(db, workspace_slug)
    member = await _get_membership(db, ws.id, user.id)
    _require_member(member)
    check_permission(member.role, Permission.AGENT_CREATE)

    # Verify agent exists and is active
    config = await db.get(AgentConfig, agent_id)
    if not config or not config.is_active:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

    # Check not already linked
    existing = await db.execute(
        select(WorkspaceAgent).where(
            WorkspaceAgent.workspace_id == ws.id,
            WorkspaceAgent.agent_id == agent_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Agent already in this workspace.")

    db.add(WorkspaceAgent(workspace_id=ws.id, agent_id=agent_id, added_by=user.id))
    await audit_log(db, action="workspace_agent.add", user_id=user.id, workspace_id=ws.id,
                    resource_type="agent", resource_id=agent_id,
                    details={"agent_name": config.name}, ip=get_client_ip(request))
    await db.commit()
    return {"message": f"Agent '{agent_id}' added to workspace."}


@router.delete("/{workspace_slug}/agents/{agent_id}", status_code=204)
async def remove_agent_from_workspace(
    workspace_slug: str,
    agent_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an agent from this workspace (does NOT delete the agent itself)."""
    ws = await _get_workspace(db, workspace_slug)
    member = await _get_membership(db, ws.id, user.id)
    _require_member(member)
    check_permission(member.role, Permission.AGENT_DELETE)

    result = await db.execute(
        select(WorkspaceAgent).where(
            WorkspaceAgent.workspace_id == ws.id,
            WorkspaceAgent.agent_id == agent_id,
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Agent not found in this workspace.")

    await audit_log(db, action="workspace_agent.remove", user_id=user.id, workspace_id=ws.id,
                    resource_type="agent", resource_id=agent_id,
                    details={}, ip=get_client_ip(request))
    await db.delete(link)
    await db.commit()
