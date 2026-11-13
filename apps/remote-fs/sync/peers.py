import json
import logging

from fastapi import WebSocket

logger = logging.getLogger("remote-fs.peers")


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, node_id: str, ws: WebSocket) -> None:
        self._connections[node_id] = ws
        logger.info(f"Peer connected: {node_id} ({len(self._connections)} active)")

    def disconnect(self, node_id: str) -> None:
        self._connections.pop(node_id, None)
        logger.info(f"Peer disconnected: {node_id}")

    @property
    def peer_ids(self) -> list[str]:
        return list(self._connections.keys())

    async def send(self, node_id: str, message: dict) -> bool:
        ws = self._connections.get(node_id)
        if not ws:
            return False
        try:
            await ws.send_text(json.dumps(message))
            return True
        except Exception:
            self.disconnect(node_id)
            return False

    async def broadcast(self, message: dict, exclude: str = "") -> None:
        for node_id, ws in list(self._connections.items()):
            if node_id == exclude:
                continue
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                self.disconnect(node_id)
