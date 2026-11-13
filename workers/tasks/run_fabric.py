"""Celery task — exécute un pattern Fabric via le container App Fabric.

Appelle l'endpoint /run du container Fabric (HTTP), poll /result pour la
complétion, publie des événements dans le bus d'activité Redis.

Le container Fabric écoute sur http://{container_name}:{container_port}.
URL par défaut : http://hive-app-{workspace_id}-fabric:8080
"""
import logging
import os
import shutil
import time
from pathlib import Path

import httpx

from celery_app import app
from activity_bus import publish_activity

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0      # secondes entre chaque poll
_TIMEOUT_S = 300.0        # timeout global max (5 minutes)


@app.task(bind=True, name="tasks.run_fabric")
def run_fabric(
    self,
    pattern: str,
    input_file: str,
    output_file: str,
    workspace_dir: str,
    model: str = "deepseek/deepseek-chat-v3.1",
    container_name: str = "fabric",
    container_port: int = 8080,
    workspace_id: str | None = None,
    db_task_id: str | None = None,
):
    """Exécute un pattern Fabric sur un fichier texte et écrit le résultat dans .hive/outputs/."""
    source = "system"
    task_id_short = (self.request.id or "?")[:8]
    fabric_url = f"http://{container_name}:{container_port}"
    tool_name = f"fabric/{pattern}"

    logger.info(
        f"[{task_id_short}] START {tool_name} "
        f"file={input_file} model={model} container={container_name}"
    )

    # 1. Lire le fichier source
    full_input = os.path.join(workspace_dir, input_file.lstrip("/"))
    try:
        with open(full_input, encoding="utf-8") as f:
            input_text = f.read()
        logger.info(f"[{task_id_short}] read {len(input_text)} chars from {input_file}")
    except OSError as e:
        err = f"Impossible de lire '{input_file}': {e}"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(
            workspace_id, "task_error", source,
            {"error": err, "pattern": pattern, "tool": tool_name},
            task_id=db_task_id,
        )
        raise

    if not input_text.strip():
        err = "Fichier vide — aucun contenu à traiter"
        publish_activity(
            workspace_id, "task_error", source,
            {"error": err, "pattern": pattern, "tool": tool_name},
            task_id=db_task_id,
        )
        return {"status": "error", "error": err}

    # 2. Soumettre au container Fabric
    self.update_state(
        state="PROGRESS",
        meta={"current": 10, "total": 100, "detail": "Soumission au container Fabric…"},
    )
    publish_activity(
        workspace_id, "task_progress", source,
        {"current": 10, "total": 100, "detail": "Soumission…"},
        task_id=db_task_id,
    )

    # Nom de fichier de sortie pour Fabric (relatif à /workspace)
    output_name = output_file.lstrip("/")

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{fabric_url}/run",
                json={
                    "pattern": pattern,
                    "input_text": input_text,
                    "model": model,
                    # Pas de "vendor" — le container Fabric utilise son LLM_BASE_URL (proxy core)
                    # qui résout l'agent_id → vrai modèle via /internal/v1/{workspace_id}/chat/completions
                    "output_filename": output_name,
                },
            )
            resp.raise_for_status()
            run_data = resp.json()
    except Exception as e:
        err = f"Container Fabric inaccessible ({container_name}): {e}"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(
            workspace_id, "task_error", source,
            {"error": err, "pattern": pattern, "tool": tool_name},
            task_id=db_task_id,
        )
        raise RuntimeError(err) from e

    fabric_task_id = run_data.get("task_id")
    if not fabric_task_id:
        err = "Le container Fabric n'a pas retourné de task_id"
        publish_activity(workspace_id, "task_error", source, {"error": err}, task_id=db_task_id)
        raise RuntimeError(err)

    logger.info(f"[{task_id_short}] submitted to fabric, fabric_task_id={fabric_task_id}")

    # 3. Poll /result jusqu'à la complétion
    deadline = time.monotonic() + _TIMEOUT_S
    progress = 30

    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)

        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{fabric_url}/result", params={"task_id": fabric_task_id})
                r.raise_for_status()
                result = r.json()
        except Exception as e:
            logger.warning(f"[{task_id_short}] poll error (retrying): {e}")
            continue

        status = result.get("status", "pending")

        if status == "success":
            fabric_output = result.get("output_path") or ""

            # Fabric écrit dans /workspace/{output_filename}
            # On s'assure que le fichier est bien à l'emplacement attendu
            full_output = os.path.join(workspace_dir, output_file.lstrip("/"))
            full_output_dir = os.path.dirname(full_output)

            # output_path du container est relatif à /workspace
            fabric_full = (
                os.path.join(workspace_dir, fabric_output.lstrip("/workspace/").lstrip("/"))
                if fabric_output else None
            )

            if fabric_full and os.path.exists(fabric_full) and os.path.abspath(fabric_full) != os.path.abspath(full_output):
                os.makedirs(full_output_dir, exist_ok=True)
                shutil.copy2(fabric_full, full_output)
                logger.info(f"[{task_id_short}] copied {fabric_full} → {full_output}")
            elif fabric_full and os.path.exists(fabric_full):
                # Même chemin — déjà en place
                pass
            else:
                # Fallback : output_path inconnu, le fichier est peut-être déjà au bon endroit
                logger.warning(f"[{task_id_short}] fabric output path not found: {fabric_output!r}")

            self.update_state(state="PROGRESS", meta={"current": 100, "total": 100})
            publish_activity(
                workspace_id, "task_done", source,
                {
                    "pattern": pattern,
                    "output_file": output_file,
                    "tool": tool_name,
                },
                task_id=db_task_id,
            )
            logger.info(f"[{task_id_short}] DONE ✓ pattern={pattern} out={output_file}")
            return {"pattern": pattern, "output_file": output_file, "chunks_count": 1}

        if status == "error":
            err = result.get("error") or "Fabric error (unknown)"
            logger.error(f"[{task_id_short}] fabric error: {err}")
            publish_activity(
                workspace_id, "task_error", source,
                {"error": err, "pattern": pattern, "tool": tool_name},
                task_id=db_task_id,
            )
            raise RuntimeError(err)

        # Encore en cours
        progress = min(progress + 8, 90)
        self.update_state(
            state="PROGRESS",
            meta={"current": progress, "total": 100, "detail": f"Fabric {pattern}…"},
        )
        publish_activity(
            workspace_id, "task_progress", source,
            {"current": progress, "total": 100, "detail": f"Fabric {pattern}…"},
            task_id=db_task_id,
        )

    err = f"Timeout ({_TIMEOUT_S:.0f}s) — le pattern '{pattern}' ne s'est pas terminé"
    publish_activity(workspace_id, "task_error", source, {"error": err, "pattern": pattern}, task_id=db_task_id)
    raise TimeoutError(err)
