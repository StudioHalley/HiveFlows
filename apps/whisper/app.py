"""Whisper STT App — serveur FastAPI exposant un endpoint /transcribe pour les agents HiveFlows.

Endpoints :
  GET  /health                → {"status": "ok"}
  GET  /models                → liste des modèles téléchargés dans /models
  POST /transcribe            → STT en arrière-plan, retourne task_id
  GET  /result?task_id=...    → état + transcript quand prêt

Comportement :
  - Utilise faster-whisper (CTranslate2) — 4× plus rapide que Whisper original sur CPU
  - Les modèles sont mis en cache dans /models (monté en volume persistant)
  - Détection automatique de la langue si language="auto"
  - Support formats : json (défaut), txt, vtt, srt
"""
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whisper-app")

app = FastAPI(title="whisper", version="1.0")

MODELS_DIR = Path("/models")
WORKSPACE_DIR = Path("/workspace")
TRANSCRIPTS_DIR = WORKSPACE_DIR / "transcripts"

_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()

# Cache des instances de modèle chargées (évite de recharger à chaque transcription)
_model_cache: dict[str, object] = {}
_model_cache_lock = threading.Lock()

KNOWN_MODELS = ["tiny", "base", "small", "medium", "large-v3"]


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _list_cached_models() -> list[dict]:
    """Retourne la liste des modèles téléchargés dans /models."""
    models = []
    if not MODELS_DIR.exists():
        return models
    for entry in sorted(MODELS_DIR.iterdir()):
        if entry.is_dir() and entry.name.replace("-", "").replace(".", "") != "":
            models.append({
                "name": entry.name,
                "path": str(entry),
                "size_mb": round(sum(f.stat().st_size for f in entry.rglob("*") if f.is_file()) / 1024 / 1024, 1),
            })
    return models


def _get_model(model_name: str):
    """Charge ou retourne depuis le cache une instance WhisperModel."""
    from faster_whisper import WhisperModel

    with _model_cache_lock:
        if model_name not in _model_cache:
            logger.info(f"Loading model: {model_name}")
            _model_cache[model_name] = WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
                download_root=str(MODELS_DIR),
            )
            logger.info(f"Model {model_name} loaded.")
        return _model_cache[model_name]


def _format_transcript(segments, fmt: str, title: str = "") -> str:
    """Formate les segments en txt, vtt ou srt."""
    if fmt == "txt":
        return " ".join(s.text.strip() for s in segments)

    if fmt == "vtt":
        lines = ["WEBVTT", ""]
        for i, s in enumerate(segments):
            lines.append(f"{i + 1}")
            lines.append(f"{_sec_to_vtt(s.start)} --> {_sec_to_vtt(s.end)}")
            lines.append(s.text.strip())
            lines.append("")
        return "\n".join(lines)

    if fmt == "srt":
        lines = []
        for i, s in enumerate(segments):
            lines.append(f"{i + 1}")
            lines.append(f"{_sec_to_srt(s.start)} --> {_sec_to_srt(s.end)}")
            lines.append(s.text.strip())
            lines.append("")
        return "\n".join(lines)

    return ""


def _sec_to_vtt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _sec_to_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


# ── Worker de transcription ───────────────────────────────────────────────────

def _run_transcribe(task_id: str, audio_path: str, model_name: str, language: str, fmt: str) -> None:
    def _set(update: dict) -> None:
        with _tasks_lock:
            _tasks[task_id].update(update)

    _set({"status": "running"})
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    src = Path(audio_path)
    if not src.exists():
        _set({"status": "error", "error": f"Audio file not found: {audio_path}"})
        return

    try:
        model = _get_model(model_name)
    except Exception as e:
        logger.error(f"[{task_id}] Model load failed: {e}")
        _set({"status": "error", "error": f"Cannot load model {model_name}: {e}"})
        return

    lang_param = None if language == "auto" else language
    logger.info(f"[{task_id}] Transcribing {audio_path} (model={model_name}, lang={language})")

    try:
        segments_gen, info = model.transcribe(
            str(src),
            language=lang_param,
            beam_size=5,
        )
        segments = list(segments_gen)
    except Exception as e:
        logger.error(f"[{task_id}] Transcription failed: {e}")
        _set({"status": "error", "error": str(e)})
        return

    full_text = " ".join(s.text.strip() for s in segments)

    seg_list = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segments
    ]

    # Écrire le transcript dans /workspace/transcripts/
    if fmt == "json":
        transcript_path = TRANSCRIPTS_DIR / f"{task_id}.json"
        transcript_path.write_text(
            json.dumps({
                "task_id": task_id,
                "language": info.language,
                "language_probability": round(info.language_probability, 4),
                "duration_s": round(info.duration, 2),
                "transcript": full_text,
                "segments": seg_list,
                "source": audio_path,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        ext = fmt  # txt, vtt, srt
        transcript_path = TRANSCRIPTS_DIR / f"{task_id}.{ext}"
        transcript_path.write_text(
            _format_transcript(segments, fmt),
            encoding="utf-8",
        )

    logger.info(f"[{task_id}] Done. lang={info.language}, duration={info.duration:.1f}s")

    _set({
        "status": "success",
        "language": info.language,
        "language_confidence": round(info.language_probability, 4),
        "duration_s": round(info.duration, 2),
        "transcript": full_text,
        "segments": seg_list,
        "transcript_path": str(transcript_path),
        "source": audio_path,
    })


# ── Schéma request ────────────────────────────────────────────────────────────

class TranscribeRequest(BaseModel):
    audio_path: str
    model: str = "small"
    language: str = "auto"
    format: str = "json"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/models")
def list_models():
    """Retourne la liste des modèles Whisper téléchargés localement."""
    return {
        "available": KNOWN_MODELS,
        "cached": _list_cached_models(),
    }


@app.post("/transcribe")
def transcribe(req: TranscribeRequest):
    """Démarre la transcription en arrière-plan. Retourne immédiatement un task_id."""
    if req.model not in KNOWN_MODELS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown model: {req.model}. Available: {KNOWN_MODELS}"},
        )
    if req.format not in ("json", "txt", "vtt", "srt"):
        return JSONResponse(
            status_code=400,
            content={"error": "format must be one of: json, txt, vtt, srt"},
        )

    task_id = str(uuid.uuid4())
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending", "task_id": task_id}

    thread = threading.Thread(
        target=_run_transcribe,
        args=(task_id, req.audio_path, req.model, req.language, req.format),
        daemon=True,
    )
    thread.start()

    logger.info(f"[{task_id}] Transcribe started (model={req.model}, path={req.audio_path})")
    return {"task_id": task_id, "status": "pending"}


@app.get("/result")
def get_result(task_id: str = Query(..., description="task_id returned by /transcribe")):
    """Retourne l'état et le résultat d'une transcription. Poller jusqu'à status != 'pending'/'running'."""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown task_id: {task_id}"})
    return task
