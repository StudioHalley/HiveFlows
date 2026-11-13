# HiveFlows — Phase 1 : Fondation (MVP)

> Objectif : avoir un agent Deep Agents capable de recevoir une commande via CLI, l'exécuter via un worker Celery, et retourner le résultat. Le tout conteneurisé, persisté, et sécurisé par API key.

---

## 1. Périmètre de la Phase 1

Ce qu'on construit, et ce qu'on ne construit **pas encore**.

### ✅ Dans le scope
- Structure du projet et repo Git
- Core service (FastAPI) — routing basique, gestion des agents
- Un agent Deep Agents fonctionnel (LangGraph runtime)
- Worker Celery — exécution de scripts Python/Bash
- Message queue Redis — communication Core ↔ Workers
- Channel CLI — interface console pour "jaser" avec un agent
- PostgreSQL — persistance des conversations et état
- Docker Compose — tous les services conteneurisés
- Auth par API key — sécurité minimale sur toutes les routes

### ❌ Hors scope (phases suivantes)
- Système de skills (Phase 2)
- Multi-agents / sous-agents (Phase 3)
- Workspaces / multi-usagers (Phase 4)
- Interface web (Phase 3+)
- OAuth2 (Phase 4)

---

## 2. Architecture Phase 1

```
┌─────────────────────────────────────────────────┐
│                  CHANNEL CLI                    │
│  python cli.py --agent mon-agent "ta question"  │
└───────────────────┬─────────────────────────────┘
                    │ HTTP + API Key
                    ▼
┌─────────────────────────────────────────────────┐
│              CORE SERVICE (FastAPI)             │
│                                                 │
│  POST /chat          → Agent Manager            │
│  POST /tasks/run     → Task Dispatcher          │
│  GET  /tasks/{id}    → Task Status              │
│  GET  /health        → Health check             │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │         AGENT (Deep Agents)              │   │
│  │  create_deep_agent(model, tools, prompt) │   │
│  │  LangGraph runtime + checkpointer PG     │   │
│  └──────────────────────────────────────────┘   │
└─────────┬───────────────────────┬───────────────┘
          │ Celery task dispatch  │ SQLAlchemy
          ▼                       ▼
┌─────────────────┐    ┌──────────────────────────┐
│  REDIS (broker) │    │  POSTGRESQL              │
│  - task queue   │    │  - conversations         │
│  - result store │    │  - messages              │
└────────┬────────┘    │  - tasks                 │
         │             │  - agents config         │
         ▼             └──────────────────────────┘
┌─────────────────┐
│  WORKER Celery  │
│  - run_script() │
│  - run_bash()   │
└─────────────────┘
```

---

## 3. Structure du Projet

```
hiveflows/
├── docker-compose.yml
├── .env.example
├── README.md
├── GOAL.md
├── PHASE1.md
│
├── core/                          # Service principal
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                    # Entrypoint FastAPI
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── database.py                # SQLAlchemy setup
│   ├── auth.py                    # API key middleware
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes_chat.py         # POST /chat
│   │   └── routes_tasks.py        # POST /tasks/run, GET /tasks/{id}
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── manager.py             # Création et gestion des agents
│   │   ├── default_agent.py       # Config de l'agent par défaut
│   │   └── prompts/
│   │       └── default.txt        # System prompt de base
│   │
│   └── models/
│       ├── __init__.py
│       ├── conversation.py        # Modèle Conversation + Message
│       └── task.py                # Modèle Task
│
├── workers/                       # Service Celery
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── celery_app.py              # Config Celery
│   └── tasks/
│       ├── __init__.py
│       ├── run_python.py          # Exécution script Python
│       └── run_bash.py            # Exécution commande Bash
│
├── channels/
│   └── cli/
│       ├── requirements.txt
│       └── cli.py                 # Client CLI (Click ou Typer)
│
├── shared/                        # Code partagé core/workers
│   ├── __init__.py
│   ├── schemas.py                 # Pydantic schemas (API contracts)
│   └── constants.py
│
└── tests/
    ├── test_core/
    └── test_workers/
```

---

## 4. Schémas de Données (PostgreSQL)

### Table `conversations`
```sql
CREATE TABLE conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    VARCHAR(100) NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW(),
    metadata    JSONB DEFAULT '{}'
);
```

### Table `messages`
```sql
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,   -- 'user', 'assistant', 'tool'
    content         TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'
);
```

### Table `tasks`
```sql
CREATE TABLE tasks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    celery_id   VARCHAR(255),              -- ID Celery pour le suivi
    type        VARCHAR(50) NOT NULL,      -- 'python', 'bash'
    payload     JSONB NOT NULL,            -- script, args, etc.
    status      VARCHAR(20) DEFAULT 'pending',  -- pending/running/success/failed
    result      JSONB,
    error       TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
```

### Table `agent_configs`
```sql
CREATE TABLE agent_configs (
    id          VARCHAR(100) PRIMARY KEY,  -- slug ex: "assistant-principal"
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    llm_config  JSONB NOT NULL,            -- provider, model, temperature
    system_prompt TEXT,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

---

## 5. Contrats d'Interface (API)

### Auth
Toutes les routes (sauf `/health`) requièrent le header :
```
X-API-Key: <clé>
```
Les clés valides sont définies dans les variables d'environnement (`API_KEYS=key1,key2,...`).

### `POST /chat`
Envoyer un message à un agent et recevoir la réponse.

**Request :**
```json
{
  "agent_id": "assistant-principal",
  "message": "Écris un script Python qui liste les fichiers d'un dossier",
  "conversation_id": "uuid-optionnel-pour-continuer-une-conversation"
}
```

**Response :**
```json
{
  "conversation_id": "uuid",
  "message_id": "uuid",
  "response": "Voici le script...",
  "tool_calls": [],
  "usage": { "input_tokens": 42, "output_tokens": 150 }
}
```

### `POST /tasks/run`
Soumettre une tâche à un worker Celery.

**Request :**
```json
{
  "type": "python",
  "script": "print('hello world')",
  "args": [],
  "timeout": 30
}
```

**Response :**
```json
{
  "task_id": "uuid",
  "celery_id": "celery-uuid",
  "status": "pending"
}
```

### `GET /tasks/{task_id}`
Vérifier l'état d'une tâche.

**Response :**
```json
{
  "task_id": "uuid",
  "status": "success",
  "result": { "stdout": "hello world\n", "returncode": 0 },
  "error": null,
  "created_at": "2026-03-06T10:00:00Z",
  "updated_at": "2026-03-06T10:00:05Z"
}
```

---

## 6. Configuration (Variables d'Environnement)

Fichier `.env` (copié depuis `.env.example`) :

```env
# LLM
OPENAI_API_KEY=sk-...
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4o-mini

# Auth
API_KEYS=dev-key-1,dev-key-2

# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=hiveflows
POSTGRES_USER=hiveflows
POSTGRES_PASSWORD=secret

# Redis
REDIS_URL=redis://redis:6379/0

# Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# Core
CORE_HOST=0.0.0.0
CORE_PORT=8000
LOG_LEVEL=INFO
```

---

## 7. Docker Compose

```yaml
# docker-compose.yml (Phase 1)
services:

  core:
    build: ./core
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./core:/app   # hot reload en dev

  worker:
    build: ./workers
    env_file: .env
    depends_on:
      - redis
      - postgres
    # Scale: docker compose up --scale worker=3

  postgres:
    image: postgres:16-alpine
    env_file: .env
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pg_data:
  redis_data:
```

---

## 8. Agent Deep Agents — Configuration Phase 1

En Phase 1, l'agent est simple : un seul agent "assistant-principal" avec les outils de base de Deep Agents (planning + filesystem). Pas encore de skills custom.

```python
# core/agents/default_agent.py
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI

def build_default_agent(checkpointer):
    llm = ChatOpenAI(model="gpt-4o-mini")

    agent = create_deep_agent(
        model=llm,
        tools=[],           # Phase 1 : outils natifs Deep Agents seulement
                            # Phase 2 : on ajoutera les skills ici
        system_prompt=open("agents/prompts/default.txt").read(),
        checkpointer=checkpointer,   # LangGraph + PostgreSQL
    )
    return agent
```

L'agent dispose nativement (via Deep Agents) de :
- `write_todos` — planifier et décomposer ses tâches
- `read_file` / `write_file` / `ls` — gérer le contexte sur fichier
- `task` — spawner des sous-agents (utilisé en Phase 3)

Pour exécuter un script, l'agent appelle l'API Core (`POST /tasks/run`) qui dispatche à Celery. En Phase 1, ce pont agent → tâche sera un tool custom simple :

```python
# Tool que l'agent peut appeler pour déclencher un worker
@tool
def run_script(script: str, type: str = "python") -> str:
    """Exécute un script Python ou Bash via le système de workers."""
    response = httpx.post(
        f"{CORE_URL}/tasks/run",
        json={"type": type, "script": script},
        headers={"X-API-Key": INTERNAL_API_KEY}
    )
    task_id = response.json()["task_id"]
    # Polling jusqu'à completion
    return wait_for_task(task_id)
```

---

## 9. Channel CLI

Interface minimaliste pour tester l'agent sans interface web.

```bash
# Usage
python channels/cli/cli.py --agent assistant-principal "Écris un script qui ping google.com"

# Avec conversation persistée
python channels/cli/cli.py --agent assistant-principal --conversation-id uuid "Continue..."

# Mode interactif (REPL)
python channels/cli/cli.py --agent assistant-principal --interactive
```

Le CLI envoie simplement des requêtes `POST /chat` au Core et affiche la réponse. Pas de logique AI dans le CLI — c'est juste un client HTTP avec du formatting.

---

## 10. Plan de Tâches (Ordonné par Dépendances)

Les tâches sont numérotées dans l'ordre recommandé d'exécution. Les dépendances sont indiquées.

### Bloc A — Infrastructure de base
```
A1. [x] Créer le repo Git + structure de dossiers
A2. [x] Écrire docker-compose.yml (postgres + redis seulement d'abord)
A3. [x] Configurer .env.example et .gitignore
A4. [x] Vérifier que `docker compose up postgres redis` fonctionne
```

### Bloc B — Core Service (squelette)
```
Dépend de : A4
B1. [x] Créer core/Dockerfile + requirements.txt
B2. [x] Créer main.py (FastAPI app + lifespan)
B3. [x] Créer config.py (pydantic-settings, lecture .env)
B4. [x] Créer database.py (SQLAlchemy async + tables)
B5. [x] Créer auth.py (middleware API key)
B6. [x] Route GET /health → retourne {"status": "ok"}
B7. [x] Ajouter core au docker-compose, vérifier le boot
```

### Bloc C — Persistence (modèles)
```
Dépend de : B4
C1. [x] Modèle Conversation + Message (SQLAlchemy)
C2. [x] Modèle Task (SQLAlchemy)
C3. [x] Modèle AgentConfig (SQLAlchemy)
C4. [x] Migration Alembic initiale (création des tables)
```

### Bloc D — Workers Celery
```
Dépend de : A4
D1. [x] Créer workers/Dockerfile + requirements.txt
D2. [x] Créer celery_app.py (config broker Redis)
D3. [x] Tâche run_python_script() — subprocess + capture stdout/stderr
D4. [x] Tâche run_bash_script() — idem pour Bash
D5. [x] Ajouter worker au docker-compose
D6. [x] Test manuel : soumettre une tâche Python simple via Celery
```

### Bloc E — API Tasks
```
Dépend de : B7, C2, D5
E1. [x] POST /tasks/run → créer Task en DB + dispatch Celery
E2. [x] GET /tasks/{id} → retourner état depuis DB
E3. [x] Callback Celery → mettre à jour Task en DB à la complétion
E4. [x] Test end-to-end : CLI curl → /tasks/run → worker → résultat
```

### Bloc F — Agent Deep Agents
```
Dépend de : B7, C1, C3
F1. [x] pip install deepagents, langchain-openrouter, langgraph-checkpoint-postgres
F2. [x] Créer PostgreSQL checkpointer (langgraph-checkpoint-postgres)
F3. [x] Créer default_agent.py avec create_deep_agent()
F4. [x] Créer le tool run_script() → appel HTTP /tasks/run
F5. [x] Créer AgentManager — instanciation et cache des agents
F6. [x] Route POST /chat → AgentManager.invoke()
F7. [x] Persistance conversation_id → thread_id LangGraph
F8. [x] Test : envoyer un message à l'agent via curl
```

### Bloc G — Channel CLI
```
Dépend de : F8
G1. [x] Créer channels/cli/cli.py (Typer ou Click)
G2. [x] Commande `chat` — appel POST /chat + affichage réponse
G3. [x] Flag --interactive (boucle REPL)
G4. [x] Persistance locale du conversation_id (fichier ~/.hiveflows/session)
G5. [x] Test complet : CLI → Core → Agent → Worker → résultat affiché
```

### Bloc H — Finition et validation
```
Dépend de : G5
H1. [x] Écrire tests unitaires Worker (run_python, run_bash)
H2. [x] Écrire tests d'intégration API (POST /chat, POST /tasks/run)
H3. [x] Vérifier que `docker compose up` part proprement de zéro
H4. [x] Rédiger README.md (installation, config, usage)
H5. [x] Passer en revue la sécurité (API keys, pas de secrets dans le repo)
```

---

## 11. Dépendances Python (Phase 1)

### core/requirements.txt
```
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.0
pydantic-settings>=2.0
sqlalchemy[asyncio]>=2.0
asyncpg>=0.29          # driver async PostgreSQL
alembic>=1.13
httpx>=0.27            # appels HTTP internes
deepagents>=0.4
langchain-openai>=0.2
langgraph>=0.2
langgraph-checkpoint-postgres>=2.0
celery[redis]>=5.3
```

### workers/requirements.txt
```
celery[redis]>=5.3
sqlalchemy[asyncio]>=2.0
asyncpg>=0.29
pydantic-settings>=2.0
```

### channels/cli/requirements.txt
```
typer>=0.12
httpx>=0.27
rich>=13.0             # formatting terminal
```

---

## 12. Angles Morts — Points d'Attention

Ces risques ont été identifiés avant de commencer :

| # | Risque | Mitigation |
|---|--------|------------|
| 1 | **LangGraph checkpointer PostgreSQL** : config non triviale, async vs sync | Tester F2 en isolation avant d'intégrer |
| 2 | **Celery + async FastAPI** : mélange sync/async délicat | Utiliser `celery` en mode sync dans les workers, async uniquement dans le core |
| 3 | **Timeout des tâches** : un script qui tourne indéfiniment bloque un worker | Implémenter timeout `subprocess` dès D3 |
| 4 | **Injection de code** : le worker exécute du code arbitraire | Phase 1 = usage personnel uniquement, sandboxing en Phase 4 |
| 5 | **Conversation_id vs thread_id** : mapping à bien gérer entre notre UUID et le thread_id LangGraph | Stocker les deux en DB dès C1 |
| 6 | **Polling vs streaming** : le CLI attend la réponse de l'agent (peut être long) | Commencer avec polling simple, streaming en Phase 3 |
| 7 | **Secrets dans .env** : facile d'accidentellement commit | .gitignore strict dès A3, vérification en H5 |

---

## 13. Définition de "Phase 1 terminée"

La Phase 1 est complète quand on peut faire ceci **de bout en bout** :

```bash
# 1. Démarrer tous les services
docker compose up

# 2. Tester la santé
curl http://localhost:8000/health
# → {"status": "ok"}

# 3. Demander à l'agent d'écrire et exécuter un script
python channels/cli/cli.py \
  --agent assistant-principal \
  "Écris un script Python qui calcule les 10 premiers nombres de Fibonacci et exécute-le"

# Résultat attendu : l'agent écrit le script, l'envoie au worker,
# le worker l'exécute, et l'agent retourne le résultat : 0, 1, 1, 2, 3, 5, 8, 13, 21, 34
```

---

*Phase 1 planifiée le 6 mars 2026.*
