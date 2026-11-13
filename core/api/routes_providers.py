"""Routes CRUD pour les credentials LLM provider (Phase 5 — Bloc C1)."""
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import audit_log, get_client_ip
from credential_vault import decrypt, encrypt
from database import get_db
from models.provider_credential import ProviderCredential
from permissions import AuthContext, Permission, require_permission

logger = logging.getLogger("hiveflows.providers")

router = APIRouter(prefix="/providers", tags=["providers"])

_SUPPORTED_PROVIDERS = {"openrouter", "anthropic", "google", "ollama", "openai_compatible"}


# ── Schémas ────────────────────────────────────────────────────────────────────

class ProviderCredentialCreate(BaseModel):
    provider: str
    display_name: str | None = None
    credentials: dict  # {"api_key": "...", "base_url": "..."} — jamais stocké en clair
    is_default: bool = False


class ProviderCredentialRead(BaseModel):
    id: uuid.UUID
    provider: str
    display_name: str | None
    is_default: bool
    key_preview: str          # ex: "sk-ant-...abc" (8 premiers + "..." + 3 derniers)
    workspace_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _key_preview(api_key: str) -> str:
    if len(api_key) <= 11:
        return "***"
    return f"{api_key[:8]}...{api_key[-3:]}"


def _parse_cred(cred: ProviderCredential) -> ProviderCredentialRead:
    try:
        data = json.loads(decrypt(cred.credential_data))
        key_preview = _key_preview(data.get("api_key", ""))
    except Exception:
        key_preview = "***"
    return ProviderCredentialRead(
        id=cred.id,
        provider=cred.provider,
        display_name=cred.display_name,
        is_default=cred.is_default,
        key_preview=key_preview,
        workspace_id=cred.workspace_id,
        created_at=cred.created_at,
    )


# ── GET /providers ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[ProviderCredentialRead])
async def list_providers(
    ctx: AuthContext = Depends(require_permission(Permission.CHAT_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Liste les credentials provider du workspace courant + globaux."""
    conditions = [ProviderCredential.workspace_id == None]  # globaux
    if ctx.workspace_id:
        conditions = [
            ProviderCredential.workspace_id == ctx.workspace_id,
        ]
        # inclure aussi les globaux
        from sqlalchemy import or_
        result = await db.execute(
            select(ProviderCredential).where(
                or_(
                    ProviderCredential.workspace_id == ctx.workspace_id,
                    ProviderCredential.workspace_id == None,
                )
            ).order_by(ProviderCredential.provider, ProviderCredential.created_at)
        )
    else:
        result = await db.execute(
            select(ProviderCredential)
            .where(ProviderCredential.workspace_id == None)
            .order_by(ProviderCredential.provider, ProviderCredential.created_at)
        )
    return [_parse_cred(c) for c in result.scalars().all()]


# ── POST /providers ────────────────────────────────────────────────────────────

@router.post("", response_model=ProviderCredentialRead, status_code=201)
async def create_provider(
    body: ProviderCredentialCreate,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.AGENT_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    if body.provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(400, f"Provider non supporté: {body.provider}. Supportés: {sorted(_SUPPORTED_PROVIDERS)}")

    if "api_key" not in body.credentials and body.provider not in {"ollama", "openai_compatible"}:
        raise HTTPException(400, "Le champ 'api_key' est requis dans 'credentials'.")

    # Si is_default=True → désactiver l'ancien default pour ce provider+workspace
    if body.is_default:
        old_result = await db.execute(
            select(ProviderCredential).where(
                ProviderCredential.workspace_id == ctx.workspace_id,
                ProviderCredential.provider == body.provider,
                ProviderCredential.is_default == True,
            )
        )
        for old in old_result.scalars().all():
            old.is_default = False

    encrypted = encrypt(json.dumps(body.credentials))
    cred = ProviderCredential(
        workspace_id=ctx.workspace_id,
        provider=body.provider,
        display_name=body.display_name,
        credential_data=encrypted,
        is_default=body.is_default,
        created_by=ctx.user.id,
    )
    db.add(cred)

    await audit_log(db, action="provider.create", user_id=ctx.user.id,
                    workspace_id=ctx.workspace_id, resource_type="provider_credential",
                    resource_id=str(cred.id),
                    details={"provider": body.provider, "display_name": body.display_name},
                    ip=get_client_ip(request))
    await db.commit()
    await db.refresh(cred)
    return _parse_cred(cred)


# ── DELETE /providers/{id} ─────────────────────────────────────────────────────

@router.delete("/{cred_id}", status_code=204)
async def delete_provider(
    cred_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_permission(Permission.AGENT_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ProviderCredential).where(ProviderCredential.id == cred_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(404, "Credential introuvable.")

    # Vérif: appartient au workspace courant ou admin global
    if cred.workspace_id and cred.workspace_id != ctx.workspace_id:
        raise HTTPException(403, "Accès refusé.")

    await db.delete(cred)
    await audit_log(db, action="provider.delete", user_id=ctx.user.id,
                    workspace_id=ctx.workspace_id, resource_type="provider_credential",
                    resource_id=str(cred_id),
                    details={"provider": cred.provider},
                    ip=get_client_ip(request))
    await db.commit()


# ── POST /providers/{id}/test ──────────────────────────────────────────────────

@router.post("/{cred_id}/test")
async def test_provider(
    cred_id: uuid.UUID,
    ctx: AuthContext = Depends(require_permission(Permission.CHAT_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Teste une credential en envoyant 'Hello' au provider."""
    result = await db.execute(select(ProviderCredential).where(ProviderCredential.id == cred_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(404, "Credential introuvable.")

    try:
        data = json.loads(decrypt(cred.credential_data))
        api_key = data.get("api_key")
        base_url = data.get("base_url")
    except Exception as e:
        raise HTTPException(500, f"Impossible de déchiffrer la credential: {e}")

    from agents.llm_factory import create_llm
    import time

    # Modèle de test minimal par provider
    _TEST_MODELS = {
        "openrouter":      "openai/gpt-4o-mini",
        "anthropic":       "claude-haiku-4-5-20251001",
        "google":          "gemini-1.5-flash",
        "ollama":          "llama3",
        "openai_compatible": data.get("model", "gpt-4o-mini"),
    }
    model = _TEST_MODELS.get(cred.provider, "gpt-4o-mini")

    try:
        llm = create_llm(cred.provider, model, api_key=api_key, base_url=base_url, max_tokens=10)
        t0 = time.monotonic()
        response = await llm.ainvoke("Say 'ok'")
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"status": "ok", "model": model, "latency_ms": latency_ms}
    except Exception as e:
        logger.warning(f"Provider test failed ({cred.provider}): {e!r}")
        return {"status": "error", "error": str(e)}
