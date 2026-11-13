"""Workspace filesystem — scoped directories + LangChain tools (Phase 4, Bloc O)."""

import logging
import mimetypes
import os
import shutil
from datetime import datetime

from langchain_core.tools import tool

from config import settings

logger = logging.getLogger("hiveflows")


# ── Directory lifecycle ──────────────────────────────────────────────────────

def _workspace_root(workspace_slug: str) -> str:
    """Return the absolute path for a workspace's data directory."""
    return os.path.join(settings.workspaces_data_dir, workspace_slug)


def ensure_workspace_dir(workspace_slug: str) -> str:
    """Create the workspace directory if it doesn't exist. Returns the path."""
    path = _workspace_root(workspace_slug)
    os.makedirs(path, exist_ok=True)
    logger.info(f"Workspace directory ensured: {path}")
    return path


def archive_workspace_dir(workspace_slug: str) -> str | None:
    """Rename the workspace directory to {slug}_archived. Returns new path or None."""
    path = _workspace_root(workspace_slug)
    if not os.path.isdir(path):
        logger.warning(f"Workspace directory not found for archiving: {path}")
        return None
    archived = path + "_archived"
    # If an archived version already exists, remove it first
    if os.path.exists(archived):
        shutil.rmtree(archived)
    os.rename(path, archived)
    logger.info(f"Workspace directory archived: {path} → {archived}")
    return archived


# ── Path isolation ───────────────────────────────────────────────────────────

def _safe_resolve(workspace_slug: str, relative_path: str) -> str:
    """Resolve a relative path within the workspace root, raising on traversal.

    Uses os.path.realpath to resolve symlinks and '..' components,
    then checks the result is under the workspace root.
    """
    root = _workspace_root(workspace_slug)
    # Normalise: strip leading slashes so join works correctly
    cleaned = relative_path.lstrip("/")
    target = os.path.realpath(os.path.join(root, cleaned))
    real_root = os.path.realpath(root)
    if not target.startswith(real_root + os.sep) and target != real_root:
        raise PermissionError(
            f"Access denied: path '{relative_path}' resolves outside workspace."
        )
    return target


# ── Web filesystem operations (HTTP file manager) ────────────────────────────

def list_dir(workspace_slug: str, relative_path: str = ".") -> list[dict]:
    """List directory contents with metadata. Dirs first, then files."""
    target = _safe_resolve(workspace_slug, relative_path)
    root = _workspace_root(workspace_slug)
    if not os.path.isdir(target):
        raise NotADirectoryError(f"Not a directory: {relative_path}")
    entries = []
    for name in sorted(os.listdir(target)):
        full = os.path.join(target, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        is_dir = os.path.isdir(full)
        _, ext = os.path.splitext(name)
        entries.append({
            "name": name,
            "path": os.path.relpath(full, root),
            "type": "dir" if is_dir else "file",
            "size": stat.st_size if not is_dir else None,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "extension": ext.lstrip(".").lower() if not is_dir and ext else None,
        })
    entries.sort(key=lambda e: (e["type"] == "file", e["name"].lower()))
    return entries


def get_file_info(workspace_slug: str, relative_path: str) -> dict:
    """Get metadata for a single file or directory."""
    target = _safe_resolve(workspace_slug, relative_path)
    root = _workspace_root(workspace_slug)
    if not os.path.exists(target):
        raise FileNotFoundError(f"Not found: {relative_path}")
    stat = os.stat(target)
    is_dir = os.path.isdir(target)
    name = os.path.basename(target)
    _, ext = os.path.splitext(name)
    mime, _ = mimetypes.guess_type(name)
    return {
        "name": name,
        "path": os.path.relpath(target, root),
        "type": "dir" if is_dir else "file",
        "size": stat.st_size if not is_dir else None,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "extension": ext.lstrip(".").lower() if ext else None,
        "mime_type": mime,
    }


def get_absolute_path(workspace_slug: str, relative_path: str) -> str:
    """Resolve and return the absolute path for a workspace-scoped file.

    Raises PermissionError on path traversal, FileNotFoundError if missing.
    """
    target = _safe_resolve(workspace_slug, relative_path)
    if not os.path.isfile(target):
        raise FileNotFoundError(f"File not found: {relative_path}")
    return target


def read_file_bytes(workspace_slug: str, relative_path: str) -> bytes:
    """Read file contents as bytes."""
    target = _safe_resolve(workspace_slug, relative_path)
    if not os.path.isfile(target):
        raise FileNotFoundError(f"File not found: {relative_path}")
    with open(target, "rb") as f:
        return f.read()


def write_file_bytes(workspace_slug: str, relative_path: str, data: bytes) -> int:
    """Write bytes to a file, creating parent directories as needed."""
    target = _safe_resolve(workspace_slug, relative_path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "wb") as f:
        f.write(data)
    return len(data)


def delete_path(workspace_slug: str, relative_path: str) -> None:
    """Delete a file or directory (recursive for directories)."""
    target = _safe_resolve(workspace_slug, relative_path)
    if not os.path.exists(target):
        raise FileNotFoundError(f"Not found: {relative_path}")
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)


def rename_path(workspace_slug: str, old_relative: str, new_relative: str) -> None:
    """Rename or move a path within the workspace."""
    old_target = _safe_resolve(workspace_slug, old_relative)
    new_target = _safe_resolve(workspace_slug, new_relative)
    if not os.path.exists(old_target):
        raise FileNotFoundError(f"Not found: {old_relative}")
    if os.path.exists(new_target):
        raise FileExistsError(f"Destination already exists: {new_relative}")
    parent = os.path.dirname(new_target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    os.rename(old_target, new_target)


def make_dir(workspace_slug: str, relative_path: str) -> str:
    """Create a directory (and parents). Returns the absolute path."""
    target = _safe_resolve(workspace_slug, relative_path)
    os.makedirs(target, exist_ok=True)
    logger.info(f"Directory created: {target}")
    return target


# ── LangChain tools factory ─────────────────────────────────────────────────

def make_workspace_fs_tools(workspace_slug: str):
    """Return (read_file, write_file, list_files) tools scoped to a workspace."""
    # Ensure the directory exists (handles workspaces created before Bloc O)
    ensure_workspace_dir(workspace_slug)

    @tool
    def read_file(path: str) -> str:
        """Read a file from the workspace filesystem.

        Args:
            path: Relative path within the workspace (e.g. "notes/todo.txt").

        Returns:
            The file contents as text, or an error message.
        """
        try:
            target = _safe_resolve(workspace_slug, path)
            if not os.path.isfile(target):
                return f"Error: file not found: {path}"
            with open(target, encoding="utf-8", errors="replace") as f:
                return f.read()
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file in the workspace filesystem.

        Creates parent directories as needed. Overwrites existing files.

        Args:
            path: Relative path within the workspace (e.g. "output/report.txt").
            content: The text content to write.

        Returns:
            A confirmation message, or an error message.
        """
        try:
            target = _safe_resolve(workspace_slug, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return f"File written: {path} ({len(content)} bytes)"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"

    @tool
    def list_files(path: str = ".") -> str:
        """List files and directories in the workspace filesystem.

        Args:
            path: Relative directory path within the workspace (default: root).

        Returns:
            A newline-separated list of entries, or an error message.
        """
        try:
            target = _safe_resolve(workspace_slug, path)
            if not os.path.isdir(target):
                return f"Error: not a directory: {path}"
            entries = sorted(os.listdir(target))
            result = []
            for entry in entries:
                full = os.path.join(target, entry)
                suffix = "/" if os.path.isdir(full) else ""
                result.append(f"{entry}{suffix}")
            return "\n".join(result) if result else "(empty directory)"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing files: {e}"

    return read_file, write_file, list_files
