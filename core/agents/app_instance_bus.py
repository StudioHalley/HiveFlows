"""App instance bus — invalide le cache d'agents quand une app démarre ou s'arrête.

Écoute le canal Redis 'hiveflows:app_instance_changed' publié par Beekeeper
après chaque start/stop/restart/remove d'app. Invalide le cache des agents
du workspace concerné pour que le prochain appel recharge les tools à jour.
"""
import json
import logging
import threading

import redis

from config import settings

logger = logging.getLogger("hiveflows")

CHANNEL = "hiveflows:app_instance_changed"
_subscriber_thread: threading.Thread | None = None


def _subscriber_loop():
    """Blocking loop — runs in a daemon thread."""
    from agents.manager import agent_manager

    try:
        r = redis.Redis.from_url(settings.redis_url)
        pubsub = r.pubsub()
        pubsub.subscribe(CHANNEL)
        logger.info(f"App instance bus: subscribed to '{CHANNEL}'.")

        for raw_message in pubsub.listen():
            if raw_message["type"] != "message":
                continue
            try:
                payload = json.loads(raw_message["data"])
                workspace_id = payload.get("workspace_id")
                if not workspace_id:
                    continue
                event = payload.get("event", "changed")
                app_slug = payload.get("app_slug", "?")
                logger.info(
                    f"App instance bus: {event} '{app_slug}' "
                    f"in workspace {workspace_id} — invalidating agent cache."
                )
                agent_manager.invalidate_workspace_agents(workspace_id)
            except Exception as e:
                logger.error(f"App instance bus: error processing message: {e!r}", exc_info=True)
    except Exception as e:
        logger.error(f"App instance bus: subscriber crashed: {e!r}", exc_info=True)


def start_app_instance_bus():
    """Start the background subscriber thread (called once at startup)."""
    global _subscriber_thread
    if _subscriber_thread is not None:
        return

    _subscriber_thread = threading.Thread(
        target=_subscriber_loop,
        daemon=True,
        name="app-instance-bus",
    )
    _subscriber_thread.start()
    logger.info("App instance bus: background subscriber started.")
