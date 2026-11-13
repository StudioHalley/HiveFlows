import asyncio
import logging
import time
from contextlib import asynccontextmanager

import docker
import redis
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Request, status

from config import settings

logger = logging.getLogger("beekeeper")

_startup_time = time.time()
_docker_client: docker.DockerClient | None = None
_db_engine: sa.Engine | None = None
_redis_client: redis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _docker_client, _db_engine, _redis_client
    logging.basicConfig(level=settings.log_level)
    logger.info("Beekeeper starting up...")

    # ── Docker ──
    _docker_client = docker.from_env()
    logger.info(f"Docker connected: {_docker_client.version()['Version']}")

    # ── Redis ──
    _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        _redis_client.ping()
        logger.info("Redis connecté")
    except Exception as e:
        logger.warning(f"Redis ping failed: {e} — events et métriques désactivés")
        _redis_client = None

    # ── DB ──
    if settings.database_url:
        _db_engine = sa.create_engine(settings.database_url)
        logger.info("DB engine initialisé")
    else:
        logger.warning("DATABASE_URL non configurée — fonctions réseau désactivées")

    # ── Sync manifests → app_catalog ──
    if _db_engine:
        from services.manifest_loader import sync_manifests
        try:
            result = sync_manifests(_db_engine, settings.apps_dir)
            logger.info(f"Manifest sync: {result}")
        except Exception as e:
            logger.warning(f"Manifest sync failed (non-fatal): {e}")

    # ── Connecter Redis au réseau hive-backend ──
    # Permet aux app containers d'atteindre Redis par hostname "redis".
    try:
        net = _docker_client.networks.get(settings.hive_backend_network)
        from urllib.parse import urlparse
        redis_alias = urlparse(settings.redis_url).hostname or "redis"
        redis_container = _docker_client.containers.get(settings.redis_container_name)
        net.connect(redis_container, aliases=[redis_alias])
        logger.info(f"Redis connecté au réseau {settings.hive_backend_network} (alias: {redis_alias})")
    except docker.errors.APIError as e:
        if "already exists" in str(e).lower() or "endpoint with name" in str(e).lower():
            logger.info(f"Redis déjà connecté au réseau {settings.hive_backend_network}")
        else:
            logger.warning(f"Impossible de connecter Redis à {settings.hive_backend_network}: {e}")
    except Exception as e:
        logger.warning(f"Redis→hive-backend connexion ignorée: {e}")

    # ── Background tasks ──
    if _docker_client and _redis_client:
        from services.event_listener import start_event_listener
        start_event_listener(_docker_client, _redis_client)

        from services.metrics_publisher import metrics_loop
        asyncio.create_task(metrics_loop(_docker_client, _redis_client))

    yield

    if _redis_client:
        _redis_client.close()
    if _docker_client:
        _docker_client.close()
    if _db_engine:
        _db_engine.dispose()
    logger.info("Beekeeper shut down.")


app = FastAPI(title="Beekeeper", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # /health est accessible sans auth pour les healthchecks Docker
    if request.url.path == "/health":
        return await call_next(request)

    key = request.headers.get("X-Beekeeper-Key")
    if not key or key != settings.beekeeper_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Beekeeper-Key",
        )
    return await call_next(request)


@app.get("/health")
def health():
    if _docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client not initialized")

    info = _docker_client.version()
    return {
        "status": "ok",
        "docker_version": info.get("Version"),
        "docker_api_version": info.get("ApiVersion"),
        "uptime_seconds": round(time.time() - _startup_time, 1),
        "db_connected": _db_engine is not None,
        "redis_connected": _redis_client is not None,
    }


# ── Routers ──
from api.routes_networks import router as networks_router  # noqa: E402
from api.routes_apps import router as apps_router          # noqa: E402
from api.routes_self import router as self_router          # noqa: E402
app.include_router(networks_router)
app.include_router(apps_router)
app.include_router(self_router)
