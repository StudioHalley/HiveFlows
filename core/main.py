import logging
import os
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from database import async_session, engine
import models  # noqa: F401 — register all models with Base.metadata
from models.agent_config import AgentConfig
from models.api_key import ApiKey, hash_api_key
from models.workspace import Workspace
from agents.agent_bus import start_agent_bus
from agents.app_instance_bus import start_app_instance_bus
from agents.manager import agent_manager
from api.routes_audit import router as audit_router
from api.routes_chat import router as chat_router
from api.routes_oauth import router as oauth_router
from api.routes_tasks import router as tasks_router
from api.routes_agents import router as agents_router
from api.routes_auth import router as auth_router
from api.routes_dashboard import router as dashboard_router
from api.routes_api_keys import router as api_keys_router
from api.routes_admin import router as admin_router
from api.routes_workspaces import router as workspaces_router
from api.routes_files import router as files_router
from api.routes_providers import router as providers_router
from api.routes_apps import router as apps_router
from api.routes_llm_proxy import router as llm_proxy_router
from api.routes_portal import router as portal_router
from api.routes_portal_devices import router as portal_devices_router
from api.routes_tunnel import router as tunnel_router
from api.routes_tools import router as tools_router

logger = logging.getLogger("hiveflows")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level)
    logger.info("HiveFlows Core starting up...")

    env = os.environ.copy()
    env["PYTHONPATH"] = "/app"
    result = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        logger.error(f"Alembic migration failed: {result.stderr}")
        raise RuntimeError("Database migration failed")
    logger.info("Database migrations applied.")

    agent_manager.setup()

    # Charger tous les agents actifs depuis la DB dans le registry
    async with async_session() as db:
        rows = await db.execute(
            select(AgentConfig, Workspace.slug)
            .outerjoin(Workspace, Workspace.id == AgentConfig.workspace_id)
            .where(AgentConfig.is_active == True)
        )
        pairs = rows.all()
        for config, ws_slug in pairs:
            if ws_slug:
                config._workspace_slug = ws_slug
            agent_manager.register_config(config)
        logger.info(f"{len(pairs)} agent config(s) chargée(s) depuis la DB.")

    # F4: Start agent-to-agent async message bus (Redis pub/sub)
    start_agent_bus()

    # Invalidation cache agent quand une app démarre/s'arrête dans un workspace
    start_app_instance_bus()

    # E4: Migrate legacy .env API keys to DB (idempotent)
    legacy_keys = settings.api_keys_list
    if legacy_keys:
        async with async_session() as db:
            migrated = 0
            for raw_key in legacy_keys:
                key_hash = hash_api_key(raw_key)
                existing = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
                if existing.scalar_one_or_none():
                    continue  # Already migrated
                # Find the first workspace (owner's personal workspace)
                ws_result = await db.execute(
                    select(Workspace).where(Workspace.is_active == True).limit(1)
                )
                ws = ws_result.scalar_one_or_none()
                if not ws:
                    logger.warning("No workspace found — cannot migrate legacy API key.")
                    break
                prefix = raw_key[:8] if len(raw_key) >= 8 else raw_key
                api_key = ApiKey(
                    key_hash=key_hash,
                    key_prefix=prefix,
                    name=f"Legacy key ({prefix}...)",
                    workspace_id=ws.id,
                    created_by=ws.owner_id,
                )
                db.add(api_key)
                migrated += 1
            if migrated:
                await db.commit()
                logger.info(f"{migrated} legacy API key(s) migrated to DB.")

    yield

    # Shutdown
    from chat_broadcaster import broadcaster
    await broadcaster.close()
    await engine.dispose()
    logger.info("HiveFlows Core shut down.")


app = FastAPI(title="HiveFlows Core", version="0.4.0", lifespan=lifespan)

# Rate limiting (K10 security)
from api.routes_auth import limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Session middleware — required by authlib for OAuth2 CSRF state
app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret_key)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(auth_router)
app.include_router(oauth_router)
app.include_router(chat_router)
app.include_router(tasks_router)
app.include_router(agents_router)
app.include_router(audit_router)
app.include_router(dashboard_router)
app.include_router(workspaces_router)
app.include_router(files_router)
app.include_router(api_keys_router)
app.include_router(admin_router)
app.include_router(providers_router)
app.include_router(apps_router)
app.include_router(llm_proxy_router)
app.include_router(portal_router)
app.include_router(portal_devices_router)
app.include_router(tunnel_router)
app.include_router(tools_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard/")


@app.get("/health")
async def health():
    return {"status": "ok"}
