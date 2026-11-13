"""Redis pub/sub broadcaster for real-time workspace chat events.

All connected users in a workspace receive events via a persistent SSE
connection that subscribes to the workspace's Redis channel.
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger("hiveflows.broadcaster")

_CHANNEL_PREFIX = "hiveflows:ws:"
_CHANNEL_SUFFIX = ":chat"


def _channel(workspace_id: str) -> str:
    return f"{_CHANNEL_PREFIX}{workspace_id}{_CHANNEL_SUFFIX}"


class ChatBroadcaster:
    """Singleton Redis pub/sub broadcaster for workspace chat."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _ensure_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url, decode_responses=True
            )
        return self._redis

    async def publish(self, workspace_id: str, event: dict) -> None:
        """Publish an event to a workspace chat channel."""
        r = await self._ensure_redis()
        payload = json.dumps(event, ensure_ascii=False, default=str)
        await r.publish(_channel(workspace_id), payload)

    async def subscribe(self, workspace_id: str):
        """Async generator yielding parsed event dicts from the channel.

        The caller must consume this inside an async for and handle cleanup
        (generator will unsubscribe on finalization).
        """
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(_channel(workspace_id))
        try:
            while True:
                raw = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if raw is None:
                    yield None  # heartbeat signal
                    continue
                try:
                    yield json.loads(raw["data"])
                except (json.JSONDecodeError, KeyError):
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(_channel(workspace_id))
            await pubsub.close()
            await r.close()

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None


broadcaster = ChatBroadcaster()
