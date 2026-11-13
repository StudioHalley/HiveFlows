"""Activity Bus — version sync pour les workers Celery.

Même canal et même format que core/services/activity_bus.py.
Canal : hive:ws:{workspace_id}:activity
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis as redis_lib

logger = logging.getLogger("hiveflows.activity_bus")

REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CHANNEL_TPL = "hive:ws:{workspace_id}:activity"


def activity_channel(workspace_id: str) -> str:
    return CHANNEL_TPL.format(workspace_id=workspace_id)


def publish_activity(
    workspace_id: str | None,
    event_type: str,
    source: str,
    data: dict[str, Any],
    task_id: str | None = None,
) -> None:
    """Publie un événement dans le bus d'activité (sync — pour Celery workers)."""
    if not workspace_id:
        return
    try:
        r = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
        payload = json.dumps(
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
        r.publish(activity_channel(workspace_id), payload)
        r.close()
    except Exception as e:
        logger.warning(f"[activity_bus] publish failed ({event_type}): {e}")
