import uuid
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import audit_log, get_client_ip
from celery_client import celery_app
from database import get_db
from models.task import Task
from models.workspace import Workspace
from permissions import AuthContext, Permission, require_permission
from services.activity_bus import publish_activity

router = APIRouter(prefix="/tasks", tags=["tasks"])

CELERY_TASK_MAP = {
    "python": "tasks.run_python",
    "bash": "tasks.run_bash",
    "translate": "tasks.run_translate",
    "translate_file": "tasks.run_translate",
    "fabric_extract_wisdom": "tasks.run_fabric",
    "fabric_summarize": "tasks.run_fabric",
    "fabric_extract_references": "tasks.run_fabric",
    "fabric_run": "tasks.run_fabric",
}

# Types that create a Task record without dispatching to Celery (tracked externally)
_PASSTHROUGH_TYPES = {"app_tool"}

_SCRIPT_TYPES = {"python", "bash"}

_DEFAULT_NAMES = {
    "python": "Run Python Script",
    "bash": "Run Bash Script",
    "translate": "Translate",
    "translate_file": "Translate File",
}


# ── Schemas ──────────────────────────────────────────────────────────────────

class TaskRunRequest(BaseModel):
    type: str
    name: str | None = None
    script: str | None = None
    timeout: int | None = None
    params: dict | None = None
    workspace_id: str | None = None


class TaskRead(BaseModel):
    task_id: str
    name: str | None
    type: str
    status: str
    progress: int
    progress_detail: str | None
    triggered_by: str | None
    backend_ref: str | None
    params: dict | None
    result: dict | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskProgress(BaseModel):
    task_id: str
    status: str
    progress: int
    progress_detail: str | None


class TaskRunResponse(BaseModel):
    task_id: str
    backend_ref: str | None
    status: str


class TaskPatchRequest(BaseModel):
    status: str | None = None
    progress: int | None = None
    progress_detail: str | None = None
    result: dict | None = None
    error: str | None = None


class CleanupResponse(BaseModel):
    deleted: int


# ── POST /tasks/run ───────────────────────────────────────────────────────────

@router.post("/run", response_model=TaskRunResponse)
async def run_task(
    req: TaskRunRequest,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.TASK_RUN)),
    db: AsyncSession = Depends(get_db),
):
    is_passthrough = req.type in _PASSTHROUGH_TYPES
    if not is_passthrough and req.type not in CELERY_TASK_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown task type '{req.type}'. Use: {', '.join(CELERY_TASK_MAP)}.",
        )

    task_uuid = uuid.uuid4()
    celery_ref: str | None = None

    if is_passthrough:
        params = dict(req.params or {})
    elif req.type in _SCRIPT_TYPES:
        if not req.script:
            raise HTTPException(status_code=400, detail="'script' is required for python/bash tasks.")
        kwargs = {
            "script": req.script,
            "db_task_id": str(task_uuid),
            "workspace_id": req.workspace_id or (str(ctx.workspace_id) if ctx.workspace_id else None),
        }
        if req.timeout:
            kwargs["timeout"] = req.timeout
        params = {"script": req.script, "timeout": req.timeout}
        celery_ref = celery_app.send_task(CELERY_TASK_MAP[req.type], kwargs=kwargs).id
    else:
        kwargs = dict(req.params or {})
        kwargs["db_task_id"] = str(task_uuid)
        kwargs.setdefault("workspace_id", req.workspace_id or (str(ctx.workspace_id) if ctx.workspace_id else None))
        params = kwargs.copy()
        celery_ref = celery_app.send_task(CELERY_TASK_MAP[req.type], kwargs=kwargs).id

    triggered_by = f"user:{ctx.user.id}" if ctx.user else "system"
    task_label = req.name or _DEFAULT_NAMES.get(req.type, req.type)

    # Resolve effective workspace_id: ctx first (JWT/DB key), fallback to req.workspace_id
    # (used when the legacy .env API key is used and workspace_id is passed in the body)
    effective_ws_id = ctx.workspace_id
    if effective_ws_id is None and req.workspace_id:
        try:
            ws_uuid = UUID(req.workspace_id)
            ws_row = await db.execute(
                select(Workspace.id).where(Workspace.id == ws_uuid, Workspace.is_active == True)
            )
            if ws_row.scalar_one_or_none():
                effective_ws_id = ws_uuid
        except (ValueError, Exception):
            pass

    db_task = Task(
        id=task_uuid,
        name=task_label,
        type=req.type,
        status="running" if is_passthrough else "pending",
        triggered_by=triggered_by,
        backend_ref=celery_ref,
        params=params,
        workspace_id=effective_ws_id,
        user_id=ctx.user.id if ctx.user else None,
        started_at=datetime.utcnow() if is_passthrough else None,
    )
    db.add(db_task)
    await audit_log(
        db, action="task.run",
        user_id=ctx.user.id if ctx.user else None,
        workspace_id=effective_ws_id,
        resource_type="task", resource_id=str(db_task.id),
        details={"type": req.type, "name": task_label, "script_preview": (req.script or "")[:100]},
        ip=get_client_ip(request),
    )
    await db.commit()
    await db.refresh(db_task)

    # B5 — publier task_created sur le bus d'activité
    if effective_ws_id:
        await publish_activity(
            workspace_id=str(effective_ws_id),
            event_type="task_created",
            source=triggered_by,
            data={"type": req.type, "name": task_label},
            task_id=str(db_task.id),
        )

    return TaskRunResponse(
        task_id=str(db_task.id),
        backend_ref=celery_ref,
        status=db_task.status,
    )


# ── PATCH /tasks/{task_id} — mise à jour de statut (app tools, workers externes) ──

@router.patch("/{task_id}")
async def patch_task(
    task_id: str,
    body: TaskPatchRequest,
    ctx: AuthContext = Depends(require_permission(Permission.TASK_RUN)),
    db: AsyncSession = Depends(get_db),
):
    """Met à jour le statut/résultat d'une task. Utilisé par les app tools pour signaler la completion."""
    query = select(Task).where(Task.id == uuid.UUID(task_id))
    if ctx.workspace_id:
        query = query.where(Task.workspace_id == ctx.workspace_id)
    result = await db.execute(query)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.status is not None:
        task.status = body.status
    if body.progress is not None:
        task.progress = body.progress
    if body.progress_detail is not None:
        task.progress_detail = body.progress_detail
    if body.result is not None:
        task.result = body.result
    if body.error is not None:
        task.error = body.error
    if body.status in ("success", "failed", "cancelled") and task.completed_at is None:
        task.completed_at = datetime.utcnow()
    task.updated_at = datetime.utcnow()
    await db.commit()

    # Publish completion event to activity bus
    if task.workspace_id:
        event_type = "task_done" if body.status == "success" else \
                     "task_error" if body.status == "failed" else \
                     "task_cancelled" if body.status == "cancelled" else "task_progress"
        await publish_activity(
            workspace_id=str(task.workspace_id),
            event_type=event_type,
            source=task.triggered_by or "system",
            data={"type": task.type, "name": task.name, **({"error": body.error} if body.error else {})},
            task_id=str(task.id),
        )

    return {"task_id": task_id, "status": task.status}


# ── DELETE /tasks/cleanup  (avant /{task_id} pour éviter conflit de route) ───

@router.delete("/cleanup", response_model=CleanupResponse)
async def cleanup_tasks(
    ctx: AuthContext = Depends(require_permission(Permission.TASK_RUN)),
    db: AsyncSession = Depends(get_db),
):
    stmt = delete(Task).where(
        Task.status.in_(["success", "failed", "cancelled"])
    )
    if ctx.workspace_id:
        stmt = stmt.where(Task.workspace_id == ctx.workspace_id)
    result = await db.execute(stmt)
    await db.commit()
    return CleanupResponse(deleted=result.rowcount)


# ── GET /tasks/{task_id} ──────────────────────────────────────────────────────

@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: str,
    ctx: AuthContext = Depends(require_permission(Permission.TASK_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    query = select(Task).where(Task.id == uuid.UUID(task_id))
    if ctx.workspace_id:
        query = query.where(Task.workspace_id == ctx.workspace_id)
    result = await db.execute(query)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Poll Celery si la tâche n'est pas encore terminée
    if task.status in ("pending", "running") and task.backend_ref:
        celery_result = celery_app.AsyncResult(task.backend_ref)
        state = celery_result.state

        if state in ("STARTED", "PROGRESS"):
            task.status = "running"
            if task.started_at is None:
                task.started_at = datetime.utcnow()
            if state == "PROGRESS":
                info = celery_result.info or {}
                task.progress = info.get("current", 0)
                task.progress_detail = info.get("detail")
            await db.commit()
        elif state == "SUCCESS":
            task.status = "success"
            task.progress = 100
            task.result = celery_result.result
            task.completed_at = datetime.utcnow()
            task.updated_at = datetime.utcnow()
            await db.commit()
        elif state in ("FAILURE", "REVOKED"):
            task.status = "failed" if state == "FAILURE" else "cancelled"
            task.error = str(celery_result.result)
            task.completed_at = datetime.utcnow()
            task.updated_at = datetime.utcnow()
            await db.commit()

    return TaskRead(
        task_id=str(task.id),
        name=task.name,
        type=task.type,
        status=task.status,
        progress=task.progress,
        progress_detail=task.progress_detail,
        triggered_by=task.triggered_by,
        backend_ref=task.backend_ref,
        params=task.params,
        result=task.result,
        error=task.error,
        started_at=task.started_at,
        completed_at=task.completed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


# ── POST /tasks/{task_id}/cancel ──────────────────────────────────────────────

@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.TASK_RUN)),
    db: AsyncSession = Depends(get_db),
):
    query = select(Task).where(Task.id == uuid.UUID(task_id))
    if ctx.workspace_id:
        query = query.where(Task.workspace_id == ctx.workspace_id)
    result = await db.execute(query)
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

    await audit_log(
        db, action="task.cancel",
        user_id=ctx.user.id if ctx.user else None,
        workspace_id=ctx.workspace_id,
        resource_type="task", resource_id=str(task.id),
        details={"backend_ref": task.backend_ref},
        ip=get_client_ip(request),
    )
    await db.commit()
    await db.refresh(task)

    # B6 — publier task_cancelled sur le bus d'activité
    if ctx.workspace_id:
        source = f"user:{ctx.user.id}" if ctx.user else "system"
        await publish_activity(
            workspace_id=str(ctx.workspace_id),
            event_type="task_cancelled",
            source=source,
            data={"type": task.type, "name": task.name},
            task_id=str(task.id),
        )

    return TaskRead(
        task_id=str(task.id),
        name=task.name,
        type=task.type,
        status=task.status,
        progress=task.progress,
        progress_detail=task.progress_detail,
        triggered_by=task.triggered_by,
        backend_ref=task.backend_ref,
        params=task.params,
        result=task.result,
        error=task.error,
        started_at=task.started_at,
        completed_at=task.completed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
