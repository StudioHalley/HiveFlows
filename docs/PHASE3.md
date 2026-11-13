# HiveFlows — Phase 3 : Multi-Agents & Streaming

> Objectif : des agents configurables depuis la DB, capables de spawner des sous-agents, de se souvenir entre les conversations, de demander l'approbation humaine, et accessibles via streaming (SSE) dans un dashboard web HTMX.

---

## 1. Perimetre de la Phase 3

### Dans le scope
- Agent Registry — AgentConfig chargee depuis la DB + API CRUD
- Sous-agents via le tool `task` natif de Deep Agents
- Memoire persistante cross-conversation (LangGraph Store + PostgreSQL)
- Contexte partage via backends Deep Agents (Filesystem + Composite)
- Human-in-the-loop — interruption et approbation sur les outils sensibles
- Streaming SSE — reponses en temps reel (API + CLI)
- Dashboard web minimal — HTMX + Jinja2 integre dans Core

### Hors scope (phases suivantes)
- Agent-to-agent via message queue (Phase 4)
- Permissions skills par agent (Phase 4)
- Workspaces / multi-usagers (Phase 4)
- OAuth2 (Phase 4)
- Interface web complete (Phase 4)

---

## 2. Architecture Phase 3

```
                   +-----------------------------+
                   |      DASHBOARD WEB          |
                   |  HTMX + Jinja2 (dans Core)  |
                   |  /dashboard/*               |
                   +-------------+---------------+
                                 |
+----------------+               |               +----------------+
|  CHANNEL CLI   |               |               |   API REST     |
|  (streaming)   |               |               |  POST /chat/*  |
+-------+--------+               |               +-------+--------+
        |                        |                        |
        +------------------------+------------------------+
                                 | HTTP + SSE
                                 v
+----------------------------------------------------------------+
|                    CORE SERVICE (FastAPI)                      |
|                                                                |
|  GET/POST/PUT/DELETE /agents   -> Agent Registry               |
|  POST /chat                    -> invoke (sync)                |
|  POST /chat/stream             -> SSE streaming                |
|  POST /chat/{id}/resume        -> HITL resume                  |
|  GET  /tasks/{id}              -> Task Status                  |
|  GET  /dashboard/*             -> HTMX pages                   |
|                                                                |
|  +----------------------------------------------------------+  |
|  |              AGENT MANAGER (multi-agent)                  | |
|  |                                                           | |
|  |  AgentConfig DB  -->  build_agent_from_config()           | |
|  |                                                           | |
|  |  +------------------+    +------------------+             | |
|  |  | Agent Principal  |    | Agent Recherche  |  ...        | |
|  |  |  +------------+  |    |  +------------+  |             | |
|  |  |  | SubAgent 1 |  |    |  | SubAgent A |  |             | |
|  |  |  +------------+  |    |  +------------+  |             | |
|  |  +------------------+    +------------------+             | |
|  +----------------------------------------------------------+  |
|                    |                    |                      |
|            +-------+-------+    +------+------+                |
|            | LangGraph     |    | LangGraph   |                |
|            | Checkpointer  |    | Store       |                |
|            | (threads)     |    | (memoire)   |                |
|            +-------+-------+    +------+------+                |
|                    |                    |                      |
+--------------------+--------------------+---------+------------+
                     |                              |
                     v                              v
              +------+------+             +---------+---------+
              |  PostgreSQL |             |   REDIS (broker)  |
              |  - configs  |             +--------+----------+
              |  - threads  |                      |
              |  - store    |                      v
              |  - tasks    |             +--------+----------+
              +-------------+             |   WORKER Celery   |
                                          +-------------------+
```

---

## 3. Decisions Techniques

| Decision | Choix | Justification |
|----------|-------|---------------|
| Dashboard | HTMX + Jinja2 dans Core | Pas de service separe, rapide a livrer, SSE natif |
| Multi-agent | Parent -> SubAgent via `task` | Natif deepagents, isole le contexte |
| Agent-to-agent | Reporte Phase 4 | Complexe, pas necessaire pour le MVP |
| Memoire | LangGraph Store + PostgreSQL | Cross-conversation, persistant, natif |
| Backend | CompositeBackend | Route /memories/ -> Store, /temp/ -> State |
| Streaming | SSE (Server-Sent Events) | Uni-directionnel suffit, supporte par HTMX |
| Permissions skills | Reporte Phase 4 | Sera integre avec le systeme de workspaces |
| HITL | interrupt_on par tool | Configurable par agent dans la DB |

---

## 4. Schemas de Donnees (Changements)

### Table `agent_configs` — colonnes ajoutees

```sql
ALTER TABLE agent_configs ADD COLUMN
    tools           JSONB DEFAULT '[]',        -- ["run_script", "run_skill_script"]
    skills          JSONB DEFAULT '[]',        -- ["demo", "web-search"]
    subagents       JSONB DEFAULT '[]',        -- [{"name":"researcher", ...}]
    memory_sources  JSONB DEFAULT '[]',        -- ["/memories/AGENTS.md"]
    interrupt_on    JSONB DEFAULT '{}',        -- {"run_script": true}
    backend_config  JSONB DEFAULT '{}',        -- {"type": "composite", ...}
    updated_at      TIMESTAMP DEFAULT NOW();
```

### Tables LangGraph Store
Creees automatiquement par `langgraph.store.postgres` au setup :
- `store` — stockage cle/valeur namespace pour la memoire persistante

### Migration Alembic
- `002_agent_configs_phase3.py` — ajout des colonnes + seed "assistant-principal"

---

## 5. Contrats d'Interface (Nouveaux Endpoints)

### `GET /agents`
Liste tous les agents configures.

**Response :**
```json
[
  {
    "id": "assistant-principal",
    "name": "Assistant Principal",
    "description": "Agent generaliste HiveFlows",
    "is_active": true,
    "llm_config": {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
    "skills": ["demo"],
    "subagents": [{"name": "researcher", "description": "..."}],
    "created_at": "2026-03-06T10:00:00Z"
  }
]
```

### `POST /agents`
Creer un nouvel agent.

**Request :**
```json
{
  "id": "data-analyst",
  "name": "Data Analyst",
  "description": "Agent specialise en analyse de donnees",
  "llm_config": {"provider": "openrouter", "model": "openai/gpt-4o"},
  "system_prompt": "Tu es un analyste de donnees...",
  "skills": ["shell"],
  "subagents": [
    {
      "name": "sql-expert",
      "description": "Genere et execute des requetes SQL",
      "system_prompt": "Tu es un expert SQL..."
    }
  ],
  "interrupt_on": {"run_script": true}
}
```

### `GET /agents/{id}`
Detail d'un agent.

### `PUT /agents/{id}`
Modifier un agent. Invalide le cache de l'AgentManager (force rebuild au prochain invoke).

### `DELETE /agents/{id}`
Desactiver un agent (`is_active = false`).

### `POST /chat/stream`
Streaming SSE. Meme body que `POST /chat`.

**Request :**
```json
{
  "agent_id": "assistant-principal",
  "message": "Explique-moi les design patterns",
  "conversation_id": "uuid-optionnel"
}
```

**Response SSE :**
```
event: message_start
data: {"conversation_id": "uuid", "agent_id": "assistant-principal"}

event: token
data: {"content": "Les design"}

event: token
data: {"content": " patterns sont"}

event: tool_call
data: {"name": "run_script", "args": {"script": "print('hello')", "type": "python"}}

event: tool_result
data: {"name": "run_script", "output": "hello\n"}

event: interrupt
data: {"tool_calls": [{"name": "run_script", "args": {...}}], "thread_id": "uuid"}

event: message_end
data: {"message_id": "uuid", "usage": {"input_tokens": 42, "output_tokens": 150}}
```

### `POST /chat/{conversation_id}/resume`
Reprendre une conversation interrompue (HITL).

**Request :**
```json
{
  "agent_id": "assistant-principal",
  "decisions": [
    {"type": "approve"}
  ]
}
```

**Response :** Meme format que `POST /chat` (ou SSE si appele via stream).

---

## 6. Dashboard Web Minimal (HTMX + Jinja2)

### Pages

| Route | Page | Description |
|-------|------|-------------|
| `/dashboard` | Accueil | Agents actifs, conversations recentes, tasks en cours |
| `/dashboard/chat` | Chat | Chat avec un agent, streaming SSE, approbation HITL |
| `/dashboard/chat/{id}` | Conversation | Reprendre une conversation existante |
| `/dashboard/agents` | Agents | Liste, creer, modifier, supprimer des agents |
| `/dashboard/agents/{id}` | Detail agent | Config complete, subagents, historique |
| `/dashboard/tasks` | Tasks | Liste des tasks executees (status, output) |

### Stack technique
- **Jinja2** templates dans `core/templates/`
- **HTMX** pour les interactions dynamiques (pas de JS custom)
- **SSE** pour le streaming du chat (htmx-ext-sse)
- **CSS minimal** — classless CSS (Pico, Simple.css, ou Water.css)
- Fichiers statiques dans `core/static/`

### Structure fichiers
```
core/
  templates/
    base.html            # Layout de base (nav, head, body)
    dashboard/
      index.html         # Page accueil
      chat.html          # Interface chat
      agents/
        list.html        # Liste des agents
        detail.html      # Detail / edit agent
        create.html      # Formulaire creation
      tasks/
        list.html        # Liste des tasks
  static/
    style.css            # Overrides CSS minimal
```

---

## 7. Streaming — Details Techniques

### Cote serveur (FastAPI)
```python
from fastapi.responses import StreamingResponse

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    async def event_generator():
        async for event in agent.astream_events(input, config=config):
            # Formatter en SSE
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### Cote CLI
```python
# httpx streaming
async with httpx.AsyncClient() as client:
    async with client.stream("POST", url, json=payload) as response:
        async for line in response.aiter_lines():
            # Parser les events SSE et afficher en temps reel
```

### Cote Dashboard (HTMX)
```html
<div hx-ext="sse" sse-connect="/chat/stream" sse-swap="token">
  <!-- Les tokens arrivent en temps reel -->
</div>
```

---

## 8. Human-in-the-Loop — Workflow

```
Utilisateur envoie un message
        |
        v
Agent reflechit... (streaming tokens)
        |
        v
Agent veut appeler run_script()
        |
    interrupt_on: {"run_script": true}  ?
        |
   +----+----+
   | OUI     | NON
   v         v
PAUSE     Execute normalement
   |
   v
API retourne event "interrupt"
avec les details du tool call
   |
   v
Utilisateur voit le script dans le dashboard/CLI
   |
   +-----> [Approuver] --> POST /chat/{id}/resume {"decisions": [{"type":"approve"}]}
   |
   +-----> [Rejeter]   --> POST /chat/{id}/resume {"decisions": [{"type":"reject"}]}
   |
   +-----> [Modifier]  --> POST /chat/{id}/resume {"decisions": [{"type":"edit", "args": {...}}]}
```

---

## 9. Plan de Taches (Ordonne par Dependances)

### Bloc A — Agent Registry (DB + CRUD API)
```
Depend de : Phase 2
A1. [x] Migration Alembic : nouvelles colonnes agent_configs
A2. [x] Seed "assistant-principal" dans la migration
A3. [x] Schema Pydantic AgentConfigCreate / AgentConfigRead
A4. [x] Routes CRUD : GET/POST/PUT/DELETE /agents
A5. [x] build_agent_from_config() — factory qui cree un agent depuis un AgentConfig DB
A6. [x] AgentManager : charger depuis DB au lieu de hardcode, cache + invalidation
A7. [x] Test : CRUD API + build agent depuis config DB
```

### Bloc B — Memoire Persistante (LangGraph Store)
```
Depend de : A6
B1. [x] pip install langgraph-store-postgres (ou equivalent) — inclus dans langgraph-checkpoint-postgres>=2.0
B2. [x] Setup du Store PostgreSQL au startup (dans AgentManager.setup())
B3. [x] Passer store= + backend= (CompositeBackend) + memory= a create_deep_agent()
B4. [x] Migration 003 : memory_sources=["/memories/AGENTS.md"] pour assistant-principal
B5. [x] Test : agent ecrit un souvenir, nouvelle conversation le retrouve (TestMemory)
```

### Bloc C — Sous-Agents
```
Depend de : A6
C1. [x] Supporter subagents dans build_agent_from_config() — deja fait dans Bloc A
C2. [x] Passer subagents= a create_deep_agent() depuis la config DB — deja fait dans Bloc A
C3. [x] Migration 004 : subagent "researcher" sur assistant-principal
C4. [x] Test : agent parent spawne un subagent via le tool "task" (TestSubAgents)
```

### Bloc D — Backends & Contexte Partage
```
Depend de : A6
D1. [x] Configurer FilesystemBackend avec root_dir (volume Docker /agent-workspace)
D2. [x] CompositeBackend : /memories/ -> StoreBackend, reste -> backend selectionne — deja fait Bloc B
D3. [x] Passer backend= depuis backend_config DB (StateBackend ou FilesystemBackend dynamique)
D4. [x] Volume Docker agent_workspace dans docker-compose.yml
D5. [x] Test : agent ecrit un fichier, le retrouve dans une autre invocation (TestFilesystemBackend)
```

### Bloc E — Streaming SSE
```
Depend de : A6
E1. [x] Route POST /chat/stream — SSE via StreamingResponse
E2. [x] Formater les events LangGraph en SSE (token, tool_call, tool_result, message_end)
E3. [x] Gerer le conversation_id dans le stream (message_start event)
E4. [x] CLI : --stream flag avec httpx synchrone (affichage temps reel)
E5. [x] Test : stream complet, verifier message_start/token/message_end (TestStreamSSE x5)
```

### Bloc F — Human-in-the-Loop
```
Depend de : E5 (le streaming rend le HITL beaucoup plus naturel)
F1. [x] Supporter interrupt_on dans build_agent_from_config()
F2. [x] Detecter l'etat "interrupted" dans la reponse agent
F3. [x] Event SSE "interrupt" avec les tool calls en attente
F4. [x] Route POST /chat/{conversation_id}/resume
F5. [x] CLI : afficher le tool call en attente, demander approbation, envoyer resume
F6. [x] Test : agent interrompu sur run_script, approve, agent continue
```

### Bloc G — Dashboard Web (HTMX + Jinja2)
```
Depend de : E5, F6
G1. [x] Setup Jinja2 + static files dans FastAPI
G2. [x] Template de base (base.html) — layout, nav, CSS (style landing HiveFlows)
G3. [x] Page /dashboard — accueil avec stats
G4. [x] Page /dashboard/agents — liste + formulaire creation/edition
G5. [x] Page /dashboard/chat — interface chat avec streaming SSE
G6. [x] Page /dashboard/chat — support HITL (boutons approve/reject)
G7. [x] Page /dashboard/tasks — liste des tasks avec statut
G8. [x] Test : navigation complete du dashboard (200 OK sur serveur distant)
```

### Bloc H — Finition et Validation
```
Depend de : G8
H1. [x] Tests unitaires : Agent Registry CRUD (TestAgentsCRUD dans test_api_integration.py)
H2. [x] Tests unitaires : streaming SSE events (TestStreamSSE)
H3. [x] Tests integration : chat stream end-to-end (TestStreamSSE x5)
H4. [x] Tests integration : HITL flow complet (TestHITL)
H5. [x] Tests integration : memoire cross-conversation (TestMemory)
H6. [x] Mettre a jour README.md
H7. [x] Verifier que docker compose up part proprement (200 OK confirme)
H8. [x] Revue securite : auth cookie dashboard (/dashboard/login), HITL protege par API key
```

---

## 10. Dependances Python (Ajouts Phase 3)

### core/requirements.txt — ajouts
```
# Store memoire persistante
langgraph-store-postgres>=0.1

# Dashboard
jinja2>=3.1
python-multipart>=0.0.9    # formulaires HTMX

# Streaming
sse-starlette>=2.0         # SSE helper (optionnel, StreamingResponse suffit peut-etre)
```

### channels/cli/requirements.txt — ajouts
```
# Streaming async
httpx[http2]>=0.27         # support HTTP/2 + streaming
```

---

## 11. Configuration (Variables d'Environnement — Ajouts)

```env
# Agent filesystem (workspace pour les fichiers agents)
AGENT_WORKSPACE_DIR=/agent-workspace

# Store memoire
LANGGRAPH_STORE_URI=postgresql://hiveflows:secret@postgres:5432/hiveflows
```

---

## 12. Docker Compose (Changements)

```yaml
# Ajouts au service core
core:
  volumes:
    - ./core:/app
    - ./skills:/skills
    - agent_workspace:/agent-workspace   # NOUVEAU : filesystem agents
    - ./core/static:/app/static          # NOUVEAU : fichiers statiques dashboard
    - ./core/templates:/app/templates    # NOUVEAU : templates Jinja2

# Nouveau volume
volumes:
  pg_data:
  redis_data:
  workspaces_data:
  agent_workspace:    # NOUVEAU
```

---

## 13. Angles Morts — Points d'Attention

| # | Risque | Mitigation |
|---|--------|------------|
| 1 | **LangGraph Store PostgreSQL** : package peut-etre jeune, API instable | Tester B1-B3 en isolation, verifier la compatibilite avec notre version PG |
| 2 | **Streaming + HITL** : l'interruption mid-stream est complexe | Le stream s'arrete naturellement quand l'agent est interrompu, event "interrupt" explicite |
| 3 | **Cache invalidation agents** : PUT /agents doit forcer un rebuild | Versionner les configs, comparer avant/apres pour invalider |
| 4 | **HTMX + SSE** : SSE unidirectionnel, le HITL resume doit passer par un POST HTMX classique | Pas bloquant, HTMX gere bien le mix SSE + POST |
| 5 | **astream vs invoke** : l'agent utilise invoke() (sync) actuellement, il faut passer a ainvoke/astream | Refactorer AgentManager pour supporter les deux modes |
| 6 | **SubAgent overhead** : chaque subagent cree un nouveau graph LangGraph | Garder les subagents legers (peu d'outils), monitorer la memoire |
| 7 | **Dashboard auth** : le dashboard doit aussi etre protege par API key | Cookie/session derivee de l'API key, ou basic auth |
| 8 | **Templates en volume** : en dev c'est pratique (hot reload), en prod il faut COPY dans le Dockerfile | Dockerfile multi-stage ou COPY conditionnel |
| 9 | **CompositeBackend + Store** : le namespace du store doit etre isole par agent/conversation | Factory de namespace basee sur thread_id ou agent_id |

---

## 14. Definition de "Phase 3 terminee"

La Phase 3 est complete quand on peut faire ceci **de bout en bout** :

```bash
# 1. Demarrer tous les services
docker compose up

# 2. Creer un agent via l'API
curl -X POST http://localhost:8002/agents \
  -H "X-API-Key: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "analyste",
    "name": "Analyste",
    "llm_config": {"model": "openai/gpt-4o-mini"},
    "system_prompt": "Tu es un analyste...",
    "subagents": [{"name": "calculateur", "description": "Fait des calculs", "system_prompt": "Tu calcules."}],
    "interrupt_on": {"run_script": true}
  }'

# 3. Chatter en streaming via le CLI
python channels/cli/cli.py --agent analyste --stream \
  "Calcule les 20 premiers nombres premiers en utilisant ton subagent calculateur"

# Resultat attendu :
# - Tokens arrivent en temps reel
# - L'agent spawne le subagent via "task"
# - Quand run_script est appele, le CLI demande approbation
# - Apres approbation, le script s'execute et le resultat s'affiche

# 4. Verifier la memoire persistante
python channels/cli/cli.py --agent analyste \
  "Souviens-toi que mon langage prefere est Python"
# ... nouvelle conversation ...
python channels/cli/cli.py --agent analyste \
  "Quel est mon langage prefere?"
# Resultat attendu : "Python" (retrouve depuis le store)

# 5. Ouvrir le dashboard
# Naviguer vers http://localhost:8002/dashboard
# - Voir les agents configures
# - Chatter avec un agent en temps reel
# - Voir les tasks executees
```

---

*Phase 3 planifiee le 7 mars 2026.*
