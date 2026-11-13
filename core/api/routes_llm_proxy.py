"""LLM Proxy — endpoint OpenAI-compatible proxifiant vers le vrai provider de l'agent.

POST /internal/v1/{workspace_id}/chat/completions

Sécurité — deux modes d'authentification (Bearer token) :
  1. Token = workspace_id (UUID) — accepté UNIQUEMENT depuis les réseaux de
     confiance (LLM_PROXY_TRUSTED_NETWORKS, défaut : réseau Docker interne).
     C'est le mode des apps Beekeeper : le manifest injecte LLM_API_KEY={workspace_id}.
  2. Token = clé API du workspace (hf_...) — accepté depuis n'importe où.
     Générée/révoquée depuis le dashboard (manage ou page API Server).

Utilisation côté app interne (ex: Fabric) :
  - LLM_BASE_URL = http://core:8000/internal/v1/{workspace_id}
  - LLM_API_KEY  = {workspace_id}
  - model field  = agent_id → le proxy résout le vrai provider + clé + modèle

Si model ne correspond à aucun agent du workspace → fallback sur l'agent is_default=True.

Documentation complète : docs/api_server.md
"""
import ipaddress
import logging
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from agents.credential_resolver import resolve_credential
from config import settings
from database import async_session
from models.agent_config import AgentConfig
from models.api_key import ApiKey, hash_api_key

logger = logging.getLogger("hiveflows.llm_proxy")

router = APIRouter(tags=["llm-proxy"])

# Providers dont l'API est compatible OpenAI (même format de requête/réponse)
_OPENAI_COMPAT_PROVIDERS = {"openrouter"}


# ── Authentification ──────────────────────────────────────────────────────────

def _parse_trusted_networks(raw: str) -> list:
    """Parse la liste CIDR de LLM_PROXY_TRUSTED_NETWORKS (invalides ignorés)."""
    networks = []
    for cidr in raw.split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            networks.append(ipaddress.ip_network(cidr))
        except ValueError:
            logger.warning(f"LLM proxy: CIDR invalide ignoré dans LLM_PROXY_TRUSTED_NETWORKS: {cidr!r}")
    return networks


_TRUSTED_NETWORKS = _parse_trusted_networks(settings.llm_proxy_trusted_networks)


def _client_is_internal(request: Request) -> bool:
    """True si l'IP source (connexion directe, pas les headers) est un réseau de confiance."""
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_NETWORKS)


async def _authorize(workspace_id: str, request: Request) -> uuid.UUID:
    """Valide le Bearer token pour un workspace. Retourne le workspace UUID.

    Mode interne : token == workspace_id, exigé depuis un réseau de confiance.
    Mode externe : token == clé API active du workspace (hf_...).
    """
    try:
        ws_uuid = uuid.UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="workspace_id invalide (attendu: UUID)")

    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token manquant (header Authorization: Bearer ...)")

    if token == workspace_id:
        if _client_is_internal(request):
            return ws_uuid
        client_host = request.client.host if request.client else "?"
        logger.warning(
            f"LLM proxy: token workspace refusé depuis une IP externe "
            f"({client_host}) workspace={workspace_id}"
        )
        raise HTTPException(
            status_code=401,
            detail=(
                "Le token workspace n'est accepté que depuis le réseau interne. "
                "Pour un accès externe, utilisez une clé API du workspace (hf_...) — "
                "voir la page API Server du dashboard."
            ),
        )

    if token.startswith("hf_"):
        key_hash = hash_api_key(token)
        async with async_session() as db:
            result = await db.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
            )
            key = result.scalar_one_or_none()
            if key is not None and key.workspace_id == ws_uuid:
                key.last_used_at = datetime.utcnow()
                await db.commit()
                return ws_uuid

    raise HTTPException(status_code=401, detail="Token invalide")


async def _resolve_agent_config(workspace_id: str, model_field: str):
    """Résout l'AgentConfig depuis un agent_id (= champ model) dans un workspace.

    Fallback sur l'agent is_default=True du workspace si agent_id introuvable.
    Retourne (agent, provider, api_key, base_url, real_model).
    """
    try:
        ws_uuid = uuid.UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="workspace_id invalide (attendu: UUID)")

    async with async_session() as db:
        # 1. Chercher l'agent par id exact dans ce workspace
        result = await db.execute(
            select(AgentConfig).where(
                AgentConfig.id == model_field,
                AgentConfig.workspace_id == ws_uuid,
                AgentConfig.is_active == True,
            )
        )
        agent = result.scalar_one_or_none()

        # 2. Fallback : agent default du workspace
        if agent is None:
            result = await db.execute(
                select(AgentConfig).where(
                    AgentConfig.workspace_id == ws_uuid,
                    AgentConfig.is_default == True,
                    AgentConfig.is_active == True,
                )
            )
            agent = result.scalar_one_or_none()
            if agent:
                logger.info(
                    f"LLM proxy: agent {model_field!r} introuvable → fallback default "
                    f"({agent.id}) workspace={workspace_id}"
                )

        if agent is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Agent {model_field!r} introuvable dans le workspace {workspace_id} "
                    f"et aucun agent default configuré."
                ),
            )

        provider, api_key, base_url = await resolve_credential(agent, ws_uuid, db)
        real_model = agent.llm_model or settings.default_llm_model

    return provider, api_key, base_url, real_model


@router.get("/internal/v1/{workspace_id}/models")
async def llm_proxy_models(workspace_id: str, request: Request):
    """Endpoint GET /models — retourne les agents du workspace + aliases OpenAI courants.

    Fabric appelle GET /models au démarrage pour valider que le modèle configuré
    existe. On inclut :
      - Les agent_id (UUID) et noms des agents actifs du workspace → routing par agent
      - Les aliases OpenAI courants → compatibilité avec les apps qui testent "gpt-4o"
    Le vrai modèle est résolu par /chat/completions via _resolve_agent_config.
    """
    ws_uuid = await _authorize(workspace_id, request)

    # Aliases OpenAI de base (pour les apps qui testent un modèle par défaut)
    base_ids = [
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo",
        "gpt-4o-2024-08-06", "gpt-4o-mini-2024-07-18",
    ]
    model_entries = [{"id": m, "object": "model", "owned_by": "hiveflows"} for m in base_ids]

    # Ajouter les agents actifs du workspace (id UUID + name) pour validation Fabric
    try:
        async with async_session() as db:
            result = await db.execute(
                select(AgentConfig.id, AgentConfig.name).where(
                    AgentConfig.workspace_id == ws_uuid,
                    AgentConfig.is_active == True,
                )
            )
            for row in result.all():
                agent_id = str(row.id)
                agent_name = row.name or agent_id
                # Ajouter UUID et nom (le frontend peut passer l'un ou l'autre)
                model_entries.append({"id": agent_id, "object": "model", "owned_by": "hiveflows"})
                if agent_name != agent_id:
                    model_entries.append({"id": agent_name, "object": "model", "owned_by": "hiveflows"})
    except Exception as e:
        logger.warning(f"LLM proxy /models: impossible de charger les agents du workspace: {e}")

    return {"object": "list", "data": model_entries}


@router.post("/internal/v1/{workspace_id}/responses")
async def llm_proxy_responses(workspace_id: str, request: Request):
    """Proxy OpenAI /responses (nouvelle API 2025) → transforme en /chat/completions vers OpenRouter.

    Fabric utilise le SDK OpenAI récent qui appelle /responses par défaut.
    On convertit le format Responses API → Chat Completions avant de proxifier.

    Responses API input:
      {"model": "...", "input": "text" | [...], "instructions": "system prompt"}
    Chat Completions équivalent:
      {"model": "...", "messages": [{"role": "system", ...}, {"role": "user", ...}]}
    """
    await _authorize(workspace_id, request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corps de requête JSON invalide")

    model_field = body.get("model", "")
    if not model_field:
        raise HTTPException(status_code=400, detail="Champ 'model' requis")

    provider, api_key, base_url, real_model = await _resolve_agent_config(workspace_id, model_field)

    if provider not in _OPENAI_COMPAT_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Provider {provider!r} non supporté par le proxy LLM.")

    # ── Transformer Responses API → Chat Completions ──────────────────────────
    messages = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    inp = body.get("input", "")
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        # input peut être une liste de content parts ou de messages
        for item in inp:
            if isinstance(item, dict):
                messages.append(item)
            else:
                messages.append({"role": "user", "content": str(item)})

    chat_body = {
        "model": real_model,
        "messages": messages,
        "stream": body.get("stream", False),
    }
    # Passer les paramètres communs s'ils sont présents
    for key in ("temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty"):
        if key in body:
            chat_body[key] = body[key]

    target_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    forward_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    logger.debug(f"LLM proxy /responses: workspace={workspace_id} agent={model_field} → {provider}/{real_model}")

    if chat_body.get("stream"):
        async def _stream():
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", target_url, json=chat_body, headers=forward_headers) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        return StreamingResponse(_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(target_url, json=chat_body, headers=forward_headers)

    if resp.status_code != 200:
        return Response(content=resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))

    # ── Transformer Chat Completions → Responses API ──────────────────────────
    # Fabric (via le SDK OpenAI récent) lit response.output[0].content[0].text
    try:
        cc = resp.json()
        text = cc["choices"][0]["message"]["content"] or ""
        usage = cc.get("usage", {})
        responses_body = {
            "id": cc.get("id", "resp_hiveflows"),
            "object": "response",
            "created_at": cc.get("created", 0),
            "status": "completed",
            "model": cc.get("model", real_model),
            "output": [{
                "type": "message",
                "id": "msg_hiveflows",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }],
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        import json as _json
        return Response(content=_json.dumps(responses_body), status_code=200,
                        media_type="application/json")
    except Exception as e:
        logger.warning(f"LLM proxy /responses: échec de la transformation réponse: {e} — retour brut")
        return Response(content=resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))


@router.post("/internal/v1/{workspace_id}/chat/completions")
async def llm_proxy(workspace_id: str, request: Request):
    """Proxy OpenAI /chat/completions vers le vrai provider de l'agent.

    Le champ 'model' du body doit contenir l'agent_id (slug de l'agent).
    Supporte le streaming (stream: true) et le non-streaming.
    """
    # ── Auth : token workspace (interne) ou clé API hf_ (externe) ─────────────
    await _authorize(workspace_id, request)

    # ── Parse body ────────────────────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corps de requête JSON invalide")

    model_field = body.get("model", "")
    if not model_field:
        raise HTTPException(status_code=400, detail="Champ 'model' requis (= agent_id ou slug)")

    is_stream = bool(body.get("stream", False))

    # ── Résolution agent + credentials ────────────────────────────────────────
    provider, api_key, base_url, real_model = await _resolve_agent_config(workspace_id, model_field)

    if provider not in _OPENAI_COMPAT_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider {provider!r} non supporté par le proxy LLM. "
                f"Supportés: {', '.join(sorted(_OPENAI_COMPAT_PROVIDERS))}. "
                f"Configurez l'agent avec OpenRouter."
            ),
        )

    target_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    forward_body = {**body, "model": real_model}
    forward_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.debug(
        f"LLM proxy: workspace={workspace_id} agent={model_field} → "
        f"provider={provider} model={real_model} stream={is_stream}"
    )

    # ── Streaming ─────────────────────────────────────────────────────────────
    if is_stream:
        async def _stream():
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST", target_url, json=forward_body, headers=forward_headers
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Non-streaming ─────────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(target_url, json=forward_body, headers=forward_headers)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )
