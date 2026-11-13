"""Routes self — mise à jour de la plateforme déclenchée depuis l'UI.

Beekeeper exécute scripts/update.sh sur l'hôte (via le repo monté à l'identique
+ le socket Docker). Le mode --exclude-beekeeper garantit que beekeeper n'est PAS
recréé : il reste vivant pour streamer la progression (pas de self-restart).

Endpoints :
  POST /self/update          → lance la mise à jour (202), non bloquant
  GET  /self/update/status   → état + lignes de log (polling, paramètre ?since=N)
  GET  /self/version         → commit courant, branche, retard vs origin
"""
import logging
import os
import subprocess
import threading
import time

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from config import settings

logger = logging.getLogger("beekeeper.routes_self")

router = APIRouter(prefix="/self", tags=["self"])

# État partagé d'une mise à jour en cours (beekeeper reste vivant → mémoire OK).
_update_state: dict = {
    "status": "idle",        # idle | running | ok | failed
    "lines": [],             # lignes de stdout/stderr de update.sh
    "returncode": None,
    "started_at": None,
    "finished_at": None,
}
_update_lock = threading.Lock()


def _repo_dir() -> str | None:
    """Chemin du repo si le self-update est configuré et disponible, sinon None."""
    d = settings.hive_repo_host_dir
    if not d:
        return None
    script = os.path.join(d, "scripts", "update.sh")
    if not os.path.isfile(script):
        return None
    return d


def _git(repo: str, *args: str, timeout: int = 30) -> str:
    # -c safe.directory : beekeeper (root) agit sur un repo possédé par l'utilisateur hôte.
    out = subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", repo, *args],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "GIT_SSH_COMMAND": "ssh -o StrictHostKeyChecking=accept-new"},
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return out.stdout.strip()


# ── Update ───────────────────────────────────────────────────────────────────
class UpdateRequest(BaseModel):
    force: bool = False
    strict_env: bool = False


def _run_update(repo: str, force: bool, strict_env: bool) -> None:
    """Exécute update.sh en sous-process et accumule la sortie ligne par ligne."""
    cmd = [os.path.join(repo, "scripts", "update.sh"), "--exclude-beekeeper"]
    if force:
        cmd.append("--force")
    if strict_env:
        cmd.append("--strict-env")

    env = {
        **os.environ,
        "GIT_SSH_COMMAND": "ssh -o StrictHostKeyChecking=accept-new",
        # core est joignable par DNS Docker depuis beekeeper (pas localhost).
        "HEALTH_URL": "http://core:8000/health",
        # Sortie non bufferisée pour un streaming fluide.
        "PYTHONUNBUFFERED": "1",
    }
    try:
        proc = subprocess.Popen(
            cmd, cwd=repo, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            _update_state["lines"].append(line.rstrip("\n"))
        proc.wait()
        _update_state["returncode"] = proc.returncode
        _update_state["status"] = "ok" if proc.returncode == 0 else "failed"
    except Exception as e:  # noqa: BLE001
        logger.exception("Self-update a échoué")
        _update_state["lines"].append(f"[beekeeper] erreur: {e}")
        _update_state["returncode"] = -1
        _update_state["status"] = "failed"
    finally:
        _update_state["finished_at"] = time.time()
        logger.info(f"Self-update terminé: status={_update_state['status']}")


@router.post("/update", status_code=status.HTTP_202_ACCEPTED)
def self_update(body: UpdateRequest):
    repo = _repo_dir()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Self-update non configuré (HIVE_REPO_HOST_DIR absent ou repo introuvable).",
        )
    with _update_lock:
        if _update_state["status"] == "running":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Mise à jour déjà en cours.")
        _update_state.update(
            status="running", lines=[], returncode=None,
            started_at=time.time(), finished_at=None,
        )
    threading.Thread(
        target=_run_update, args=(repo, body.force, body.strict_env),
        daemon=True, name="self-update",
    ).start()
    return {"status": "running"}


@router.get("/update/status")
def self_update_status(since: int = 0):
    lines = _update_state["lines"]
    return {
        "status": _update_state["status"],
        "returncode": _update_state["returncode"],
        "total": len(lines),
        "lines": lines[since:] if since < len(lines) else [],
        "started_at": _update_state["started_at"],
        "finished_at": _update_state["finished_at"],
    }


# ── Version ──────────────────────────────────────────────────────────────────
@router.get("/version")
def self_version():
    repo = _repo_dir()
    if not repo:
        return {"available": False, "reason": "self-update non configuré"}
    try:
        branch = os.environ.get("HIVE_BRANCH", "main")
        current = _git(repo, "rev-parse", "--short", "HEAD")
        current_full = _git(repo, "rev-parse", "HEAD")
        subject = _git(repo, "log", "-1", "--pretty=%s")
        # Compare avec origin (best-effort : fetch peut échouer hors-ligne).
        behind = None
        remote = None
        try:
            _git(repo, "fetch", "--quiet", "origin", branch, timeout=60)
            remote = _git(repo, "rev-parse", "--short", f"origin/{branch}")
            behind = int(_git(repo, "rev-list", "--count", f"HEAD..origin/{branch}"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"git fetch impossible: {e}")
        return {
            "available": True,
            "branch": branch,
            "current": current,
            "current_full": current_full,
            "subject": subject,
            "remote": remote,
            "behind": behind,
            "up_to_date": (behind == 0) if behind is not None else None,
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
