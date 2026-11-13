"""Integration tests for HiveFlows API.

These tests hit the live server. Run with:
    HIVEFLOWS_CORE_URL=http://localhost:8002 HIVEFLOWS_API_KEY=<key> pytest tests/test_core/ -v

On the remote server:
    cd ~/hiveflows && source .env && API_KEY=$(echo $API_KEYS | cut -d',' -f1) \
    HIVEFLOWS_CORE_URL=http://localhost:8002 HIVEFLOWS_API_KEY=$API_KEY \
    python3 -m pytest tests/test_core/ -v

Phase 4 auth tests:
    HIVEFLOWS_CORE_URL=http://localhost:8002 HIVEFLOWS_API_KEY=<key> \
    pytest tests/test_core/test_api_integration.py::TestAuthJWT -v
"""
from __future__ import annotations

import json
import os
import time

import uuid

import httpx
import pytest

CORE_URL = os.environ.get("HIVEFLOWS_CORE_URL", "http://localhost:8002")
API_KEY = os.environ.get("HIVEFLOWS_API_KEY", "")

HEADERS = {"Content-Type": "application/json", "X-API-Key": API_KEY}


@pytest.fixture(autouse=True)
def skip_if_no_key():
    if not API_KEY:
        pytest.skip("HIVEFLOWS_API_KEY not set — skipping integration tests")


# --- Health ---

class TestHealth:
    def test_health_returns_ok(self):
        resp = httpx.get(f"{CORE_URL}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# --- Auth ---

class TestAuth:
    def test_missing_api_key_returns_401(self):
        resp = httpx.post(
            f"{CORE_URL}/tasks/run",
            json={"type": "python", "script": "print(1)"},
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert resp.status_code in (401, 403)

    def test_wrong_api_key_returns_401(self):
        resp = httpx.post(
            f"{CORE_URL}/tasks/run",
            json={"type": "python", "script": "print(1)"},
            headers={"Content-Type": "application/json", "X-API-Key": "wrong-key"},
            timeout=5,
        )
        assert resp.status_code in (401, 403)


# --- POST /tasks/run + GET /tasks/{id} ---

class TestTasks:
    def test_submit_python_task(self):
        resp = httpx.post(
            f"{CORE_URL}/tasks/run",
            json={"type": "python", "script": "print(42)"},
            headers=HEADERS, timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "pending"

    def test_python_task_completes(self):
        resp = httpx.post(
            f"{CORE_URL}/tasks/run",
            json={"type": "python", "script": "print(7 * 6)"},
            headers=HEADERS, timeout=10,
        )
        task_id = resp.json()["task_id"]

        # Poll for completion
        for _ in range(15):
            time.sleep(1)
            poll = httpx.get(f"{CORE_URL}/tasks/{task_id}", headers=HEADERS, timeout=5)
            data = poll.json()
            if data["status"] in ("success", "failed"):
                break

        assert data["status"] == "success"
        assert data["result"]["stdout"].strip() == "42"
        assert data["result"]["returncode"] == 0

    def test_bash_task_completes(self):
        resp = httpx.post(
            f"{CORE_URL}/tasks/run",
            json={"type": "bash", "script": "echo HiveFlows"},
            headers=HEADERS, timeout=10,
        )
        task_id = resp.json()["task_id"]

        for _ in range(15):
            time.sleep(1)
            poll = httpx.get(f"{CORE_URL}/tasks/{task_id}", headers=HEADERS, timeout=5)
            data = poll.json()
            if data["status"] in ("success", "failed"):
                break

        assert data["status"] == "success"
        assert data["result"]["stdout"].strip() == "HiveFlows"

    def test_nonexistent_task_returns_404(self):
        resp = httpx.get(
            f"{CORE_URL}/tasks/00000000-0000-0000-0000-000000000000",
            headers=HEADERS, timeout=5,
        )
        assert resp.status_code == 404


# --- POST /chat ---

class TestChat:
    def test_chat_returns_response(self):
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={"message": "Dis juste 'OK' et rien d'autre."},
            headers=HEADERS, timeout=120,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "conversation_id" in data
        assert "response" in data
        assert len(data["response"]) > 0

    def test_chat_conversation_persistence(self):
        # First message
        r1 = httpx.post(
            f"{CORE_URL}/chat",
            json={"message": "Mon mot secret est 'banane'. Retiens-le."},
            headers=HEADERS, timeout=120,
        )
        assert r1.status_code == 200
        conv_id = r1.json()["conversation_id"]

        # Second message in same conversation
        r2 = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "message": "Quel est mon mot secret ?",
                "conversation_id": conv_id,
            },
            headers=HEADERS, timeout=120,
        )
        assert r2.status_code == 200
        assert "banane" in r2.json()["response"].lower()


# --- Streaming SSE ---

class TestStreamSSE:
    """Teste que POST /chat/stream retourne des events SSE valides."""

    def _collect_events(self, message: str, agent_id: str = "assistant-principal") -> list[dict]:
        """Consomme le stream SSE et retourne la liste des events parsés."""
        events = []
        current_event = None

        with httpx.stream(
            "POST",
            f"{CORE_URL}/chat/stream",
            json={"agent_id": agent_id, "message": message},
            headers=HEADERS,
            timeout=180,
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: ") and current_event:
                    try:
                        data = json.loads(line[6:])
                        events.append({"event": current_event, "data": data})
                    except json.JSONDecodeError:
                        pass

        return events

    def test_stream_returns_message_start_and_end(self):
        events = self._collect_events("Réponds uniquement 'OK'.")
        types = [e["event"] for e in events]
        assert "message_start" in types, f"Pas de message_start dans {types}"
        assert "message_end" in types, f"Pas de message_end dans {types}"

    def test_stream_returns_tokens(self):
        events = self._collect_events("Dis juste 'bonjour'.")
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) > 0, "Aucun token reçu dans le stream"
        # Vérifier la structure de chaque token
        for e in token_events:
            assert "content" in e["data"]
            assert isinstance(e["data"]["content"], str)

    def test_stream_message_start_has_conversation_id(self):
        events = self._collect_events("Dis 'test'.")
        starts = [e for e in events if e["event"] == "message_start"]
        assert len(starts) == 1
        assert "conversation_id" in starts[0]["data"]
        assert "agent_id" in starts[0]["data"]
        assert len(starts[0]["data"]["conversation_id"]) > 0

    def test_stream_tokens_form_coherent_response(self):
        events = self._collect_events("Réponds juste 'VÉRIFICATION' en majuscules.")
        full_text = "".join(
            e["data"]["content"] for e in events if e["event"] == "token"
        )
        assert len(full_text) > 0
        assert "VÉRIFICATION" in full_text.upper()

    def test_stream_unknown_agent_returns_error(self):
        with httpx.stream(
            "POST",
            f"{CORE_URL}/chat/stream",
            json={"agent_id": "agent-inexistant", "message": "test"},
            headers=HEADERS,
            timeout=10,
        ) as response:
            # Peut être 200 (stream avec event error) ou 404/500
            if response.status_code == 200:
                events = []
                current_event = None
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        current_event = line[7:]
                    elif line.startswith("data: ") and current_event:
                        try:
                            data = json.loads(line[6:])
                            events.append({"event": current_event, "data": data})
                        except json.JSONDecodeError:
                            pass
                error_events = [e for e in events if e["event"] == "error"]
                assert len(error_events) > 0, "Stream devrait contenir un event 'error'"
            else:
                assert response.status_code in (404, 422, 500)


# --- Mémoire persistante cross-conversation ---

class TestMemory:
    """Teste que l'agent peut écrire un souvenir et le retrouver dans une nouvelle conversation.

    Nécessite que memory_sources soit configuré sur l'agent (migration 003).
    Ce test est lent (deux appels LLM + écriture store).
    """

    def setup_method(self):
        # S'assurer que l'agent a memory_sources configuré
        httpx.put(
            f"{CORE_URL}/agents/assistant-principal",
            json={"memory_sources": ["/memories/AGENTS.md"]},
            headers=HEADERS, timeout=10,
        )

    def test_memory_persists_across_conversations(self):
        secret = "MOTTEST_CERISE"
        # Conversation 1 — demander à l'agent de retenir un mot dans /memories/AGENTS.md
        r1 = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "message": (
                    f"Retiens ce mot dans ta mémoire persistante : {secret}. "
                    "Utilise le tool edit_file pour l'écrire dans /memories/AGENTS.md maintenant."
                )
            },
            headers=HEADERS, timeout=180,
        )
        assert r1.status_code == 200

        # Conversation 2 — nouvelle conversation, demander le mot
        r2 = httpx.post(
            f"{CORE_URL}/chat",
            json={"message": "Quel mot t'ai-je demandé de retenir dans ta mémoire ?"},
            headers=HEADERS, timeout=180,
        )
        assert r2.status_code == 200
        # Vérifier que la réponse contient le mot secret
        response_text = r2.json()["response"].upper()
        assert secret in response_text, (
            f"L'agent n'a pas retrouvé '{secret}' dans la nouvelle conversation. "
            f"Réponse: {r2.json()['response']}"
        )


# --- Sous-agents ---

class TestSubAgents:
    """Teste que l'agent parent peut déléguer une tâche à un sous-agent via le tool 'task'.

    Nécessite que migration 004 ait ajouté le subagent 'researcher' sur assistant-principal.
    Ce test est lent (appel LLM + invocation subagent).
    """

    def setup_method(self):
        # S'assurer que le subagent researcher est configuré
        httpx.put(
            f"{CORE_URL}/agents/assistant-principal",
            json={"subagents": [{
                "name": "researcher",
                "description": "Spécialiste de la recherche d'informations.",
                "system_prompt": "Tu es un expert en recherche. Réponds de façon concise et structurée.",
            }]},
            headers=HEADERS, timeout=10,
        )

    def test_subagent_config_visible_in_api(self):
        resp = httpx.get(f"{CORE_URL}/agents/assistant-principal", headers=HEADERS, timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        subagents = data.get("subagents", [])
        names = [s["name"] for s in subagents]
        assert "researcher" in names

    def test_agent_can_delegate_to_subagent(self):
        # Demander explicitement à l'agent d'utiliser son sous-agent
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "message": (
                    "Utilise ton sous-agent 'researcher' pour répondre à cette question : "
                    "Qu'est-ce que le pattern Factory en programmation ? Réponds en 2 phrases max."
                )
            },
            headers=HEADERS, timeout=180,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["response"]) > 0
        # Vérifier que le tool 'task' a été appelé (délégation au subagent)
        task_calls = [tc for tc in data.get("tool_calls", []) if tc.get("name") == "task"]
        assert len(task_calls) > 0, (
            f"L'agent n'a pas utilisé le tool 'task'. tool_calls={data.get('tool_calls')}"
        )


# --- FilesystemBackend (Bloc D) ---

class TestFilesystemBackend:
    """Teste que FilesystemBackend persiste les fichiers entre conversations.

    Nécessite le volume Docker agent_workspace monté sur /agent-workspace.
    Ce test est lent (deux appels LLM + écritures disque).
    """

    TEST_AGENT_ID = "test-agent-filesystem"

    def setup_method(self):
        httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)
        httpx.post(
            f"{CORE_URL}/agents",
            json={
                "id": self.TEST_AGENT_ID,
                "name": "Agent Test Filesystem",
                "llm_config": {"model": "openai/gpt-4o-mini"},
                "system_prompt": "Tu es un agent de test. Exécute les instructions exactement.",
                "skills": [],
                "backend_config": {"type": "filesystem", "root_dir": "/agent-workspace"},
            },
            headers=HEADERS, timeout=5,
        )

    def teardown_method(self):
        httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)

    def test_filesystem_backend_config_stored_in_api(self):
        resp = httpx.get(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)
        assert resp.status_code == 200
        assert resp.json()["backend_config"]["type"] == "filesystem"

    def test_file_persists_across_conversations(self):
        import uuid
        marker = f"D5_{uuid.uuid4().hex[:8]}"
        filename = f"/test_{uuid.uuid4().hex[:8]}.txt"

        # Conversation 1 : écrire le fichier
        r1 = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "agent_id": self.TEST_AGENT_ID,
                "message": (
                    f"Crée le fichier {filename} et écris-y exactement ce texte: {marker}. "
                    "Utilise write_file."
                ),
            },
            headers=HEADERS, timeout=180,
        )
        assert r1.status_code == 200

        # Conversation 2 : lire le fichier (nouvelle conversation_id)
        r2 = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "agent_id": self.TEST_AGENT_ID,
                "message": f"Lis le fichier {filename} et répète-moi exactement son contenu.",
            },
            headers=HEADERS, timeout=180,
        )
        assert r2.status_code == 200
        assert marker in r2.json()["response"], (
            f"Le marker '{marker}' n'a pas été retrouvé dans la conversation 2. "
            f"Réponse: {r2.json()['response']}"
        )


# --- GET/POST/PUT/DELETE /agents ---

class TestAgentsCRUD:
    TEST_AGENT_ID = "test-agent-bloc-a"

    def setup_method(self):
        # Cleanup au cas ou un test précédent aurait laissé l'agent
        httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)

    def teardown_method(self):
        httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)

    def test_list_agents_returns_assistant_principal(self):
        resp = httpx.get(f"{CORE_URL}/agents", headers=HEADERS, timeout=5)
        assert resp.status_code == 200
        agents = resp.json()
        ids = [a["id"] for a in agents]
        assert "assistant-principal" in ids

    def test_get_agent_by_id(self):
        resp = httpx.get(f"{CORE_URL}/agents/assistant-principal", headers=HEADERS, timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "assistant-principal"
        assert data["is_active"] is True
        assert "llm_config" in data

    def test_get_unknown_agent_returns_404(self):
        resp = httpx.get(f"{CORE_URL}/agents/agent-inexistant", headers=HEADERS, timeout=5)
        assert resp.status_code == 404

    def test_create_agent(self):
        payload = {
            "id": self.TEST_AGENT_ID,
            "name": "Agent Test Bloc A",
            "llm_config": {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
            "system_prompt": "Tu es un agent de test.",
            "skills": [],
            "subagents": [],
        }
        resp = httpx.post(f"{CORE_URL}/agents", json=payload, headers=HEADERS, timeout=5)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == self.TEST_AGENT_ID
        assert data["name"] == "Agent Test Bloc A"
        assert data["is_active"] is True

    def test_create_duplicate_agent_returns_409(self):
        payload = {
            "id": self.TEST_AGENT_ID,
            "name": "Agent Test",
            "llm_config": {"model": "openai/gpt-4o-mini"},
        }
        httpx.post(f"{CORE_URL}/agents", json=payload, headers=HEADERS, timeout=5)
        resp = httpx.post(f"{CORE_URL}/agents", json=payload, headers=HEADERS, timeout=5)
        assert resp.status_code == 409

    def test_update_agent(self):
        # Créer d'abord
        httpx.post(
            f"{CORE_URL}/agents",
            json={"id": self.TEST_AGENT_ID, "name": "Avant", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=HEADERS, timeout=5,
        )
        # Modifier
        resp = httpx.put(
            f"{CORE_URL}/agents/{self.TEST_AGENT_ID}",
            json={"name": "Après"},
            headers=HEADERS, timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Après"

    def test_delete_agent_soft_deletes(self):
        httpx.post(
            f"{CORE_URL}/agents",
            json={"id": self.TEST_AGENT_ID, "name": "À supprimer", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=HEADERS, timeout=5,
        )
        resp = httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)
        assert resp.status_code == 200
        # L'agent ne doit plus apparaître dans la liste active
        agents = httpx.get(f"{CORE_URL}/agents", headers=HEADERS, timeout=5).json()
        ids = [a["id"] for a in agents]
        assert self.TEST_AGENT_ID not in ids

    def test_create_agent_reactivates_soft_deleted(self):
        """POST /agents avec un ID soft-deleted le re-active."""
        payload = {
            "id": self.TEST_AGENT_ID,
            "name": "Réactivé",
            "llm_config": {"model": "openai/gpt-4o-mini"},
        }
        # Create then soft-delete
        httpx.post(f"{CORE_URL}/agents", json=payload, headers=HEADERS, timeout=5)
        httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)
        # Re-create should succeed (reactivation)
        resp = httpx.post(f"{CORE_URL}/agents", json=payload, headers=HEADERS, timeout=5)
        assert resp.status_code == 201
        assert resp.json()["is_active"] is True
        assert resp.json()["name"] == "Réactivé"


# --- HITL — Human-in-the-Loop (Bloc F) ---

class TestHITL:
    """Teste le flow Human-in-the-Loop : interrupt → resume.

    Crée un agent avec interrupt_on={"run_script": True}, envoie un message qui
    déclenche run_script, vérifie l'event SSE "interrupt", puis résume avec
    "approve". Test lent (appel LLM réel).
    """

    TEST_AGENT_ID = "test-agent-hitl"

    def _collect_sse_events(self, agent_id: str, message: str) -> list[dict]:
        """Collecte tous les events SSE d'un stream."""
        events = []
        current_event = None
        with httpx.stream(
            "POST",
            f"{CORE_URL}/chat/stream",
            json={"agent_id": agent_id, "message": message},
            headers=HEADERS,
            timeout=120,
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: ") and current_event:
                    try:
                        data = json.loads(line[6:])
                        events.append({"event": current_event, "data": data})
                    except json.JSONDecodeError:
                        pass
        return events

    def setup_method(self):
        # Supprimer l'agent test s'il existe
        httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)
        # Créer l'agent avec interrupt_on sur run_script
        httpx.post(
            f"{CORE_URL}/agents",
            json={
                "id": self.TEST_AGENT_ID,
                "name": "Agent Test HITL",
                "llm_config": {"model": "openai/gpt-4o-mini"},
                "system_prompt": (
                    "Tu es un agent de test. Quand on te demande d'exécuter un script, "
                    "utilise toujours le tool run_script."
                ),
                "skills": [],
                "interrupt_on": {"run_script": True},
            },
            headers=HEADERS, timeout=5,
        )

    def teardown_method(self):
        httpx.delete(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)

    def test_resume_endpoint_exists(self):
        """Vérifie que POST /chat/{id}/resume répond (même sans vrai interrupt)."""
        import uuid
        fake_conv_id = str(uuid.uuid4())
        resp = httpx.post(
            f"{CORE_URL}/chat/{fake_conv_id}/resume",
            json={"agent_id": self.TEST_AGENT_ID, "decisions": []},
            headers=HEADERS, timeout=10,
        )
        # LangGraph lèvera une erreur car pas de state dans ce thread —
        # on attend 500 ou 422 (pas 404), qui prouve que la route existe
        assert resp.status_code != 404, "La route /resume doit exister"

    def test_resume_endpoint_requires_auth(self):
        """Vérifie que /resume est protégé par API key."""
        import uuid
        resp = httpx.post(
            f"{CORE_URL}/chat/{uuid.uuid4()}/resume",
            json={"agent_id": self.TEST_AGENT_ID, "decisions": []},
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert resp.status_code in (401, 403)

    def test_hitl_agent_config_has_interrupt_on(self):
        """Vérifie que interrupt_on est bien stocké dans la DB."""
        resp = httpx.get(f"{CORE_URL}/agents/{self.TEST_AGENT_ID}", headers=HEADERS, timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("interrupt_on") == {"run_script": True}

    def test_hitl_stream_interrupt_then_approve(self):
        """Flow complet HITL : stream → interrupt → approve → réponse finale.

        L'agent est configuré avec interrupt_on={"run_script": True}.
        On lui envoie un message qui le pousse à appeler run_script.
        On vérifie qu'un event "interrupt" est émis dans le stream SSE,
        puis on envoie approve et on vérifie qu'on obtient une réponse finale.
        """
        events = self._collect_sse_events(
            self.TEST_AGENT_ID,
            "Exécute ce script Python exactement : print('hello HITL'). Utilise run_script.",
        )

        interrupt_events = [e for e in events if e["event"] == "interrupt"]

        if not interrupt_events:
            # L'agent n'a pas choisi run_script — test non déterministe, skip
            pytest.skip("L'agent n'a pas appelé run_script — test HITL non déterministe")

        interrupt = interrupt_events[0]
        conv_id = interrupt["data"]["conversation_id"]
        action_requests = interrupt["data"]["action_requests"]
        assert len(action_requests) > 0
        assert any(r["name"] == "run_script" for r in action_requests)

        # Extraire la conversation_id depuis message_start
        starts = [e for e in events if e["event"] == "message_start"]
        if starts:
            conv_id = starts[0]["data"].get("conversation_id", conv_id)

        # Envoyer l'approbation
        decisions = [{"type": "approve"} for _ in action_requests]
        resp = httpx.post(
            f"{CORE_URL}/chat/{conv_id}/resume",
            json={"agent_id": self.TEST_AGENT_ID, "decisions": decisions},
            headers=HEADERS, timeout=120,
        )
        assert resp.status_code == 200
        result = resp.json()
        assert "conversation_id" in result
        assert "response" in result
        assert len(result["response"]) > 0


# --- Auth JWT (Phase 4 — Bloc A) ---

class TestAuthJWT:
    """Tests pour l'authentification JWT : register, login, refresh, me, accès protégé."""

    TEST_EMAIL = f"test_{uuid.uuid4().hex[:8]}@hiveflows.dev"
    TEST_PASSWORD = "testpass123"
    TEST_NAME = "Test User"

    def test_register_creates_user(self):
        resp = httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": self.TEST_EMAIL, "display_name": self.TEST_NAME, "password": self.TEST_PASSWORD},
            timeout=10,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == self.TEST_EMAIL
        assert data["display_name"] == self.TEST_NAME
        assert "user_id" in data

    def test_register_duplicate_returns_409(self):
        email = f"dup_{uuid.uuid4().hex[:8]}@hiveflows.dev"
        httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "Dup", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        resp = httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "Dup2", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        assert resp.status_code == 409

    def test_login_returns_tokens(self):
        email = f"login_{uuid.uuid4().hex[:8]}@hiveflows.dev"
        httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "Login Test", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        resp = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": email, "password": self.TEST_PASSWORD},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

    def test_login_wrong_password_returns_401(self):
        email = f"wrongpw_{uuid.uuid4().hex[:8]}@hiveflows.dev"
        httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "WrongPW", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        resp = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": email, "password": "mauvais-mot-de-passe"},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_login_unknown_email_returns_401(self):
        resp = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": "inexistant@hiveflows.dev", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_me_returns_user_info(self):
        email = f"me_{uuid.uuid4().hex[:8]}@hiveflows.dev"
        httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "Me Test", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        login = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": email, "password": self.TEST_PASSWORD},
            timeout=10,
        )
        token = login.json()["access_token"]
        resp = httpx.get(
            f"{CORE_URL}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == email
        assert data["display_name"] == "Me Test"
        assert data["is_active"] is True

    def test_me_without_token_returns_401(self):
        resp = httpx.get(f"{CORE_URL}/auth/me", timeout=10)
        assert resp.status_code in (401, 403)

    def test_refresh_returns_new_access_token(self):
        email = f"refresh_{uuid.uuid4().hex[:8]}@hiveflows.dev"
        httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "Refresh", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        login = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": email, "password": self.TEST_PASSWORD},
            timeout=10,
        )
        refresh_token = login.json()["refresh_token"]
        resp = httpx.post(
            f"{CORE_URL}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["expires_in"] > 0

    def test_refresh_with_invalid_token_returns_401(self):
        resp = httpx.post(
            f"{CORE_URL}/auth/refresh",
            json={"refresh_token": "invalid-token"},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_jwt_bearer_accesses_protected_routes(self):
        """Vérifie qu'un JWT Bearer peut accéder aux routes protégées (/agents)."""
        email = f"bearer_{uuid.uuid4().hex[:8]}@hiveflows.dev"
        httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "Bearer", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        login = httpx.post(
            f"{CORE_URL}/auth/login",
            json={"email": email, "password": self.TEST_PASSWORD},
            timeout=10,
        )
        token = login.json()["access_token"]
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_api_key_still_works(self):
        """Rétro-compatibilité : les API keys existantes fonctionnent toujours."""
        resp = httpx.get(f"{CORE_URL}/agents", headers=HEADERS, timeout=10)
        assert resp.status_code == 200

    def test_register_creates_personal_workspace(self):
        """L'inscription crée automatiquement un workspace personnel."""
        email = f"ws_{uuid.uuid4().hex[:8]}@hiveflows.dev"
        resp = httpx.post(
            f"{CORE_URL}/auth/register",
            json={"email": email, "display_name": "WS Test", "password": self.TEST_PASSWORD},
            timeout=10,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "workspace" in data
        assert "id" in data["workspace"]
        assert "slug" in data["workspace"]
        assert data["workspace"]["name"] == "WS Test"


# --- Workspaces (Phase 4 — Bloc B) ---

def _create_user_with_token(name_prefix: str = "wstest") -> tuple[str, str, dict]:
    """Helper: register a user, login, return (email, access_token, register_data)."""
    email = f"{name_prefix}_{uuid.uuid4().hex[:8]}@hiveflows.dev"
    reg = httpx.post(
        f"{CORE_URL}/auth/register",
        json={"email": email, "display_name": name_prefix.title(), "password": "testpass123"},
        timeout=10,
    )
    assert reg.status_code == 201
    login = httpx.post(
        f"{CORE_URL}/auth/login",
        json={"email": email, "password": "testpass123"},
        timeout=10,
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    return email, token, reg.json()


class TestWorkspaces:
    """Tests CRUD workspaces + membres (Phase 4 Bloc B)."""

    def test_list_workspaces_returns_personal(self):
        """Un user nouvellement inscrit voit son workspace personnel."""
        _, token, _ = _create_user_with_token("listws")
        resp = httpx.get(
            f"{CORE_URL}/workspaces",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        workspaces = resp.json()
        assert len(workspaces) >= 1
        assert any(w["role"] == "owner" for w in workspaces)

    def test_create_workspace(self):
        _, token, _ = _create_user_with_token("createws")
        slug = f"ws-{uuid.uuid4().hex[:8]}"
        resp = httpx.post(
            f"{CORE_URL}/workspaces",
            json={"name": "Equipe Test", "slug": slug, "description": "Test workspace"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Equipe Test"
        assert data["slug"] == slug

    def test_create_workspace_duplicate_slug_returns_409(self):
        _, token, _ = _create_user_with_token("dupslug")
        slug = f"dup-{uuid.uuid4().hex[:8]}"
        httpx.post(
            f"{CORE_URL}/workspaces",
            json={"name": "First", "slug": slug},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp = httpx.post(
            f"{CORE_URL}/workspaces",
            json={"name": "Second", "slug": slug},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 409

    def test_get_workspace(self):
        _, token, reg = _create_user_with_token("getws")
        ws_id = reg["workspace"]["id"]
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == ws_id

    def test_update_workspace(self):
        _, token, reg = _create_user_with_token("updws")
        ws_id = reg["workspace"]["id"]
        resp = httpx.put(
            f"{CORE_URL}/workspaces/{ws_id}",
            json={"name": "Nouveau Nom"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Nouveau Nom"

    def test_delete_workspace(self):
        _, token, _ = _create_user_with_token("delws")
        slug = f"del-{uuid.uuid4().hex[:8]}"
        create = httpx.post(
            f"{CORE_URL}/workspaces",
            json={"name": "To Delete", "slug": slug},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        ws_id = create.json()["id"]
        resp = httpx.delete(
            f"{CORE_URL}/workspaces/{ws_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 204

    def test_list_members(self):
        _, token, reg = _create_user_with_token("memws")
        ws_id = reg["workspace"]["id"]
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}/members",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        members = resp.json()
        assert len(members) == 1
        assert members[0]["role"] == "owner"

    def test_add_member(self):
        _, owner_token, reg = _create_user_with_token("owner")
        ws_id = reg["workspace"]["id"]
        member_email, _, _ = _create_user_with_token("member")

        resp = httpx.post(
            f"{CORE_URL}/workspaces/{ws_id}/members",
            json={"email": member_email, "role": "member"},
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=10,
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "member"

        # Verify member count
        members = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}/members",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=10,
        ).json()
        assert len(members) == 2

    def test_update_member_role(self):
        _, owner_token, reg = _create_user_with_token("roleown")
        ws_id = reg["workspace"]["id"]
        member_email, _, member_reg = _create_user_with_token("rolemem")
        member_user_id = member_reg["user_id"]

        httpx.post(
            f"{CORE_URL}/workspaces/{ws_id}/members",
            json={"email": member_email, "role": "member"},
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=10,
        )
        resp = httpx.put(
            f"{CORE_URL}/workspaces/{ws_id}/members/{member_user_id}",
            json={"role": "admin"},
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    def test_remove_member(self):
        _, owner_token, reg = _create_user_with_token("remown")
        ws_id = reg["workspace"]["id"]
        member_email, _, member_reg = _create_user_with_token("remmem")
        member_user_id = member_reg["user_id"]

        httpx.post(
            f"{CORE_URL}/workspaces/{ws_id}/members",
            json={"email": member_email, "role": "member"},
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=10,
        )
        resp = httpx.delete(
            f"{CORE_URL}/workspaces/{ws_id}/members/{member_user_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=10,
        )
        assert resp.status_code == 204

    def test_non_member_cannot_access_workspace(self):
        _, _, reg = _create_user_with_token("priv1")
        ws_id = reg["workspace"]["id"]
        _, outsider_token, _ = _create_user_with_token("priv2")

        resp = httpx.get(
            f"{CORE_URL}/workspaces/{ws_id}",
            headers={"Authorization": f"Bearer {outsider_token}"},
            timeout=10,
        )
        assert resp.status_code == 403

    def test_member_cannot_delete_workspace(self):
        _, owner_token, reg = _create_user_with_token("delown")
        ws_id = reg["workspace"]["id"]
        member_email, member_token, _ = _create_user_with_token("delmem")

        httpx.post(
            f"{CORE_URL}/workspaces/{ws_id}/members",
            json={"email": member_email, "role": "member"},
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=10,
        )
        resp = httpx.delete(
            f"{CORE_URL}/workspaces/{ws_id}",
            headers={"Authorization": f"Bearer {member_token}"},
            timeout=10,
        )
        assert resp.status_code == 403


# --- RBAC Permissions (Phase 4 — Bloc D) ---


def _add_member_with_role(owner_token: str, ws_id: str, role: str) -> tuple[str, str]:
    """Create a user, add as member with given role, return (email, token)."""
    email, token, _ = _create_user_with_token(f"rbac_{role}")
    httpx.post(
        f"{CORE_URL}/workspaces/{ws_id}/members",
        json={"email": email, "role": role},
        headers={"Authorization": f"Bearer {owner_token}"},
        timeout=10,
    )
    return email, token


class TestPermissionsRBAC:
    """Tests RBAC permission enforcement (Phase 4 Bloc D).

    D5 spec:
    - viewer cannot create agent
    - member cannot delete agent
    - admin can do everything except delete workspace
    """

    @pytest.fixture(autouse=True)
    def setup_workspace(self):
        """Create a workspace with owner + members of each role."""
        self.owner_email, self.owner_token, reg = _create_user_with_token("rbacown")
        self.ws_id = reg["workspace"]["id"]

        _, self.admin_token = _add_member_with_role(self.owner_token, self.ws_id, "admin")
        _, self.member_token = _add_member_with_role(self.owner_token, self.ws_id, "member")
        _, self.viewer_token = _add_member_with_role(self.owner_token, self.ws_id, "viewer")

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}", "X-Workspace-Id": self.ws_id}

    # ── Viewer cannot create agent ──

    def test_viewer_cannot_create_agent(self):
        resp = httpx.post(
            f"{CORE_URL}/agents",
            json={"id": f"v-{uuid.uuid4().hex[:8]}", "name": "Test", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=self._auth(self.viewer_token),
            timeout=10,
        )
        assert resp.status_code == 403

    def test_viewer_cannot_delete_agent(self):
        # Create agent as owner first
        agent_id = f"vd-{uuid.uuid4().hex[:8]}"
        httpx.post(
            f"{CORE_URL}/agents",
            json={"id": agent_id, "name": "Test", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=self._auth(self.owner_token),
            timeout=10,
        )
        resp = httpx.delete(
            f"{CORE_URL}/agents/{agent_id}",
            headers=self._auth(self.viewer_token),
            timeout=10,
        )
        assert resp.status_code == 403

    def test_viewer_can_list_agents(self):
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers=self._auth(self.viewer_token),
            timeout=10,
        )
        assert resp.status_code == 200

    def test_viewer_can_view_tasks(self):
        resp = httpx.get(
            f"{CORE_URL}/tasks/00000000-0000-0000-0000-000000000000",
            headers=self._auth(self.viewer_token),
            timeout=10,
        )
        # 404 (not found) is fine — it means auth passed
        assert resp.status_code == 404

    def test_viewer_cannot_run_task(self):
        resp = httpx.post(
            f"{CORE_URL}/tasks/run",
            json={"type": "python", "script": "print(1)"},
            headers=self._auth(self.viewer_token),
            timeout=10,
        )
        assert resp.status_code == 403

    def test_viewer_cannot_chat(self):
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={"message": "test"},
            headers=self._auth(self.viewer_token),
            timeout=10,
        )
        assert resp.status_code == 403

    # ── Member can create but cannot delete agent ──

    def test_member_can_create_agent(self):
        agent_id = f"mc-{uuid.uuid4().hex[:8]}"
        resp = httpx.post(
            f"{CORE_URL}/agents",
            json={"id": agent_id, "name": "Member Agent", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=self._auth(self.member_token),
            timeout=10,
        )
        assert resp.status_code == 201
        # Cleanup
        httpx.delete(f"{CORE_URL}/agents/{agent_id}", headers=self._auth(self.owner_token), timeout=5)

    def test_member_cannot_delete_agent(self):
        agent_id = f"md-{uuid.uuid4().hex[:8]}"
        httpx.post(
            f"{CORE_URL}/agents",
            json={"id": agent_id, "name": "Test", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=self._auth(self.owner_token),
            timeout=10,
        )
        resp = httpx.delete(
            f"{CORE_URL}/agents/{agent_id}",
            headers=self._auth(self.member_token),
            timeout=10,
        )
        assert resp.status_code == 403
        # Cleanup
        httpx.delete(f"{CORE_URL}/agents/{agent_id}", headers=self._auth(self.owner_token), timeout=5)

    def test_member_cannot_manage_members(self):
        new_email, _, _ = _create_user_with_token("rbacnew")
        resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/members",
            json={"email": new_email, "role": "viewer"},
            headers={"Authorization": f"Bearer {self.member_token}"},
            timeout=10,
        )
        assert resp.status_code == 403

    # ── Admin can do everything except delete workspace ──

    def test_admin_can_create_agent(self):
        agent_id = f"ac-{uuid.uuid4().hex[:8]}"
        resp = httpx.post(
            f"{CORE_URL}/agents",
            json={"id": agent_id, "name": "Admin Agent", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=self._auth(self.admin_token),
            timeout=10,
        )
        assert resp.status_code == 201
        # Cleanup
        httpx.delete(f"{CORE_URL}/agents/{agent_id}", headers=self._auth(self.owner_token), timeout=5)

    def test_admin_can_delete_agent(self):
        agent_id = f"ad-{uuid.uuid4().hex[:8]}"
        httpx.post(
            f"{CORE_URL}/agents",
            json={"id": agent_id, "name": "Test", "llm_config": {"model": "openai/gpt-4o-mini"}},
            headers=self._auth(self.owner_token),
            timeout=10,
        )
        resp = httpx.delete(
            f"{CORE_URL}/agents/{agent_id}",
            headers=self._auth(self.admin_token),
            timeout=10,
        )
        assert resp.status_code == 200

    def test_admin_can_manage_members(self):
        new_email, _, _ = _create_user_with_token("rbacadminnew")
        resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/members",
            json={"email": new_email, "role": "viewer"},
            headers={"Authorization": f"Bearer {self.admin_token}"},
            timeout=10,
        )
        assert resp.status_code == 201

    def test_admin_cannot_delete_workspace(self):
        resp = httpx.delete(
            f"{CORE_URL}/workspaces/{self.ws_id}",
            headers={"Authorization": f"Bearer {self.admin_token}"},
            timeout=10,
        )
        assert resp.status_code == 403

    def test_admin_cannot_change_roles(self):
        # Admin should not be able to change roles (only owner can)
        member_email, _, member_reg = _create_user_with_token("rbacroletgt")
        member_user_id = member_reg["user_id"]
        httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/members",
            json={"email": member_email, "role": "viewer"},
            headers={"Authorization": f"Bearer {self.owner_token}"},
            timeout=10,
        )
        resp = httpx.put(
            f"{CORE_URL}/workspaces/{self.ws_id}/members/{member_user_id}",
            json={"role": "admin"},
            headers={"Authorization": f"Bearer {self.admin_token}"},
            timeout=10,
        )
        assert resp.status_code == 403


# --- DB API Keys (Phase 4 — Bloc E) ---


class TestDBApiKeys:
    """Tests for workspace-scoped DB API keys: create, use for auth, revoke."""

    @pytest.fixture(autouse=True)
    def setup_user(self):
        self.email, self.token, reg = _create_user_with_token("apikey")
        self.ws_id = reg["workspace"]["id"]

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_create_api_key(self):
        resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys",
            json={"name": "Test Key"},
            headers=self._auth(),
            timeout=10,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Key"
        assert data["key"].startswith("hf_")
        assert len(data["key"]) > 20
        assert "key_prefix" in data
        assert "permissions" in data

    def test_list_api_keys(self):
        # Create a key first
        httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys",
            json={"name": "List Test"},
            headers=self._auth(),
            timeout=10,
        )
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys",
            headers=self._auth(),
            timeout=10,
        )
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) >= 1
        assert any(k["name"] == "List Test" for k in keys)
        # Full key should NOT be in list response
        for k in keys:
            assert "key" not in k or not k.get("key", "").startswith("hf_")

    def test_use_db_api_key_for_auth(self):
        """Create a DB API key, then use it to access /agents."""
        create_resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys",
            json={"name": "Auth Test", "permissions": ["chat", "tasks", "agents"]},
            headers=self._auth(),
            timeout=10,
        )
        assert create_resp.status_code == 201
        full_key = create_resp.json()["key"]

        # Use the DB key to list agents
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers={"X-API-Key": full_key},
            timeout=10,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_db_api_key_permission_denied(self):
        """A key with only 'chat' permission cannot access tasks."""
        create_resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys",
            json={"name": "Chat Only", "permissions": ["chat"]},
            headers=self._auth(),
            timeout=10,
        )
        assert create_resp.status_code == 201
        full_key = create_resp.json()["key"]

        # Try to run a task — should be denied
        resp = httpx.post(
            f"{CORE_URL}/tasks/run",
            json={"type": "python", "script": "print(1)"},
            headers={"X-API-Key": full_key, "Content-Type": "application/json"},
            timeout=10,
        )
        assert resp.status_code == 403

    def test_revoke_api_key(self):
        """Create, revoke, then verify the key no longer works."""
        create_resp = httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys",
            json={"name": "Revoke Test"},
            headers=self._auth(),
            timeout=10,
        )
        assert create_resp.status_code == 201
        full_key = create_resp.json()["key"]
        key_id = create_resp.json()["id"]

        # Revoke
        resp = httpx.delete(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys/{key_id}",
            headers=self._auth(),
            timeout=10,
        )
        assert resp.status_code == 204

        # Verify key no longer works
        resp = httpx.get(
            f"{CORE_URL}/agents",
            headers={"X-API-Key": full_key},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_member_cannot_manage_api_keys(self):
        """Only owner/admin can manage API keys."""
        member_email, member_token, _ = _create_user_with_token("apikeymem")
        httpx.post(
            f"{CORE_URL}/workspaces/{self.ws_id}/members",
            json={"email": member_email, "role": "member"},
            headers=self._auth(),
            timeout=10,
        )
        resp = httpx.get(
            f"{CORE_URL}/workspaces/{self.ws_id}/api-keys",
            headers={"Authorization": f"Bearer {member_token}"},
            timeout=10,
        )
        assert resp.status_code == 403


# --- Agent-to-Agent Communication (Phase 4 — Bloc F) ---


class TestAgentToAgent:
    """Tests for agent-to-agent communication via message_agent tool.

    Creates two agents in the same workspace:
    - Agent A (coordinator): told to delegate to Agent B
    - Agent B (responder): answers with a known marker
    """

    AGENT_A = "test-a2a-coordinator"
    AGENT_B = "test-a2a-responder"

    @pytest.fixture(autouse=True)
    def setup_agents(self):
        self.email, self.token, reg = _create_user_with_token("a2a")
        self.ws_id = reg["workspace"]["id"]
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Workspace-Id": self.ws_id,
        }

        # Create Agent B (responder) first
        httpx.delete(f"{CORE_URL}/agents/{self.AGENT_B}", headers=self._headers, timeout=5)
        httpx.post(
            f"{CORE_URL}/agents",
            json={
                "id": self.AGENT_B,
                "name": "Responder B",
                "llm_config": {"model": "openai/gpt-4o-mini"},
                "system_prompt": (
                    "Tu es un agent de test. Quand on te pose une question, "
                    "reponds toujours en incluant le mot exact 'PONG42' dans ta reponse."
                ),
                "tools": ["none"],
            },
            headers=self._headers, timeout=10,
        )

        # Create Agent A (coordinator)
        httpx.delete(f"{CORE_URL}/agents/{self.AGENT_A}", headers=self._headers, timeout=5)
        httpx.post(
            f"{CORE_URL}/agents",
            json={
                "id": self.AGENT_A,
                "name": "Coordinator A",
                "llm_config": {"model": "openai/gpt-4o-mini"},
                "system_prompt": (
                    "Tu es un coordinateur. Quand on te demande de contacter un autre agent, "
                    "utilise TOUJOURS le tool message_agent avec l'agent_id qu'on te donne "
                    "et transmets le message exactement. Rapporte la reponse exacte que tu recois."
                ),
            },
            headers=self._headers, timeout=10,
        )
        yield
        httpx.delete(f"{CORE_URL}/agents/{self.AGENT_A}", headers=self._headers, timeout=5)
        httpx.delete(f"{CORE_URL}/agents/{self.AGENT_B}", headers=self._headers, timeout=5)

    def test_agent_a_calls_agent_b(self):
        """Agent A uses message_agent to call Agent B, gets PONG42 marker back."""
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "agent_id": self.AGENT_A,
                "message": (
                    f"Contacte l'agent '{self.AGENT_B}' via message_agent et "
                    "demande-lui 'Dis-moi le mot secret'. Rapporte sa reponse exacte."
                ),
            },
            headers=self._headers, timeout=180,
        )
        assert resp.status_code == 200
        data = resp.json()
        # The coordinator should have called message_agent and relayed PONG42
        tool_names = [tc["name"] for tc in data.get("tool_calls", [])]
        assert "message_agent" in tool_names, (
            f"Agent A should have used message_agent. Tool calls: {tool_names}"
        )
        assert "PONG42" in data["response"], (
            f"Expected PONG42 in response. Got: {data['response'][:300]}"
        )

    def test_message_agent_wrong_workspace_denied(self):
        """An agent cannot message an agent from a different workspace."""
        # Create a second workspace with an agent
        _, other_token, other_reg = _create_user_with_token("a2aother")
        other_ws_id = other_reg["workspace"]["id"]
        other_headers = {
            "Authorization": f"Bearer {other_token}",
            "X-Workspace-Id": other_ws_id,
        }
        httpx.post(
            f"{CORE_URL}/agents",
            json={
                "id": "test-a2a-foreign",
                "name": "Foreign Agent",
                "llm_config": {"model": "openai/gpt-4o-mini"},
                "tools": ["none"],
            },
            headers=other_headers, timeout=10,
        )

        # Agent A tries to message the foreign agent — should fail
        resp = httpx.post(
            f"{CORE_URL}/chat",
            json={
                "agent_id": self.AGENT_A,
                "message": (
                    "Contacte l'agent 'test-a2a-foreign' via message_agent "
                    "et demande-lui 'hello'."
                ),
            },
            headers=self._headers, timeout=180,
        )
        assert resp.status_code == 200
        # The response should mention an error about the agent not being found
        response_lower = resp.json()["response"].lower()
        assert "error" in response_lower or "not found" in response_lower or "erreur" in response_lower, (
            f"Expected error about foreign agent. Got: {resp.json()['response'][:300]}"
        )

        # Cleanup
        httpx.delete(f"{CORE_URL}/agents/test-a2a-foreign", headers=other_headers, timeout=5)

    def test_message_agent_stream_shows_tool_events(self):
        """SSE stream includes tool_call/tool_result events for message_agent."""
        events = []
        current_event = None
        with httpx.stream(
            "POST",
            f"{CORE_URL}/chat/stream",
            json={
                "agent_id": self.AGENT_A,
                "message": (
                    f"Contacte l'agent '{self.AGENT_B}' via message_agent et "
                    "demande-lui 'Dis-moi le mot secret'."
                ),
            },
            headers=self._headers,
            timeout=180,
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: ") and current_event:
                    try:
                        data = json.loads(line[6:])
                        events.append({"event": current_event, "data": data})
                    except json.JSONDecodeError:
                        pass

        tool_calls = [e for e in events if e["event"] == "tool_call"]
        tool_results = [e for e in events if e["event"] == "tool_result"]

        msg_calls = [tc for tc in tool_calls if tc["data"].get("name") == "message_agent"]
        msg_results = [tr for tr in tool_results if tr["data"].get("name") == "message_agent"]

        if not msg_calls:
            pytest.skip("Agent did not use message_agent — non-deterministic LLM behavior")

        assert len(msg_calls) >= 1, "Should have at least one message_agent tool_call event"
        assert len(msg_results) >= 1, "Should have at least one message_agent tool_result event"
        assert "PONG42" in msg_results[0]["data"]["output"], (
            f"Expected PONG42 in tool result. Got: {msg_results[0]['data']['output'][:200]}"
        )
