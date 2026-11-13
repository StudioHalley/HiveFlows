"""
Outgoing WS connector — initiates connections to known/discovered peers.

Client-side protocol (mirror of api/ws.py server side):
  1. Connect to peer ws_url
  2. Send HELLO (with our node_id + api_url)
  3. Receive peer MANIFEST
  4. Send our MANIFEST
  5. Receive peer CATCH_UP → process
  6. Listen loop: handle FILE_CHANGED / FILE_DELETED / FILE_MOVED / CATCH_UP
"""

import asyncio
import json
import logging
import os
import time

import websockets
from fastapi import FastAPI

from config import cfg
from portal.client import get_peers
from sync import journal, manifest, transfer
from sync.peers import ConnectionManager

logger = logging.getLogger("remote-fs.connector")

NODE_ID = cfg["node"]["id"]
_TOKEN = cfg["sync"].get("secret", "")
_OWN_API_URL = os.environ.get("OWN_API_URL", "")  # e.g. http://instance-a:8090

_RECONNECT_INITIAL = 5
_RECONNECT_MAX = 120


async def _process_catch_up(entries: list[dict], db, peer_api: str, syncing: set) -> None:
    from api.ws import _process_catch_up as _pc
    await _pc(entries, db, peer_api, syncing)


async def _handle_event(msg: dict, db, peer_api: str, syncing: set) -> None:
    from api.ws import _handle_event as _he
    await _he(msg, db, peer_api, syncing)


async def _run_peer_session(peer: dict, app: FastAPI) -> None:
    """Run a full sync session with one peer. Raises on disconnect."""
    db = app.state.db
    connections: ConnectionManager = app.state.connections
    syncing: set = app.state.syncing
    peer_id = peer["id"]
    peer_api = peer.get("api_url", "")
    ws_url = peer["ws_url"]

    ws_headers = {"X-Sync-Token": _TOKEN} if _TOKEN else {}

    async with websockets.connect(ws_url, additional_headers=ws_headers) as ws:
        # Step 1 — HELLO
        await ws.send(json.dumps({
            "type": "HELLO",
            "node_id": NODE_ID,
            "api_url": _OWN_API_URL,
        }))

        # Step 2 — Receive peer MANIFEST
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("type") != "MANIFEST":
            raise ValueError(f"Expected MANIFEST, got {msg.get('type')}")
        peer_files = msg.get("files", [])

        # Update peer record
        now = int(time.time())
        rows = await db.execute_fetchall("SELECT last_seen FROM peers WHERE id = ?", (peer_id,))
        peer_last_seen = rows[0]["last_seen"] if rows else 0
        await db.execute(
            "INSERT OR REPLACE INTO peers (id, api_url, last_seen, status) VALUES (?, ?, ?, 'connected')",
            (peer_id, peer_api, now),
        )
        await db.commit()
        await connections.connect(peer_id, ws)

        # Step 3 — Send our MANIFEST
        local_files = await manifest.get_all(db)
        await ws.send(json.dumps({"type": "MANIFEST", "files": local_files}))

        # Step 4 — Delta sync (we pull what we're missing)
        from api.ws import _delta_sync
        await _delta_sync(db, peer_api, peer_files, syncing)

        # Step 5 — Receive peer CATCH_UP
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("type") == "CATCH_UP":
            await _process_catch_up(msg.get("entries", []), db, peer_api, syncing)

        # Step 6 — Send our CATCH_UP to peer
        entries = await journal.get_since(db, peer_last_seen, exclude_node=peer_id)
        await ws.send(json.dumps({"type": "CATCH_UP", "entries": entries}))
        if entries:
            logger.info(f"Catch-up sent to {peer_id}: {len(entries)} entries")

        logger.info(f"Synced with {peer_id} — listening for changes")

        # Step 7 — Listen loop
        async for raw in ws:
            try:
                msg = json.loads(raw)
                await _handle_event(msg, db, peer_api, syncing)
            except Exception as e:
                logger.error(f"Event error from {peer_id}: {e}")


async def _peer_loop(peer: dict, app: FastAPI) -> None:
    """Maintain connection to a peer with exponential backoff reconnect."""
    peer_id = peer["id"]
    backoff = _RECONNECT_INITIAL

    while True:
        try:
            logger.info(f"Connecting to peer {peer_id} @ {peer['ws_url']}")
            await _run_peer_session(peer, app)
            backoff = _RECONNECT_INITIAL  # reset on clean exit
        except Exception as e:
            logger.warning(f"Peer {peer_id} disconnected: {e} — retrying in {backoff}s")
        finally:
            db = app.state.db
            connections: ConnectionManager = app.state.connections
            connections.disconnect(peer_id)
            await db.execute(
                "UPDATE peers SET status = 'offline', last_seen = ? WHERE id = ?",
                (int(time.time()), peer_id),
            )
            await db.commit()

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _RECONNECT_MAX)


async def run_connector(app: FastAPI) -> None:
    """
    Background task: collect peers from config + Portal, spawn a _peer_loop
    for each. Re-checks Portal for new peers every 60s.
    """
    peer_tasks: dict[str, asyncio.Task] = {}
    portal_cfg = cfg.get("portal", {})

    while True:
        # Collect all known peers
        peers: list[dict] = list(cfg.get("peers", []))

        if portal_cfg.get("enabled"):
            portal_peers = await get_peers(
                portal_url=portal_cfg.get("api_url", ""),
                workspace_id=portal_cfg.get("workspace_id", ""),
                api_key=os.environ.get("PORTAL_API_KEY", ""),
            )
            # Merge — portal peers take precedence on ws_url/api_url
            known_ids = {p["id"] for p in peers}
            for pp in portal_peers:
                if pp["id"] not in known_ids:
                    peers.append(pp)

        # Spawn tasks for new peers
        for peer in peers:
            pid = peer["id"]
            if pid == NODE_ID:
                continue  # skip self
            if pid not in peer_tasks or peer_tasks[pid].done():
                logger.info(f"Spawning connector for peer {pid}")
                peer_tasks[pid] = asyncio.create_task(_peer_loop(peer, app))

        await asyncio.sleep(60)
