import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

import db as database
from api.files import router as files_router
from api.ws import router as ws_router
from config import DB_PATH, SYNC_ROOT, WORKSPACE, cfg
from portal.connector import run_connector
from sync import manifest
from sync.peers import ConnectionManager
from sync.watcher import FileEvent, start_watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("remote-fs")

NODE_ID = cfg["node"]["id"]


async def _drain_events(app: FastAPI):
    queue: asyncio.Queue[FileEvent] = app.state.event_queue
    db = app.state.db
    connections: ConnectionManager = app.state.connections
    syncing: set = app.state.syncing

    while True:
        event = await queue.get()
        try:
            # Skip broadcast for paths currently being downloaded from a peer (avoid echo loop)
            if event.path in syncing:
                continue

            if event.op in ("create", "update"):
                result = await manifest.upsert(db, event.path, NODE_ID)
                if result:
                    logger.info(f"[{event.op}] {event.path} ({result['hash'][:8]}...)")
                    await connections.broadcast({
                        "type": "FILE_CHANGED",
                        "path": event.path,
                        "hash": result["hash"],
                        "mtime": result["mtime"],
                        "op": event.op,
                    })
            elif event.op == "delete":
                await manifest.delete(db, event.path, NODE_ID)
                logger.info(f"[delete] {event.path}")
                await connections.broadcast({"type": "FILE_DELETED", "path": event.path})
            elif event.op == "move":
                await manifest.move(db, event.src, event.path, NODE_ID)
                logger.info(f"[move] {event.src} → {event.path}")
                await connections.broadcast({"type": "FILE_MOVED", "src": event.src, "path": event.path})
        except Exception as e:
            logger.error(f"Event error: {e}")
        finally:
            queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting remote-fs node '{NODE_ID}'")

    app.state.db = await database.get_db()
    await database.init(app.state.db)
    logger.info(f"DB ready at {DB_PATH}")

    app.state.connections = ConnectionManager()
    app.state.syncing = set()
    app.state.event_queue = asyncio.Queue()

    loop = asyncio.get_event_loop()
    observer = start_watcher(SYNC_ROOT, app.state.event_queue, loop)
    drain_task = asyncio.create_task(_drain_events(app))
    scan_task = asyncio.create_task(database.initial_scan(app.state.db))
    connector_task = asyncio.create_task(run_connector(app))

    yield

    connector_task.cancel()
    scan_task.cancel()
    drain_task.cancel()
    observer.stop()
    observer.join()
    await app.state.db.close()
    logger.info("remote-fs stopped")


app = FastAPI(title="remote-fs", version="1.0", lifespan=lifespan)
app.include_router(files_router)
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "node_id": NODE_ID,
        "workspace": str(WORKSPACE),
        "sync_root": str(SYNC_ROOT),
        "peers_connected": len(app.state.connections.peer_ids) if hasattr(app.state, "connections") else 0,
        "peers_configured": len(cfg.get("peers", [])),
        "portal_enabled": cfg["portal"]["enabled"],
    }


@app.get("/status")
async def status():
    db = app.state.db
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) as total, SUM(size) as total_bytes FROM file_manifest"
    )
    row = rows[0]
    peers = await db.execute_fetchall("SELECT id, status, last_seen FROM peers")
    return {
        "node_id": NODE_ID,
        "files_indexed": row["total"],
        "total_bytes": row["total_bytes"] or 0,
        "queue_depth": app.state.event_queue.qsize(),
        "peers_connected": app.state.connections.peer_ids,
        "peers": [dict(p) for p in peers],
        "uptime": int(time.time()),
    }
