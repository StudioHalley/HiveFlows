"""Piper TTS App — serveur FastAPI exposant des endpoints /speak pour les agents HiveFlows.

Endpoints :
  GET  /health                → {"status": "ok"}
  GET  /voices                → liste des voix disponibles (scan de /voices)
  POST /speak                 → TTS depuis texte inline, retourne task_id
  POST /speak/file            → TTS depuis un fichier texte, retourne task_id
  GET  /result?task_id=...    → état + chemin du fichier audio généré

Comportement :
  - Appel au binaire `piper` installé dans le conteneur
  - Les voix ONNX sont chargées depuis /voices (monté en volume)
  - L'audio généré est écrit dans /workspace/tts/ ou à côté du fichier source
"""
import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("piper-app")

app = FastAPI(title="piper", version="1.0")

VOICES_DIR = Path("/voices")
WORKSPACE_DIR = Path("/workspace")
TTS_OUT_DIR = WORKSPACE_DIR / "tts"

_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _scan_voices() -> list[dict]:
    """Scanne /voices et retourne la liste des voix disponibles."""
    voices = []
    if not VOICES_DIR.exists():
        return voices
    for onnx_file in sorted(VOICES_DIR.rglob("*.onnx")):
        json_file = onnx_file.with_suffix(".onnx.json")
        if not json_file.exists():
            continue
        # Nom de la voix = stem du fichier (ex: fr_FR-siwis-medium)
        name = onnx_file.stem
        # Langue depuis le préfixe (ex: fr_FR, en_US)
        lang = name.split("-")[0] if "-" in name else "unknown"
        voices.append({
            "name": name,
            "lang": lang,
            "path": str(onnx_file),
        })
    return voices


def _resolve_voice(voice_name: str) -> Optional[Path]:
    """Retourne le chemin du fichier .onnx pour une voix donnée, ou None."""
    for v in _scan_voices():
        if v["name"] == voice_name:
            return Path(v["path"])
    return None


def _piper_synthesize(text: str, voice_path: Path, output_path: Path, speed: float = 1.0) -> None:
    """Appelle le binaire piper pour générer un fichier WAV."""
    cmd = [
        "piper",
        "--model", str(voice_path),
        "--output_file", str(output_path),
        "--length_scale", str(1.0 / speed) if speed > 0 else "1.0",
    ]
    proc = subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))


def _convert_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Convertit un WAV en MP3 via ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-acodec", "libmp3lame", "-q:a", "4", str(mp3_path)],
        check=True,
        capture_output=True,
    )
    wav_path.unlink(missing_ok=True)


# ── Workers ───────────────────────────────────────────────────────────────────

def _run_speak(task_id: str, text: str, voice_name: str, fmt: str, speed: float) -> None:
    def _set(update: dict) -> None:
        with _tasks_lock:
            _tasks[task_id].update(update)

    _set({"status": "running"})
    TTS_OUT_DIR.mkdir(parents=True, exist_ok=True)

    voice_path = _resolve_voice(voice_name)
    if voice_path is None:
        _set({"status": "error", "error": f"Voice not found: {voice_name}"})
        return

    wav_path = TTS_OUT_DIR / f"{task_id}.wav"
    try:
        logger.info(f"[{task_id}] Synthesizing with voice={voice_name}, speed={speed}")
        _piper_synthesize(text, voice_path, wav_path, speed)
    except Exception as e:
        logger.error(f"[{task_id}] Piper failed: {e}")
        _set({"status": "error", "error": str(e)})
        return

    if fmt == "mp3":
        mp3_path = TTS_OUT_DIR / f"{task_id}.mp3"
        try:
            _convert_to_mp3(wav_path, mp3_path)
            audio_path = str(mp3_path)
        except Exception as e:
            logger.error(f"[{task_id}] ffmpeg conversion failed: {e}")
            audio_path = str(wav_path)  # fallback WAV
    else:
        audio_path = str(wav_path)

    logger.info(f"[{task_id}] Done: {audio_path}")
    _set({
        "status": "success",
        "audio_path": audio_path,
        "voice": voice_name,
        "text_length": len(text),
    })


def _run_speak_file(task_id: str, text_path: str, voice_name: str, fmt: str, speed: float) -> None:
    def _set(update: dict) -> None:
        with _tasks_lock:
            _tasks[task_id].update(update)

    _set({"status": "running"})

    src = Path(text_path)
    if not src.exists():
        _set({"status": "error", "error": f"File not found: {text_path}"})
        return

    try:
        text = src.read_text(encoding="utf-8")
    except Exception as e:
        _set({"status": "error", "error": f"Cannot read file: {e}"})
        return

    voice_path = _resolve_voice(voice_name)
    if voice_path is None:
        _set({"status": "error", "error": f"Voice not found: {voice_name}"})
        return

    # Audio généré dans le même dossier que le fichier source
    stem = src.stem
    out_dir = src.parent
    wav_path = out_dir / f"{stem}.wav"

    try:
        logger.info(f"[{task_id}] Synthesizing file={text_path}, voice={voice_name}")
        _piper_synthesize(text, voice_path, wav_path, speed)
    except Exception as e:
        logger.error(f"[{task_id}] Piper failed: {e}")
        _set({"status": "error", "error": str(e)})
        return

    if fmt == "mp3":
        mp3_path = out_dir / f"{stem}.mp3"
        try:
            _convert_to_mp3(wav_path, mp3_path)
            audio_path = str(mp3_path)
        except Exception as e:
            logger.error(f"[{task_id}] ffmpeg conversion failed: {e}")
            audio_path = str(wav_path)
    else:
        audio_path = str(wav_path)

    logger.info(f"[{task_id}] Done: {audio_path}")
    _set({
        "status": "success",
        "audio_path": audio_path,
        "voice": voice_name,
        "text_length": len(text),
        "source_path": text_path,
    })


# ── Schémas request ───────────────────────────────────────────────────────────

class SpeakRequest(BaseModel):
    text: str
    voice: str = "fr_FR-siwis-medium"
    format: str = "wav"
    speed: float = 1.0


class SpeakFileRequest(BaseModel):
    text_path: str
    voice: str = "fr_FR-siwis-medium"
    format: str = "wav"
    speed: float = 1.0


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/voices")
def list_voices():
    """Retourne la liste des voix disponibles dans /voices."""
    return {"voices": _scan_voices()}


@app.post("/speak")
def speak(req: SpeakRequest):
    """Démarre la synthèse TTS depuis texte inline. Retourne immédiatement un task_id."""
    task_id = str(uuid.uuid4())
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending", "task_id": task_id}

    thread = threading.Thread(
        target=_run_speak,
        args=(task_id, req.text, req.voice, req.format, req.speed),
        daemon=True,
    )
    thread.start()

    logger.info(f"[{task_id}] Speak started (voice={req.voice}, chars={len(req.text)})")
    return {"task_id": task_id, "status": "pending"}


@app.post("/speak/file")
def speak_file(req: SpeakFileRequest):
    """Démarre la synthèse TTS depuis un fichier texte. Retourne immédiatement un task_id."""
    task_id = str(uuid.uuid4())
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending", "task_id": task_id}

    thread = threading.Thread(
        target=_run_speak_file,
        args=(task_id, req.text_path, req.voice, req.format, req.speed),
        daemon=True,
    )
    thread.start()

    logger.info(f"[{task_id}] Speak-file started (path={req.text_path}, voice={req.voice})")
    return {"task_id": task_id, "status": "pending"}


@app.get("/result")
def get_result(task_id: str = Query(..., description="task_id returned by /speak or /speak/file")):
    """Retourne l'état et le résultat d'une synthèse. Poller jusqu'à status != 'pending'/'running'."""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown task_id: {task_id}"})
    return task
