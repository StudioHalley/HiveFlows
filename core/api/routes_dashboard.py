import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import BackgroundTasks

from celery_client import celery_app

logger = logging.getLogger("hiveflows.dashboard")

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.manager import agent_manager
from api.routes_auth import create_personal_workspace
from beekeeper_client import beekeeper_client
from chat_broadcaster import broadcaster
from chat_persistence import (
    ensure_workspace_conversation,
    get_recent_messages,
    persist_agent_message,
    persist_exchange,
    persist_user_message,
)
from api.routes_oauth import get_enabled_providers
from audit import audit_log, get_client_ip
from config import settings
from auth_service import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from database import get_db
from models.agent_config import AgentConfig
from models.audit_log import AuditLog
from models.api_key import ApiKey, generate_api_key, hash_api_key
from models.conversation import Conversation, Message
from models.task import Task
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from models.portal import PortalDevice, PortalAuditLog
from permissions import Permission, check_permission, has_permission
from services.portal_enrollment import create_device as portal_create_device, revoke_device as portal_revoke_device
from services.activity_bus import publish_activity

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="templates")

_static_hashes: dict[str, str] = {}

def _static_v(path: str) -> str:
    """Retourne /static/<path>?v=<md5[:8]> — hash calculé au premier appel."""
    if path not in _static_hashes:
        full = os.path.join("static", path)
        try:
            data = open(full, "rb").read()
            _static_hashes[path] = hashlib.md5(data).hexdigest()[:8]
        except OSError:
            _static_hashes[path] = "0"
    return f"/static/{path}?v={_static_hashes[path]}"

templates.env.globals["static_v"] = _static_v

_COOKIE_NAME = "hf_token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 jours


# ── AUTH ──────────────────────────────────────────────────────────────────────

async def require_auth(request: Request, db: AsyncSession = Depends(get_db)):
    """Dependency: vérifie le cookie JWT, redirige vers /login sinon."""
    token = request.cookies.get(_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=307, headers={"Location": "/dashboard/login"})

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=307, headers={"Location": "/dashboard/login"})

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=307, headers={"Location": "/dashboard/login"})

    request.state.user = user
    return user


async def require_workspace_auth(slug: str, request: Request, db: AsyncSession):
    """Vérifie le cookie JWT + l'appartenance au workspace via slug.
    Retourne (user, workspace, role).
    """
    token = request.cookies.get(_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=307, headers={"Location": "/dashboard/login"})

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=307, headers={"Location": "/dashboard/login"})

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=307, headers={"Location": "/dashboard/login"})

    ws_result = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.slug == slug, Workspace.is_active == True, WorkspaceMember.user_id == user.id)
    )
    row = ws_result.first()
    if not row:
        raise HTTPException(status_code=307, headers={"Location": "/dashboard/workspaces"})

    ws, role = row
    request.state.user = user
    request.state.workspace = ws
    request.state.workspace_role = role
    return user, ws, role


# ── LOGIN / REGISTER ─────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(_COOKIE_NAME, "")
    if token:
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "dashboard/login.html", {
        "oauth_providers": get_enabled_providers(),
    })


@router.post("/login")
async def login_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request, "dashboard/login.html",
            {"error": "Email ou mot de passe invalide.", "oauth_providers": get_enabled_providers()},
            status_code=401,
        )

    if not user.is_active:
        return templates.TemplateResponse(
            request, "dashboard/login.html",
            {"error": "Compte désactivé.", "oauth_providers": get_enabled_providers()},
            status_code=403,
        )

    access_token = create_access_token(user.id)

    await audit_log(db, action="auth.login", user_id=user.id,
                    resource_type="user", resource_id=str(user.id),
                    details={"email": user.email, "via": "dashboard"}, ip=get_client_ip(request))
    await db.commit()

    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(_COOKIE_NAME, access_token, max_age=_COOKIE_MAX_AGE,
                    httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return templates.TemplateResponse(request, "dashboard/register.html", {
        "oauth_providers": get_enabled_providers(),
    })


@router.post("/register")
async def register_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            request, "dashboard/register.html",
            {"error": "Cet email est déjà utilisé.", "oauth_providers": get_enabled_providers()},
            status_code=409,
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            request, "dashboard/register.html",
            {"error": "Le mot de passe doit contenir au moins 6 caractères.", "oauth_providers": get_enabled_providers()},
            status_code=400,
        )

    user = User(
        email=email,
        display_name=display_name.strip(),
        hashed_password=hash_password(password),
    )
    db.add(user)
    await db.flush()

    ws = await create_personal_workspace(db, user)

    await audit_log(db, action="auth.register", user_id=user.id, workspace_id=ws.id,
                    resource_type="user", resource_id=str(user.id),
                    details={"email": user.email, "via": "dashboard"}, ip=get_client_ip(request))

    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(user.id)
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(_COOKIE_NAME, access_token, max_age=_COOKIE_MAX_AGE,
                    httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/dashboard/login", status_code=303)
    resp.delete_cookie(_COOKIE_NAME)
    return resp


# ── PROFILE ──────────────────────────────────────────────────────────────────

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    user = request.state.user
    all_ws = await _get_user_workspaces(user, db)
    default_slug = all_ws[0]["slug"] if all_ws else None
    return templates.TemplateResponse(
        request, "dashboard/profile.html",
        {
            "user": user, "flash": _get_flash(request),
            "oauth_providers": get_enabled_providers(),
            "workspace_slug": default_slug, "all_workspaces": all_ws,
            "active_page": "profile",
        },
    )


@router.post("/profile")
async def profile_update(
    request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
    display_name: str = Form(...), email: str = Form(...),
):
    user = request.state.user
    all_ws = await _get_user_workspaces(user, db)
    default_slug = all_ws[0]["slug"] if all_ws else None
    ctx = {
        "user": user, "oauth_providers": get_enabled_providers(),
        "workspace_slug": default_slug, "all_workspaces": all_ws, "active_page": "profile",
    }

    email = email.strip().lower()
    if email != user.email:
        existing = await db.execute(select(User).where(User.email == email, User.id != user.id))
        if existing.scalar_one_or_none():
            ctx["flash"] = {"type": "error", "message": "Cet email est deja utilise."}
            return templates.TemplateResponse(request, "dashboard/profile.html", ctx, status_code=409)

    user.display_name = display_name.strip()
    user.email = email
    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    ctx["user"] = user
    ctx["flash"] = {"type": "success", "message": "Profil mis a jour."}
    return templates.TemplateResponse(request, "dashboard/profile.html", ctx)


@router.post("/profile/password")
async def profile_change_password(
    request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
    current_password: str = Form(""), new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = request.state.user
    all_ws = await _get_user_workspaces(user, db)
    default_slug = all_ws[0]["slug"] if all_ws else None
    ctx = {
        "user": user, "oauth_providers": get_enabled_providers(),
        "workspace_slug": default_slug, "all_workspaces": all_ws, "active_page": "profile",
    }

    if user.hashed_password and not verify_password(current_password, user.hashed_password):
        ctx["flash"] = {"type": "error", "message": "Mot de passe actuel incorrect."}
        return templates.TemplateResponse(request, "dashboard/profile.html", ctx, status_code=403)

    if new_password != confirm_password:
        ctx["flash"] = {"type": "error", "message": "Les mots de passe ne correspondent pas."}
        return templates.TemplateResponse(request, "dashboard/profile.html", ctx, status_code=400)

    if len(new_password) < 6:
        ctx["flash"] = {"type": "error", "message": "Le mot de passe doit contenir au moins 6 caracteres."}
        return templates.TemplateResponse(request, "dashboard/profile.html", ctx, status_code=400)

    user.hashed_password = hash_password(new_password)
    user.updated_at = datetime.utcnow()
    await db.commit()

    ctx["flash"] = {"type": "success", "message": "Mot de passe mis a jour."}
    return templates.TemplateResponse(request, "dashboard/profile.html", ctx)


@router.post("/profile/oauth/unlink")
async def profile_oauth_unlink(request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    user = request.state.user
    if not user.hashed_password:
        return RedirectResponse("/dashboard/profile", status_code=303)
    user.oauth_provider = None
    user.oauth_id = None
    user.updated_at = datetime.utcnow()
    await db.commit()
    return RedirectResponse("/dashboard/profile", status_code=303)


# ── HELPERS ───────────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._@:~-]*$")


async def _agent_linked_to_workspace(db: AsyncSession, agent_id: str, workspace_id) -> bool:
    """Check if an agent is linked to a workspace via workspace_agents."""
    from models.agent_config import WorkspaceAgent
    if not workspace_id:
        return True
    result = await db.execute(
        select(WorkspaceAgent).where(
            WorkspaceAgent.agent_id == agent_id,
            WorkspaceAgent.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _list_agents(db: AsyncSession, workspace_id=None) -> list[AgentConfig]:
    from models.agent_config import WorkspaceAgent
    query = select(AgentConfig).where(AgentConfig.is_active == True)
    if workspace_id:
        query = query.join(
            WorkspaceAgent,
            (WorkspaceAgent.agent_id == AgentConfig.id)
            & (WorkspaceAgent.workspace_id == workspace_id),
        )
    result = await db.execute(query)
    return list(result.scalars().all())


def _get_flash(request: Request) -> dict | None:
    return getattr(request.state, "flash", None)


async def _get_user_workspaces(user: User, db: AsyncSession) -> list[dict]:
    ws_result = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.is_active == True)
    )
    return [{"id": ws.id, "name": ws.name, "slug": ws.slug, "role": role} for ws, role in ws_result.all()]


async def _get_default_workspace_slug(user: User, db: AsyncSession) -> str | None:
    result = await db.execute(
        select(Workspace.slug)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.is_active == True)
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── ROOT — redirect to default workspace ──────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def dashboard_root(request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    user = request.state.user
    slug = await _get_default_workspace_slug(user, db)
    if slug:
        return RedirectResponse(f"/dashboard/w/{slug}/", status_code=303)
    return RedirectResponse("/dashboard/workspaces", status_code=303)


# ── WORKSPACE-SCOPED PAGES (/dashboard/w/{slug}/) ────────────────────────────

@router.get("/w/{slug}/", response_class=HTMLResponse)
async def workspace_index(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)

    agents = await _list_agents(db, workspace_id=ws.id)
    task_count = (await db.execute(
        select(func.count()).select_from(Task).where(Task.workspace_id == ws.id)
    )).scalar() or 0
    running_count = (await db.execute(
        select(func.count()).select_from(Task).where(
            Task.workspace_id == ws.id, Task.status.in_(["pending", "running"])
        )
    )).scalar() or 0
    recent_result = await db.execute(
        select(Task).where(Task.workspace_id == ws.id).order_by(Task.created_at.desc()).limit(8)
    )
    recent_tasks = list(recent_result.scalars().all())
    all_ws = await _get_user_workspaces(user, db)

    return templates.TemplateResponse(
        request, "dashboard/index.html",
        {
            "agents": agents, "task_count": task_count, "running_count": running_count,
            "recent_tasks": recent_tasks, "user": user, "workspace": ws,
            "workspace_slug": slug, "workspace_role": role, "all_workspaces": all_ws,
            "active_page": "dashboard",
        },
    )


@router.get("/w/{slug}/chat", response_class=HTMLResponse)
async def workspace_chat(request: Request, slug: str, agent_id: str | None = None, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    agents = await _list_agents(db, workspace_id=ws.id)
    all_ws = await _get_user_workspaces(user, db)

    can_chat = has_permission(role, Permission.CHAT)
    display_name = user.display_name or user.slug or user.email.split("@")[0]

    # Query workspace members with user info
    mem_result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == ws.id)
        .order_by(WorkspaceMember.created_at)
    )
    members = [
        {
            "slug": u.slug or u.email.split("@")[0],
            "display_name": u.display_name or u.email.split("@")[0],
            "role": wm.role,
        }
        for wm, u in mem_result.all()
    ]

    return templates.TemplateResponse(
        request, "dashboard/chat.html",
        {
            "agents": agents,
            "selected_agent_id": agent_id or (agents[0].id if agents else None),
            "user": user, "workspace": ws, "workspace_slug": slug,
            "workspace_id": str(ws.id), "all_workspaces": all_ws,
            "active_page": "chat", "can_chat": can_chat,
            "current_user_id": str(user.id),
            "current_display_name": display_name,
            "members": members,
        },
    )


@router.get("/w/{slug}/agents", response_class=HTMLResponse)
async def workspace_agents(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    agents = await _list_agents(db, workspace_id=ws.id)
    all_ws = await _get_user_workspaces(user, db)

    can_create = has_permission(role, Permission.AGENT_CREATE)
    can_delete = has_permission(role, Permission.AGENT_DELETE)
    can_chat = has_permission(role, Permission.CHAT)

    # L10: Find user's own agents not already in this workspace (for "Add agent" button)
    from models.agent_config import WorkspaceAgent
    own_agents_result = await db.execute(
        select(AgentConfig)
        .where(AgentConfig.owner_id == user.id, AgentConfig.is_active == True)
        .where(~AgentConfig.id.in_(
            select(WorkspaceAgent.agent_id).where(WorkspaceAgent.workspace_id == ws.id)
        ))
    )
    available_agents = list(own_agents_result.scalars().all())

    return templates.TemplateResponse(
        request, "dashboard/agents/list.html",
        {
            "agents": agents, "flash": _get_flash(request),
            "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "agents",
            "can_create": can_create, "can_delete": can_delete, "can_chat": can_chat,
            "available_agents": available_agents,
            "current_user_id": str(user.id),
        },
    )


# C13 — /tasks redirected to /activity
@router.get("/w/{slug}/tasks", response_class=HTMLResponse)
async def workspace_tasks_redirect(slug: str):
    return RedirectResponse(f"/dashboard/w/{slug}/activity", status_code=301)


# ── ACTIVITY (C1-C11) ────────────────────────────────────────────────────────

@router.get("/w/{slug}/activity", response_class=HTMLResponse)
async def workspace_activity(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """Dashboard Activity — liste temps réel des tasks (tous backends)."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(Task).where(Task.workspace_id == ws.id).order_by(Task.created_at.desc()).limit(100)
    )
    tasks = list(result.scalars().all())

    # Sync Celery state for active tasks (initial load accuracy)
    needs_commit = False
    for task in tasks:
        if task.status in ("pending", "running") and task.backend_ref:
            cr = celery_app.AsyncResult(task.backend_ref)
            state = cr.state
            if state in ("STARTED", "PROGRESS"):
                task.status = "running"
                if state == "PROGRESS":
                    info = cr.info or {}
                    task.progress = info.get("current", 0)
                    task.progress_detail = info.get("detail")
                needs_commit = True
            elif state == "SUCCESS":
                task.status = "success"
                task.progress = 100
                task.result = cr.result
                task.completed_at = datetime.utcnow()
                task.updated_at = datetime.utcnow()
                needs_commit = True
            elif state in ("FAILURE", "REVOKED"):
                task.status = "failed" if state == "FAILURE" else "cancelled"
                task.error = str(cr.result)
                task.completed_at = datetime.utcnow()
                task.updated_at = datetime.utcnow()
                needs_commit = True
    if needs_commit:
        await db.commit()

    all_ws = await _get_user_workspaces(user, db)
    return templates.TemplateResponse(
        request, "dashboard/activity/list.html",
        {
            "tasks": tasks, "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "activity",
        },
    )


@router.post("/w/{slug}/activity/tasks/{task_id}/cancel")
async def activity_cancel_task(
    slug: str, task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Annule une task Celery depuis le dashboard Activity."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.TASK_RUN)

    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.workspace_id == ws.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel task with status '{task.status}'")

    if task.backend_ref:
        celery_app.control.revoke(task.backend_ref, terminate=True)

    task.status = "cancelled"
    task.completed_at = datetime.utcnow()
    task.updated_at = datetime.utcnow()
    await db.commit()

    source = f"user:{user.id}"
    await publish_activity(
        workspace_id=str(ws.id), event_type="task_cancelled",
        source=source, data={"type": task.type, "name": task.name},
        task_id=str(task.id),
    )
    return JSONResponse({"status": "cancelled", "task_id": task_id})


@router.delete("/w/{slug}/activity/tasks/cleanup")
async def activity_cleanup_tasks(
    slug: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Supprime les tasks terminées du workspace depuis le dashboard Activity."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.TASK_RUN)

    stmt = delete(Task).where(
        Task.workspace_id == ws.id,
        Task.status.in_(["success", "failed", "cancelled"]),
    )
    res = await db.execute(stmt)
    await db.commit()
    return JSONResponse({"deleted": res.rowcount})


@router.post("/w/{slug}/activity/tasks/{task_id}/rerun")
async def activity_rerun_task(
    slug: str, task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Relance une task échouée avec les mêmes params."""
    import uuid as _uuid
    from celery_client import celery_app as _celery
    from models.task import Task as _Task

    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.TASK_RUN)

    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.workspace_id == ws.id)
    )
    orig = result.scalar_one_or_none()
    if not orig:
        raise HTTPException(status_code=404, detail="Task not found")
    if orig.status not in ("failed", "cancelled"):
        raise HTTPException(status_code=400, detail="Only failed or cancelled tasks can be re-run")

    _CELERY_MAP = {
        "python": "tasks.run_python",
        "bash": "tasks.run_bash",
        "translate": "tasks.run_translate",
        "translate_file": "tasks.run_translate",
    }
    celery_name = _CELERY_MAP.get(orig.type)
    if not celery_name:
        raise HTTPException(status_code=400, detail=f"Cannot re-run task type '{orig.type}'")

    new_uuid = _uuid.uuid4()
    params = orig.params or {}
    _SCRIPT_TYPES = {"python", "bash"}
    if orig.type in _SCRIPT_TYPES:
        kwargs = {
            "script": params.get("script", ""),
            "db_task_id": str(new_uuid),
            "workspace_id": str(ws.id),
        }
        if params.get("timeout"):
            kwargs["timeout"] = params["timeout"]
    else:
        kwargs = dict(params)
        kwargs["db_task_id"] = str(new_uuid)
        kwargs.setdefault("workspace_id", str(ws.id))

    celery_result = _celery.send_task(celery_name, kwargs=kwargs)
    triggered_by = f"user:{user.id}"

    new_task = Task(
        id=new_uuid,
        name=orig.name,
        type=orig.type,
        status="pending",
        triggered_by=triggered_by,
        backend_ref=celery_result.id,
        params=params,
        workspace_id=ws.id,
        user_id=user.id,
    )
    db.add(new_task)
    await db.commit()
    await db.refresh(new_task)

    await publish_activity(
        workspace_id=str(ws.id), event_type="task_created",
        source=triggered_by, data={"type": orig.type, "name": orig.name},
        task_id=str(new_task.id),
    )
    return JSONResponse({"task_id": str(new_task.id), "status": "pending"})


# ── AUDIT LOG (workspace-scoped, owner/admin only) ──────────────────────────

@router.get("/w/{slug}/audit", response_class=HTMLResponse)
async def workspace_audit(
    request: Request, slug: str, db: AsyncSession = Depends(get_db),
    action: str | None = None, resource_type: str | None = None,
    user_id: str | None = None, offset: int = 0, limit: int = 50,
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AUDIT_VIEW)
    all_ws = await _get_user_workspaces(user, db)

    # Build query
    base = select(AuditLog).where(AuditLog.workspace_id == ws.id)
    if action:
        base = base.where(AuditLog.action == action)
    if resource_type:
        base = base.where(AuditLog.resource_type == resource_type)
    if user_id:
        base = base.where(AuditLog.user_id == user_id)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    result = await db.execute(base.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit))
    logs = list(result.scalars().all())

    # Distinct actions & resource_types for filter dropdowns
    actions_result = await db.execute(
        select(AuditLog.action).where(AuditLog.workspace_id == ws.id).distinct()
    )
    available_actions = sorted([r[0] for r in actions_result.all()])

    rt_result = await db.execute(
        select(AuditLog.resource_type).where(
            AuditLog.workspace_id == ws.id, AuditLog.resource_type.isnot(None)
        ).distinct()
    )
    available_resource_types = sorted([r[0] for r in rt_result.all()])

    # Members for user filter dropdown
    members_result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == ws.id)
    )
    members = [
        {"user_id": wm.user_id, "display_name": u.display_name}
        for wm, u in members_result.all()
    ]

    # Map user_id -> display_name for rendering
    user_map = {str(m["user_id"]): m["display_name"] for m in members}

    return templates.TemplateResponse(
        request, "dashboard/audit.html",
        {
            "logs": logs, "total": total, "limit": limit, "offset": offset,
            "filter_action": action, "filter_resource_type": resource_type,
            "filter_user_id": user_id or "",
            "available_actions": available_actions,
            "available_resource_types": available_resource_types,
            "members": members, "user_map": user_map,
            "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "audit",
        },
    )


# ── CONVERSATIONS (workspace-scoped) ─────────────────────────────────────────

@router.get("/w/{slug}/conversations", response_class=HTMLResponse)
async def workspace_conversations(
    request: Request, slug: str, db: AsyncSession = Depends(get_db),
    agent_id: str | None = None, q: str | None = None,
    offset: int = 0, limit: int = 30,
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    all_ws = await _get_user_workspaces(user, db)
    agents = await _list_agents(db, workspace_id=ws.id)

    # Build query: conversations with message count and last message preview
    base = (
        select(
            Conversation,
            func.count(Message.id).label("message_count"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.workspace_id == ws.id)
        .group_by(Conversation.id)
    )

    if agent_id:
        base = base.where(Conversation.agent_id == agent_id)

    # Text search in messages
    if q and q.strip():
        matching_conv_ids = (
            select(Message.conversation_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.workspace_id == ws.id, Message.content.ilike(f"%{q.strip()}%"))
            .distinct()
        )
        base = base.where(Conversation.id.in_(matching_conv_ids))

    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    result = await db.execute(
        base.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit)
    )
    rows = result.all()

    # Fetch last message for each conversation
    conversations = []
    for conv, msg_count in rows:
        last_msg_result = await db.execute(
            select(Message.content)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none()
        conversations.append({
            "id": conv.id,
            "agent_id": conv.agent_id,
            "updated_at": conv.updated_at,
            "message_count": msg_count,
            "last_message": (last_msg[:120] + "...") if last_msg and len(last_msg) > 120 else last_msg,
        })

    return templates.TemplateResponse(
        request, "dashboard/conversations/list.html",
        {
            "conversations": conversations, "total": total,
            "offset": offset, "limit": limit,
            "filter_agent_id": agent_id, "filter_q": q,
            "agents": agents,
            "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "conversations",
        },
    )


@router.get("/w/{slug}/conversations/{conversation_id}", response_class=HTMLResponse)
async def workspace_conversation_detail(
    request: Request, slug: str, conversation_id: str, db: AsyncSession = Depends(get_db),
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    all_ws = await _get_user_workspaces(user, db)

    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.workspace_id != ws.id:
        return RedirectResponse(f"/dashboard/w/{slug}/conversations", status_code=303)

    result = await db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.asc())
    )
    messages = list(result.scalars().all())

    can_chat = has_permission(role, Permission.CHAT)

    return templates.TemplateResponse(
        request, "dashboard/conversations/detail.html",
        {
            "conversation": conv, "messages": messages, "can_chat": can_chat,
            "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "conversations",
        },
    )


# ── FILE MANAGER ──────────────────────────────────────────────────────────────

@router.get("/w/{slug}/files", response_class=HTMLResponse)
async def workspace_files(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    all_ws = await _get_user_workspaces(user, db)
    can_write = has_permission(role, Permission.CHAT)
    return templates.TemplateResponse(
        request, "dashboard/workspaces/files.html",
        {
            "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "files",
            "user_role": role, "can_write": can_write,
        },
    )


# ── AGENTS CRUD (workspace-scoped) ────────────────────────────────────────────

async def _load_provider_credentials(db: AsyncSession, workspace_id):
    """Charge les credentials provider du workspace + globaux pour le select du formulaire agent."""
    from sqlalchemy import or_
    from models.provider_credential import ProviderCredential
    from credential_vault import decrypt as _decrypt
    import json as _json
    result = await db.execute(
        select(ProviderCredential).where(
            or_(
                ProviderCredential.workspace_id == workspace_id,
                ProviderCredential.workspace_id == None,
            )
        ).order_by(ProviderCredential.provider, ProviderCredential.created_at)
    )
    creds = []
    for c in result.scalars().all():
        try:
            data = _json.loads(_decrypt(c.credential_data))
            api_key = data.get("api_key", "")
            preview = f"{api_key[:8]}...{api_key[-3:]}" if len(api_key) > 11 else "***"
        except Exception:
            preview = "***"
        creds.append({"id": str(c.id), "provider": c.provider,
                      "display_name": c.display_name, "key_preview": preview, "is_default": c.is_default})
    return creds


@router.get("/w/{slug}/agents/new", response_class=HTMLResponse)
async def workspace_agents_new_form(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_CREATE)
    all_ws = await _get_user_workspaces(user, db)
    provider_credentials = await _load_provider_credentials(db, ws.id)
    return templates.TemplateResponse(
        request, "dashboard/agents/form.html",
        {"agent": None, "user": user, "workspace": ws, "workspace_slug": slug,
         "all_workspaces": all_ws, "provider_credentials": provider_credentials},
    )


@router.post("/w/{slug}/agents/new")
async def workspace_agents_create(
    request: Request, slug: str, db: AsyncSession = Depends(get_db),
    id: str = Form(...), name: str = Form(...), description: str = Form(""),
    llm_model: str = Form("openai/gpt-4o-mini"), llm_provider: str = Form("openrouter"),
    provider_credential_id: str = Form(""),
    system_prompt: str = Form(""), skills: str = Form("[]"), tools: str = Form("[]"),
    subagents: str = Form("[]"), interrupt_on: str = Form("{}"),
    context_window_size: int = Form(50),
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_CREATE)
    all_ws = await _get_user_workspaces(user, db)
    provider_credentials = await _load_provider_credentials(db, ws.id)
    try:
        agent_id = id.strip()
        if not _SLUG_RE.match(agent_id):
            raise ValueError("L'ID ne doit contenir que des caractères alphanumériques, tirets, underscores, points.")
        import uuid as _uuid
        cred_id = _uuid.UUID(provider_credential_id) if provider_credential_id.strip() else None
        from models.agent_config import WorkspaceAgent
        config = AgentConfig(
            id=agent_id, workspace_id=ws.id, owner_id=user.id, name=name.strip(),
            description=description.strip() or None,
            llm_config={"model": llm_model},
            llm_provider=llm_provider, llm_model=llm_model,
            provider_credential_id=cred_id,
            system_prompt=system_prompt.strip() or None,
            skills=json.loads(skills), tools=json.loads(tools),
            subagents=json.loads(subagents), interrupt_on=json.loads(interrupt_on),
            context_window_size=max(1, context_window_size),
        )
        db.add(config)
        await db.flush()
        db.add(WorkspaceAgent(workspace_id=ws.id, agent_id=agent_id, added_by=user.id))
        await db.commit()
        await db.refresh(config)
        config._workspace_slug = ws.slug
        agent_manager.register_config(config)
    except Exception as e:
        return templates.TemplateResponse(
            request, "dashboard/agents/form.html",
            {"agent": None, "flash": {"type": "error", "message": str(e)},
             "user": user, "workspace": ws, "workspace_slug": slug,
             "all_workspaces": all_ws, "provider_credentials": provider_credentials},
        )
    return RedirectResponse(f"/dashboard/w/{slug}/agents?created={id}", status_code=303)


@router.get("/w/{slug}/agents/{agent_id}/edit", response_class=HTMLResponse)
async def workspace_agents_edit_form(request: Request, slug: str, agent_id: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_CREATE)
    config = await db.get(AgentConfig, agent_id)
    if not config or not await _agent_linked_to_workspace(db, agent_id, ws.id):
        return RedirectResponse(f"/dashboard/w/{slug}/agents", status_code=303)
    all_ws = await _get_user_workspaces(user, db)
    provider_credentials = await _load_provider_credentials(db, ws.id)
    return templates.TemplateResponse(
        request, "dashboard/agents/form.html",
        {"agent": config, "user": user, "workspace": ws, "workspace_slug": slug,
         "all_workspaces": all_ws, "provider_credentials": provider_credentials},
    )


@router.post("/w/{slug}/agents/{agent_id}/edit")
async def workspace_agents_update(
    request: Request, slug: str, agent_id: str, db: AsyncSession = Depends(get_db),
    name: str = Form(...), description: str = Form(""),
    llm_model: str = Form("openai/gpt-4o-mini"), llm_provider: str = Form("openrouter"),
    provider_credential_id: str = Form(""),
    system_prompt: str = Form(""), skills: str = Form("[]"), tools: str = Form("[]"),
    subagents: str = Form("[]"), interrupt_on: str = Form("{}"),
    context_window_size: int = Form(50),
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_CREATE)
    config = await db.get(AgentConfig, agent_id)
    if not config or not await _agent_linked_to_workspace(db, agent_id, ws.id):
        return RedirectResponse(f"/dashboard/w/{slug}/agents", status_code=303)
    all_ws = await _get_user_workspaces(user, db)
    provider_credentials = await _load_provider_credentials(db, ws.id)
    try:
        import uuid as _uuid
        config.name = name.strip()
        config.description = description.strip() or None
        config.llm_config = {"model": llm_model}
        config.llm_provider = llm_provider
        config.llm_model = llm_model
        config.provider_credential_id = _uuid.UUID(provider_credential_id) if provider_credential_id.strip() else None
        config.system_prompt = system_prompt.strip() or None
        config.skills = json.loads(skills) if skills.strip() else []
        config.tools = json.loads(tools) if tools.strip() else []
        config.subagents = json.loads(subagents) if subagents.strip() else []
        config.interrupt_on = json.loads(interrupt_on) if interrupt_on.strip() else {}
        config.context_window_size = max(1, context_window_size)
        config.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(config)
        config._workspace_slug = ws.slug
        agent_manager.register_config(config)
    except Exception as e:
        return templates.TemplateResponse(
            request, "dashboard/agents/form.html",
            {"agent": config, "flash": {"type": "error", "message": str(e)},
             "user": user, "workspace": ws, "workspace_slug": slug,
             "all_workspaces": all_ws, "provider_credentials": provider_credentials},
        )
    return RedirectResponse(f"/dashboard/w/{slug}/agents?updated={agent_id}", status_code=303)


@router.post("/w/{slug}/agents/{agent_id}/delete")
async def workspace_agents_delete(request: Request, slug: str, agent_id: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_DELETE)
    config = await db.get(AgentConfig, agent_id)
    if config and await _agent_linked_to_workspace(db, agent_id, ws.id):
        config.is_active = False
        config.updated_at = datetime.utcnow()
        await db.commit()
        agent_manager.deactivate(agent_id, str(ws.id))
    return RedirectResponse(f"/dashboard/w/{slug}/agents", status_code=303)


@router.post("/w/{slug}/agents/{agent_id}/add-to-workspace")
async def workspace_agents_add_existing(request: Request, slug: str, agent_id: str, db: AsyncSession = Depends(get_db)):
    """Add an existing agent (owned by user) to this workspace."""
    from models.agent_config import WorkspaceAgent
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_CREATE)
    config = await db.get(AgentConfig, agent_id)
    if not config or not config.is_active:
        return RedirectResponse(f"/dashboard/w/{slug}/agents", status_code=303)
    # Check not already linked
    if not await _agent_linked_to_workspace(db, agent_id, ws.id):
        db.add(WorkspaceAgent(workspace_id=ws.id, agent_id=agent_id, added_by=user.id))
        await db.commit()
        config._workspace_slug = ws.slug
        agent_manager.register_config(config)
    return RedirectResponse(f"/dashboard/w/{slug}/agents", status_code=303)


@router.post("/w/{slug}/agents/{agent_id}/remove-from-workspace")
async def workspace_agents_remove(request: Request, slug: str, agent_id: str, db: AsyncSession = Depends(get_db)):
    """Remove an agent from this workspace (does NOT delete it)."""
    from models.agent_config import WorkspaceAgent
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_DELETE)
    result = await db.execute(
        select(WorkspaceAgent).where(
            WorkspaceAgent.workspace_id == ws.id,
            WorkspaceAgent.agent_id == agent_id,
        )
    )
    link = result.scalar_one_or_none()
    if link:
        await db.delete(link)
        await db.commit()
    return RedirectResponse(f"/dashboard/w/{slug}/agents", status_code=303)


# ── PROVIDERS (Phase 5) ───────────────────────────────────────────────────────

@router.get("/w/{slug}/providers", response_class=HTMLResponse)
async def workspace_providers_page(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    from sqlalchemy import or_
    from models.provider_credential import ProviderCredential
    from credential_vault import decrypt as _decrypt
    import json as _json
    result = await db.execute(
        select(ProviderCredential).where(
            or_(
                ProviderCredential.workspace_id == ws.id,
                ProviderCredential.workspace_id == None,
            )
        ).order_by(ProviderCredential.provider, ProviderCredential.created_at)
    )
    raw_creds = result.scalars().all()
    # Construire des dicts enrichis avec key_preview
    credentials = []
    for c in raw_creds:
        try:
            data = _json.loads(_decrypt(c.credential_data))
            api_key = data.get("api_key", "")
            preview = f"{api_key[:8]}...{api_key[-3:]}" if len(api_key) > 11 else "***"
        except Exception:
            preview = "***"
        credentials.append({
            "id": str(c.id),
            "provider": c.provider,
            "display_name": c.display_name,
            "is_default": c.is_default,
            "key_preview": preview,
            "workspace_id": str(c.workspace_id) if c.workspace_id else None,
            "created_at": c.created_at,
        })
    all_ws = await _get_user_workspaces(user, db)
    can_edit = has_permission(role, Permission.AGENT_CREATE)
    return templates.TemplateResponse(
        request, "dashboard/providers/list.html",
        {"user": user, "workspace": ws, "workspace_slug": slug,
         "all_workspaces": all_ws, "active_page": "providers",
         "credentials": credentials, "can_edit": can_edit},
    )


@router.post("/w/{slug}/providers/new")
async def workspace_providers_create(
    request: Request, slug: str, db: AsyncSession = Depends(get_db),
    provider: str = Form(...),
    display_name: str = Form(""),
    api_key: str = Form(""),
    base_url: str = Form(""),
    is_default: str = Form("off"),
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_CREATE)
    all_ws = await _get_user_workspaces(user, db)
    from models.provider_credential import ProviderCredential
    from credential_vault import encrypt as _encrypt
    import json as _json

    _SUPPORTED = {"openrouter", "anthropic", "google", "ollama", "openai_compatible"}
    if provider not in _SUPPORTED:
        return templates.TemplateResponse(
            request, "dashboard/providers/list.html",
            {"flash": {"type": "error", "message": f"Provider non supporté: {provider}"},
             "user": user, "workspace": ws, "workspace_slug": slug,
             "all_workspaces": all_ws, "active_page": "providers", "credentials": [], "can_edit": True},
        )

    is_default_bool = is_default.lower() in ("on", "true", "1", "yes")

    # Désactiver l'ancien default
    if is_default_bool:
        from sqlalchemy import or_
        old_result = await db.execute(
            select(ProviderCredential).where(
                ProviderCredential.workspace_id == ws.id,
                ProviderCredential.provider == provider,
                ProviderCredential.is_default == True,
            )
        )
        for old in old_result.scalars().all():
            old.is_default = False

    cred_data = {"api_key": api_key}
    if base_url.strip():
        cred_data["base_url"] = base_url.strip()
    form_data = await request.form()
    if model_name := form_data.get("model", "").strip():
        cred_data["model"] = model_name

    cred = ProviderCredential(
        workspace_id=ws.id,
        provider=provider,
        display_name=display_name.strip() or None,
        credential_data=_encrypt(_json.dumps(cred_data)),
        is_default=is_default_bool,
        created_by=user.id,
    )
    db.add(cred)
    await db.commit()
    return RedirectResponse(f"/dashboard/w/{slug}/providers?created=1", status_code=303)


@router.post("/w/{slug}/providers/{cred_id}/delete")
async def workspace_providers_delete(
    request: Request, slug: str, cred_id: str, db: AsyncSession = Depends(get_db),
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.AGENT_CREATE)
    from models.provider_credential import ProviderCredential
    import uuid as _uuid
    result = await db.execute(
        select(ProviderCredential).where(ProviderCredential.id == _uuid.UUID(cred_id))
    )
    cred = result.scalar_one_or_none()
    if cred and (cred.workspace_id == ws.id or cred.workspace_id is None):
        await db.delete(cred)
        await db.commit()
    return RedirectResponse(f"/dashboard/w/{slug}/providers", status_code=303)


@router.get("/w/{slug}/api-server", response_class=HTMLResponse)
async def workspace_api_server_page(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """Page d'instructions pour connecter une app tierce à l'API OpenAI-compatible du workspace."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    check_permission(role, Permission.APIKEY_MANAGE)

    all_ws = await _get_user_workspaces(user, db)
    agents = sorted(
        await _list_agents(db, ws.id),
        key=lambda a: (not a.is_default, (a.name or "").lower()),
    )
    example_model = agents[0].id if agents else "AGENT_ID"

    base_url = settings.oauth_redirect_base_url.rstrip("/") + f"/internal/v1/{ws.id}"
    internal_base_url = f"http://core:{settings.core_port}/internal/v1/{ws.id}"

    keys_result = await db.execute(
        select(ApiKey).where(ApiKey.workspace_id == ws.id, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
    )
    api_keys = list(keys_result.scalars().all())

    # Clé affichée une seule fois après création (?new_key=hf_...)
    new_key = request.query_params.get("new_key")

    return templates.TemplateResponse(
        request, "dashboard/api_server/index.html",
        {"user": user, "workspace": ws, "workspace_slug": slug,
         "all_workspaces": all_ws, "active_page": "api_server",
         "base_url": base_url, "internal_base_url": internal_base_url,
         "internal_token": str(ws.id), "api_keys": api_keys, "new_key": new_key,
         "agents": agents, "example_model": example_model},
    )


# ── SYSTÈME / MISE À JOUR (global, auth requise) ──────────────────────────────

@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Page d'administration : version de la plateforme + bouton de mise à jour."""
    user = request.state.user
    all_ws = await _get_user_workspaces(user, db)
    return templates.TemplateResponse(
        request, "dashboard/system/index.html",
        {"user": user, "all_workspaces": all_ws,
         "workspace_slug": (all_ws[0]["slug"] if all_ws else None),
         "active_page": "system"},
    )


@router.get("/system/version")
async def system_version(_auth=Depends(require_auth)):
    """Proxy JSON vers beekeeper /self/version (peut prendre qq s — git fetch)."""
    try:
        async with beekeeper_client() as client:
            r = await client.get("/self/version", timeout=70.0)
            return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"available": False, "reason": f"beekeeper injoignable: {e}"}, status_code=200)


class _SystemUpdateRequest(BaseModel):
    force: bool = False


@router.post("/system/update")
async def system_update_trigger(
    body: _SystemUpdateRequest, request: Request,
    _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
):
    """Déclenche la mise à jour côté beekeeper (non bloquant)."""
    user = request.state.user
    try:
        async with beekeeper_client() as client:
            r = await client.post("/self/update", json={"force": body.force})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"detail": f"beekeeper injoignable: {e}"}, status_code=502)
    try:
        await audit_log(
            db, action="system.update", user_id=user.id,
            ip=get_client_ip(request), details={"force": body.force},
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — l'audit ne doit jamais bloquer la mise à jour
        logger.warning("audit system.update échoué", exc_info=True)
    return JSONResponse(r.json(), status_code=r.status_code)


@router.get("/system/update/stream")
async def system_update_stream(_auth=Depends(require_auth)):
    """SSE — relaie la progression de la mise à jour depuis beekeeper (polling)."""
    async def event_stream():
        since = 0
        async with beekeeper_client() as client:
            while True:
                try:
                    r = await client.get(f"/self/update/status?since={since}", timeout=10.0)
                    data = r.json()
                except Exception:  # noqa: BLE001
                    # core ou beekeeper redémarre : on patiente et on retente.
                    yield f"data: {json.dumps({'i': -1, 'line': '… reconnexion (un service redémarre)'})}\n\n"
                    await asyncio.sleep(2)
                    continue
                for k, line in enumerate(data.get("lines", [])):
                    yield f"data: {json.dumps({'i': since + k, 'line': line})}\n\n"
                since = data.get("total", since)
                st = data.get("status")
                if st in ("ok", "failed"):
                    yield f"event: done\ndata: {json.dumps({'status': st, 'returncode': data.get('returncode')})}\n\n"
                    return
                await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── CHAT PROXY (auth cookie JWT, workspace-scoped) ────────────────────────────

class _DashChatRequest(BaseModel):
    agent_id: str | None = None
    message: str
    conversation_id: str | None = None
    workspace_id: str | None = None  # UUID string passé par le frontend


class _DashResumeRequest(BaseModel):
    agent_id: str | None = None
    decisions: list[dict]
    workspace_id: str | None = None


@router.post("/chat/stream")
async def dashboard_chat_stream(req: _DashChatRequest, request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Proxy SSE streaming — auth par cookie JWT httponly."""
    from mention_parser import parse_mentions
    from models.agent_config import WorkspaceAgent
    from slash_commands import execute_slash_command, is_slash_command
    user = request.state.user

    # N1: Detect and execute slash commands before sending to LLM
    if is_slash_command(req.message):
        from uuid import UUID as _UUID
        ws_uuid = _UUID(req.workspace_id) if req.workspace_id else None
        member = None
        if ws_uuid:
            mem_result = await db.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == ws_uuid,
                    WorkspaceMember.user_id == user.id,
                )
            )
            member = mem_result.scalar_one_or_none()
        slash = await execute_slash_command(req.message, user, ws_uuid, member, db)
        if slash.is_command:
            async def slash_msg():
                yield f"event: message_start\ndata: {json.dumps({'conversation_id': req.conversation_id or '', 'agent_id': 'system'})}\n\n"
                yield f"event: token\ndata: {json.dumps({'content': slash.system_message or ''})}\n\n"
                yield f"event: message_end\ndata: {json.dumps({'conversation_id': req.conversation_id or ''})}\n\n"
            return StreamingResponse(slash_msg(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    # M1-M4: Parse @mentions
    available = []
    if req.workspace_id:
        from uuid import UUID as _UUID
        wa_result = await db.execute(
            select(WorkspaceAgent.agent_id)
            .join(AgentConfig, AgentConfig.id == WorkspaceAgent.agent_id)
            .where(WorkspaceAgent.workspace_id == _UUID(req.workspace_id), AgentConfig.is_active == True)
        )
        available = [row[0] for row in wa_result.all()]

    mention = parse_mentions(req.message, available, req.agent_id)

    if mention.system_message:
        async def system_msg():
            yield f"event: message_start\ndata: {json.dumps({'conversation_id': req.conversation_id or '', 'agent_id': 'system'})}\n\n"
            yield f"event: token\ndata: {json.dumps({'content': mention.system_message})}\n\n"
            yield f"event: message_end\ndata: {json.dumps({'conversation_id': req.conversation_id or ''})}\n\n"
        return StreamingResponse(system_msg(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    agent_id = mention.resolved_agent_id or req.agent_id
    message_for_agent = mention.clean_message

    async def event_generator():
        collected_text = []
        final_conversation_id = req.conversation_id

        try:
            async for sse in agent_manager.astream(
                agent_id=agent_id,
                message=message_for_agent,
                conversation_id=req.conversation_id,
                workspace_id=req.workspace_id,
            ):
                event_type = sse["event"]
                data = sse["data"]

                # Track conversation_id from first event
                if data.get("conversation_id"):
                    final_conversation_id = data["conversation_id"]

                # Accumulate agent text tokens
                if event_type == "token" and data.get("content"):
                    collected_text.append(data["content"])

                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

        # Persist conversation + messages to DB
        if final_conversation_id:
            try:
                agent_response = "".join(collected_text)
                await persist_exchange(
                    conversation_id=final_conversation_id,
                    agent_id=agent_id,
                    user_message=req.message,
                    agent_response=agent_response,
                    workspace_id=req.workspace_id,
                    user_id=str(user.id),
                )
            except Exception as e:
                logger.warning(f"Failed to persist chat exchange: {e}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/{conversation_id}/resume")
async def dashboard_chat_resume(conversation_id: str, req: _DashResumeRequest, request: Request, _auth=Depends(require_auth)):
    """Resume HITL — auth par cookie JWT httponly."""
    user = request.state.user
    result = await asyncio.to_thread(
        agent_manager.resume,
        agent_id=req.agent_id,
        conversation_id=conversation_id,
        decisions=req.decisions,
        workspace_id=req.workspace_id,
    )

    # Persist resume response
    try:
        await persist_exchange(
            conversation_id=conversation_id,
            agent_id=req.agent_id,
            user_message=f"[HITL resume: {req.decisions}]",
            agent_response=result.get("response", ""),
            workspace_id=req.workspace_id,
            user_id=str(user.id),
        )
    except Exception as e:
        logger.warning(f"Failed to persist resume exchange: {e}")

    return result


# ── SHARED CHAT — Bloc P (real-time multi-user) ──────────────────────────────

class _ChatSendRequest(BaseModel):
    agent_id: str | None = None
    message: str
    workspace_id: str | None = None
    workspace_slug: str | None = None


# Background tasks set — prevents garbage collection of fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()

# Concurrency guard: one agent invocation at a time per (workspace, agent)
_agent_locks: dict[str, asyncio.Lock] = {}


def _get_agent_lock(workspace_id: str, agent_id: str) -> asyncio.Lock:
    key = f"{workspace_id}:{agent_id}"
    if key not in _agent_locks:
        _agent_locks[key] = asyncio.Lock()
    return _agent_locks[key]


@router.post("/chat/send")
async def dashboard_chat_send(
    req: _ChatSendRequest,
    request: Request,
    _auth=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the workspace chat. Returns immediately.
    Agent processing happens in the background; events broadcast via Redis.
    """
    from mention_parser import parse_mentions
    from models.agent_config import WorkspaceAgent
    from slash_commands import execute_slash_command, is_slash_command
    from uuid import UUID as _UUID

    user = request.state.user
    workspace_id = req.workspace_id
    if not workspace_id:
        raise HTTPException(400, "workspace_id requis")

    ws_uuid = _UUID(workspace_id)

    # Check workspace membership
    mem_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws_uuid,
            WorkspaceMember.user_id == user.id,
        )
    )
    member = mem_result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Pas membre de ce workspace")
    if not has_permission(member.role, Permission.CHAT):
        raise HTTPException(403, "Permission CHAT requise")

    # N1: Slash commands
    if is_slash_command(req.message):
        slash = await execute_slash_command(req.message, user, ws_uuid, member, db)
        if slash.is_command:
            await broadcaster.publish(workspace_id, {
                "type": "system_message",
                "data": {"content": slash.system_message or "", "source": "slash_command"},
            })
            return {"ok": True}

    display_name = user.display_name or user.slug or user.email.split("@")[0]

    # Human-only mode: no agent selected → persist + broadcast, skip agent entirely
    if not req.agent_id:
        conversation_id, message_id = await persist_user_message(
            workspace_id=workspace_id,
            content=req.message,
            user_id=str(user.id),
            display_name=display_name,
            target_agent_id=None,
        )
        await broadcaster.publish(workspace_id, {
            "type": "user_message",
            "data": {
                "message_id": message_id,
                "user_id": str(user.id),
                "display_name": display_name,
                "content": req.message,
                "agent_id": None,
            },
        })
        return {"ok": True, "conversation_id": conversation_id, "message_id": message_id}

    # M1-M4: @mentions — resolve target agent
    wa_result = await db.execute(
        select(WorkspaceAgent.agent_id)
        .join(AgentConfig, AgentConfig.id == WorkspaceAgent.agent_id)
        .where(WorkspaceAgent.workspace_id == ws_uuid, AgentConfig.is_active == True)
    )
    available = [row[0] for row in wa_result.all()]

    mention = parse_mentions(req.message, available, req.agent_id)

    if mention.system_message:
        await broadcaster.publish(workspace_id, {
            "type": "system_message",
            "data": {"content": mention.system_message, "source": "mention"},
        })
        return {"ok": True}

    agent_id = mention.resolved_agent_id or req.agent_id
    message_for_agent = mention.clean_message

    # Persist user message immediately
    conversation_id, message_id = await persist_user_message(
        workspace_id=workspace_id,
        content=req.message,
        user_id=str(user.id),
        display_name=display_name,
        target_agent_id=agent_id,
    )

    # Broadcast user message to all connected clients
    await broadcaster.publish(workspace_id, {
        "type": "user_message",
        "data": {
            "message_id": message_id,
            "user_id": str(user.id),
            "display_name": display_name,
            "content": req.message,
            "agent_id": agent_id,
        },
    })

    # Launch agent processing in background
    async def _process_agent():
        try:
            await broadcaster.publish(workspace_id, {
                "type": "agent_start",
                "data": {"agent_id": agent_id},
            })

            collected_text: list[str] = []
            collected_tool_events: list[dict] = []

            async with _get_agent_lock(workspace_id, agent_id):
                async for sse in agent_manager.astream(
                    agent_id=agent_id,
                    message=message_for_agent,
                    workspace_id=workspace_id,
                    workspace_slug=req.workspace_slug,
                ):
                    event_type = sse["event"]
                    data = sse["data"]

                    if event_type == "token" and data.get("content"):
                        collected_text.append(data["content"])
                        await broadcaster.publish(workspace_id, {
                            "type": "agent_token",
                            "data": {"agent_id": agent_id, "content": data["content"]},
                        })
                    elif event_type == "tool_call":
                        collected_tool_events.append({
                            "type": "call",
                            "name": data.get("name"),
                            "args": data.get("args"),
                        })
                        await broadcaster.publish(workspace_id, {
                            "type": "agent_tool_call",
                            "data": {"agent_id": agent_id, "name": data.get("name"), "args": data.get("args")},
                        })
                    elif event_type == "tool_result":
                        collected_tool_events.append({
                            "type": "result",
                            "name": data.get("name"),
                            "output": str(data.get("output", "")),
                        })
                        await broadcaster.publish(workspace_id, {
                            "type": "agent_tool_result",
                            "data": {"agent_id": agent_id, "name": data.get("name"), "output": data.get("output")},
                        })
                    elif event_type == "interrupt":
                        await broadcaster.publish(workspace_id, {
                            "type": "interrupt",
                            "data": {
                                "agent_id": agent_id,
                                "conversation_id": conversation_id,
                                "action_requests": data.get("action_requests", []),
                            },
                        })
                        return  # Don't publish agent_end on interrupt
                    elif event_type == "error":
                        await broadcaster.publish(workspace_id, {
                            "type": "agent_error",
                            "data": {"agent_id": agent_id, "message": data.get("message", "")},
                        })
                        return

            # Persist agent response
            full_response = "".join(collected_text)
            if full_response:
                agent_msg_id = await persist_agent_message(
                    conversation_id, agent_id, full_response,
                    tool_events=collected_tool_events or None,
                )
            else:
                agent_msg_id = ""

            await broadcaster.publish(workspace_id, {
                "type": "agent_end",
                "data": {"agent_id": agent_id, "message_id": agent_msg_id},
            })

        except Exception as e:
            logger.error(f"Background agent processing failed: {e!r}", exc_info=True)
            await broadcaster.publish(workspace_id, {
                "type": "agent_error",
                "data": {"agent_id": agent_id, "message": str(e)},
            })

    task = asyncio.create_task(_process_agent())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    # Audit
    try:
        await audit_log(
            db, action="chat.send", user_id=user.id, workspace_id=ws_uuid,
            details={"agent_id": agent_id, "message_preview": req.message[:80]},
            ip=get_client_ip(request),
        )
    except Exception:
        pass

    return {"ok": True, "conversation_id": conversation_id, "message_id": message_id}


@router.get("/w/{slug}/chat/events")
async def workspace_chat_events(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Persistent SSE stream — broadcasts all chat events for the workspace."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    workspace_id = str(ws.id)

    async def event_stream():
        heartbeat_interval = 15  # seconds
        last_heartbeat = asyncio.get_event_loop().time()

        async for event in broadcaster.subscribe(workspace_id):
            # Check client disconnect
            if await request.is_disconnected():
                break

            if event is None:
                # Heartbeat on timeout
                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= heartbeat_interval:
                    yield ": keepalive\n\n"
                    last_heartbeat = now
                continue

            event_type = event.get("type", "unknown")
            data = json.dumps(event.get("data", {}), ensure_ascii=False, default=str)
            yield f"event: {event_type}\ndata: {data}\n\n"
            last_heartbeat = asyncio.get_event_loop().time()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/w/{slug}/chat/history")
async def workspace_chat_history(
    request: Request,
    slug: str,
    before: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Paginated message history for the workspace chat."""
    user, ws, role = await require_workspace_auth(slug, request, db)

    if limit > 200:
        limit = 200

    messages, has_more = await get_recent_messages(
        db, str(ws.id), limit=limit, before_id=before,
    )

    return {
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "user_id": str(m.user_id) if m.user_id else None,
                "display_name": m.display_name,
                "agent_id": m.agent_id,
                "created_at": m.created_at.isoformat(),
                "metadata": m.metadata_ or {},
            }
            for m in messages
        ],
        "has_more": has_more,
    }


# ── WORKSPACES ────────────────────────────────────────────────────────────────

@router.get("/workspaces", response_class=HTMLResponse)
async def workspaces_list(request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db), q: str | None = None):
    from models.agent_config import WorkspaceAgent
    user = request.state.user
    ws_result = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.is_active == True)
    )
    workspaces = []
    for ws, role in ws_result.all():
        member_count = (await db.execute(
            select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id)
        )).scalar() or 0
        agent_count = (await db.execute(
            select(func.count()).select_from(WorkspaceAgent).where(WorkspaceAgent.workspace_id == ws.id)
        )).scalar() or 0
        workspaces.append({
            "id": ws.id, "name": ws.name, "slug": ws.slug, "description": ws.description,
            "owner_id": ws.owner_id, "is_active": ws.is_active, "role": role,
            "created_at": ws.created_at, "member_count": member_count, "agent_count": agent_count,
            "last_match": None,
        })

    # Recherche dans les messages de tous les workspaces
    if q and q.strip():
        q_strip = q.strip()
        match_map = {}
        for ws_data in workspaces:
            msg = (await db.execute(
                select(Message.content)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Conversation.workspace_id == ws_data["id"], Message.content.ilike(f"%{q_strip}%"))
                .order_by(Message.created_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if msg is not None:
                match_map[ws_data["id"]] = msg
        workspaces = [
            {**ws, "last_match": (ws_match[:120] + "...") if len(ws_match) > 120 else ws_match}
            for ws in workspaces
            if (ws_match := match_map.get(ws["id"])) is not None
        ]

    return templates.TemplateResponse(
        request, "dashboard/workspaces/list.html",
        {"workspaces": workspaces, "flash": _get_flash(request), "user": user, "filter_q": q},
    )


@router.get("/workspaces/new", response_class=HTMLResponse)
async def workspaces_new_form(request: Request, _auth=Depends(require_auth)):
    return templates.TemplateResponse(
        request, "dashboard/workspaces/form.html",
        {"workspace": None, "user": request.state.user},
    )


@router.post("/workspaces/new")
async def workspaces_create(
    request: Request, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
    name: str = Form(...), slug: str = Form(...), description: str = Form(""),
):
    user = request.state.user
    slug_val = slug.strip().lower()
    try:
        existing = await db.execute(select(Workspace).where(Workspace.slug == slug_val))
        if existing.scalar_one_or_none():
            raise ValueError("Ce slug est deja utilise.")
        ws = Workspace(name=name.strip(), slug=slug_val, description=description.strip() or None, owner_id=user.id)
        db.add(ws)
        await db.flush()
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
        await db.commit()
    except Exception as e:
        return templates.TemplateResponse(
            request, "dashboard/workspaces/form.html",
            {"workspace": None, "flash": {"type": "error", "message": str(e)}, "user": user},
        )
    return RedirectResponse("/dashboard/workspaces", status_code=303)


@router.get("/workspaces/{workspace_slug}", response_class=HTMLResponse)
async def workspaces_detail(request: Request, workspace_slug: str, _auth=Depends(require_auth)):
    return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)


@router.get("/workspaces/{workspace_slug}/edit", response_class=HTMLResponse)
async def workspaces_edit_form(request: Request, workspace_slug: str, _auth=Depends(require_auth)):
    return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)


@router.get("/workspaces/{workspace_slug}/manage", response_class=HTMLResponse)
async def workspaces_manage(request: Request, workspace_slug: str, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    user = request.state.user
    ws_result = await db.execute(select(Workspace).where(Workspace.slug == workspace_slug, Workspace.is_active == True))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        return RedirectResponse("/dashboard/workspaces", status_code=303)

    membership = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
    )
    member = membership.scalar_one_or_none()
    if not member:
        return RedirectResponse("/dashboard/workspaces", status_code=303)

    result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == ws.id)
    )
    members = [
        {"id": wm.id, "user_id": wm.user_id, "email": u.email,
         "display_name": u.display_name, "role": wm.role, "created_at": wm.created_at}
        for wm, u in result.all()
    ]

    api_keys = []
    if has_permission(member.role, Permission.APIKEY_MANAGE):
        keys_result = await db.execute(
            select(ApiKey).where(ApiKey.workspace_id == ws.id, ApiKey.is_active == True)
            .order_by(ApiKey.created_at.desc())
        )
        api_keys = list(keys_result.scalars().all())

    return templates.TemplateResponse(
        request, "dashboard/workspaces/manage.html",
        {"workspace": ws, "members": members, "user_role": member.role,
         "flash": _get_flash(request), "user": user, "api_keys": api_keys,
         "new_key": request.query_params.get("new_key")},
    )


@router.post("/workspaces/{workspace_slug}/edit")
async def workspaces_update(
    request: Request, workspace_slug: str, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
    name: str = Form(...), description: str = Form(""),
):
    user = request.state.user
    ws_result = await db.execute(select(Workspace).where(Workspace.slug == workspace_slug, Workspace.is_active == True))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    membership = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
    )
    member = membership.scalar_one_or_none()
    if not member:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    check_permission(member.role, Permission.WORKSPACE_MANAGE)
    ws.name = name.strip()
    ws.description = description.strip() or None
    ws.updated_at = datetime.utcnow()
    await db.commit()
    return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)


@router.post("/workspaces/{workspace_slug}/invite")
async def workspaces_invite(
    request: Request, workspace_slug: str, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
    email: str = Form(...), role: str = Form("member"),
):
    user = request.state.user
    ws_result = await db.execute(select(Workspace).where(Workspace.slug == workspace_slug, Workspace.is_active == True))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    caller_result = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
    )
    caller = caller_result.scalar_one_or_none()
    if not caller:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    check_permission(caller.role, Permission.MEMBERS_MANAGE)

    result = await db.execute(select(User).where(User.email == email))
    target_user = result.scalar_one_or_none()
    if not target_user:
        request.state.flash = {"type": "error", "message": "Aucun utilisateur avec cet email."}
        return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)

    existing = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == target_user.id)
    )
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)

    db.add(WorkspaceMember(workspace_id=ws.id, user_id=target_user.id, role=role))
    await db.commit()
    return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)


@router.post("/workspaces/{workspace_slug}/members/{member_user_id}/remove")
async def workspaces_remove_member(
    request: Request, workspace_slug: str, member_user_id: str,
    _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
):
    user = request.state.user
    ws_result = await db.execute(select(Workspace).where(Workspace.slug == workspace_slug, Workspace.is_active == True))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    caller_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.user_id == user.id,
        )
    )
    caller = caller_result.scalar_one_or_none()
    if not caller:
        return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)
    check_permission(caller.role, Permission.MEMBERS_MANAGE)

    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.user_id == member_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member and member.role != "owner":
        await db.delete(member)
        await db.commit()
    return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)


# ── API KEYS (workspace-scoped dashboard) ──────────────────────────────────

@router.post("/workspaces/{workspace_slug}/api-keys/create")
async def workspaces_create_api_key(
    request: Request, workspace_slug: str, _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
    name: str = Form(...), permissions: list[str] = Form(["chat", "tasks", "agents"]),
    next: str = Form("manage"),
):
    user = request.state.user
    ws_result = await db.execute(select(Workspace).where(Workspace.slug == workspace_slug, Workspace.is_active == True))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    caller_result = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
    )
    caller = caller_result.scalar_one_or_none()
    if not caller:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    check_permission(caller.role, Permission.APIKEY_MANAGE)

    full_key, prefix = generate_api_key()
    key_hash = hash_api_key(full_key)
    api_key = ApiKey(
        key_hash=key_hash, key_prefix=prefix, name=name.strip(),
        workspace_id=ws.id, created_by=user.id, permissions=permissions,
    )
    db.add(api_key)
    await db.commit()

    # Redirect with new_key shown once (the full key)
    if next == "api-server":
        return RedirectResponse(
            f"/dashboard/w/{workspace_slug}/api-server?new_key={full_key}", status_code=303,
        )
    return RedirectResponse(
        f"/dashboard/workspaces/{workspace_slug}/manage?new_key={full_key}", status_code=303,
    )


@router.post("/workspaces/{workspace_slug}/api-keys/{key_id}/revoke")
async def workspaces_revoke_api_key(
    request: Request, workspace_slug: str, key_id: str,
    _auth=Depends(require_auth), db: AsyncSession = Depends(get_db),
    next: str = Form("manage"),
):
    user = request.state.user
    ws_result = await db.execute(select(Workspace).where(Workspace.slug == workspace_slug, Workspace.is_active == True))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        return RedirectResponse("/dashboard/workspaces", status_code=303)
    caller_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id,
        )
    )
    caller = caller_result.scalar_one_or_none()
    if not caller:
        return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)
    check_permission(caller.role, Permission.APIKEY_MANAGE)

    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.workspace_id == ws.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key:
        api_key.is_active = False
        await db.commit()

    if next == "api-server":
        return RedirectResponse(f"/dashboard/w/{workspace_slug}/api-server", status_code=303)
    return RedirectResponse(f"/dashboard/workspaces/{workspace_slug}/manage", status_code=303)


# ── APPS (Phase 6 — Bloc I) ──────────────────────────────────────────────────

@router.get("/w/{slug}/apps", response_class=HTMLResponse)
async def workspace_apps(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    all_ws = await _get_user_workspaces(user, db)

    from models.app import AppCatalog, AppInstance
    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id)
        .order_by(AppCatalog.name)
    )
    rows = result.all()
    apps = [
        {
            "id": str(inst.id),
            "app_slug": cat.slug,
            "app_name": cat.name,
            "app_image": cat.image,
            "app_icon": cat.icon,
            "app_type": cat.app_type,
            "ingress_mode": cat.ingress_mode,
            "default_port": cat.default_port,
            "container_name": inst.container_name,
            "status": inst.status,
            "container_ip": inst.container_ip,
            "description": cat.description,
            "env_schema": cat.env_schema or [],
            "resources": cat.resources or {},
            "started_at": inst.started_at.isoformat() if inst.started_at else None,
            "installed_version": inst.installed_version,
            "catalog_version": cat.version,
            "update_available": bool(inst.installed_version and inst.installed_version != cat.version),
        }
        for inst, cat in rows
    ]

    return templates.TemplateResponse(
        request, "dashboard/apps/list.html",
        {
            "apps": apps,
            "workspace_id": str(ws.id),
            "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "apps",
        },
    )


@router.get("/w/{slug}/apps/sidebar-apps")
async def workspace_apps_sidebar(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """JSON list of running iframe apps for the sidebar."""
    user, ws, _ = await require_workspace_auth(slug, request, db)
    from models.app import AppCatalog, AppInstance
    result = await db.execute(
        select(AppCatalog.slug, AppCatalog.name, AppCatalog.icon)
        .join(AppInstance, AppInstance.app_id == AppCatalog.id)
        .where(
            AppInstance.workspace_id == ws.id,
            AppCatalog.ingress_mode == "iframe",
            AppInstance.status == "running",
        )
        .order_by(AppCatalog.name)
    )
    rows = result.all()
    from fastapi.responses import JSONResponse
    return JSONResponse([{"slug": r[0], "name": r[1], "icon": r[2]} for r in rows])


@router.get("/w/{slug}/apps/catalog", response_class=HTMLResponse)
async def workspace_apps_catalog(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    user, ws, role = await require_workspace_auth(slug, request, db)
    all_ws = await _get_user_workspaces(user, db)

    from models.app import AppCatalog, AppInstance
    cat_result = await db.execute(
        select(AppCatalog).where(AppCatalog.is_active == True).order_by(AppCatalog.name)
    )
    catalog = list(cat_result.scalars().all())

    inst_result = await db.execute(
        select(AppCatalog.slug)
        .join(AppInstance, AppInstance.app_id == AppCatalog.id)
        .where(AppInstance.workspace_id == ws.id)
    )
    installed_slugs = {row[0] for row in inst_result.all()}

    return templates.TemplateResponse(
        request, "dashboard/apps/catalog.html",
        {
            "catalog": catalog,
            "installed_slugs": installed_slugs,
            "user": user, "workspace": ws, "workspace_slug": slug,
            "all_workspaces": all_ws, "active_page": "apps",
        },
    )


@router.post("/w/{slug}/apps/install")
async def dashboard_install_app(
    request: Request,
    slug: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    app_slug: str = Form(...),
):
    from models.app import AppCatalog, AppInstance
    from credential_vault import encrypt as _encrypt

    user, ws, role = await require_workspace_auth(slug, request, db)

    form = await request.form()
    env_values = {}
    for key, val in form.items():
        if key.startswith("env_") and str(val):
            env_values[key[4:]] = str(val)

    cat_result = await db.execute(
        select(AppCatalog).where(AppCatalog.slug == app_slug, AppCatalog.is_active == True)
    )
    app = cat_result.scalar_one_or_none()
    if not app:
        return RedirectResponse(f"/dashboard/w/{slug}/apps/catalog", status_code=303)

    existing = await db.execute(
        select(AppInstance).where(
            AppInstance.workspace_id == ws.id, AppInstance.app_id == app.id,
        )
    )
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)

    encrypted_env = None
    if env_values and settings.credential_encryption_key:
        encrypted_env = _encrypt(json.dumps(env_values))

    instance = AppInstance(
        workspace_id=ws.id,
        app_id=app.id,
        container_name=f"hive-app-{ws.id}-{app_slug}",
        status="pulling",
        env_values=encrypted_env,
        installed_by=user.id,
        installed_version=app.version,
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)

    from api.routes_apps import _install_worker
    background_tasks.add_task(
        _install_worker,
        str(ws.id), ws.slug, str(instance.id), app.image, app.slug,
        app.default_port, encrypted_env, app.default_mounts, app.resources or {},
    )

    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.post("/w/{slug}/apps/{app_slug}/start")
async def dashboard_start_app(
    request: Request, slug: str, app_slug: str, db: AsyncSession = Depends(get_db),
):
    from models.app import AppCatalog, AppInstance
    from beekeeper_client import beekeeper_client

    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id, AppCatalog.slug == app_slug)
    )
    row = result.first()
    if row:
        instance, app = row
        async with beekeeper_client() as client:
            r = await client.post("/apps/start", json={
                "workspace_id": str(ws.id), "workspace_slug": ws.slug, "app_slug": app_slug,
                "image": app.image, "port": app.default_port,
                "encrypted_env": instance.env_values,
                "mounts": app.default_mounts, "resources": app.resources or {},
            }, timeout=120.0)
        if r.status_code in (200, 201):
            data = r.json()
            instance.status = data.get("status", "running")
            instance.container_ip = data.get("container_ip")
            instance.started_at = datetime.utcnow()
            await db.commit()

    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.post("/w/{slug}/apps/{app_slug}/stop")
async def dashboard_stop_app(
    request: Request, slug: str, app_slug: str, db: AsyncSession = Depends(get_db),
):
    from models.app import AppCatalog, AppInstance
    from beekeeper_client import beekeeper_client

    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id, AppCatalog.slug == app_slug)
    )
    row = result.first()
    if row:
        instance, _ = row
        async with beekeeper_client() as client:
            await client.post("/apps/stop", json={"container_name": instance.container_name})
        instance.status = "stopped"
        instance.stopped_at = datetime.utcnow()
        await db.commit()

    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.post("/w/{slug}/apps/{app_slug}/update")
async def dashboard_update_app(
    request: Request, slug: str, app_slug: str,
    background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db),
):
    """Met à jour une app (rebuild/pull + recreate, données préservées)."""
    from models.app import AppCatalog, AppInstance

    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id, AppCatalog.slug == app_slug)
    )
    row = result.first()
    if row:
        instance, app = row
        instance.status = "pulling"
        await db.commit()
        from api.routes_apps import _update_worker
        background_tasks.add_task(
            _update_worker,
            str(ws.id), ws.slug, str(instance.id), app.image, app_slug,
            app.default_port, instance.env_values, app.default_mounts,
            app.resources or {}, app.version,
        )

    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.post("/w/{slug}/apps/check-updates")
async def dashboard_check_updates(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """Re-synchronise le catalogue depuis les manifests (beekeeper) pour rafraîchir
    les versions disponibles, puis recharge la page apps."""
    await require_workspace_auth(slug, request, db)
    try:
        async with beekeeper_client() as client:
            await client.post("/apps/sync-catalog", timeout=60.0)
    except Exception:  # noqa: BLE001
        logger.warning("check-updates: sync-catalog beekeeper échoué", exc_info=True)
    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.post("/w/{slug}/apps/{app_slug}/restart")
async def dashboard_restart_app(
    request: Request, slug: str, app_slug: str, db: AsyncSession = Depends(get_db),
):
    from models.app import AppCatalog, AppInstance
    from beekeeper_client import beekeeper_client

    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id, AppCatalog.slug == app_slug)
    )
    row = result.first()
    if row:
        instance, _ = row
        async with beekeeper_client() as client:
            r = await client.post("/apps/restart", json={"container_name": instance.container_name})
        if r.status_code == 200:
            instance.status = r.json().get("status", "running")
            await db.commit()
        elif r.status_code == 404:
            # Container disparu (supprimé manuellement) — corriger le status en DB
            instance.status = "stopped"
            await db.commit()

    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.post("/w/{slug}/apps/{app_slug}/uninstall")
async def dashboard_uninstall_app(
    request: Request, slug: str, app_slug: str, db: AsyncSession = Depends(get_db),
):
    from models.app import AppCatalog, AppInstance
    from beekeeper_client import beekeeper_client

    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id, AppCatalog.slug == app_slug)
    )
    row = result.first()
    if row:
        instance, _ = row
        async with beekeeper_client() as client:
            await client.delete(f"/apps/{instance.container_name}")
        await db.delete(instance)
        await db.commit()

    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.post("/w/{slug}/apps/{app_slug}/env")
async def dashboard_update_env(
    request: Request, slug: str, app_slug: str, db: AsyncSession = Depends(get_db),
):
    from models.app import AppCatalog, AppInstance
    from credential_vault import encrypt as _encrypt

    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id, AppCatalog.slug == app_slug)
    )
    row = result.first()
    if row:
        instance, _ = row
        form = await request.form()
        env_values = {}
        for key, val in form.items():
            if key.startswith("env_") and str(val):
                env_values[key[4:]] = str(val)
        if env_values and settings.credential_encryption_key:
            instance.env_values = _encrypt(json.dumps(env_values))
            await db.commit()

    return RedirectResponse(f"/dashboard/w/{slug}/apps", status_code=303)


@router.get("/w/{slug}/activity/stream")
async def dashboard_activity_stream(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """SSE — bus d'activité unifié (tasks + app events). Canal : hive:ws:{id}:activity"""
    user, ws, role = await require_workspace_auth(slug, request, db)
    channel = f"hive:ws:{ws.id}:activity"

    async def event_stream():
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if raw is None:
                    yield ": heartbeat\n\n"
                    continue
                try:
                    yield f"data: {raw['data']}\n\n"
                except (KeyError, TypeError):
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await r.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/w/{slug}/apps/metrics")
async def dashboard_app_metrics(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """SSE — métriques CPU/mémoire de toutes les apps (psubscribe, cookie auth)."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    pattern = f"hive:app:{ws.id}:*:metrics"

    async def event_stream():
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.psubscribe(pattern)
        try:
            while True:
                raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if raw is None:
                    yield ": heartbeat\n\n"
                    continue
                try:
                    ch = raw.get("channel") or raw.get("pattern", "")
                    parts = ch.split(":")
                    app_slug = parts[3] if len(parts) >= 5 else "unknown"
                    payload = json.loads(raw["data"])
                    payload["app_slug"] = app_slug
                    yield f"data: {json.dumps(payload)}\n\n"
                except (KeyError, TypeError, json.JSONDecodeError, IndexError):
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.punsubscribe(pattern)
            await pubsub.close()
            await r.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@router.get("/w/{slug}/apps/{app_slug}/logs")
async def dashboard_app_logs(
    request: Request, slug: str, app_slug: str,
    lines: int = 100, follow: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """SSE — logs du container, proxy vers Beekeeper (cookie auth)."""
    from models.app import AppCatalog, AppInstance
    from beekeeper_client import beekeeper_client

    user, ws, role = await require_workspace_auth(slug, request, db)
    result = await db.execute(
        select(AppInstance)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == ws.id, AppCatalog.slug == app_slug)
    )
    instance = result.scalar_one_or_none()
    if not instance:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': 'App not found'})}\n\n"]),
            media_type="text/event-stream",
        )

    bk_url = (
        f"{settings.beekeeper_url}/apps/logs"
        f"?container_name={instance.container_name}"
        f"&lines={lines}&follow={str(follow).lower()}"
        f"&workspace_id={ws.id}&app_slug={app_slug}"
    )

    async def event_stream():
        async with beekeeper_client() as client:
            async with client.stream("GET", bk_url, timeout=None) as response:
                if response.status_code != 200:
                    error = await response.aread()
                    yield f"data: {json.dumps({'error': error.decode()})}\n\n"
                    return
                async for line in response.aiter_lines():
                    if line:
                        yield f"{line}\n\n"
                    else:
                        yield "\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Portal / Devices ──────────────────────────────────────────────────────────

@router.get("/w/{slug}/portal/devices")
async def portal_devices_list(
    request: Request, slug: str,
    db: AsyncSession = Depends(get_db),
):
    user, ws, role = await require_workspace_auth(slug, request, db)
    all_ws = await _get_user_workspaces(user, db)

    result = await db.execute(
        select(PortalDevice)
        .where(PortalDevice.workspace_id == ws.id)
        .order_by(PortalDevice.created_at.desc())
    )
    devices = result.scalars().all()

    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "dashboard/portal/devices/list.html", {
        "user": user,
        "workspace": ws,
        "workspace_slug": slug,
        "role": role,
        "all_workspaces": all_ws,
        "active_page": "portal_devices",
        "devices": devices,
        "flash": flash,
    })


@router.post("/w/{slug}/portal/devices/create")
async def portal_device_create(
    request: Request, slug: str,
    db: AsyncSession = Depends(get_db),
):
    """HTMX endpoint — returns HTML fragment with init_command."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    if role not in ("owner", "admin", "member"):
        return HTMLResponse("Forbidden", status_code=403)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return HTMLResponse(
            '<div class="portal-modal-body"><p class="dim">Nom requis.</p></div>',
            status_code=422,
        )

    device, enroll_token_obj, plain_token = await portal_create_device(db, ws.id, name)
    await db.commit()

    _base = settings.oauth_redirect_base_url.rstrip("/")
    _ws_url = _base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/portal/ws"
    init_command = (
        f"portal-client init "
        f"--server {_ws_url} "
        f"--workspace {slug} "
        f"--token {plain_token} "
        f"--device-slug {device.slug}"
    )

    return templates.TemplateResponse(request, "dashboard/portal/devices/_created.html", {
        "device": device,
        "init_command": init_command,
    })


@router.get("/w/{slug}/portal/devices/{device_id}")
async def portal_device_detail(
    request: Request, slug: str, device_id: str,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    from uuid import UUID
    user, ws, role = await require_workspace_auth(slug, request, db)
    all_ws = await _get_user_workspaces(user, db)

    try:
        dev_uuid = UUID(device_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await db.execute(
        select(PortalDevice).where(
            PortalDevice.id == dev_uuid,
            PortalDevice.workspace_id == ws.id,
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    audit_limit = 50
    count_result = await db.execute(
        select(func.count()).select_from(PortalAuditLog)
        .where(PortalAuditLog.device_id == dev_uuid)
    )
    total_audit = count_result.scalar() or 0

    logs_result = await db.execute(
        select(PortalAuditLog)
        .where(PortalAuditLog.device_id == dev_uuid)
        .order_by(PortalAuditLog.ts.desc())
        .offset(offset)
        .limit(audit_limit)
    )
    audit_logs = logs_result.scalars().all()

    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "dashboard/portal/devices/detail.html", {
        "user": user,
        "workspace": ws,
        "workspace_slug": slug,
        "role": role,
        "all_workspaces": all_ws,
        "active_page": "portal_devices",
        "device": device,
        "audit_logs": audit_logs,
        "total_audit": total_audit,
        "audit_offset": offset,
        "audit_limit": audit_limit,
        "flash": flash,
    })


@router.post("/w/{slug}/portal/devices/{device_id}/revoke")
async def portal_device_revoke(
    request: Request, slug: str, device_id: str,
    db: AsyncSession = Depends(get_db),
):
    from uuid import UUID
    user, ws, role = await require_workspace_auth(slug, request, db)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        dev_uuid = UUID(device_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await db.execute(
        select(PortalDevice).where(
            PortalDevice.id == dev_uuid,
            PortalDevice.workspace_id == ws.id,
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await portal_revoke_device(db, device)
    await db.commit()

    request.session["flash"] = {"type": "success", "message": f"Device « {device.name} » révoqué."}
    return RedirectResponse(
        url=f"/dashboard/w/{slug}/portal/devices",
        status_code=303,
    )


_ALLOWED_CLIENTS = {
    "portal-client-darwin-amd64",
    "portal-client-darwin-arm64",
    "portal-client-linux-amd64",
    "portal-client-linux-arm64",
    "portal-client-windows-amd64.exe",
    "checksums.sha256",
}

@router.get("/portal/client/download/{filename}")
async def portal_client_download(filename: str):
    if filename not in _ALLOWED_CLIENTS:
        raise HTTPException(status_code=404, detail="File not found")
    path = f"/app/clients/portal/dist/{filename}"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Binary not available")
    media_type = "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=filename)
