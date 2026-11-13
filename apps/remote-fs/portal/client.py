import logging

import httpx

logger = logging.getLogger("remote-fs.portal")


async def get_peers(portal_url: str, workspace_id: str, api_key: str) -> list[dict]:
    """
    Query Portal API for online devices in the workspace.
    Returns peers formatted for the connector:
      {"id": slug, "ws_url": "ws://...", "api_url": "http://..."}

    Requires the core to expose GET /internal/portal/peers — a lightweight
    internal endpoint that lists connected devices with remote-fs capability.
    Falls back to empty list on any error so static peers still work.
    """
    if not portal_url or not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{portal_url}/internal/portal/peers",
                params={"workspace_id": workspace_id, "service": "remote-fs"},
                headers={"X-Internal-Key": api_key},
            )
            if r.status_code != 200:
                logger.warning(f"Portal peers returned {r.status_code}")
                return []
            return r.json().get("peers", [])
    except Exception as e:
        logger.warning(f"Portal peer discovery failed: {e}")
        return []
