"""Human Tools — endpoints pour le registre de tools invocables depuis le file manager.

Endpoints :
  GET  /api/w/{slug}/tools/available                — liste des tools filtrés par extension
  GET  /api/w/{slug}/tools/fabric/patterns           — catalogue Fabric (proxy container)
  GET  /api/w/{slug}/tools/fabric/pattern/{name}     — system prompt d'un pattern (preview)
  POST /api/w/{slug}/tools/execute                   — lance l'exécution d'un tool

Phase 7 — Bloc D (D6-D9)
"""

import logging
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes_dashboard import require_workspace_auth
from celery_client import celery_app
from database import get_db
from models.agent_config import AgentConfig, WorkspaceAgent
from models.task import Task
from permissions import has_permission, Permission
from services.activity_bus import publish_activity
from services.human_tools import get_available_tools, prepare_execution

logger = logging.getLogger("hiveflows.routes_tools")

router = APIRouter(prefix="/api/w", tags=["tools"])

_PINNED_PATTERNS = {"extract_wisdom", "summarize", "extract_references"}


# ── Schemas ───────────────────────────────────────────────────────────────────


class ToolExecuteRequest(BaseModel):
    tool_key: str
    file_path: str
    params: dict = {}


# ── GET /tools/available ──────────────────────────────────────────────────────


@router.get("/{slug}/tools/available")
async def get_tools_available(
    slug: str,
    request: Request,
    file_ext: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Retourne les tools compatibles avec l'extension de fichier."""
    await require_workspace_auth(slug, request, db)

    tools = get_available_tools(file_ext)
    return [
        {
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "icon": t.icon,
            "file_extensions": t.file_extensions,
            "params": [
                {
                    "key": p.key,
                    "type": p.type,
                    "label": p.label,
                    "options": p.options,
                    "options_endpoint": p.options_endpoint,
                    "default": p.default,
                }
                for p in t.params
            ],
            "executor": t.executor,
            "ui": t.ui,
            "pinned": t.pinned,
            "group": t.group,
        }
        for t in tools
    ]


# ── GET /tools/agents ─────────────────────────────────────────────────────────


@router.get("/{slug}/tools/agents")
async def get_workspace_agents(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retourne les agents actifs du workspace (id + name) pour les selectors UI."""
    _, ws, _ = await require_workspace_auth(slug, request, db)

    query = (
        select(AgentConfig.id, AgentConfig.name)
        .join(WorkspaceAgent, WorkspaceAgent.agent_id == AgentConfig.id)
        .where(
            WorkspaceAgent.workspace_id == ws.id,
            AgentConfig.is_active == True,  # noqa: E712
        )
        .order_by(AgentConfig.name)
    )
    result = await db.execute(query)
    return [{"id": str(row.id), "name": row.name} for row in result.all()]


# ── GET /tools/fabric/patterns ─────────────────────────────────────────────────


@router.get("/{slug}/tools/fabric/patterns")
async def get_fabric_patterns(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retourne le catalogue des patterns Fabric depuis le container app.

    Proxy vers http://hive-app-{workspace_id}-fabric:8080/patterns.
    En cas d'indisponibilité, retourne la liste de fallback des patterns épinglés.
    """
    _, ws, _ = await require_workspace_auth(slug, request, db)

    container_name = f"hive-app-{ws.id}-fabric"
    fabric_url = f"http://{container_name}:8080/patterns"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(fabric_url)
            resp.raise_for_status()
            data = resp.json()
            patterns = data.get("patterns", [])
            return [
                {
                    "key": p["name"],
                    "description": p["name"].replace("_", " ").title(),
                    "pinned": p["name"] in _PINNED_PATTERNS,
                }
                for p in patterns
                if p.get("name")
            ]
    except Exception as e:
        logger.warning(f"[routes_tools] fabric patterns unavailable ({container_name}): {e}")
        # Fallback minimal pour ne pas bloquer l'UI
        return [
            {"key": "extract_wisdom", "description": "Extract Wisdom", "pinned": True},
            {"key": "summarize", "description": "Summarize", "pinned": True},
            {"key": "extract_references", "description": "Extract References", "pinned": True},
            {"key": "analyze_claims", "description": "Analyze Claims", "pinned": False},
            {"key": "create_summary", "description": "Create Summary", "pinned": False},
            {"key": "improve_writing", "description": "Improve Writing", "pinned": False},
            {"key": "explain_code", "description": "Explain Code", "pinned": False},
            {"key": "find_logical_fallacies", "description": "Find Logical Fallacies", "pinned": False},
            {"key": "extract_insights", "description": "Extract Insights", "pinned": False},
            {"key": "extract_questions", "description": "Extract Questions", "pinned": False},
        ]


# ── GET /tools/fabric/pattern/{name} — preview du system prompt ───────────────


@router.get("/{slug}/tools/fabric/pattern/{name}")
async def get_fabric_pattern_detail(
    slug: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retourne le system prompt d'un pattern Fabric pour la prévisualisation."""
    _, ws, _ = await require_workspace_auth(slug, request, db)

    container_name = f"hive-app-{ws.id}-fabric"

    # Essaie d'accéder au system prompt via le container Fabric
    for endpoint in [
        f"http://{container_name}:8080/pattern/{name}/system",
        f"http://{container_name}:8080/patterns/{name}",
    ]:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(endpoint)
                if resp.status_code == 200:
                    content = resp.text
                    if content:
                        return {"name": name, "system_prompt": content[:2000]}
        except Exception:
            continue

    return {"name": name, "system_prompt": None}


# ── GET /tools/piper/voices ───────────────────────────────────────────────────


@router.get("/{slug}/tools/piper/voices")
async def get_piper_voices(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retourne les voix Piper disponibles dans le workspace (proxy vers le container).

    Retourne une liste de {key, description} pour les selectors UI.
    Fallback sur les voix par défaut si le container est indisponible.
    """
    _, ws, _ = await require_workspace_auth(slug, request, db)

    container_name = f"hive-app-{ws.id}-piper"
    piper_url = f"http://{container_name}:8080/voices"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(piper_url)
            resp.raise_for_status()
            data = resp.json()
            voices = data.get("voices", [])
            return [
                {"key": v["name"], "description": f"{v['name']} ({v.get('lang', '?')})"}
                for v in voices
                if v.get("name")
            ]
    except Exception as e:
        logger.warning(f"[routes_tools] piper voices unavailable ({container_name}): {e}")
        return [
            {"key": "fr_FR-siwis-medium",  "description": "fr_FR-siwis-medium (fr)"},
            {"key": "fr_FR-mls-medium",    "description": "fr_FR-mls-medium (fr)"},
            {"key": "en_US-lessac-medium", "description": "en_US-lessac-medium (en)"},
            {"key": "en_US-ryan-high",     "description": "en_US-ryan-high (en)"},
            {"key": "en_GB-alba-medium",   "description": "en_GB-alba-medium (en)"},
        ]


# ── POST /tools/execute ────────────────────────────────────────────────────────


@router.post("/{slug}/tools/execute")
async def execute_tool(
    slug: str,
    body: ToolExecuteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Lance l'exécution d'un Human Tool — crée une Task triggered_by 'user:{id}'.

    D8 — dispatche vers le backend Celery.
    D9 — publie task_created sur le bus d'activité.
    """
    user, ws, role = await require_workspace_auth(slug, request, db)

    if not has_permission(role, Permission.CHAT):
        raise HTTPException(403, "Les viewers ne peuvent pas exécuter des tools.")

    task_uuid = uuid.uuid4()
    task_uuid_str = str(task_uuid)
    triggered_by = f"user:{user.id}"

    try:
        celery_task_name, celery_kwargs, task_label = prepare_execution(
            tool_key=body.tool_key,
            workspace_id=str(ws.id),
            workspace_slug=ws.slug,
            file_path=body.file_path,
            params=body.params,
            task_uuid_str=task_uuid_str,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Dispatch vers Celery
    celery_result = celery_app.send_task(celery_task_name, kwargs=celery_kwargs)

    # Créer le Task record en DB
    db_task = Task(
        id=task_uuid,
        name=task_label,
        type=body.tool_key,
        status="pending",
        triggered_by=triggered_by,
        backend_ref=celery_result.id,
        params={**body.params, "file_path": body.file_path},
        workspace_id=ws.id,
        user_id=user.id,
    )
    db.add(db_task)
    await db.commit()

    # D9 — publier task_created sur le bus d'activité
    await publish_activity(
        workspace_id=str(ws.id),
        event_type="task_created",
        source=triggered_by,
        data={
            "type": body.tool_key,
            "name": task_label,
            "file_path": body.file_path,
        },
        task_id=task_uuid_str,
    )

    return {
        "task_id": task_uuid_str,
        "status": "pending",
        "name": task_label,
        "backend_ref": celery_result.id,
    }
