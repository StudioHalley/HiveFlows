import hashlib
import json
import time
from pathlib import Path

import aiosqlite

from config import cfg, SYNC_ROOT


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def should_ignore(rel: str) -> bool:
    for pat in cfg["sync"].get("ignore", []):
        if pat.endswith("/") and rel.startswith(pat):
            return True
        if pat.startswith("*.") and rel.endswith(pat[1:]):
            return True
    return False


async def upsert(db: aiosqlite.Connection, rel: str, node_id: str) -> dict | None:
    path = SYNC_ROOT / rel
    if not path.is_file():
        return None
    try:
        stat = path.stat()
        h = file_hash(path)
        now = int(time.time())
        await db.execute(
            "INSERT OR REPLACE INTO file_manifest (path, hash, mtime, size, updated_at) VALUES (?, ?, ?, ?, ?)",
            (rel, h, int(stat.st_mtime), stat.st_size, now),
        )
        await db.execute(
            "INSERT INTO sync_log (path, operation, hash, mtime, node_id, synced, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (rel, "update", h, int(stat.st_mtime), node_id, now),
        )
        await db.commit()
        return {"path": rel, "hash": h, "mtime": int(stat.st_mtime), "size": stat.st_size}
    except Exception:
        return None


async def delete(db: aiosqlite.Connection, rel: str, node_id: str) -> None:
    now = int(time.time())
    await db.execute("DELETE FROM file_manifest WHERE path = ?", (rel,))
    await db.execute(
        "INSERT INTO sync_log (path, operation, node_id, synced, created_at) VALUES (?, 'delete', ?, 0, ?)",
        (rel, node_id, now),
    )
    await db.commit()


async def move(db: aiosqlite.Connection, src: str, dst: str, node_id: str) -> None:
    now = int(time.time())
    await db.execute("UPDATE file_manifest SET path = ?, updated_at = ? WHERE path = ?", (dst, now, src))
    await db.execute(
        "INSERT INTO sync_log (path, operation, node_id, synced, meta, created_at) VALUES (?, 'move', ?, 0, ?, ?)",
        (dst, node_id, json.dumps({"src": src}), now),
    )
    await db.commit()


async def get_all(db: aiosqlite.Connection) -> list[dict]:
    rows = await db.execute_fetchall("SELECT path, hash, mtime, size FROM file_manifest ORDER BY path")
    return [dict(r) for r in rows]


def diff(local: list[dict], remote: list[dict]) -> dict:
    local_map = {f["path"]: f for f in local}
    remote_map = {f["path"]: f for f in remote}

    to_download, to_upload, to_delete = [], [], []

    for path, rf in remote_map.items():
        lf = local_map.get(path)
        if lf is None:
            to_download.append(rf)
        elif rf["hash"] != lf["hash"]:
            # remote is newer → download; local is newer → upload
            if rf["mtime"] > lf["mtime"]:
                to_download.append(rf)
            else:
                to_upload.append(lf)

    for path, lf in local_map.items():
        if path not in remote_map:
            to_upload.append(lf)

    return {"to_download": to_download, "to_upload": to_upload, "to_delete": to_delete}
