"""Credential Resolver — résout la cascade (agent → workspace → global → env)
pour obtenir (provider, api_key, base_url) à partir d'un AgentConfig.
"""
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("hiveflows.credentials")


async def resolve_credential(
    agent_config,
    workspace_id: str | uuid.UUID | None,
    db: AsyncSession,
) -> tuple[str, str, str | None]:
    """Retourne (provider, api_key, base_url) en suivant la cascade de résolution.

    Ordre de priorité :
    1. Credential spécifique à l'agent (provider_credential_id)
    2. Credential par défaut du workspace pour ce provider
    3. Credential globale (site-admin, workspace_id=NULL)
    4. Fallback .env (OPENROUTER_API_KEY — rétro-compat Phase 1-4)
    """
    from models.provider_credential import ProviderCredential
    from credential_vault import decrypt

    provider = getattr(agent_config, "llm_provider", None) or "openrouter"

    # 1. Credential spécifique à l'agent
    cred_id = getattr(agent_config, "provider_credential_id", None)
    if cred_id:
        result = await db.execute(
            select(ProviderCredential).where(ProviderCredential.id == cred_id)
        )
        cred = result.scalar_one_or_none()
        if cred:
            data = _parse_credential_data(cred.credential_data, decrypt)
            logger.debug(f"Credential resolved: agent-specific (id={cred_id}, provider={cred.provider})")
            return cred.provider, data["api_key"], data.get("base_url")

    # 2. Credential par défaut du workspace
    if workspace_id:
        ws_uuid = uuid.UUID(str(workspace_id)) if not isinstance(workspace_id, uuid.UUID) else workspace_id
        result = await db.execute(
            select(ProviderCredential).where(
                ProviderCredential.workspace_id == ws_uuid,
                ProviderCredential.provider == provider,
                ProviderCredential.is_default == True,
            )
        )
        cred = result.scalar_one_or_none()
        if cred:
            data = _parse_credential_data(cred.credential_data, decrypt)
            logger.debug(f"Credential resolved: workspace default (provider={provider}, ws={workspace_id})")
            return provider, data["api_key"], data.get("base_url")

    # 3. Credential globale (site-admin, workspace_id=NULL)
    result = await db.execute(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == None,
            ProviderCredential.provider == provider,
            ProviderCredential.is_default == True,
        )
    )
    cred = result.scalar_one_or_none()
    if cred:
        data = _parse_credential_data(cred.credential_data, decrypt)
        logger.debug(f"Credential resolved: global admin (provider={provider})")
        return provider, data["api_key"], data.get("base_url")

    # 4. Fallback .env (rétro-compat)
    from config import settings
    logger.debug(f"Credential resolved: env fallback (OPENROUTER_API_KEY)")
    return "openrouter", settings.openrouter_api_key, "https://openrouter.ai/api/v1"


def _parse_credential_data(credential_data: str, decrypt_fn) -> dict:
    """Déchiffre et parse le JSON de credential_data."""
    plaintext = decrypt_fn(credential_data)
    return json.loads(plaintext)
