"""Routes apps — proxy Core ��� Beekeeper (Phase 6 — Bloc G).

Toutes les routes workspace-scoped vérifient l'appartenance de l'utilisateur
au workspace avant de relayer la requête au Beekeeper.

Prefix /w/{workspace_id}/apps   — routes d'instance (scoped workspace)
Prefix /apps                    — routes globales (catalog)
Prefix /w/{workspace_id}/apps/events — SSE Redis events
"""
import asyncio
import json
import logging
import uuid
from typing import Optional
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from beekeeper_client import beekeeper_client
from config import settings
from credential_vault import encrypt
from database import async_session, get_db
from models.app import AppCatalog, AppInstance
from models.user import User
from models.workspace import Workspace, WorkspaceMember

logger = logging.getLogger("hiveflows.apps")

router = APIRouter(tags=["apps"])


# ── Auth helpers ──────────────────────────────────────────────────────────────

async def _get_member(
    workspace_id: UUID,
    user: User,
    db: AsyncSession,
    min_role: str = "member",
) -> tuple[Workspace, WorkspaceMember]:
    """Retourne (workspace, member) ou lève 403/404."""
    ws_result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.is_active == True)
    )
    ws = ws_result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace introuvable.")

    mem_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    member = mem_result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=403, detail="Permission refusée.")

    role_rank = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}
    if role_rank.get(member.role, 0) < role_rank.get(min_role, 2):
        raise HTTPException(status_code=403, detail="Permission refusée.")

    return ws, member


async def _get_instance(
    workspace_id: UUID,
    app_slug: str,
    db: AsyncSession,
) -> AppInstance:
    """Retourne l'AppInstance ou lève 404."""
    result = await db.execute(
        select(AppInstance)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(
            AppInstance.workspace_id == workspace_id,
            AppCatalog.slug == app_slug,
        )
    )
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(
            status_code=404,
            detail=f"App {app_slug!r} non installée dans ce workspace.",
        )
    return instance


async def _ensure_workspace_network(workspace_id: str) -> None:
    """Crée le réseau Docker du workspace s'il n'existe pas encore."""
    async with beekeeper_client() as client:
        r = await client.post("/networks", json={"workspace_id": workspace_id})
        if r.status_code not in (201, 409, 500):
            # 500 peut signifier "network existe déjà" selon la version
            pass
        # Erreurs ignorées — si le network existe déjà, le beekeeper retourne une erreur
        # mais ce n'est pas bloquant. Le create_workspace_network est idempotent.


# ── Schémas ───────────────────────────────────────────────────────────────────

class InstallRequest(BaseModel):
    app_slug: str
    env_values: Optional[dict] = None   # ex: {"PASSWORD": "secret123"}


class EnvUpdateRequest(BaseModel):
    env_values: dict


class AppInstanceRead(BaseModel):
    id: uuid.UUID
    app_slug: str
    app_name: str
    app_image: str
    app_icon: Optional[str]
    container_name: str
    status: str
    container_ip: Optional[str]
    installed_by: uuid.UUID

    model_config = {"from_attributes": True}


class AppCatalogRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    version: str
    description: Optional[str]
    image: str
    app_type: str
    ingress_mode: str
    default_port: Optional[int]
    env_schema: list
    default_mounts: list
    resources: dict
    icon: Optional[str]
    is_active: bool

    model_config = {"from_attributes": True}


# ── Background task install ───────────────────────────────────────────────────

async def _install_worker(
    workspace_id: str,
    workspace_slug: str,
    instance_id: str,
    image: str,
    app_slug: str,
    port: Optional[int],
    encrypted_env: Optional[str],
    mounts: list,
    resources: dict,
) -> None:
    """Tâche background : appelle le Beekeeper pour démarrer le container.

    Met à jour le statut en DB en fonction du résultat.
    """
    async with async_session() as db:
        try:
            # S'assurer que le réseau workspace existe
            await _ensure_workspace_network(workspace_id)

            # Démarrer le container (Docker pull auto si image absente)
            payload = {
                "workspace_id": workspace_id,
                "workspace_slug": workspace_slug,
                "app_slug": app_slug,
                "image": image,
                "port": port,
                "encrypted_env": encrypted_env,
                "mounts": mounts,
                "resources": resources,
            }
            async with beekeeper_client() as client:
                r = await client.post("/apps/start", json=payload, timeout=300.0)

            if r.status_code not in (200, 201):
                raise RuntimeError(f"Beekeeper start_app erreur {r.status_code}: {r.text}")

            data = r.json()
            new_status = "running" if data.get("status") == "running" else "stopped"
            container_ip = data.get("container_ip")

            # Update instance in DB
            result = await db.execute(
                select(AppInstance).where(AppInstance.id == uuid.UUID(instance_id))
            )
            instance = result.scalar_one_or_none()
            if instance:
                instance.status = new_status
                instance.container_ip = container_ip
                from datetime import datetime
                instance.started_at = datetime.utcnow() if new_status == "running" else None
                await db.commit()
                logger.info(f"App {app_slug} ({workspace_id}) → {new_status}")

        except Exception as e:
            logger.error(f"Install worker erreur {app_slug}: {e}", exc_info=True)
            result = await db.execute(
                select(AppInstance).where(AppInstance.id == uuid.UUID(instance_id))
            )
            instance = result.scalar_one_or_none()
            if instance:
                instance.status = "error"
                await db.commit()


async def _update_worker(
    workspace_id: str,
    workspace_slug: str,
    instance_id: str,
    image: str,
    app_slug: str,
    port: Optional[int],
    encrypted_env: Optional[str],
    mounts: list,
    resources: dict,
    catalog_version: str,
) -> None:
    """Tâche background : demande au beekeeper de rafraîchir l'image + recréer le
    container, puis met à jour le statut et installed_version en DB."""
    async with async_session() as db:
        try:
            payload = {
                "workspace_id": workspace_id,
                "workspace_slug": workspace_slug,
                "app_slug": app_slug,
                "image": image,
                "port": port,
                "encrypted_env": encrypted_env,
                "mounts": mounts,
                "resources": resources,
            }
            # build/pull peut être long → timeout généreux
            async with beekeeper_client() as client:
                r = await client.post("/apps/update", json=payload, timeout=900.0)

            if r.status_code not in (200, 201):
                raise RuntimeError(f"Beekeeper update erreur {r.status_code}: {r.text}")

            data = r.json()
            new_status = "running" if data.get("status") == "running" else "stopped"

            result = await db.execute(
                select(AppInstance).where(AppInstance.id == uuid.UUID(instance_id))
            )
            instance = result.scalar_one_or_none()
            if instance:
                from datetime import datetime
                instance.status = new_status
                instance.container_ip = data.get("container_ip")
                instance.started_at = datetime.utcnow() if new_status == "running" else None
                instance.installed_version = catalog_version
                await db.commit()
                logger.info(f"App {app_slug} ({workspace_id}) mise à jour → v{catalog_version} ({new_status})")

        except Exception as e:
            logger.error(f"Update worker erreur {app_slug}: {e}", exc_info=True)
            result = await db.execute(
                select(AppInstance).where(AppInstance.id == uuid.UUID(instance_id))
            )
            instance = result.scalar_one_or_none()
            if instance:
                instance.status = "error"
                await db.commit()


# ── Routes catalog ────────────────────────────────────────────────────────────

@router.get("/apps/catalog", response_model=list[AppCatalogRead])
async def get_catalog(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Liste toutes les apps disponibles dans le catalogue."""
    result = await db.execute(
        select(AppCatalog).where(AppCatalog.is_active == True).order_by(AppCatalog.name)
    )
    return result.scalars().all()


# ── Routes workspace-scoped ───────────────────────────────────────────────────

@router.get("/w/{workspace_id}/apps")
async def list_workspace_apps(
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Liste les apps installées dans le workspace avec leur statut."""
    await _get_member(workspace_id, user, db, min_role="viewer")

    result = await db.execute(
        select(AppInstance, AppCatalog)
        .join(AppCatalog, AppCatalog.id == AppInstance.app_id)
        .where(AppInstance.workspace_id == workspace_id)
        .order_by(AppCatalog.name)
    )
    rows = result.all()

    apps = []
    for instance, catalog in rows:
        apps.append({
            "id": str(instance.id),
            "app_slug": catalog.slug,
            "app_name": catalog.name,
            "app_image": catalog.image,
            "app_icon": catalog.icon,
            "app_type": catalog.app_type,
            "ingress_mode": catalog.ingress_mode,
            "default_port": catalog.default_port,
            "container_name": instance.container_name,
            "status": instance.status,
            "container_ip": instance.container_ip,
            "installed_by": str(instance.installed_by),
            "started_at": instance.started_at.isoformat() if instance.started_at else None,
        })
    return apps


@router.post("/w/{workspace_id}/apps/install", status_code=status.HTTP_202_ACCEPTED)
async def install_app(
    workspace_id: UUID,
    body: InstallRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Installe une app dans le workspace.

    Retourne 202 immédiatement. L'état passe de "pulling" → "running" en background.
    Suivre la progression via GET /w/{workspace_id}/apps/events (SSE Redis).
    """
    ws, member = await _get_member(workspace_id, user, db, min_role="member")

    # Récupérer l'app dans le catalogue
    cat_result = await db.execute(
        select(AppCatalog).where(AppCatalog.slug == body.app_slug, AppCatalog.is_active == True)
    )
    app = cat_result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"App {body.app_slug!r} introuvable dans le catalogue.")

    # Vérifier qu'elle n'est pas déjà installée
    existing = await db.execute(
        select(AppInstance).where(
            AppInstance.workspace_id == workspace_id,
            AppInstance.app_id == app.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"App {body.app_slug!r} déjà installée dans ce workspace.")

    # Chiffrer les env values
    encrypted_env: Optional[str] = None
    if body.env_values and settings.credential_encryption_key:
        encrypted_env = encrypt(json.dumps(body.env_values))

    container_name = f"hive-app-{workspace_id}-{body.app_slug}"

    instance = AppInstance(
        workspace_id=workspace_id,
        app_id=app.id,
        container_name=container_name,
        status="pulling",
        env_values=encrypted_env,
        installed_by=user.id,
        installed_version=app.version,
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)

    background_tasks.add_task(
        _install_worker,
        str(workspace_id),
        ws.slug,
        str(instance.id),
        app.image,
        app.slug,
        app.default_port,
        encrypted_env,
        app.default_mounts,
        app.resources or {},
    )

    return {
        "status": "installing",
        "instance_id": str(instance.id),
        "app_slug": body.app_slug,
        "container_name": container_name,
    }


@router.post("/w/{workspace_id}/apps/{app_slug}/start", status_code=status.HTTP_200_OK)
async def start_app(
    workspace_id: UUID,
    app_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Démarre une app arrêtée."""
    ws, _ = await _get_member(workspace_id, user, db, min_role="member")
    instance = await _get_instance(workspace_id, app_slug, db)
    async with beekeeper_client() as client:
        # Récupérer les infos app pour le start
        cat_result = await db.execute(select(AppCatalog).where(AppCatalog.id == instance.app_id))
        app = cat_result.scalar_one()

        r = await client.post("/apps/start", json={
            "workspace_id": str(workspace_id),
            "workspace_slug": ws.slug,
            "app_slug": app_slug,
            "image": app.image,
            "port": app.default_port,
            "encrypted_env": instance.env_values,
            "mounts": app.default_mounts,
            "resources": app.resources or {},
        }, timeout=120.0)

    if r.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Beekeeper erreur: {r.text}")

    data = r.json()
    instance.status = data.get("status", "running")
    instance.container_ip = data.get("container_ip")
    await db.commit()
    return data


@router.post("/w/{workspace_id}/apps/{app_slug}/stop", status_code=status.HTTP_200_OK)
async def stop_app(
    workspace_id: UUID,
    app_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Arrête une app en cours d'exécution."""
    await _get_member(workspace_id, user, db, min_role="member")
    instance = await _get_instance(workspace_id, app_slug, db)

    async with beekeeper_client() as client:
        r = await client.post("/apps/stop", json={"container_name": instance.container_name})

    if r.status_code not in (200, 404):
        raise HTTPException(status_code=502, detail=f"Beekeeper erreur: {r.text}")

    from datetime import datetime
    instance.status = "stopped"
    instance.stopped_at = datetime.utcnow()
    await db.commit()
    return {"status": "stopped", "app_slug": app_slug}


@router.post("/w/{workspace_id}/apps/{app_slug}/restart", status_code=status.HTTP_200_OK)
async def restart_app(
    workspace_id: UUID,
    app_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Redémarre une app."""
    await _get_member(workspace_id, user, db, min_role="member")
    instance = await _get_instance(workspace_id, app_slug, db)

    async with beekeeper_client() as client:
        r = await client.post("/apps/restart", json={"container_name": instance.container_name})

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Beekeeper erreur: {r.text}")

    data = r.json()
    instance.status = data.get("status", "running")
    await db.commit()
    return data


@router.delete("/w/{workspace_id}/apps/{app_slug}", status_code=status.HTTP_200_OK)
async def uninstall_app(
    workspace_id: UUID,
    app_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Désinstalle une app : arrête le container, le supprime, retire l'instance de la DB."""
    await _get_member(workspace_id, user, db, min_role="member")
    instance = await _get_instance(workspace_id, app_slug, db)

    async with beekeeper_client() as client:
        r = await client.delete(f"/apps/{instance.container_name}")
    # 200 (removed) ou 200 (already_absent) — les deux sont OK

    await db.delete(instance)
    await db.commit()
    return {"status": "uninstalled", "app_slug": app_slug}


@router.put("/w/{workspace_id}/apps/{app_slug}/env", status_code=status.HTTP_200_OK)
async def update_env(
    workspace_id: UUID,
    app_slug: str,
    body: EnvUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Met à jour les variables d'environnement d'une app (chiffrées en DB).

    Les nouvelles valeurs ne sont appliquées qu'au prochain démarrage du container.
    """
    await _get_member(workspace_id, user, db, min_role="member")
    instance = await _get_instance(workspace_id, app_slug, db)

    encrypted: Optional[str] = None
    if body.env_values and settings.credential_encryption_key:
        encrypted = encrypt(json.dumps(body.env_values))

    instance.env_values = encrypted
    await db.commit()
    return {"status": "updated", "app_slug": app_slug, "keys": list(body.env_values.keys())}


@router.get("/w/{workspace_id}/apps/{app_slug}/logs")
async def stream_logs(
    workspace_id: UUID,
    app_slug: str,
    lines: int = 100,
    follow: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Proxy SSE — streame les logs du container depuis le Beekeeper."""
    await _get_member(workspace_id, user, db, min_role="viewer")
    instance = await _get_instance(workspace_id, app_slug, db)

    bk_url = (
        f"{settings.beekeeper_url}/apps/logs"
        f"?container_name={instance.container_name}"
        f"&lines={lines}&follow={str(follow).lower()}"
        f"&workspace_id={workspace_id}&app_slug={app_slug}"
    )

    async def event_stream():
        async with beekeeper_client() as client:
            async with client.stream("GET", bk_url, timeout=None) as response:
                if response.status_code != 200:
                    error = await response.aread()
                    yield f"event: error\ndata: {error.decode()}\n\n"
                    return
                async for line in response.aiter_lines():
                    if line:
                        yield f"{line}\n\n"
                    else:
                        yield "\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

