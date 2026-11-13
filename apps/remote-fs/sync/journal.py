import aiosqlite


async def get_since(db: aiosqlite.Connection, since_ts: int, exclude_node: str) -> list[dict]:
    """Return all local sync_log entries since since_ts, excluding changes from exclude_node."""
    rows = await db.execute_fetchall(
        "SELECT id, path, operation, hash, mtime, meta, created_at FROM sync_log "
        "WHERE created_at > ? AND node_id != ? ORDER BY created_at ASC",
        (since_ts, exclude_node),
    )
    return [dict(r) for r in rows]


async def get_pending_count(db: aiosqlite.Connection) -> int:
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM sync_log WHERE synced = 0")
    return rows[0][0]
