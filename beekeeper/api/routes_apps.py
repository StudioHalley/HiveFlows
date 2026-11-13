"""Routes apps — POST /apps/start|stop|restart|pull, DELETE /apps/{name},
GET /apps/status, GET /apps/logs."""
import asyncio
import json
import logging
import queue
import shlex
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.container_manager import (
    _container_name,
    build_image_local,
    get_workspace_containers,
    pull_image,
    pull_image_background,
    remove_app,
    restart_app,
    start_app,
    stop_app,
)
from services.ingress_manager import refresh_nginx_config

logger = logging.getLogger("beekeeper.routes_apps")

router = APIRouter(prefix="/apps", tags=["apps"])

_pull_status: dict[str, str] = {}  # image → dernier message de progression


def _get_docker():
    from main import _docker_client
    return _docker_client


def _get_redis():
    from main import _redis_client
    return _redis_client


def _get_settings():
    from config import settings
    return settings


_APP_INSTANCE_CHANNEL = "hiveflows:app_instance_changed"


def _notify_app_instance_changed(workspace_id: str, app_slug: str, event: str) -> None:
    """Publie sur Redis pour que Core invalide son cache d'agents."""
    redis_client = _get_redis()
    if not redis_client or not workspace_id:
        return
    try:
        redis_client.publish(
            _APP_INSTANCE_CHANNEL,
            json.dumps({"workspace_id": workspace_id, "app_slug": app_slug, "event": event}),
        )
    except Exception as e:
        logger.warning(f"app_instance_changed publish error: {e}")


def _refresh_nginx():
    """Régénère la config nginx de façon non-bloquante (best-effort)."""
    docker_client = _get_docker()
    cfg = _get_settings()
    if docker_client is None:
        return
    try:
        refresh_nginx_config(
            docker_client,
            nginx_conf_dir=cfg.nginx_conf_dir,
            nginx_container_name=cfg.nginx_container_name,
        )
    except Exception as e:
        logger.warning(f"refresh_nginx_config failed (non-fatal): {e}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_manifest(app_slug: str, apps_dir: str) -> dict:
    """Lit manifest.yaml pour app_slug. Retourne {} si introuvable."""
    path = Path(apps_dir) / app_slug / "manifest.yaml"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Impossible de lire manifest {app_slug}: {e}")
        return {}


# ── Schemas ──────────────────────────────────────────────────────────────────

class StartAppRequest(BaseModel):
    workspace_id: str
    workspace_slug: Optional[str] = None
    app_slug: str
    image: str
    port: Optional[int] = None
    env_values: Optional[dict] = None       # plaintext (prioritaire)
    encrypted_env: Optional[str] = None     # JSON Fernet chiffré
    mounts: Optional[list[dict]] = None     # [{host, container}]
    resources: Optional[dict] = None        # {memory, cpus}


class StopAppRequest(BaseModel):
    container_name: str
    timeout: int = 10


class RestartAppRequest(BaseModel):
    container_name: str
    timeout: int = 10


class PullImageRequest(BaseModel):
    image: str
    workspace_id: Optional[str] = None   # pour publier progress sur Redis
    app_slug: Optional[str] = None       # pour publier progress sur Redis


class BuildImageRequest(BaseModel):
    app_slug: str
    tag: Optional[str] = None            # défaut: hiveflows/{app_slug}:latest
    workspace_id: Optional[str] = None   # pour publier progress sur Redis


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/start", status_code=status.HTTP_201_CREATED)
def app_start(body: StartAppRequest):
    """Crée et démarre un container pour une app workspace."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")
    return _do_start(docker_client, body)


def _do_start(docker_client, body: StartAppRequest) -> dict:
    """Logique de démarrage d'une app (partagée entre /start et /update)."""
    cfg = _get_settings()

    # Lire le manifest pour env_inject, strip_prefix et rewrite_paths
    manifest = _load_manifest(body.app_slug, cfg.apps_dir)
    base_url = f"/app/{body.workspace_id}/{body.app_slug}/"
    strip_prefix = manifest.get("strip_prefix", True)
    rewrite_paths = manifest.get("rewrite_paths", False)

    extra_labels: dict = {}
    if not strip_prefix:
        extra_labels["hive.strip_prefix"] = "false"
    if rewrite_paths:
        extra_labels["hive.rewrite_paths"] = "true"

    # Injecter REDIS_URL dans tous les containers (ne pas écraser si déjà fourni par l'utilisateur)
    env_inject: dict = {"REDIS_URL": cfg.redis_url}
    for key, template in (manifest.get("env_inject") or {}).items():
        env_inject[key] = template.format(
            workspace_id=body.workspace_id,
            app_slug=body.app_slug,
            base_url=base_url,
        )

    app_type = manifest.get("type", "persistent")
    backend_network = cfg.hive_backend_network if app_type == "backend" else None

    try:
        result = start_app(
            docker_client=docker_client,
            workspace_id=body.workspace_id,
            workspace_slug=body.workspace_slug,
            app_slug=body.app_slug,
            image=body.image,
            port=body.port,
            env_values=body.env_values,
            encrypted_env=body.encrypted_env,
            fernet_key=cfg.credential_encryption_key or None,
            mounts=body.mounts or [],
            resources=body.resources or {},
            workspace_data_dir=cfg.workspaces_data_dir,
            workspace_data_host_dir=cfg.workspaces_data_host_dir or None,
            extra_labels=extra_labels,
            env_inject=env_inject,
            backend_network=backend_network,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    _refresh_nginx()
    _notify_app_instance_changed(body.workspace_id, body.app_slug, "started")
    return result


def _parse_container_name(container_name: str) -> tuple[str, str]:
    """Extrait (workspace_id, app_slug) depuis 'hive-app-{workspace_id}-{app_slug}'.

    Retourne ("", "") si le format ne correspond pas.
    """
    prefix = "hive-app-"
    if not container_name.startswith(prefix):
        return "", ""
    rest = container_name[len(prefix):]
    # workspace_id est un UUID (36 chars) : ex 75cc5994-8178-4eae-81d9-3e85ec5f319c
    # app_slug suit après le dernier tiret du UUID
    # UUID format: 8-4-4-4-12 = 36 chars
    if len(rest) > 37 and rest[36] == "-":
        return rest[:36], rest[37:]
    return "", ""


@router.post("/stop", status_code=status.HTTP_200_OK)
def app_stop(body: StopAppRequest):
    """Arrête un container."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")
    try:
        result = stop_app(docker_client, body.container_name, body.timeout)
    except RuntimeError as e:
        raise HTTPException(status_code=404 if "introuvable" in str(e) else 500, detail=str(e))
    _refresh_nginx()
    ws_id, app_slug = _parse_container_name(body.container_name)
    _notify_app_instance_changed(ws_id, app_slug, "stopped")
    return result


@router.post("/restart", status_code=status.HTTP_200_OK)
def app_restart(body: RestartAppRequest):
    """Redémarre un container."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")
    try:
        result = restart_app(docker_client, body.container_name, body.timeout)
    except RuntimeError as e:
        raise HTTPException(status_code=404 if "introuvable" in str(e) else 500, detail=str(e))
    _refresh_nginx()
    ws_id, app_slug = _parse_container_name(body.container_name)
    _notify_app_instance_changed(ws_id, app_slug, "restarted")
    return result


@router.delete("/{container_name}", status_code=status.HTTP_200_OK)
def app_remove(container_name: str):
    """Arrête et supprime un container."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")
    try:
        removed = remove_app(docker_client, container_name)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    _refresh_nginx()
    ws_id, app_slug = _parse_container_name(container_name)
    _notify_app_instance_changed(ws_id, app_slug, "removed")
    return {"status": "removed" if removed else "already_absent", "container_name": container_name}


@router.get("/status", status_code=status.HTTP_200_OK)
def app_status(workspace_id: str):
    """Liste les containers hive-managed d'un workspace avec leur statut."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")
    try:
        return get_workspace_containers(docker_client, workspace_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pull", status_code=status.HTTP_202_ACCEPTED)
def app_pull(body: PullImageRequest, background_tasks: BackgroundTasks):
    """Lance le pull d'une image Docker en arrière-plan.

    Retourne immédiatement 202.
    Si workspace_id + app_slug fournis, publie la progression sur Redis :
      hive:app:{workspace_id}:{app_slug}:pull
    La progression est aussi disponible via GET /apps/pull/status?image=...
    """
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")

    redis_client = _get_redis()
    image = body.image
    workspace_id = body.workspace_id
    app_slug = body.app_slug

    _pull_status[image] = "starting"

    def on_progress(line: dict):
        """Reçoit chaque ligne de progression du pull Docker SDK (dict décodé)."""
        pull_status_str = line.get("status", "")
        progress_detail = line.get("progressDetail", {})
        progress_str = line.get("progress", "")

        # Mettre à jour le cache local
        _pull_status[image] = f"{pull_status_str} {progress_str}".strip()

        # Publier sur Redis si les infos workspace sont disponibles
        if redis_client and workspace_id and app_slug:
            current = progress_detail.get("current", 0)
            total = progress_detail.get("total", 0)
            percent = round(current / total * 100) if total > 0 else 0

            payload = {
                "status": pull_status_str,
                "progress": percent,
                "layer": line.get("id", ""),
                "timestamp": _now_iso(),
            }
            if pull_status_str in ("Pull complete", "Already exists", "Download complete"):
                payload["progress"] = 100

            try:
                redis_client.publish(
                    f"hive:app:{workspace_id}:{app_slug}:pull",
                    json.dumps(payload),
                )
            except Exception as e:
                logger.warning(f"Redis pull progress publish error: {e}")

    def _pull_with_dict_progress():
        """Wrapper qui parse chaque ligne brute et appelle on_progress."""
        import docker as _docker
        dc = docker_client
        try:
            for line in dc.api.pull(image, stream=True, decode=True):
                on_progress(line)

            _pull_status[image] = "complete"
            # Publier "complete" sur Redis
            if redis_client and workspace_id and app_slug:
                try:
                    redis_client.publish(
                        f"hive:app:{workspace_id}:{app_slug}:pull",
                        json.dumps({"status": "complete", "progress": 100, "timestamp": _now_iso()}),
                    )
                except Exception as e:
                    logger.warning(f"Redis pull complete publish error: {e}")
        except Exception as e:
            _pull_status[image] = f"error: {e}"
            logger.error(f"Pull error {image}: {e}")

    t = threading.Thread(target=_pull_with_dict_progress, daemon=True, name=f"pull-{image[:30]}")
    t.start()

    return {"status": "pulling", "image": image}


@router.get("/pull/status", status_code=status.HTTP_200_OK)
def app_pull_status(image: str):
    """Retourne le dernier message de progression pour un pull en cours."""
    if image not in _pull_status:
        raise HTTPException(status_code=404, detail=f"Aucun pull en cours pour {image!r}")
    return {"image": image, "progress": _pull_status[image]}


@router.post("/build", status_code=status.HTTP_202_ACCEPTED)
def app_build(body: BuildImageRequest, background_tasks: BackgroundTasks):
    """Lance le build d'une image locale depuis /apps/{app_slug}/Dockerfile en arrière-plan.

    Retourne immédiatement 202.
    Publie la progression sur Redis : hive:app:{workspace_id}:{app_slug}:build
    """
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")

    redis_client = _get_redis()
    cfg = _get_settings()
    app_slug = body.app_slug
    workspace_id = body.workspace_id
    tag = body.tag  # None → build_image_local choisit hiveflows/{slug}:latest

    def _run_build():
        try:
            if redis_client and workspace_id:
                redis_client.publish(
                    f"hive:app:{workspace_id}:{app_slug}:build",
                    json.dumps({"status": "building", "progress": 0, "timestamp": _now_iso()}),
                )

            def on_progress(line: str):
                if redis_client and workspace_id:
                    try:
                        redis_client.publish(
                            f"hive:app:{workspace_id}:{app_slug}:build",
                            json.dumps({"status": "building", "line": line, "timestamp": _now_iso()}),
                        )
                    except Exception:
                        pass

            build_image_local(docker_client, app_slug, apps_dir=cfg.apps_dir, tag=tag, on_progress=on_progress)

            if redis_client and workspace_id:
                redis_client.publish(
                    f"hive:app:{workspace_id}:{app_slug}:build",
                    json.dumps({"status": "complete", "progress": 100, "timestamp": _now_iso()}),
                )
            logger.info(f"Build {app_slug} terminé")

        except Exception as e:
            logger.error(f"Build {app_slug} échoué: {e}")
            if redis_client and workspace_id:
                try:
                    redis_client.publish(
                        f"hive:app:{workspace_id}:{app_slug}:build",
                        json.dumps({"status": "error", "error": str(e), "timestamp": _now_iso()}),
                    )
                except Exception:
                    pass

    t = threading.Thread(target=_run_build, daemon=True, name=f"build-{app_slug[:30]}")
    t.start()
    return {"status": "building", "app_slug": app_slug}


def _is_local_app(app_slug: str, apps_dir: str) -> bool:
    """True si l'app se build localement (Dockerfile présent dans /apps/{slug})."""
    import os
    return os.path.exists(os.path.join(apps_dir, app_slug, "Dockerfile"))


@router.post("/update", status_code=status.HTTP_200_OK)
def app_update(body: StartAppRequest):
    """Met à jour une app : rafraîchit l'image (build local OU pull) puis recrée le
    container. Synchrone et bloquant (peut prendre quelques minutes) — à appeler depuis
    une tâche background côté core. Les volumes de données sont préservés (chemins hôte
    persistants), seul le container est recréé.
    """
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")

    cfg = _get_settings()
    container_name = _container_name(body.workspace_id, body.app_slug)

    # ── 1. Rafraîchir l'image ──
    try:
        if _is_local_app(body.app_slug, cfg.apps_dir):
            logger.info(f"Update {body.app_slug}: rebuild local")
            build_image_local(docker_client, body.app_slug, apps_dir=cfg.apps_dir)
        else:
            logger.info(f"Update {body.app_slug}: pull {body.image}")
            pull_image(docker_client, body.image)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Rafraîchissement image échoué: {e}")

    # ── 2. Supprimer l'ancien container (les volumes de données ne sont pas touchés) ──
    try:
        remove_app(docker_client, container_name, force=True)
    except RuntimeError as e:
        logger.warning(f"Remove avant update {container_name}: {e}")

    # ── 3. Recréer sur la nouvelle image ──
    return _do_start(docker_client, body)


@router.post("/sync-catalog", status_code=status.HTTP_200_OK)
def app_sync_catalog():
    """Re-synchronise app_catalog depuis les manifests de /apps (sans redémarrer
    beekeeper). À appeler après un git pull pour que les nouvelles versions de
    manifests soient reflétées dans le catalogue."""
    from main import _db_engine
    from services.manifest_loader import sync_manifests
    cfg = _get_settings()
    if _db_engine is None:
        raise HTTPException(status_code=503, detail="DB non configurée")
    try:
        return sync_manifests(_db_engine, cfg.apps_dir)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/logs")
async def app_logs(
    container_name: str,
    lines: int = 100,
    follow: bool = False,
    workspace_id: Optional[str] = None,
    app_slug: Optional[str] = None,
):
    """Stream les logs d'un container via SSE (text/event-stream).

    Chaque événement SSE : data: {"line": "...", "stream": "stdout", "timestamp": "..."}

    Params :
      container_name : nom exact du container (ex: hive-app-abc123-vscode)
      lines          : nombre de lignes à remonter (tail)
      follow         : si true, garde la connexion ouverte et stream les nouveaux logs
      workspace_id   : si fourni avec app_slug, publie aussi les logs sur Redis
      app_slug       : voir ci-dessus
    """
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")

    import docker.errors
    try:
        container = docker_client.containers.get(container_name)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container {container_name!r} introuvable")

    redis_client = _get_redis()
    redis_channel = (
        f"hive:app:{workspace_id}:{app_slug}:logs"
        if redis_client and workspace_id and app_slug
        else None
    )

    async def event_generator():
        log_queue: queue.Queue = queue.Queue()

        def stream_thread():
            try:
                for chunk in container.logs(
                    stream=True,
                    follow=follow,
                    tail=lines,
                    timestamps=True,
                ):
                    log_queue.put(chunk)
            except Exception as e:
                log_queue.put(e)
            finally:
                log_queue.put(None)  # sentinel de fin

        t = threading.Thread(target=stream_thread, daemon=True, name=f"logs-{container_name[:30]}")
        t.start()

        while True:
            try:
                item = log_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue

            if item is None:
                break

            if isinstance(item, Exception):
                yield f"event: error\ndata: {json.dumps({'error': str(item)})}\n\n"
                break

            line = item.decode("utf-8", errors="replace").rstrip("\n\r")
            # Les logs Docker avec timestamps ont le format : "2024-01-01T... message"
            parts = line.split(" ", 1)
            ts = parts[0] if len(parts) == 2 else _now_iso()
            text = parts[1] if len(parts) == 2 else line

            data = json.dumps({"line": text, "timestamp": ts})
            yield f"data: {data}\n\n"

            # Publier sur Redis (D6)
            if redis_channel:
                try:
                    redis_client.publish(redis_channel, data)
                except Exception as e:
                    logger.warning(f"Redis logs publish error: {e}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
