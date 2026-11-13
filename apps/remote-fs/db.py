import asyncio
import hashlib
import logging
import time
from pathlib import Path

import aiosqlite

from config import DB_PATH, SYNC_ROOT, cfg

logger = logging.getLogger("remote-fs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    NOT NULL,
    operation   TEXT    NOT NULL,
    hash        TEXT,
    mtime       INTEGER,
    node_id     TEXT,
    synced      INTEGER DEFAULT 0,
    meta        TEXT,
    created_at  INTEGER
);

CREATE TABLE IF NOT EXISTS file_manifest (
    path        TEXT    PRIMARY KEY,
    hash        TEXT    NOT NULL,
    mtime       INTEGER NOT NULL,
    size        INTEGER,
    updated_at  INTEGER
);

CREATE TABLE IF NOT EXISTS peers (
    id          TEXT    PRIMARY KEY,
    ws_url      TEXT,
    api_url     TEXT,
    last_seen   INTEGER,
    status      TEXT    DEFAULT 'offline'
);
"""


def _should_ignore(rel: str) -> bool:
    for pat in cfg["sync"].get("ignore", []):
        if pat.endswith("/") and rel.startswith(pat):
            return True
        if pat.startswith("*.") and rel.endswith(pat[1:]):
            return True
    return False


def _file_hash_sync(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def init(db: aiosqlite.Connection) -> None:
    """Fast init: schema + migration only. Scan is a separate background task."""
    await db.executescript(SCHEMA)
    try:
        await db.execute("ALTER TABLE sync_log ADD COLUMN meta TEXT")
        await db.commit()
    except Exception:
        pass  # column already exists


async def initial_scan(db: aiosqlite.Connection) -> None:
    """Background task: index workspace files if manifest is empty."""
    row = await db.execute_fetchall("SELECT COUNT(*) FROM file_manifest")
    if row[0][0] > 0:
        return

    logger.info(f"Initial scan — indexing {SYNC_ROOT} (background)...")
    workspace = SYNC_ROOT
    scanned = 0
    batch = []
    now = int(time.time())

    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(workspace))
        if _should_ignore(rel):
            continue
        try:
            stat = path.stat()
            # Run blocking hash in thread so we don't starve the event loop
            file_hash = await asyncio.to_thread(_file_hash_sync, path)
            batch.append((rel, file_hash, int(stat.st_mtime), stat.st_size, now))
            scanned += 1
            # Commit in batches of 500 to avoid holding one huge transaction
            if len(batch) >= 500:
                await db.executemany(
                    "INSERT OR REPLACE INTO file_manifest (path, hash, mtime, size, updated_at) VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                await db.commit()
                batch.clear()
                logger.info(f"  ... {scanned} files indexed")
        except Exception as e:
            logger.warning(f"Skip {rel}: {e}")

    if batch:
        await db.executemany(
            "INSERT OR REPLACE INTO file_manifest (path, hash, mtime, size, updated_at) VALUES (?, ?, ?, ?, ?)",
            batch,
        )
        await db.commit()

    logger.info(f"Initial scan complete — {scanned} files indexed")


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db
