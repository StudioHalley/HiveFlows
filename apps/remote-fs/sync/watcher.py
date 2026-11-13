import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from sync.manifest import should_ignore

logger = logging.getLogger("remote-fs.watcher")


@dataclass
class FileEvent:
    op: str       # "create" | "update" | "delete" | "move"
    path: str     # relative path (destination for moves)
    src: str = "" # relative path source (moves only)


class _WorkspaceHandler(FileSystemEventHandler):
    def __init__(self, workspace: Path, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.workspace = workspace
        self.queue = queue
        self.loop = loop

    def _put(self, event: FileEvent) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def _rel(self, abs_path: str) -> str | None:
        try:
            return str(Path(abs_path).relative_to(self.workspace))
        except ValueError:
            return None

    def on_created(self, event):
        if event.is_directory:
            return
        rel = self._rel(event.src_path)
        if rel and not should_ignore(rel):
            logger.debug(f"create {rel}")
            self._put(FileEvent("create", rel))

    def on_modified(self, event):
        if event.is_directory:
            return
        rel = self._rel(event.src_path)
        if rel and not should_ignore(rel):
            logger.debug(f"update {rel}")
            self._put(FileEvent("update", rel))

    def on_deleted(self, event):
        if event.is_directory:
            return
        rel = self._rel(event.src_path)
        if rel and not should_ignore(rel):
            logger.debug(f"delete {rel}")
            self._put(FileEvent("delete", rel))

    def on_moved(self, event):
        if event.is_directory:
            return
        src = self._rel(event.src_path)
        dst = self._rel(event.dest_path)
        if src and dst and not should_ignore(dst):
            logger.debug(f"move {src} → {dst}")
            self._put(FileEvent("move", dst, src))


def start_watcher(workspace: Path, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> Observer:
    handler = _WorkspaceHandler(workspace, queue, loop)
    observer = Observer()
    observer.schedule(handler, str(workspace), recursive=True)
    observer.start()
    logger.info(f"Watching {workspace}")
    return observer
