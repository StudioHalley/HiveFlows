"""Celery task — transcription STT via le container Whisper.

Appelle POST /transcribe sur le container faster-whisper, poll /result
jusqu'à la complétion, publie des événements dans le bus d'activité Redis.

Le transcript est écrit dans /workspace/transcripts/{task_id}.{format}
par le container Whisper.
"""
import logging
import os
import time
from pathlib import Path

import httpx

from celery_app import app
from activity_bus import publish_activity

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 3.0    # secondes entre chaque poll (transcription plus longue que TTS)
_TIMEOUT_S = 1200.0      # timeout global max (20 minutes pour les longs fichiers)


@app.task(bind=True, name="tasks.run_whisper")
def run_whisper(
    self,
    audio_path: str,
    workspace_dir: str,
    model: str = "small",
    language: str = "auto",
    format: str = "txt",
    container_name: str = "whisper",
    container_port: int = 8080,
    workspace_id: str | None = None,
    db_task_id: str | None = None,
):
    """Transcrit un fichier audio en texte via faster-whisper.

    audio_path    : chemin relatif au workspace (ex: downloads/reunion.mp3)
    workspace_dir : chemin absolu du workspace sur l'hôte
    """
    source = "system"
    task_id_short = (self.request.id or "?")[:8]
    whisper_url = f"http://{container_name}:{container_port}"

    rel = audio_path.lstrip("/")
    full_input = os.path.join(workspace_dir, rel)
    container_audio_path = f"/workspace/{rel}"

    logger.info(
        f"[{task_id_short}] START whisper/transcribe "
        f"file={audio_path} model={model} lang={language} container={container_name}"
    )

    if not os.path.exists(full_input):
        err = f"Fichier audio introuvable : '{audio_path}'"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "whisper"}, task_id=db_task_id)
        raise FileNotFoundError(err)

    # 1. Soumettre au container Whisper
    self.update_state(state="PROGRESS", meta={"current": 5, "total": 100, "detail": f"Chargement modèle {model}…"})
    publish_activity(
        workspace_id, "task_progress", source,
        {"current": 5, "total": 100, "detail": f"Chargement modèle Whisper {model}…"},
        task_id=db_task_id,
    )

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{whisper_url}/transcribe",
                json={
                    "audio_path": container_audio_path,
                    "model": model,
                    "language": language,
                    "format": format,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        err = f"Container Whisper inaccessible ({container_name}): {e}"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "whisper"}, task_id=db_task_id)
        raise RuntimeError(err) from e

    whisper_task_id = data.get("task_id")
    if not whisper_task_id:
        err = "Whisper n'a pas retourné de task_id"
        publish_activity(workspace_id, "task_error", source, {"error": err}, task_id=db_task_id)
        raise RuntimeError(err)

    logger.info(f"[{task_id_short}] submitted to whisper, whisper_task_id={whisper_task_id}")

    # 2. Poll /result jusqu'à la complétion
    deadline = time.monotonic() + _TIMEOUT_S
    progress = 15

    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)

        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{whisper_url}/result", params={"task_id": whisper_task_id})
                r.raise_for_status()
                result = r.json()
        except Exception as e:
            logger.warning(f"[{task_id_short}] poll error (retrying): {e}")
            continue

        status = result.get("status", "pending")

        if status == "success":
            transcript_path = result.get("transcript_path", "")
            transcript_rel = transcript_path.lstrip("/workspace/").lstrip("/")
            detected_lang = result.get("language", "?")
            duration = result.get("duration_s", 0)
            transcript_preview = (result.get("transcript") or "")[:200]

            self.update_state(state="PROGRESS", meta={"current": 100, "total": 100})
            publish_activity(
                workspace_id, "task_done", source,
                {
                    "tool": "whisper",
                    "transcript_file": transcript_rel,
                    "audio_file": audio_path,
                    "language": detected_lang,
                    "duration_s": duration,
                    "transcript_preview": transcript_preview,
                },
                task_id=db_task_id,
            )
            logger.info(
                f"[{task_id_short}] DONE ✓ lang={detected_lang} "
                f"duration={duration:.1f}s transcript={transcript_rel}"
            )
            return {
                "transcript_file": transcript_rel,
                "audio_file": audio_path,
                "language": detected_lang,
                "duration_s": duration,
            }

        if status == "error":
            err = result.get("error") or "Whisper error (unknown)"
            logger.error(f"[{task_id_short}] whisper error: {err}")
            publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "whisper"}, task_id=db_task_id)
            raise RuntimeError(err)

        # En cours — progression approximative basée sur le temps écoulé
        elapsed = time.monotonic() - (deadline - _TIMEOUT_S)
        progress = min(int(15 + (elapsed / _TIMEOUT_S) * 75), 90)
        detail = f"Transcription en cours… ({detected_lang if (detected_lang := result.get('language')) else 'langue inconnue'})"
        self.update_state(state="PROGRESS", meta={"current": progress, "total": 100, "detail": detail})
        publish_activity(
            workspace_id, "task_progress", source,
            {"current": progress, "total": 100, "detail": detail},
            task_id=db_task_id,
        )

    err = f"Timeout ({_TIMEOUT_S:.0f}s) — la transcription Whisper ne s'est pas terminée"
    publish_activity(workspace_id, "task_error", source, {"error": err, "tool": "whisper"}, task_id=db_task_id)
    raise TimeoutError(err)
