"""Tests — LLM Proxy (API Server par workspace) : authentification à deux modes.

Mode interne : Bearer token == workspace_id, accepté seulement depuis les
réseaux de confiance (LLM_PROXY_TRUSTED_NETWORKS).
Mode externe : Bearer token == clé API du workspace (hf_...), accepté partout.
"""
import asyncio
import sys
import os
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException

from api import routes_llm_proxy as proxy
from models.api_key import hash_api_key


def run(coro):
    return asyncio.run(coro)


def make_request(client_ip: str | None, token: str | None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    client = SimpleNamespace(host=client_ip) if client_ip else None
    return SimpleNamespace(client=client, headers=headers)


# ── _parse_trusted_networks ───────────────────────────────────────────────────

def test_parse_trusted_networks_valid():
    nets = proxy._parse_trusted_networks("172.16.0.0/12,127.0.0.0/8")
    assert len(nets) == 2


def test_parse_trusted_networks_ignores_invalid():
    nets = proxy._parse_trusted_networks("172.16.0.0/12, pas-un-cidr, 10.0.0.0/8")
    assert len(nets) == 2


def test_parse_trusted_networks_empty():
    assert proxy._parse_trusted_networks("") == []


# ── _client_is_internal ───────────────────────────────────────────────────────

def test_internal_ip_docker_bridge():
    assert proxy._client_is_internal(make_request("172.18.0.5", None)) is True


def test_internal_ip_localhost():
    assert proxy._client_is_internal(make_request("127.0.0.1", None)) is True


def test_external_ip_lan():
    assert proxy._client_is_internal(make_request("192.168.1.50", None)) is False


def test_external_ip_public():
    assert proxy._client_is_internal(make_request("8.8.8.8", None)) is False


def test_no_client_is_external():
    assert proxy._client_is_internal(make_request(None, None)) is False


# ── _authorize : mode interne (token == workspace_id) ─────────────────────────

def test_authorize_workspace_token_from_internal_ok():
    ws_id = str(uuid.uuid4())
    result = run(proxy._authorize(ws_id, make_request("172.18.0.5", ws_id)))
    assert result == uuid.UUID(ws_id)


def test_authorize_workspace_token_from_external_rejected():
    ws_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        run(proxy._authorize(ws_id, make_request("192.168.1.50", ws_id)))
    assert exc.value.status_code == 401
    assert "hf_" in exc.value.detail  # le message pointe vers les clés API


def test_authorize_invalid_workspace_uuid():
    with pytest.raises(HTTPException) as exc:
        run(proxy._authorize("pas-un-uuid", make_request("172.18.0.5", "pas-un-uuid")))
    assert exc.value.status_code == 400


def test_authorize_missing_token():
    ws_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        run(proxy._authorize(ws_id, make_request("172.18.0.5", None)))
    assert exc.value.status_code == 401


def test_authorize_random_token_rejected():
    ws_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        run(proxy._authorize(ws_id, make_request("172.18.0.5", "n-importe-quoi")))
    assert exc.value.status_code == 401


# ── _authorize : mode externe (clé API hf_) ───────────────────────────────────

class _FakeResult:
    def __init__(self, key):
        self._key = key

    def scalar_one_or_none(self):
        return self._key


class _FakeSession:
    """Session async minimale : execute() renvoie la clé injectée, commit() trace."""

    def __init__(self, key):
        self._key = key
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._key)

    async def commit(self):
        self.committed = True


def _patch_session(monkeypatch, key):
    session = _FakeSession(key)
    monkeypatch.setattr(proxy, "async_session", lambda: session)
    return session


def test_authorize_api_key_valid(monkeypatch):
    ws_uuid = uuid.uuid4()
    key = SimpleNamespace(workspace_id=ws_uuid, is_active=True, last_used_at=None)
    session = _patch_session(monkeypatch, key)

    result = run(proxy._authorize(str(ws_uuid), make_request("192.168.1.50", "hf_bonne-cle")))

    assert result == ws_uuid
    assert isinstance(key.last_used_at, datetime)
    assert session.committed is True


def test_authorize_api_key_wrong_workspace(monkeypatch):
    key = SimpleNamespace(workspace_id=uuid.uuid4(), is_active=True, last_used_at=None)
    _patch_session(monkeypatch, key)

    other_ws = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        run(proxy._authorize(other_ws, make_request("192.168.1.50", "hf_bonne-cle")))
    assert exc.value.status_code == 401


def test_authorize_api_key_unknown(monkeypatch):
    _patch_session(monkeypatch, None)  # aucune clé active trouvée en DB

    ws_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        run(proxy._authorize(ws_id, make_request("192.168.1.50", "hf_cle-revoquee")))
    assert exc.value.status_code == 401


def test_hash_api_key_deterministic():
    assert hash_api_key("hf_abc") == hash_api_key("hf_abc")
    assert hash_api_key("hf_abc") != hash_api_key("hf_abd")
