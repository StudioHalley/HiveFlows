"""Docker Events Listener — traduit les events Docker en messages Redis Pub/Sub.

Filtre uniquement les containers labellisés hive.managed=true.
Publie sur :
  hive:app:{workspace_id}:{app_slug}:status  → changements d'état (inchangé)
  hive:ws:{workspace_id}:activity            → bus d'activité unifié (Phase 7)
"""
import json
import logging
import threading
from datetime import datetime, timezone

import docker
import redis

logger = logging.getLogger("beekeeper.event_listener")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publish_status(
    redis_client: redis.Redis,
    workspace_id: str,
    app_slug: str,
    payload: dict,
) -> None:
    channel = f"hive:app:{workspace_id}:{app_slug}:status"
    redis_client.publish(channel, json.dumps(payload))


def _publish_activity(
    redis_client: redis.Redis,
    workspace_id: str,
    event_type: str,
    app_slug: str,
    extra: dict | None = None,
) -> None:
    """Publie un événement app dans le bus d'activité unifié."""
    channel = f"hive:ws:{workspace_id}:activity"
    payload = json.dumps({
        "type": event_type,
        "task_id": None,
        "workspace_id": workspace_id,
        "source": "beekeeper",
        "timestamp": _now_iso(),
        "data": {"app": app_slug, **(extra or {})},
    })
    redis_client.publish(channel, payload)


def _handle_event(event: dict, redis_client: redis.Redis) -> None:
    """Traite un event Docker container et publie sur Redis."""
    if event.get("Type") != "container":
        return

    attrs = event.get("Actor", {}).get("Attributes", {})
    workspace_id = attrs.get("hive.workspace_id")
    app_slug = attrs.get("hive.app_slug")
    if not workspace_id or not app_slug:
        return

    action = event.get("Action", "")
    ts = _now_iso()

    if action == "start":
        _publish_status(redis_client, workspace_id, app_slug, {
            "status": "running", "timestamp": ts,
        })
        _publish_activity(redis_client, workspace_id, "app_started", app_slug)
        logger.info(f"Event: {app_slug} ({workspace_id}) → running")

    elif action == "die":
        exit_code = attrs.get("exitCode", "unknown")
        hive_status = "crashed" if str(exit_code) not in ("0",) else "stopped"
        _publish_status(redis_client, workspace_id, app_slug, {
            "status": hive_status, "exit_code": exit_code, "timestamp": ts,
        })
        event_type = f"app_{hive_status}"
        _publish_activity(redis_client, workspace_id, event_type, app_slug,
                          extra={"exit_code": exit_code})
        logger.info(f"Event: {app_slug} ({workspace_id}) → {hive_status} (exit={exit_code})")

    elif action in ("stop", "kill"):
        _publish_status(redis_client, workspace_id, app_slug, {
            "status": "stopped", "timestamp": ts,
        })
        _publish_activity(redis_client, workspace_id, "app_stopped", app_slug)
        logger.info(f"Event: {app_slug} ({workspace_id}) → stopped ({action})")

    elif action == "pause":
        _publish_status(redis_client, workspace_id, app_slug, {
            "status": "paused", "timestamp": ts,
        })

    elif action == "unpause":
        _publish_status(redis_client, workspace_id, app_slug, {
            "status": "running", "timestamp": ts,
        })


def listen_docker_events(
    docker_client: docker.DockerClient,
    redis_client: redis.Redis,
) -> None:
    """Boucle bloquante — à appeler dans un thread background.

    Filtre automatiquement les containers hive-managed via le filtre label Docker.
    """
    logger.info("Event listener démarré — écoute des events Docker")
    try:
        for event in docker_client.events(
            decode=True,
            filters={"label": "hive.managed=true"},
        ):
            try:
                _handle_event(event, redis_client)
            except Exception as e:
                logger.warning(f"Erreur traitement event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Event listener arrêté suite à une erreur: {e}", exc_info=True)


def start_event_listener(
    docker_client: docker.DockerClient,
    redis_client: redis.Redis,
) -> threading.Thread:
    """Lance l'event listener dans un thread daemon. Retourne le thread."""
    t = threading.Thread(
        target=listen_docker_events,
        args=(docker_client, redis_client),
        daemon=True,
        name="docker-event-listener",
    )
    t.start()
    logger.info(f"Event listener thread démarré: {t.name}")
    return t
