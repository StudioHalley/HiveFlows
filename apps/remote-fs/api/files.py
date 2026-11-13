import logging
import time
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.auth import verify_token
from config import SYNC_ROOT, cfg
from sync import manifest

logger = logging.getLogger("remote-fs.api")

router = APIRouter(prefix="/files", tags=["files"], dependencies=[Depends(verify_token)])

CHUNK_SIZE = cfg["sync"]["chunk_size"]


def _safe_path(rel: str) -> Path:
    resolved = (SYNC_ROOT / rel).resolve()
    if not str(resolved).startswith(str(SYNC_ROOT)):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    return resolved


def _db(request: Request) -> aiosqlite.Connection:
    return request.app.state.db


# NOTE: /manifest must be declared before /{path:path} to avoid capture conflict
@router.get("/manifest")
async def get_manifest(db: aiosqlite.Connection = Depends(_db)):
    files = await manifest.get_all(db)
    return {
        "node_id": cfg["node"]["id"],
        "count": len(files),
        "files": files,
    }


@router.get("/{path:path}")
async def get_file(
    path: str,
    meta: bool = False,
    db: aiosqlite.Connection = Depends(_db),
):
    safe = _safe_path(path)

    if meta:
        rows = await db.execute_fetchall(
            "SELECT hash, mtime, size FROM file_manifest WHERE path = ?", (path,)
        )
        if not rows:
            raise HTTPException(status_code=404, detail="File not in manifest")
        return dict(rows[0])

    if not safe.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    def _iter():
        with open(safe, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                yield chunk

    return StreamingResponse(
        _iter(),
        media_type="application/octet-stream",
        headers={"X-File-Path": path},
    )


@router.put("/{path:path}", status_code=200)
async def put_file(
    path: str,
    request: Request,
    db: aiosqlite.Connection = Depends(_db),
):
    safe = _safe_path(path)
    safe.parent.mkdir(parents=True, exist_ok=True)

    tmp = safe.with_suffix(safe.suffix + ".tmp")
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        tmp.replace(safe)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        logger.error(f"PUT {path} failed: {e}")
        raise HTTPException(status_code=500, detail="Upload failed")

    node_id = cfg["node"]["id"]
    result = await manifest.upsert(db, path, node_id)
    logger.info(f"PUT {path} → {result['hash'][:8] if result else '?'}...")
    return {"path": path, "status": "ok", "hash": result["hash"] if result else None}


@router.delete("/{path:path}", status_code=200)
async def delete_file(
    path: str,
    db: aiosqlite.Connection = Depends(_db),
):
    safe = _safe_path(path)
    if not safe.exists():
        raise HTTPException(status_code=404, detail="File not found")

    safe.unlink()
    await manifest.delete(db, path, cfg["node"]["id"])
    logger.info(f"DELETE {path}")
    return {"path": path, "status": "deleted"}
