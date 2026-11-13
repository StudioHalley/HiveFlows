"""File manager API — workspace-scoped filesystem operations (Sprint File)."""

import mimetypes
import os
import re
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes_dashboard import require_workspace_auth
from database import get_db
from permissions import Permission, has_permission
from workspace_fs import (
    delete_path,
    get_absolute_path,
    get_file_info,
    list_dir,
    make_dir,
    read_file_bytes,
    rename_path,
    write_file_bytes,
)

router = APIRouter(prefix="/api/w", tags=["files"])

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_SAFE_NAME_RE = re.compile(r"[^\w\s\-.()\[\]{}@#+=%~,!]", re.UNICODE)


def _sanitize_name(name: str) -> str:
    """Strip path separators and null bytes from a filename."""
    name = name.replace("\0", "").replace("/", "").replace("\\", "").strip()
    if not name:
        raise ValueError("Empty filename.")
    return name


def _require_write(role: str) -> None:
    """Raise 403 if role is viewer (lacks CHAT permission)."""
    if not has_permission(role, Permission.CHAT):
        raise HTTPException(403, "Les viewers ne peuvent pas modifier les fichiers.")


def _build_breadcrumb(path: str) -> list[dict]:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    crumb = [{"name": "root", "path": "."}]
    for i, part in enumerate(parts):
        crumb.append({"name": part, "path": "/".join(parts[: i + 1])})
    return crumb


# ── LIST ──────────────────────────────────────────────────────────────────────


@router.get("/{slug}/files")
async def list_files(
    slug: str,
    request: Request,
    path: str = ".",
    db: AsyncSession = Depends(get_db),
):
    """List directory contents with metadata."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    try:
        entries = list_dir(slug, path)
    except NotADirectoryError:
        raise HTTPException(400, "Pas un répertoire.")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "Répertoire introuvable.")
    return {
        "entries": entries,
        "path": path,
        "breadcrumb": _build_breadcrumb(path),
        "role": role,
        "can_write": has_permission(role, Permission.CHAT),
    }


# ── CONTENT ───────────────────────────────────────────────────────────────────


@router.get("/{slug}/files/content")
async def get_file_content(
    slug: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return file content as JSON (text files only)."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    try:
        data = read_file_bytes(slug, path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "Fichier introuvable.")
    try:
        content = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = None
        encoding = "binary"
    info = get_file_info(slug, path)
    return {
        "path": path,
        "name": info["name"],
        "content": content,
        "size": len(data),
        "encoding": encoding,
        "mime_type": info.get("mime_type"),
        "extension": info.get("extension"),
        "can_write": has_permission(role, Permission.CHAT),
    }


# ── DOWNLOAD ──────────────────────────────────────────────────────────────────


@router.get("/{slug}/files/download")
async def download_file(
    slug: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Stream a file as an attachment download."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    try:
        data = read_file_bytes(slug, path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "Fichier introuvable.")

    filename = os.path.basename(path)
    mime, _ = mimetypes.guess_type(filename)
    mime = mime or "application/octet-stream"

    def _iter():
        yield data

    encoded = quote(filename, safe="")
    disposition = f"attachment; filename*=UTF-8''{encoded}"
    return StreamingResponse(
        _iter(),
        media_type=mime,
        headers={"Content-Disposition": disposition},
    )


# ── STREAM (Range-aware, for in-browser video/audio) ──────────────────────────


@router.get("/{slug}/files/stream")
async def stream_file(
    slug: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Stream a file with HTTP Range support for in-browser video/audio playback."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    try:
        file_path = get_absolute_path(slug, path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "Fichier introuvable.")

    filename = os.path.basename(path)
    mime, _ = mimetypes.guess_type(filename)
    mime = mime or "application/octet-stream"

    # FileResponse handles Range headers natively (required for Safari seek support)
    return FileResponse(file_path, media_type=mime)


# ── UPLOAD ────────────────────────────────────────────────────────────────────


@router.post("/{slug}/files/upload")
async def upload_files(
    slug: str,
    request: Request,
    path: str = Form(default="."),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more files to the workspace."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    _require_write(role)

    results = []
    for f in files:
        raw_name = f.filename or "upload"
        safe_name = _sanitize_name(raw_name)
        # Build target path: path/filename
        rel_dir = path.strip("./ ").replace("\\", "/")
        target_path = f"{rel_dir}/{safe_name}".lstrip("/") if rel_dir else safe_name

        data = await f.read()
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"'{safe_name}' dépasse la limite de 50 Mo.")
        try:
            written = write_file_bytes(slug, target_path, data)
            results.append({"name": safe_name, "path": target_path, "size": written})
        except PermissionError as e:
            raise HTTPException(403, str(e))

    return {"uploaded": results}


# ── MKDIR ─────────────────────────────────────────────────────────────────────


class MkdirBody(BaseModel):
    path: str


@router.post("/{slug}/files/mkdir")
async def mkdir(
    slug: str,
    body: MkdirBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a directory."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    _require_write(role)
    try:
        make_dir(slug, body.path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"created": body.path}


# ── SAVE ──────────────────────────────────────────────────────────────────────


class SaveBody(BaseModel):
    path: str
    content: str


@router.post("/{slug}/files/save")
async def save_file(
    slug: str,
    body: SaveBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save text content to a file (create or overwrite)."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    _require_write(role)
    data = body.content.encode("utf-8")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Contenu trop volumineux (max 50 Mo).")
    try:
        write_file_bytes(slug, body.path, data)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"saved": body.path, "size": len(data)}


# ── RENAME ────────────────────────────────────────────────────────────────────


class RenameBody(BaseModel):
    old_path: str
    new_path: str


@router.post("/{slug}/files/rename")
async def rename_file(
    slug: str,
    body: RenameBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Rename or move a file/directory within the workspace."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    _require_write(role)
    try:
        rename_path(slug, body.old_path, body.new_path)
    except FileNotFoundError:
        raise HTTPException(404, "Fichier introuvable.")
    except FileExistsError:
        raise HTTPException(409, "La destination existe déjà.")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"renamed": {"from": body.old_path, "to": body.new_path}}


# ── DELETE ────────────────────────────────────────────────────────────────────


class DeleteBody(BaseModel):
    path: str


@router.post("/{slug}/files/delete")
async def delete_file(
    slug: str,
    body: DeleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a file or directory (recursive)."""
    user, ws, role = await require_workspace_auth(slug, request, db)
    _require_write(role)
    try:
        delete_path(slug, body.path)
    except FileNotFoundError:
        raise HTTPException(404, "Chemin introuvable.")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"deleted": body.path}
