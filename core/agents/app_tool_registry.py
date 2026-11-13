"""AppToolRegistry — génère dynamiquement des LangChain tools depuis les apps installées.

Au build d'un agent, interroge app_instances + app_catalog pour trouver
les apps "backend" running dans le workspace qui déclarent des tools dans
leur manifest. Crée un StructuredTool LangChain par tool déclaré, avec
le schema Pydantic construit dynamiquement depuis le manifest.

URL d'appel : http://{container_name}:{port}{endpoint}
Docker DNS résout container_name sur le réseau hive-backend (partagé entre
core et les apps de type "backend").
"""
import json
import logging
from typing import Any, Optional

import httpx
import sqlalchemy as sa
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

logger = logging.getLogger("hiveflows.app_tool_registry")

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": float,
}


def _build_input_schema(tool_def: dict) -> type:
    """Construit un Pydantic BaseModel dynamiquement depuis tool_def['schema']."""
    fields: dict[str, Any] = {}
    for fname, finfo in (tool_def.get("schema") or {}).items():
        py_type = _TYPE_MAP.get(finfo.get("type", "string"), str)
        desc = finfo.get("description", "")
        required = finfo.get("required", False)
        default = finfo.get("default", None)
        if required:
            fields[fname] = (py_type, Field(..., description=desc))
        else:
            fields[fname] = (Optional[py_type], Field(default=default, description=desc))

    model_name = "".join(w.title() for w in tool_def["name"].split("_")) + "Input"
    return create_model(model_name, **fields)


def _make_tool(tool_def: dict, container_name: str, port: int, workspace_id: str) -> StructuredTool:
    """Crée un LangChain StructuredTool qui POSTe vers l'endpoint du container."""
    endpoint = tool_def.get("endpoint", f"/{tool_def['name']}")
    method = tool_def.get("method", "POST").upper()
    name = tool_def["name"]
    description = tool_def["description"]
    url = f"http://{container_name}:{port}{endpoint}"
    InputSchema = _build_input_schema(tool_def)

    def _register_task(tool_name: str) -> str | None:
        """Crée un Task record dans la DB via POST /tasks/run (type=app_tool). Retourne le task_id."""
        try:
            api_key = settings.api_keys_list[0] if settings.api_keys_list else ""
            resp = httpx.post(
                f"http://localhost:{settings.core_port}/tasks/run",
                json={"type": "app_tool", "name": tool_name, "workspace_id": workspace_id},
                headers={"Content-Type": "application/json", "X-HF-API-Key": api_key},
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json().get("task_id")
        except Exception as e:
            logger.warning(f"app_tool_registry: impossible de créer le Task record: {e}")
        return None

    def _update_task(db_task_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
        """Met à jour le statut d'un Task record via PATCH /tasks/{id}."""
        try:
            api_key = settings.api_keys_list[0] if settings.api_keys_list else ""
            body: dict = {"status": status}
            if result:
                body["result"] = result
            if error:
                body["error"] = error
            httpx.patch(
                f"http://localhost:{settings.core_port}/tasks/{db_task_id}",
                json=body,
                headers={"Content-Type": "application/json", "X-HF-API-Key": api_key},
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"app_tool_registry: impossible de mettre à jour le Task {db_task_id}: {e}")

    def _call(**kwargs) -> str:
        # Register task in DB — appears in Activity dashboard immediately
        db_task_id = _register_task(name)

        try:
            if method == "GET":
                resp = httpx.get(url, params=kwargs, timeout=300.0)
            else:
                resp = httpx.post(url, json=kwargs, timeout=300.0)
            resp.raise_for_status()

            result_data: dict = {}
            try:
                body = resp.json()
                if isinstance(body, dict):
                    result_data = body
            except Exception:
                result_data = {"output": resp.text[:500]}

            if db_task_id:
                _update_task(db_task_id, "success", result=result_data or {"output": resp.text[:200]})

            return resp.text
        except httpx.HTTPStatusError as e:
            err_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            if db_task_id:
                _update_task(db_task_id, "failed", error=err_msg)
            return f"Error {e.response.status_code} from app tool '{name}': {e.response.text[:500]}"
        except httpx.RequestError as e:
            if db_task_id:
                _update_task(db_task_id, "failed", error=str(e))
            return f"Connection error calling app tool '{name}' at {url}: {e}"

    return StructuredTool.from_function(
        func=_call,
        name=name,
        description=description,
        args_schema=InputSchema,
    )


async def get_app_tools(workspace_id: str, db: AsyncSession) -> list:
    """Retourne les LangChain tools de toutes les apps backend running dans ce workspace.

    Appelé depuis factory.py au build de chaque agent. Les tools sont créés
    dynamiquement — aucun code core à modifier pour ajouter une nouvelle app.

    Args:
        workspace_id: UUID du workspace (str).
        db: Session SQLAlchemy async.

    Returns:
        Liste de StructuredTool LangChain, prêts à être appendés aux tools de l'agent.
    """
    try:
        result = await db.execute(
            sa.text("""
                SELECT ai.container_name, ac.default_port, ac.tools_schema
                FROM app_instances ai
                JOIN app_catalog ac ON ai.app_id = ac.id
                WHERE ai.workspace_id = :wid
                  AND ai.status = 'running'
                  AND ac.tools_schema IS NOT NULL
                  AND jsonb_array_length(ac.tools_schema) > 0
            """),
            {"wid": str(workspace_id)},
        )
        rows = result.fetchall()
    except Exception as e:
        logger.warning(f"AppToolRegistry: erreur DB pour workspace {workspace_id}: {e}")
        return []

    tools = []
    for container_name, port, tools_schema in rows:
        if not tools_schema or not port:
            continue
        for tool_def in tools_schema:
            try:
                tool = _make_tool(tool_def, container_name, port, workspace_id)
                tools.append(tool)
                logger.info(
                    f"App tool chargé: '{tool_def['name']}' "
                    f"(container={container_name}, port={port})"
                )
            except Exception as e:
                logger.warning(
                    f"AppToolRegistry: impossible de créer le tool "
                    f"'{tool_def.get('name', '?')}': {e}"
                )

    return tools
