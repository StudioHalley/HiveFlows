"""Agent message bus — Redis pub/sub subscriber for async agent-to-agent messages (F4).

Runs in a background thread, listens for messages on 'hiveflows:agent_messages',
and invokes the target agent via agent_manager.invoke().
"""

import json
import logging
import threading

import redis

from config import settings

logger = logging.getLogger("hiveflows")

_CHANNEL = "hiveflows:agent_messages"
_subscriber_thread: threading.Thread | None = None


def _subscriber_loop():
    """Blocking loop — runs in a daemon thread."""
    from agents.manager import agent_manager

    try:
        r = redis.Redis.from_url(settings.redis_url)
        pubsub = r.pubsub()
        pubsub.subscribe(_CHANNEL)
        logger.info(f"Agent bus: subscribed to '{_CHANNEL}'.")

        for raw_message in pubsub.listen():
            if raw_message["type"] != "message":
                continue
            try:
                payload = json.loads(raw_message["data"])
                agent_id = payload["agent_id"]
                message = payload["message"]
                workspace_id = payload.get("workspace_id")

                logger.info(f"Agent bus: delivering async message to '{agent_id}' (workspace={workspace_id})")
                import asyncio
                asyncio.run(agent_manager.invoke(
                    agent_id=agent_id,
                    message=message,
                    workspace_id=workspace_id,
                ))
                logger.info(f"Agent bus: '{agent_id}' processed async message.")
            except Exception as e:
                logger.error(f"Agent bus: error processing message: {e!r}", exc_info=True)
    except Exception as e:
        logger.error(f"Agent bus: subscriber crashed: {e!r}", exc_info=True)


def start_agent_bus():
    """Start the background subscriber thread (called once at startup)."""
    global _subscriber_thread
    if _subscriber_thread is not None:
        return

    _subscriber_thread = threading.Thread(target=_subscriber_loop, daemon=True, name="agent-bus")
    _subscriber_thread.start()
    logger.info("Agent bus: background subscriber started.")
