import json
import logging
import time
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import SYNC_ROOT, cfg
from sync import journal, manifest, transfer
from sync.peers import ConnectionManager

logger = logging.getLogger("remote-fs.ws")

router = APIRouter(tags=["sync"])

NODE_ID = cfg["node"]["id"]
_TOKEN = cfg["sync"].get("secret", "")
_WS_ROOT = str(SYNC_ROOT)


def _safe(path: str) -> str | None:
    resolved = str((SYNC_ROOT / path).resolve())
    return resolved if resolved.startswith(_WS_ROOT) else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delta_sync(db: aiosqlite.Connection, peer_api: str, peer_files: list[dict], syncing: set) -> None:
    local_files = await manifest.get_all(db)
    delta = manifest.diff(local_files, peer_files)
    if not delta["to_download"]:
        return
    logger.info(f"Delta sync: {len(delta['to_download'])} file(s) to pull")
    for f in delta["to_download"]:
        path = f["path"]
        syncing.add(path)
        try:
            ok = await transfer.download_file(peer_api, path, _TOKEN)
            if ok:
                await manifest.upsert(db, path, NODE_ID)
        finally:
            syncing.discard(path)


async def _process_catch_up(entries: list[dict], db: aiosqlite.Connection, peer_api: str, syncing: set) -> None:
    if not entries:
        return
    logger.info(f"Catch-up: processing {len(entries)} journal entries")
    for entry in entries:
        path = entry.get("path", "")
        op = entry.get("operation", "")

        if op == "update":
            rows = await db.execute_fetchall(
                "SELECT hash FROM file_manifest WHERE path = ?", (path,)
            )
            local_hash = rows[0]["hash"] if rows else None
            if local_hash != entry.get("hash"):
                syncing.add(path)
                try:
                    ok = await transfer.download_file(peer_api, path, _TOKEN)
                    if ok:
                        await manifest.upsert(db, path, NODE_ID)
                finally:
                    syncing.discard(path)

        elif op == "delete":
            safe = _safe(path)
            if safe and __import__("pathlib").Path(safe).is_file():
                __import__("pathlib").Path(safe).unlink()
            await manifest.delete(db, path, NODE_ID)

        elif op == "move":
            meta = json.loads(entry.get("meta") or "{}")
            src = meta.get("src", "")
            src_abs = _safe(src)
            dst_abs = _safe(path)
            if src_abs and dst_abs:
                dst = Path(dst_abs)
                dst.parent.mkdir(parents=True, exist_ok=True)
                src_p = Path(src_abs)
                if src_p.is_file():
                    src_p.rename(dst)
            await manifest.move(db, src, path, NODE_ID)


async def _handle_event(msg: dict, db: aiosqlite.Connection, peer_api: str, syncing: set) -> None:
    t = msg.get("type")
    path = msg.get("path", "")

    if t == "CATCH_UP":
        await _process_catch_up(msg.get("entries", []), db, peer_api, syncing)

    elif t == "FILE_CHANGED":
        rows = await db.execute_fetchall(
            "SELECT hash FROM file_manifest WHERE path = ?", (path,)
        )
        local_hash = rows[0]["hash"] if rows else None
        if local_hash != msg.get("hash"):
            syncing.add(path)
            try:
                ok = await transfer.download_file(peer_api, path, _TOKEN)
                if ok:
                    await manifest.upsert(db, path, NODE_ID)
            finally:
                syncing.discard(path)

    elif t == "FILE_DELETED":
        safe = _safe(path)
        if safe and Path(safe).is_file():
            Path(safe).unlink()
        await manifest.delete(db, path, NODE_ID)

    elif t == "FILE_MOVED":
        src = msg.get("src", "")
        src_abs = _safe(src)
        dst_abs = _safe(path)
        if src_abs and dst_abs:
            from pathlib import Path as P
            dst = P(dst_abs)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src_p = P(src_abs)
            if src_p.is_file():
                src_p.rename(dst)
        await manifest.move(db, src, path, NODE_ID)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/sync")
async def ws_sync(websocket: WebSocket):
    await websocket.accept()

    state = websocket.app.state
    db: aiosqlite.Connection = state.db
    connections: ConnectionManager = state.connections
    syncing: set = state.syncing

    peer_id = None
    peer_api = ""

    try:
        # Step 1 — HELLO
        raw = await websocket.receive_text()
        msg = json.loads(raw)
        if msg.get("type") != "HELLO":
            await websocket.close(code=1002, reason="Expected HELLO")
            return

        peer_id = msg["node_id"]
        peer_api = msg.get("api_url", "")

        # Read last_seen BEFORE updating — needed for catch-up window
        rows = await db.execute_fetchall("SELECT last_seen FROM peers WHERE id = ?", (peer_id,))
        peer_last_seen = rows[0]["last_seen"] if rows else 0

        now = int(time.time())
        await db.execute(
            "INSERT OR REPLACE INTO peers (id, api_url, last_seen, status) VALUES (?, ?, ?, 'connected')",
            (peer_id, peer_api, now),
        )
        await db.commit()
        await connections.connect(peer_id, websocket)

        # Step 2 — Send our MANIFEST
        local_files = await manifest.get_all(db)
        await websocket.send_text(json.dumps({"type": "MANIFEST", "files": local_files}))

        # Step 3 — Receive peer MANIFEST + delta sync
        raw = await websocket.receive_text()
        msg = json.loads(raw)
        if msg.get("type") == "MANIFEST" and peer_api:
            await _delta_sync(db, peer_api, msg.get("files", []), syncing)

        # Step 4 — Push catch-up: journal entries since peer was last seen
        entries = await journal.get_since(db, peer_last_seen, exclude_node=peer_id)
        if entries:
            logger.info(f"Catch-up push to {peer_id}: {len(entries)} entries (since {peer_last_seen})")
        await websocket.send_text(json.dumps({"type": "CATCH_UP", "entries": entries}))

        # Step 5 — Listen loop (real-time events + possible CATCH_UP from peer)
        async for raw in websocket.iter_text():
            try:
                msg = json.loads(raw)
                await _handle_event(msg, db, peer_api, syncing)
            except Exception as e:
                logger.error(f"WS event error from {peer_id}: {e}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WS error ({peer_id}): {e}")
    finally:
        if peer_id:
            connections.disconnect(peer_id)
            await db.execute(
                "UPDATE peers SET status = 'offline', last_seen = ? WHERE id = ?",
                (int(time.time()), peer_id),
            )
            await db.commit()
