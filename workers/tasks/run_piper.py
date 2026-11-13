"""Celery task — synthèse TTS via le container Piper.

Appelle POST /speak/file sur le container Piper, poll /result jusqu'à
la complétion, publie des événements dans le bus d'activité Redis.

L'audio généré est placé dans le même répertoire que le fichier source
(comportement natif de l'endpoint /speak/file de Piper).
"""
import logging
import os
import time
from pathlib import Path

import httpx

from celery_app import app
from activity_bus import publish_activity

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0   # secondes entre chaque poll
_TIMEOUT_S = 1200.0     # timeout global max (20 minutes — CPU TTS peut être long)


@app.task(bind=True, name="tasks.run_piper")
def run_piper(
    self,
    text_path: str,
    workspace_dir: str,
    voice: str = "fr_FR-siwis-medium",
    format: str = "mp3",
    speed: float = 1.0,
    container_name: str = "piper",
    container_port: int = 8080,
    workspace_id: str | None = None,
    db_task_id: str | None = None,
):
    """Synthétise le contenu d'un fichier texte en audio via Piper TTS.

    text_path  : chemin relatif au workspace (ex: notes/article.md)
    workspace_dir : chemin absolu du workspace sur l'hôte
    """
    source = "system"
    task_id_short = (self.request.id or "?")[:8]
    piper_url = f"http://{container_name}:{container_port}"

    # Chemin absolu sur l'hôte → chemin dans le container (/workspace/...)
    rel = text_path.lstrip("/")
    full_input = os.path.join(workspace_dir, rel)
    container_text_path = f"/workspace/{rel}"

    logger.info(
        f"[{task_id_short}] START piper/speak_file "
        f"file={text_path} voice={voice} format={format} container={container_name}"
    )

    if not os.path.exists(full_input):
        err = f"Fichier introuvable : '{text_path}'"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "piper"}, task_id=db_task_id)
        raise FileNotFoundError(err)

    # 1. Soumettre au container Piper
    self.update_state(state="PROGRESS", meta={"current": 10, "total": 100, "detail": "Synthèse vocale…"})
    publish_activity(
        workspace_id, "task_progress", source,
        {"current": 10, "total": 100, "detail": "Synthèse vocale en cours…"},
        task_id=db_task_id,
    )

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{piper_url}/speak/file",
                json={
                    "text_path": container_text_path,
                    "voice": voice,
                    "format": format,
                    "speed": speed,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        err = f"Container Piper inaccessible ({container_name}): {e}"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "piper"}, task_id=db_task_id)
        raise RuntimeError(err) from e

    piper_task_id = data.get("task_id")
    if not piper_task_id:
        err = "Piper n'a pas retourné de task_id"
        publish_activity(workspace_id, "task_error", source, {"error": err}, task_id=db_task_id)
        raise RuntimeError(err)

    logger.info(f"[{task_id_short}] submitted to piper, piper_task_id={piper_task_id}")

    # 2. Poll /result jusqu'à la complétion
    deadline = time.monotonic() + _TIMEOUT_S
    progress = 30

    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)

        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{piper_url}/result", params={"task_id": piper_task_id})
                r.raise_for_status()
                result = r.json()
        except Exception as e:
            logger.warning(f"[{task_id_short}] poll error (retrying): {e}")
            continue

        status = result.get("status", "pending")

        if status == "success":
            # audio_path dans le container (/workspace/...) → chemin relatif workspace
            audio_container = result.get("audio_path", "")
            audio_rel = audio_container.lstrip("/workspace/").lstrip("/")
            audio_host = os.path.join(workspace_dir, audio_rel)

            self.update_state(state="PROGRESS", meta={"current": 100, "total": 100})
            publish_activity(
                workspace_id, "task_done", source,
                {
                    "tool": "piper",
                    "audio_file": audio_rel,
                    "voice": voice,
                    "source_file": text_path,
                    "text_length": result.get("text_length"),
                },
                task_id=db_task_id,
            )
            logger.info(f"[{task_id_short}] DONE ✓ audio={audio_host}")
            return {"audio_file": audio_rel, "voice": voice, "source_file": text_path}

        if status == "error":
            err = result.get("error") or "Piper error (unknown)"
            logger.error(f"[{task_id_short}] piper error: {err}")
            publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "piper"}, task_id=db_task_id)
            raise RuntimeError(err)

        progress = min(progress + 15, 90)
        self.update_state(state="PROGRESS", meta={"current": progress, "total": 100, "detail": "Synthèse…"})

    err = f"Timeout ({_TIMEOUT_S:.0f}s) — la synthèse Piper ne s'est pas terminée"
    publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "piper"}, task_id=db_task_id)
    raise TimeoutError(err)
