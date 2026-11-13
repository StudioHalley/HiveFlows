"""Container Manager — gère le cycle de vie des containers Docker des apps.

Naming convention :
  Container : hive-app-{workspace_id}-{app_slug}
  Network   : hive-ws-{workspace_id}  (géré par network_manager)

Labels Docker posés sur chaque container :
  hive.managed        = "true"
  hive.workspace_id   = <workspace_id>
  hive.app_slug       = <app_slug>
"""
import logging
import os
import threading
from typing import Callable, Optional

import docker
import docker.errors
from cryptography.fernet import Fernet

logger = logging.getLogger("beekeeper.container_manager")

_HIVE_LABELS = {
    "hive.managed": "true",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _container_name(workspace_id: str, app_slug: str) -> str:
    return f"hive-app-{workspace_id}-{app_slug}"


def _network_name(workspace_id: str) -> str:
    return f"hive-ws-{workspace_id}"


def _validate_mount_path(host_path: str, workspace_root: str) -> str:
    """Valide que host_path ne sort pas de workspace_root (protection path traversal).

    host_path peut être relatif (ex: ".hive/code") ou absolu.
    Retourne le chemin absolu validé.
    """
    if os.path.isabs(host_path):
        abs_path = os.path.normpath(host_path)
    else:
        abs_path = os.path.normpath(os.path.join(workspace_root, host_path))

    # Vérifier que le chemin reste dans workspace_root
    real_root = os.path.realpath(workspace_root)
    real_path = os.path.realpath(abs_path)
    if not real_path.startswith(real_root + os.sep) and real_path != real_root:
        raise ValueError(
            f"Path traversal détecté : {host_path!r} sort de {workspace_root!r}"
        )
    return abs_path


def _decrypt_env(encrypted_json: str, fernet_key: str) -> dict:
    """Déchiffre les env vars stockées chiffrées (Fernet)."""
    import json
    f = Fernet(fernet_key.encode())
    plaintext = f.decrypt(encrypted_json.encode()).decode()
    return json.loads(plaintext)


# ── API publique ──────────────────────────────────────────────────────────────

def start_app(
    docker_client: docker.DockerClient,
    workspace_id: str,
    app_slug: str,
    image: str,
    workspace_slug: Optional[str] = None,
    port: Optional[int] = None,
    env_values: Optional[dict] = None,
    encrypted_env: Optional[str] = None,
    fernet_key: Optional[str] = None,
    mounts: Optional[list[dict]] = None,
    resources: Optional[dict] = None,
    workspace_data_dir: str = "/workspace",
    workspace_data_host_dir: Optional[str] = None,
    extra_labels: Optional[dict] = None,
    env_inject: Optional[dict] = None,
    backend_network: Optional[str] = None,
) -> dict:
    """Crée et démarre un container pour une app dans le réseau du workspace.

    Args:
        env_values             : dict plaintext (prioritaire sur encrypted_env)
        encrypted_env          : JSON chiffré Fernet (si env_values absent)
        fernet_key             : clé Fernet pour déchiffrer encrypted_env
        mounts                 : [{host: ".hive/code", container: "/workspace"}]
        resources              : {memory: "512m", cpus: "1.0"}
        workspace_data_dir     : chemin interne au container beekeeper (pour os.makedirs)
        workspace_data_host_dir: chemin HOST absolu (pour les specs volumes Docker).
                                 Si None, fallback sur workspace_data_dir.
    """
    name = _container_name(workspace_id, app_slug)
    network = _network_name(workspace_id)
    ws_dir = workspace_slug or workspace_id
    # Chemin interne beekeeper — utilisé pour os.makedirs
    workspace_root = os.path.join(workspace_data_dir, ws_dir)
    # Chemin hôte — utilisé comme clé dans les specs volumes Docker
    workspace_root_host = os.path.join(workspace_data_host_dir or workspace_data_dir, ws_dir)

    # ── Idempotence : si le container existe déjà, retourner son état ──
    try:
        existing = docker_client.containers.get(name)
        if existing.status == "running":
            logger.info(f"Container {name} déjà running — skip")
            return _container_info(existing)
        # Existe mais arrêté → on le redémarre
        existing.start()
        existing.reload()
        logger.info(f"Container {name} redémarré (était {existing.status})")
        return _container_info(existing)
    except docker.errors.NotFound:
        pass

    # ── Env vars ──
    env: dict = {}
    if env_values:
        env = dict(env_values)
    elif encrypted_env and fernet_key:
        env = _decrypt_env(encrypted_env, fernet_key)

    # Inject manifest env vars (don't override user-provided values)
    if env_inject:
        for k, v in env_inject.items():
            env.setdefault(k, v)

    # ── Resources ──
    mem_limit = None
    nano_cpus = None
    user_spec = None
    if resources:
        if "memory" in resources:
            mem_limit = resources["memory"]  # ex: "512m"
        if "cpus" in resources:
            try:
                nano_cpus = int(float(resources["cpus"]) * 1e9)
            except (ValueError, TypeError):
                pass
        if "user" in resources:
            user_spec = str(resources["user"])  # ex: "1000:1000"

    # ── Volumes ──
    volumes: dict = {}
    for mount in (mounts or []):
        host_path = mount.get("host", "")
        container_path = mount.get("container", "")
        if not host_path or not container_path:
            continue
        try:
            # abs_internal = chemin absolu dans le container beekeeper (pour makedirs)
            abs_internal = _validate_mount_path(host_path, workspace_root)
        except ValueError as e:
            raise RuntimeError(str(e)) from e
        # Créer le répertoire via le chemin interne (accessible au beekeeper)
        os.makedirs(abs_internal, exist_ok=True)
        # Chown au user du container si spécifié (ex: coder=1000:1000 pour code-server)
        if user_spec:
            try:
                parts = user_spec.split(":")
                uid = int(parts[0])
                gid = int(parts[1]) if len(parts) > 1 else uid
                os.chown(abs_internal, uid, gid)
            except (ValueError, AttributeError, PermissionError) as e:
                logger.warning(f"Impossible de chown {abs_internal} vers {user_spec}: {e}")
        # Chemin HOST à passer à Docker (relatif à workspace_root_host)
        rel = os.path.relpath(abs_internal, workspace_root)
        abs_host_docker = os.path.normpath(os.path.join(workspace_root_host, rel))
        volumes[abs_host_docker] = {"bind": container_path, "mode": "rw"}

    # ── Labels ──
    labels = {
        **_HIVE_LABELS,
        "hive.workspace_id": workspace_id,
        "hive.app_slug": app_slug,
    }
    if port is not None:
        labels["hive.app_port"] = str(port)
    if extra_labels:
        labels.update(extra_labels)

    # ── Créer le container ──
    kwargs: dict = dict(
        image=image,
        name=name,
        detach=True,
        network=network,
        environment=env,
        volumes=volumes,
        labels=labels,
        restart_policy={"Name": "unless-stopped"},
    )
    if mem_limit:
        kwargs["mem_limit"] = mem_limit
    if nano_cpus:
        kwargs["nano_cpus"] = nano_cpus
    if user_spec:
        kwargs["user"] = user_spec

    try:
        container = docker_client.containers.run(**kwargs)
        container.reload()
        logger.info(f"Container démarré: {name} ({image}) status={container.status}")
    except docker.errors.ImageNotFound:
        # Image absente du daemon local → tenter un build depuis /apps/{app_slug}/
        logger.info(f"Image {image!r} introuvable — tentative de build local pour {app_slug!r}")
        try:
            build_image_local(docker_client, app_slug)
        except RuntimeError as build_err:
            raise RuntimeError(
                f"Image {image!r} introuvable et build local échoué: {build_err}"
            ) from build_err
        # Réessayer après le build
        try:
            container = docker_client.containers.run(**kwargs)
            container.reload()
            logger.info(f"Container démarré après build: {name} ({image}) status={container.status}")
        except docker.errors.APIError as e:
            raise RuntimeError(f"Erreur Docker start {name} (post-build): {e}") from e
    except docker.errors.APIError as e:
        raise RuntimeError(f"Erreur Docker start {name}: {e}") from e

    # Connecter au réseau backend partagé (apps de type "backend" accessibles depuis core)
    if backend_network:
        try:
            net = docker_client.networks.get(backend_network)
            net.connect(container)
            container.reload()
            logger.info(f"Container {name} connecté au réseau {backend_network}")
        except docker.errors.NotFound:
            logger.warning(f"Réseau {backend_network!r} introuvable — app non accessible depuis core")
        except docker.errors.APIError as e:
            logger.warning(f"Impossible de connecter {name} à {backend_network}: {e}")

    return _container_info(container)


def stop_app(docker_client: docker.DockerClient, container_name: str, timeout: int = 10) -> dict:
    """Arrête un container (SIGTERM → SIGKILL après timeout)."""
    try:
        container = docker_client.containers.get(container_name)
        container.stop(timeout=timeout)
        container.reload()
        logger.info(f"Container arrêté: {container_name}")
        return _container_info(container)
    except docker.errors.NotFound:
        raise RuntimeError(f"Container {container_name!r} introuvable")
    except docker.errors.APIError as e:
        raise RuntimeError(f"Erreur Docker stop {container_name}: {e}") from e


def restart_app(docker_client: docker.DockerClient, container_name: str, timeout: int = 10) -> dict:
    """Redémarre un container."""
    try:
        container = docker_client.containers.get(container_name)
        container.restart(timeout=timeout)
        container.reload()
        logger.info(f"Container redémarré: {container_name}")
        return _container_info(container)
    except docker.errors.NotFound:
        raise RuntimeError(f"Container {container_name!r} introuvable")
    except docker.errors.APIError as e:
        raise RuntimeError(f"Erreur Docker restart {container_name}: {e}") from e


def remove_app(docker_client: docker.DockerClient, container_name: str, force: bool = True) -> bool:
    """Arrête et supprime un container."""
    try:
        container = docker_client.containers.get(container_name)
        container.remove(force=force)
        logger.info(f"Container supprimé: {container_name}")
        return True
    except docker.errors.NotFound:
        logger.warning(f"Container {container_name!r} déjà absent")
        return False
    except docker.errors.APIError as e:
        raise RuntimeError(f"Erreur Docker remove {container_name}: {e}") from e


def get_workspace_containers(docker_client: docker.DockerClient, workspace_id: str) -> list[dict]:
    """Liste tous les containers hive-managed d'un workspace."""
    try:
        containers = docker_client.containers.list(
            all=True,
            filters={
                "label": [
                    "hive.managed=true",
                    f"hive.workspace_id={workspace_id}",
                ]
            },
        )
        return [_container_info(c) for c in containers]
    except docker.errors.APIError as e:
        raise RuntimeError(f"Erreur Docker list {workspace_id}: {e}") from e


def build_image_local(
    docker_client: docker.DockerClient,
    app_slug: str,
    apps_dir: str = "/apps",
    tag: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> str:
    """Construit une image locale depuis /apps/{app_slug}/Dockerfile.

    Retourne le tag de l'image buildée.
    Lève RuntimeError si le Dockerfile est introuvable ou si le build échoue.
    """
    build_path = os.path.join(apps_dir, app_slug)
    dockerfile = os.path.join(build_path, "Dockerfile")
    if not os.path.exists(dockerfile):
        raise RuntimeError(f"Dockerfile introuvable pour {app_slug!r} dans {build_path}")

    image_tag = tag or f"hiveflows/{app_slug}:latest"
    logger.info(f"Build image locale: {image_tag} depuis {build_path}")

    try:
        _, logs = docker_client.images.build(
            path=build_path,
            tag=image_tag,
            rm=True,
            forcerm=True,
        )
        for entry in logs:
            line = entry.get("stream", "").rstrip()
            if line:
                logger.debug(f"[build {app_slug}] {line}")
                if on_progress:
                    on_progress(line)
        logger.info(f"Build terminé: {image_tag}")
        return image_tag
    except docker.errors.BuildError as e:
        raise RuntimeError(f"Build échoué pour {app_slug}: {e}") from e


def pull_image(
    docker_client: docker.DockerClient,
    image: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Pull une image Docker (bloquant — à appeler dans un thread background).

    on_progress(status_line) est appelé pour chaque ligne de progression.
    """
    logger.info(f"Pull image: {image}")
    for line in docker_client.api.pull(image, stream=True, decode=True):
        status = line.get("status", "")
        progress = line.get("progress", "")
        msg = f"{status} {progress}".strip()
        if on_progress:
            on_progress(msg)
    logger.info(f"Pull terminé: {image}")


def pull_image_background(
    docker_client: docker.DockerClient,
    image: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> threading.Thread:
    """Lance le pull en arrière-plan. Retourne le thread."""
    t = threading.Thread(
        target=pull_image,
        args=(docker_client, image, on_progress),
        daemon=True,
        name=f"pull-{image.split('/')[-1][:20]}",
    )
    t.start()
    return t


# ── Helpers internes ──────────────────────────────────────────────────────────

def _container_info(container) -> dict:
    """Extrait les infos utiles d'un objet Container Docker SDK."""
    attrs = container.attrs or {}
    net_settings = attrs.get("NetworkSettings", {})
    # Trouver l'IP dans le réseau hive-ws-*
    ip = None
    for net_name, net_data in net_settings.get("Networks", {}).items():
        if net_name.startswith("hive-ws-"):
            ip = net_data.get("IPAddress")
            break
    if ip is None:
        # Fallback : première IP disponible
        for net_data in net_settings.get("Networks", {}).values():
            ip = net_data.get("IPAddress")
            if ip:
                break

    labels = attrs.get("Config", {}).get("Labels", {})
    return {
        "container_name": container.name,
        "container_id": container.id[:12] if container.id else None,
        "status": container.status,
        "image": attrs.get("Config", {}).get("Image", ""),
        "workspace_id": labels.get("hive.workspace_id"),
        "app_slug": labels.get("hive.app_slug"),
        "container_ip": ip,
        "created": attrs.get("Created", "")[:19].replace("T", " ") if attrs.get("Created") else None,
    }
