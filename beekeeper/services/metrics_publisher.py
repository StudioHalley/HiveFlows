"""Metrics Publisher — publie CPU/mémoire des apps toutes les N secondes sur Redis.

Canal : hive:app:{workspace_id}:{app_slug}:metrics
Payload : {"cpu_percent": 2.3, "memory_mb": 145, "memory_limit_mb": 512, "timestamp": "..."}
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

import docker
import redis

logger = logging.getLogger("beekeeper.metrics_publisher")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect_stats(container) -> dict | None:
    """Collecte CPU% et mémoire d'un container (appel bloquant, une seule mesure).

    Retourne None si les stats ne sont pas disponibles.
    """
    try:
        stats = container.stats(stream=False)
    except Exception as e:
        logger.warning(f"Stats error {container.name}: {e}")
        return None

    # ── CPU % ──────────────────────────────────────────────────────────────────
    cpu_usage = stats.get("cpu_stats", {}).get("cpu_usage", {})
    precpu_usage = stats.get("precpu_stats", {}).get("cpu_usage", {})
    cpu_delta = cpu_usage.get("total_usage", 0) - precpu_usage.get("total_usage", 0)
    system_cpu = stats.get("cpu_stats", {}).get("system_cpu_usage", 0)
    presystem_cpu = stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
    system_delta = system_cpu - presystem_cpu

    percpu = cpu_usage.get("percpu_usage") or []
    num_cpus = stats.get("cpu_stats", {}).get("online_cpus") or len(percpu) or 1

    if system_delta > 0:
        cpu_percent = round((cpu_delta / system_delta) * num_cpus * 100.0, 2)
    else:
        cpu_percent = 0.0

    # ── Mémoire ────────────────────────────────────────────────────────────────
    mem_stats = stats.get("memory_stats", {})
    mem_usage = mem_stats.get("usage", 0)
    mem_limit = mem_stats.get("limit", 1)
    # Soustraire le cache (présent dans certaines versions de Docker)
    cache = mem_stats.get("stats", {}).get("cache", 0)
    mem_usage = max(0, mem_usage - cache)

    return {
        "cpu_percent": cpu_percent,
        "memory_mb": round(mem_usage / 1024 / 1024, 1),
        "memory_limit_mb": round(mem_limit / 1024 / 1024, 1),
        "timestamp": _now_iso(),
    }


async def _publish_all_metrics(
    docker_client: docker.DockerClient,
    redis_client: redis.Redis,
) -> None:
    """Collecte et publie les métriques de tous les containers hive-managed running."""
    try:
        containers = docker_client.containers.list(
            filters={"label": "hive.managed=true", "status": "running"}
        )
    except Exception as e:
        logger.warning(f"Impossible de lister les containers: {e}")
        return

    for container in containers:
        labels = container.labels or {}
        workspace_id = labels.get("hive.workspace_id")
        app_slug = labels.get("hive.app_slug")
        if not workspace_id or not app_slug:
            continue

        # _collect_stats est bloquant — exécuter dans un thread
        stats = await asyncio.to_thread(_collect_stats, container)
        if stats is None:
            continue

        channel = f"hive:app:{workspace_id}:{app_slug}:metrics"
        try:
            redis_client.publish(channel, json.dumps(stats))
            logger.debug(
                f"Metrics: {app_slug} ({workspace_id}) "
                f"cpu={stats['cpu_percent']}% mem={stats['memory_mb']}MB"
            )
        except Exception as e:
            logger.warning(f"Redis publish erreur {channel}: {e}")


async def metrics_loop(
    docker_client: docker.DockerClient,
    redis_client: redis.Redis,
    interval: int = 30,
) -> None:
    """Boucle asyncio — publie les métriques toutes les `interval` secondes.

    À lancer via asyncio.create_task() dans le lifespan FastAPI.
    """
    logger.info(f"Metrics publisher démarré (interval={interval}s)")
    while True:
        await asyncio.sleep(interval)
        try:
            await _publish_all_metrics(docker_client, redis_client)
        except Exception as e:
            logger.error(f"Metrics loop erreur: {e}", exc_info=True)
