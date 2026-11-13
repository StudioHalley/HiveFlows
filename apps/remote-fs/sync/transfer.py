import logging

import httpx

from config import WORKSPACE, cfg

logger = logging.getLogger("remote-fs.transfer")

CHUNK_SIZE = cfg["sync"]["chunk_size"]
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


def _headers(token: str) -> dict:
    return {"X-Sync-Token": token} if token else {}


async def download_file(api_url: str, path: str, token: str = "") -> bool:
    dest = WORKSPACE / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".remotetmp")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("GET", f"{api_url}/files/{path}", headers=_headers(token)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(CHUNK_SIZE):
                        f.write(chunk)
        tmp.replace(dest)
        logger.info(f"↓ {path}")
        return True
    except Exception as e:
        tmp.unlink(missing_ok=True)
        logger.error(f"Download failed {path}: {e}")
        return False


async def upload_file(api_url: str, path: str, token: str = "") -> bool:
    src = WORKSPACE / path
    if not src.is_file():
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            with open(src, "rb") as f:
                r = await client.put(f"{api_url}/files/{path}", content=f, headers=_headers(token))
                r.raise_for_status()
        logger.info(f"↑ {path}")
        return True
    except Exception as e:
        logger.error(f"Upload failed {path}: {e}")
        return False
