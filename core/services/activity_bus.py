"""Activity Bus — canal Redis unifié pour les événements workspace.

Canal    : hive:ws:{workspace_id}:activity
Format   :
  {
    "type":         "task_created|task_progress|task_done|task_error|task_cancelled
                     |app_started|app_stopped|app_crashed",
    "task_id":      "uuid | null",
    "workspace_id": "uuid",
    "source":       "user:{id} | agent:{id} | system | beekeeper",
    "timestamp":    "ISO-8601",
    "data":         { ... }
  }
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger("hiveflows.activity_bus")

CHANNEL_TPL = "hive:ws:{workspace_id}:activity"


def activity_channel(workspace_id: str) -> str:
    return CHANNEL_TPL.format(workspace_id=workspace_id)


def build_event(
    event_type: str,
    workspace_id: str,
    source: str,
    data: dict[str, Any],
    task_id: str | None = None,
) -> str:
    return json.dumps(
        {
            "type": event_type,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        },
        ensure_ascii=False,
        default=str,
    )


async def publish_activity(
    workspace_id: str,
    event_type: str,
    source: str,
    data: dict[str, Any],
    task_id: str | None = None,
) -> None:
    """Publie un événement dans le bus d'activité (async — pour FastAPI routes)."""
    if not workspace_id:
        return
    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        payload = build_event(event_type, workspace_id, source, data, task_id)
        await r.publish(activity_channel(workspace_id), payload)
        await r.aclose()
    except Exception as e:
        logger.warning(f"[activity_bus] publish failed ({event_type}): {e}")
