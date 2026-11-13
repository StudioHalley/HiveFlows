"""Network Manager — gère les Docker networks isolés par workspace.

Chaque workspace obtient un réseau Docker dédié :
  - Nom     : hive-ws-{workspace_id}
  - Subnet  : 172.20.{n}.0/24  (n alloué automatiquement depuis la DB)
  - Gateway : 172.20.{n}.1

Labels Docker posés sur chaque network :
  - hive.managed = "true"
  - hive.workspace_id = <workspace_id>
"""
import logging
from typing import Optional

import docker
import docker.errors
import sqlalchemy as sa

logger = logging.getLogger("beekeeper.network_manager")

# Subnet pool : 10.20.1.0/24 … 10.20.254.0/24 → 254 workspaces
# (évite les conflits avec les réseaux Docker existants en 172.x)
_SUBNET_BASE = "10.20.{n}.0/24"
_GATEWAY_BASE = "10.20.{n}.1"
_SUBNET_START = 1
_SUBNET_END = 254


def _network_name(workspace_id: str) -> str:
    return f"hive-ws-{workspace_id}"


def _next_subnet_index(conn: sa.Connection) -> int:
    """Retourne le prochain index de subnet disponible (1-254)."""
    used: list[int] = []
    rows = conn.execute(
        sa.text("SELECT subnet FROM workspace_networks ORDER BY subnet")
    ).fetchall()
    for (subnet,) in rows:
        # "172.20.N.0/24" → extraire N
        try:
            n = int(subnet.split(".")[2])
            used.append(n)
        except (IndexError, ValueError):
            pass

    for n in range(_SUBNET_START, _SUBNET_END + 1):
        if n not in used:
            return n

    raise RuntimeError("Subnet pool épuisé — plus de /24 disponibles dans 172.20.0.0/16")


def create_workspace_network(
    docker_client: docker.DockerClient,
    conn: sa.Connection,
    workspace_id: str,
) -> dict:
    """Crée le réseau Docker pour un workspace et persiste les infos en DB.

    Retourne un dict avec : network_name, subnet, gateway, docker_network_id.
    Idempotent : si le réseau existe déjà en DB et dans Docker, retourne les infos existantes.
    """
    net_name = _network_name(workspace_id)

    # ── Idempotence : vérifier en DB d'abord ──
    existing = conn.execute(
        sa.text("SELECT network_name, subnet, gateway, docker_network_id FROM workspace_networks WHERE workspace_id = :wid"),
        {"wid": workspace_id},
    ).fetchone()
    if existing:
        logger.info(f"Network déjà existant pour workspace {workspace_id}: {existing[0]}")
        return {
            "network_name": existing[0],
            "subnet": existing[1],
            "gateway": existing[2],
            "docker_network_id": existing[3],
        }

    # ── Allouer le subnet ──
    n = _next_subnet_index(conn)
    subnet = _SUBNET_BASE.format(n=n)
    gateway = _GATEWAY_BASE.format(n=n)

    # ── Créer le network Docker ──
    try:
        net = docker_client.networks.create(
            name=net_name,
            driver="bridge",
            ipam=docker.types.IPAMConfig(
                pool_configs=[
                    docker.types.IPAMPool(subnet=subnet, gateway=gateway)
                ]
            ),
            labels={
                "hive.managed": "true",
                "hive.workspace_id": workspace_id,
            },
        )
        docker_network_id = net.id
        logger.info(f"Network Docker créé: {net_name} ({subnet}) id={docker_network_id[:12]}")
    except docker.errors.APIError as e:
        raise RuntimeError(f"Erreur Docker lors de la création du network {net_name}: {e}") from e

    # ── Persister en DB ──
    try:
        conn.execute(
            sa.text(
                """
                INSERT INTO workspace_networks (workspace_id, network_name, subnet, gateway, docker_network_id)
                VALUES (:wid, :net_name, :subnet, :gateway, :docker_id)
                """
            ),
            {
                "wid": workspace_id,
                "net_name": net_name,
                "subnet": subnet,
                "gateway": gateway,
                "docker_id": docker_network_id,
            },
        )
        conn.commit()
    except Exception as db_err:
        # Rollback Docker si l'INSERT DB échoue
        conn.rollback()
        try:
            net = docker_client.networks.get(docker_network_id)
            net.remove()
            logger.warning(f"Network Docker {net_name} supprimé après échec DB: {db_err}")
        except Exception:
            pass
        raise RuntimeError(f"Échec persistance DB pour {net_name}: {db_err}") from db_err

    return {
        "network_name": net_name,
        "subnet": subnet,
        "gateway": gateway,
        "docker_network_id": docker_network_id,
    }


def delete_workspace_network(
    docker_client: docker.DockerClient,
    conn: sa.Connection,
    workspace_id: str,
) -> bool:
    """Supprime le réseau Docker d'un workspace et retire l'entrée DB.

    Retourne True si supprimé, False si le network n'existait pas.
    """
    net_name = _network_name(workspace_id)

    row = conn.execute(
        sa.text("SELECT docker_network_id FROM workspace_networks WHERE workspace_id = :wid"),
        {"wid": workspace_id},
    ).fetchone()

    if not row:
        logger.info(f"Aucun network en DB pour workspace {workspace_id}")
        return False

    docker_network_id: Optional[str] = row[0]

    # ── Supprimer dans Docker ──
    if docker_network_id:
        try:
            net = docker_client.networks.get(docker_network_id)
            net.remove()
            logger.info(f"Network Docker supprimé: {net_name} ({docker_network_id[:12]})")
        except docker.errors.NotFound:
            logger.warning(f"Network Docker {net_name} introuvable (déjà supprimé ?)")
        except docker.errors.APIError as e:
            raise RuntimeError(f"Erreur Docker lors de la suppression de {net_name}: {e}") from e

    # ── Supprimer en DB ──
    conn.execute(
        sa.text("DELETE FROM workspace_networks WHERE workspace_id = :wid"),
        {"wid": workspace_id},
    )
    conn.commit()
    return True


def list_workspace_networks(
    docker_client: docker.DockerClient,
    conn: sa.Connection,
) -> list[dict]:
    """Liste tous les networks workspace avec leur statut Docker réel."""
    rows = conn.execute(
        sa.text(
            "SELECT workspace_id, network_name, subnet, gateway, docker_network_id, created_at "
            "FROM workspace_networks ORDER BY created_at"
        )
    ).fetchall()

    result = []
    for row in rows:
        workspace_id, net_name, subnet, gateway, docker_id, created_at = row
        # Vérifier l'état réel dans Docker
        docker_status = "unknown"
        if docker_id:
            try:
                docker_client.networks.get(docker_id)
                docker_status = "active"
            except docker.errors.NotFound:
                docker_status = "missing"

        result.append({
            "workspace_id": str(workspace_id),
            "network_name": net_name,
            "subnet": subnet,
            "gateway": gateway,
            "docker_network_id": docker_id,
            "docker_status": docker_status,
            "created_at": created_at.isoformat() if created_at else None,
        })

    return result
