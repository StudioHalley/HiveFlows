"""Client HTTP interne vers le service Beekeeper.

Usage :
    async with beekeeper_client() as client:
        r = await client.post("/apps/start", json={...})

Pour les appels de streaming (logs SSE), utiliser client.stream().
"""
from contextlib import asynccontextmanager

import httpx

from config import settings


@asynccontextmanager
async def beekeeper_client():
    """Context manager qui fournit un AsyncClient configuré pour le Beekeeper."""
    async with httpx.AsyncClient(
        base_url=settings.beekeeper_url,
        headers={"X-Beekeeper-Key": settings.beekeeper_api_key},
        timeout=httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0),
    ) as client:
        yield client
