"""Routes réseaux — POST /networks, DELETE /networks/{workspace_id}, GET /networks."""
import logging

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from services.network_manager import (
    create_workspace_network,
    delete_workspace_network,
    list_workspace_networks,
)

logger = logging.getLogger("beekeeper.routes_networks")

router = APIRouter(prefix="/networks", tags=["networks"])

# Import tardif pour éviter la circularité avec main.py
def _get_docker():
    from main import _docker_client
    return _docker_client


def _get_conn():
    from main import _db_engine
    return _db_engine.connect()


class CreateNetworkRequest(BaseModel):
    workspace_id: str


@router.post("", status_code=status.HTTP_201_CREATED)
def create_network(body: CreateNetworkRequest):
    """Crée le réseau Docker isolé pour un workspace."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")

    try:
        with _get_conn() as conn:
            result = create_workspace_network(docker_client, conn, body.workspace_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.delete("/{workspace_id}", status_code=status.HTTP_200_OK)
def delete_network(workspace_id: str):
    """Supprime le réseau Docker d'un workspace."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")

    try:
        with _get_conn() as conn:
            deleted = delete_workspace_network(docker_client, conn, workspace_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Aucun network pour workspace {workspace_id}")

    return {"status": "deleted", "workspace_id": workspace_id}


@router.get("", status_code=status.HTTP_200_OK)
def get_networks():
    """Liste tous les réseaux workspace actifs."""
    docker_client = _get_docker()
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client non initialisé")

    with _get_conn() as conn:
        return list_workspace_networks(docker_client, conn)
