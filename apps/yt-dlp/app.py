"""yt-dlp App — serveur FastAPI exposant un endpoint /download pour les agents HiveFlows.

Endpoints :
  GET  /health             → {"status": "ok"}
  POST /download           → démarre le téléchargement en arrière-plan, retourne task_id
  GET  /result?task_id=... → état et résultat du téléchargement (à poller)

Comportement :
  - Télécharge toujours la vidéo (mp4)
  - Extrait toujours l'audio en MP3 via ffmpeg (depuis la vidéo)
  - Tente toujours le transcript (auto-captions) → garde le .vtt + écrit un .md
  - Les événements de progression sont publiés sur le bus d'activité par le Celery worker
"""
import logging
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yt-dlp-app")

app = FastAPI(title="yt-dlp", version="1.0")

# Stockage en mémoire des résultats de tâches
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:80]


def _vtt_to_markdown(vtt_content: str) -> str:
    """Convertit le contenu WebVTT en texte Markdown propre."""
    lines = []
    prev = ""
    for line in vtt_content.splitlines():
        line = line.strip()
        if (
            not line
            or line.startswith("WEBVTT")
            or "-->" in line
            or line.startswith("NOTE")
            or line.startswith("STYLE")
            or line.isdigit()
        ):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = line.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        line = line.strip()
        if line and line != prev:
            lines.append(line)
            prev = line
    return "\n\n".join(lines)


# ── Worker de téléchargement ──────────────────────────────────────────────────

def _run_download(task_id: str, url: str, quality: str) -> None:
    """Exécute le téléchargement dans un thread séparé.

    Toujours : télécharge la vidéo (mp4) + extrait l'audio (mp3 via ffmpeg)
               + tente le transcript (vtt + markdown).
    """
    out_dir = Path("/workspace/downloads")

    def _set(update: dict) -> None:
        with _tasks_lock:
            _tasks[task_id].update(update)

    _set({"status": "running"})
    logger.info(f"[{task_id}] Fetching info: {url}")

    # ── Étape 1 : récupérer les métadonnées ──────────────────────────────────
    ydl_quiet = {"quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_quiet) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"[{task_id}] extract_info failed: {e}")
        _set({"status": "error", "error": str(e)})
        return

    title = info.get("title", "video")
    slug = _slugify(title)
    task_dir = out_dir / slug
    task_dir.mkdir(parents=True, exist_ok=True)

    # ── Étape 2 : écrire metadata.yaml ───────────────────────────────────────
    metadata = {
        "title": title,
        "url": url,
        "channel": info.get("uploader"),
        "channel_url": info.get("uploader_url"),
        "duration_seconds": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "description": (info.get("description") or "")[:1000],
        "slug": slug,
        "task_id": task_id,
    }
    (task_dir / "metadata.yaml").write_text(
        yaml.dump(metadata, allow_unicode=True, sort_keys=False)
    )

    result: dict = {
        "status": "success",
        "task_id": task_id,
        "slug": slug,
        "title": title,
        "output_dir": str(task_dir),
        "metadata_path": str(task_dir / "metadata.yaml"),
    }

    # ── Étape 3 : télécharger vidéo (toujours) ───────────────────────────────
    logger.info(f"[{task_id}] Downloading video (quality={quality})...")

    # Prefer H.264+AAC for universal browser compatibility (Safari/iOS won't play AV1/VP9)
    if quality == "best":
        fmt = (
            "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]"
            "/bestvideo[vcodec^=avc1]+bestaudio"
            "/bestvideo+bestaudio/best"
        )
    else:
        height = re.sub(r"\D", "", quality)
        if height:
            fmt = (
                f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[acodec^=mp4a]"
                f"/bestvideo[height<={height}][vcodec^=avc1]+bestaudio"
                f"/bestvideo[height<={height}]+bestaudio/best"
            )
        else:
            fmt = (
                "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]"
                "/bestvideo[vcodec^=avc1]+bestaudio"
                "/bestvideo+bestaudio/best"
            )

    video_path = task_dir / f"{slug}.mp4"
    ydl_video = {
        **ydl_quiet,
        "format": fmt,
        "outtmpl": str(task_dir / f"{slug}.%(ext)s"),
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_video) as ydl:
            ydl.download([url])
        result["video_path"] = str(video_path)
    except Exception as e:
        logger.error(f"[{task_id}] Video download failed: {e}")
        result["video_error"] = str(e)

    # ── Étape 4 : extraire audio MP3 via ffmpeg (toujours) ───────────────────
    logger.info(f"[{task_id}] Extracting audio as MP3...")
    audio_path = task_dir / f"{slug}_audio.mp3"
    if video_path.exists():
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(video_path),
                    "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                    str(audio_path),
                ],
                check=True,
                capture_output=True,
            )
            result["audio_path"] = str(audio_path)
            logger.info(f"[{task_id}] Audio extracted: {audio_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"[{task_id}] ffmpeg audio extraction failed: {e.stderr.decode()}")
            result["audio_error"] = e.stderr.decode()
    else:
        result["audio_error"] = "Video file not found, skipping audio extraction."

    # ── Étape 5 : transcript auto-captions (toujours tenté) ──────────────────
    logger.info(f"[{task_id}] Attempting transcript extraction...")
    transcript_md_path = task_dir / f"{slug}_transcript.md"

    # Utilise la langue originale de la vidéo; fallback "en" si non détectée
    orig_lang = info.get("language") or "en"
    logger.info(f"[{task_id}] Subtitle language: {orig_lang}")

    ydl_subs = {
        **ydl_quiet,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": [orig_lang],
        "skip_download": True,
        "outtmpl": str(task_dir / f"{slug}.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_subs) as ydl:
            ydl.download([url])

        vtt_files = sorted(task_dir.glob("*.vtt"))
        if vtt_files:
            # Garder tous les .vtt + écrire le .md depuis le premier trouvé
            transcript_text = _vtt_to_markdown(vtt_files[0].read_text(encoding="utf-8"))
            transcript_md_path.write_text(
                f"# {title}\n\n{transcript_text}\n",
                encoding="utf-8",
            )
            result["transcript_vtt_paths"] = [str(f) for f in vtt_files]
            result["transcript_md_path"] = str(transcript_md_path)
            # Nettoyer les .json temporaires yt-dlp seulement
            for f in task_dir.glob("*.json"):
                f.unlink(missing_ok=True)
            logger.info(f"[{task_id}] Transcript written: {transcript_md_path}")
        else:
            result["transcript_note"] = (
                "No auto-captions available for this video. "
                "Try using whisper_transcribe on the audio file."
            )
            logger.info(f"[{task_id}] No captions found.")
    except Exception as e:
        logger.error(f"[{task_id}] Transcript extraction failed: {e}")
        result["transcript_error"] = str(e)

    logger.info(f"[{task_id}] Done. Files in {task_dir}")
    _set(result)


# ── Schéma request ────────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    url: str
    quality: str = "best"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/download")
def download(req: DownloadRequest):
    """Démarre le téléchargement en arrière-plan. Retourne immédiatement un task_id."""
    task_id = str(uuid.uuid4())
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending", "task_id": task_id}

    thread = threading.Thread(
        target=_run_download,
        args=(task_id, req.url, req.quality),
        daemon=True,
    )
    thread.start()

    logger.info(f"[{task_id}] Download started in background: {req.url}")
    return {"task_id": task_id, "status": "pending"}


@app.get("/result")
def get_result(task_id: str = Query(..., description="task_id returned by /download")):
    """Retourne l'état et le résultat d'un téléchargement. Poller jusqu'à status != 'pending'/'running'."""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown task_id: {task_id}"})
    return task
