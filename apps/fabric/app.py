"""Fabric App — FastAPI wrapper around the Fabric CLI for HiveFlows agents.

Endpoints:
  GET  /health              → {"status": "ok"}
  GET  /patterns            → {"patterns": [...], "count": N}
  GET  /pattern/{name}/system → system prompt preview
  POST /run                 → launch fabric pattern async → {task_id, status, output_path}
  GET  /result?task_id=...  → poll task status + result

Behaviour:
  - Calls the fabric CLI via subprocess (no REST server needed)
  - input_text is piped via stdin; input_url is passed via --url flag
  - Output is captured from stdout and written to /workspace/<output_filename>
  - Model/vendor can be overridden per-request; defaults come from env vars
  - Progress events are published to the activity bus by the Celery worker (run_fabric.py)
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
logger = logging.getLogger("fabric-app")

app = FastAPI(title="fabric", version="1.0")

PATTERNS_DIR = Path("/root/.config/fabric/patterns")

# Mode proxy HiveFlows : si LLM_BASE_URL est injecté par le Beekeeper,
# on pointe le CLI Fabric vers le proxy core (OpenAI-compatible) plutôt
# que directement vers OpenRouter.  Le champ "model" sera l'agent_id ;
# le proxy résout lui-même les vrais credentials + modèle.
_LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
_LLM_API_KEY  = os.environ.get("LLM_API_KEY",  "")

if _LLM_BASE_URL:
    DEFAULT_VENDOR = "OpenAI"           # vendor OpenAI-compatible → utilise OPENAI_BASE_URL
    DEFAULT_MODEL  = os.environ.get("FABRIC_MODEL", "gpt-4o")   # placeholder ; le proxy l'ignore
else:
    DEFAULT_VENDOR = os.environ.get("FABRIC_VENDOR", "OpenRouter")
    DEFAULT_MODEL  = os.environ.get("FABRIC_MODEL",  "openai/gpt-4o-mini")

_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()


# ── Worker ────────────────────────────────────────────────────────────────────

def _run_fabric(
    task_id: str,
    pattern: str,
    input_text: Optional[str],
    input_url: Optional[str],
    vendor: str,
    model: str,
    output_path: Path,
) -> None:
    def _set(update: dict) -> None:
        with _tasks_lock:
            _tasks[task_id].update(update)

    _set({"status": "running"})
    logger.info(f"[{task_id}] pattern={pattern} vendor={vendor} model={model}")

    cmd = [
        "fabric",
        "--pattern", pattern,
        "--vendor",  vendor,
        "--model",   model,
    ]

    if input_url:
        is_youtube = "youtube.com" in input_url or "youtu.be" in input_url
        cmd += ["--youtube", input_url] if is_youtube else ["--scrape_url", input_url]

    # Env du subprocess : si on est en mode proxy (OpenAI vendor), injecter
    # OPENAI_BASE_URL / OPENAI_API_KEY pour que le CLI Fabric les lise.
    proc_env = os.environ.copy()
    if vendor == "OpenAI" and _LLM_BASE_URL:
        proc_env["OPENAI_BASE_URL"] = _LLM_BASE_URL
        proc_env["OPENAI_API_KEY"]  = _LLM_API_KEY or "hiveflows"

    try:
        proc = subprocess.run(
            cmd,
            input=input_text if not input_url else None,
            capture_output=True,
            text=True,
            timeout=300,
            env=proc_env,
        )

        if proc.returncode != 0:
            error = proc.stderr.strip() or f"fabric exited with code {proc.returncode}"
            logger.error(f"[{task_id}] Error: {error}")
            _set({"status": "error", "error": error})
            return

        output = proc.stdout.strip()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")

        result = {
            "status": "success",
            "task_id": task_id,
            "pattern": pattern,
            "output_path": str(output_path),
            "output_size": len(output),
        }
        _set(result)
        logger.info(f"[{task_id}] Done → {output_path} ({len(output)} chars)")

    except subprocess.TimeoutExpired:
        error = "Fabric timed out (5 min limit)"
        logger.error(f"[{task_id}] {error}")
        _set({"status": "error", "error": error})
    except Exception as e:
        logger.error(f"[{task_id}] Unexpected: {e}", exc_info=True)
        _set({"status": "error", "error": str(e)})


# ── Schemas ───────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    pattern: str
    input_text: Optional[str] = None   # text piped via stdin
    input_url:  Optional[str] = None   # URL fetched by fabric itself (-u flag)
    input_file: Optional[str] = None   # path relative to /workspace; content read server-side
    model:      Optional[str] = None   # override DEFAULT_MODEL (e.g. "openai/gpt-4o")
    vendor:     Optional[str] = None   # override DEFAULT_VENDOR (e.g. "OpenRouter")
    output_filename: Optional[str] = None  # custom output path (relative to /workspace)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/patterns")
def list_patterns():
    """List all available Fabric patterns (reads from baked-in patterns directory)."""
    if not PATTERNS_DIR.exists():
        return {"patterns": [], "count": 0, "error": "Patterns directory not found"}
    patterns = sorted(p.name for p in PATTERNS_DIR.iterdir() if p.is_dir())
    return {"patterns": patterns, "count": len(patterns)}


@app.get("/pattern/{name}/system")
def get_pattern_system(name: str):
    """Return the system prompt of a Fabric pattern."""
    system_file = PATTERNS_DIR / name / "system.md"
    if not system_file.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Pattern '{name}' not found")
    return {"name": name, "system_prompt": system_file.read_text(encoding="utf-8")}


@app.post("/run")
def run_pattern(req: RunRequest):
    """Launch a Fabric pattern asynchronously. Returns task_id immediately.

    Provide input_text (raw text), input_url (URL fetched by Fabric), or input_file
    (path relative to /workspace — content is read server-side).
    When input_file is provided and output_filename is omitted, the output is written
    next to the source file: /workspace/<dir>/<stem>_<pattern><ext>.
    Poll /result?task_id=... until status is 'success' or 'error'.
    """
    if not req.input_text and not req.input_url and not req.input_file:
        return JSONResponse(
            status_code=400,
            content={"error": "Must provide input_text, input_url, or input_file"},
        )

    # If input_file is given, read its content (used as stdin for fabric)
    input_text = req.input_text
    if req.input_file and not input_text and not req.input_url:
        src = Path("/workspace") / req.input_file.lstrip("/")
        try:
            input_text = src.read_text(encoding="utf-8")
        except OSError as e:
            return JSONResponse(
                status_code=400,
                content={"error": f"Cannot read input_file '{req.input_file}': {e}"},
            )

    task_id = str(uuid.uuid4())
    vendor  = req.vendor or DEFAULT_VENDOR
    model   = req.model  or DEFAULT_MODEL

    # Derive output path: explicit > from input_file (same dir, _{pattern} suffix) > default
    if req.output_filename:
        output_path = Path("/workspace") / req.output_filename.lstrip("/")
    elif req.input_file:
        p = Path(req.input_file.lstrip("/"))
        suffix = p.suffix or ".md"
        output_path = Path("/workspace") / p.parent / f"{p.stem}_{req.pattern}{suffix}"
    else:
        output_path = Path("/workspace/fabric") / f"{req.pattern}_{task_id[:8]}.md"

    with _tasks_lock:
        _tasks[task_id] = {
            "status": "pending",
            "task_id": task_id,
            "output_path": str(output_path),
        }

    thread = threading.Thread(
        target=_run_fabric,
        args=(task_id, req.pattern, input_text, req.input_url, vendor, model, output_path),
        daemon=True,
    )
    thread.start()

    logger.info(f"[{task_id}] Queued: pattern={req.pattern} vendor={vendor} model={model}")
    return {"task_id": task_id, "status": "pending", "output_path": str(output_path)}


@app.get("/result")
def get_result(task_id: str = Query(..., description="task_id returned by /run")):
    """Poll the result of a /run task. Returns status + output_path when done."""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown task_id: {task_id}"})
    return task
