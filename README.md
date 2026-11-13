# HiveFlows

Plateforme multi-agents IA multi-usagers, auto-hebergee — FastAPI + LangGraph + Celery + PostgreSQL + Redis, entierement conteneurisee.

Des agents configurables depuis une base de donnees, capables de spawner des sous-agents, de communiquer entre eux, de se souvenir entre les conversations, de demander une approbation humaine (HITL), et accessibles via streaming SSE depuis un dashboard web ou le CLI. Le tout avec authentification JWT, workspaces isoles, permissions granulaires (RBAC), un catalogue d'apps conteneurisees (VS Code, Jupyter, Whisper/Piper, etc.), un tunnel Portal pour connecter des devices distants, et un systeme de mise a jour auto-hebergee (plateforme + apps + packages Python).

La metaphore : la ruche (plateforme), les abeilles (agents), le gardien (**Beekeeper**, control plane Docker), les ouvrieres (**workers** Celery).

## Architecture

```
CLI (Typer)  ──SSE──▶  Core (FastAPI)  ──Celery──▶  Worker
Dashboard    ──HTTP─▶       │        ──HTTP──▶  Beekeeper ──▶ Docker socket
(HTMX/Jinja2)               ▼                         │            │
                        PostgreSQL                     ▼            ▼
                        + LangGraph Store           Redis      Apps (par workspace)
                        + Redis (broker + pub/sub)   events    + Ingress Nginx
                                                                     ▲
                                                                Portal (tunnel WS)
                                                                     │
                                                              Devices distants
```

- **Core** — FastAPI : `/chat`, `/agents`, `/tasks`, `/auth`, `/workspaces`, `/files`, `/api-keys`, `/admin`, `/providers`, `/apps`, `/llm` (API server proxy), `/portal`, `/tools`, `/dashboard` (18 routers)
- **Worker** — Celery, execution sandboxee de scripts Python/Bash
- **Beekeeper** — control plane Docker (seul composant avec acces au socket) : lifecycle des apps, subnets par workspace, ingress Nginx, self-update de la plateforme
- **Portal** — tunnel WebSocket inverse pour connecter des devices/nodes distants (enrollment, registry, client Go)
- **PostgreSQL** — users, workspaces, conversations, taches, audit, credentials chiffres, apps, checkpointer LangGraph, store memoire (23 migrations)
- **Redis** — broker Celery + result backend + agent-to-agent pub/sub + bus d'activite + events Docker
- **CLI** — client terminal (Typer + Rich), login JWT, workspaces, streaming SSE, HITL
- **Dashboard** — interface web HTMX + Jinja2 (auth JWT cookie, chat streaming, CRUD agents, conversations, audit, apps, providers, systeme)

## Prerequis

- Docker + Docker Compose
- Python 3.8+ (pour le CLI, cote client)
- Un compte [OpenRouter](https://openrouter.ai) avec une cle API

## Installation

```bash
# 1. Cloner le repo
git clone <repo-url> hiveflows && cd hiveflows

# 2. Configurer les variables d'environnement
cp .env.example .env
# Editer .env : OPENROUTER_API_KEY, POSTGRES_PASSWORD, JWT_SECRET_KEY
```

### Variables importantes dans `.env`

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | Cle API OpenRouter |
| `DEFAULT_LLM_MODEL` | Modele LLM (ex: `openai/gpt-4o-mini`) |
| `JWT_SECRET_KEY` | Secret pour les tokens JWT (64 chars aleatoires) |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Duree access token (defaut: 60) |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | Duree refresh token (defaut: 30) |
| `POSTGRES_PASSWORD` | Mot de passe PostgreSQL |
| `API_KEYS` | Cles API legacy, separees par virgule (retro-compat) |
| `OAUTH_GOOGLE_CLIENT_ID` | OAuth2 Google (optionnel, vide = desactive) |
| `OAUTH_GITHUB_CLIENT_ID` | OAuth2 GitHub (optionnel, vide = desactive) |

## Demarrage

```bash
# Demarrer tous les services
docker compose up -d

# Verifier que tout tourne
docker compose ps
curl http://localhost:8002/health
# -> {"status": "ok"}
```

> **Note** : Le Core est expose sur le port **8002**. Modifiable dans `docker-compose.yml`.

## Authentification

HiveFlows supporte plusieurs methodes d'authentification :

| Methode | Usage | Header/Cookie |
|---------|-------|---------------|
| JWT Bearer | API REST, CLI | `Authorization: Bearer <token>` |
| Cookie JWT | Dashboard web | `hf_token` (httponly) |
| API Key (DB) | Programmatique | `X-API-Key: hf_...` |
| API Key (legacy) | Retro-compat | `X-API-Key: <cle .env>` |
| OAuth2 | Dashboard | Google / GitHub (optionnel) |

### Inscription & Login

```bash
# S'inscrire (cree un workspace personnel automatiquement)
curl -X POST http://localhost:8002/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "user@example.com", "display_name": "User", "password": "secret"}'

# Se connecter
curl -X POST http://localhost:8002/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "user@example.com", "password": "secret"}'
# -> {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer", "expires_in": 3600}

# Rafraichir le token
curl -X POST http://localhost:8002/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token": "eyJ..."}'
```

## Workspaces & Permissions (RBAC)

Chaque utilisateur a un workspace personnel cree a l'inscription. Les workspaces peuvent etre partages avec d'autres utilisateurs.

| Role | Permissions |
|------|------------|
| **owner** | Tout (y compris supprimer le workspace) |
| **admin** | Gerer workspace, membres, agents, API keys, audit |
| **member** | Creer agents, chatter, executer tasks |
| **viewer** | Voir conversations et tasks (lecture seule) |

```bash
TOKEN=<access_token>

# Lister ses workspaces
curl http://localhost:8002/workspaces -H "Authorization: Bearer $TOKEN"

# Creer un workspace partage
curl -X POST http://localhost:8002/workspaces \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Mon Equipe", "slug": "mon-equipe"}'

# Inviter un membre
curl -X POST http://localhost:8002/workspaces/<ws_id>/members \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email": "collegue@example.com", "role": "member"}'
```

## Dashboard Web

Accessible a `http://localhost:8002/dashboard` (auth JWT cookie).

| Route | Description |
|-------|-------------|
| `/dashboard/login` | Connexion email/password + OAuth |
| `/dashboard/register` | Inscription |
| `/dashboard/w/{slug}/` | Accueil workspace — stats, agents, tasks |
| `/dashboard/w/{slug}/chat` | Chat streaming SSE + sidebar conversations |
| `/dashboard/w/{slug}/conversations` | Historique conversations (recherche, filtre) |
| `/dashboard/w/{slug}/conversations/{id}` | Relire une conversation |
| `/dashboard/w/{slug}/agents` | CRUD agents + agent-to-agent test |
| `/dashboard/w/{slug}/activity` | Bus d'activite temps reel (tasks + apps) |
| `/dashboard/w/{slug}/audit` | Journal d'audit |
| `/dashboard/w/{slug}/apps` | Catalogue d'apps — install/start/stop, badges de mise a jour |
| `/dashboard/w/{slug}/providers` | Gestion des credentials LLM (coffre-fort chiffre) |
| `/dashboard/w/{slug}/files` | File manager (upload, viewer, editeur, tools) |
| `/dashboard/w/{slug}/api-server` | Cle API externe + config du proxy LLM du workspace |
| `/dashboard/portal` | Devices Portal enroles (pairing, registry) |
| `/dashboard/workspaces` | Gestion des workspaces |
| `/dashboard/profile` | Profil utilisateur |
| `/dashboard/system` | Etat du node + bouton de mise a jour (self-update via Beekeeper) |

## Beekeeper & Apps

Le **Beekeeper** est le seul service avec acces au socket Docker : il gere le cycle de vie des apps (install/start/stop/restart/uninstall), un subnet Docker isole par workspace, l'ingress Nginx, et relaie les evenements Docker vers Redis (statut, logs, metriques temps reel forwardes en SSE au dashboard).

Catalogue actuel (`apps/`) : `fabric`, `jupyter`, `node-red`, `piper` (TTS), `remote-fs` (synchro fichiers inter-nodes), `silverbullet`, `vscode-server`, `whisper` (STT), `xterm.js`, `yt-dlp`. Chaque app est decrite par un `manifest.yaml` (voir `apps/AppTools.md`) ; les apps `piper`, `whisper`, `remote-fs`, `yt-dlp` et `fabric` sont buildees localement depuis un `Dockerfile`.

Une app dont le catalogue a une version de manifest plus recente que la version installee affiche un badge « MAJ vX » dans le dashboard ; la mise a jour preserve les volumes de donnees.

## Portal

Tunnel WebSocket inverse permettant a un device distant (ou un futur node) de se connecter au Core sans exposer de port entrant. Inclut un flux d'enrollment par pairing, un registry des devices, l'audit des connexions, et un client **Go** dans `clients/portal/`.

## Mise a jour

Trois couches de mise a jour, orchestrees depuis `scripts/update.sh` et/ou le dashboard (`/dashboard/system`) :

1. **Plateforme** — `scripts/update.sh` : fetch/pull, `pg_dump` avant migration, rebuild/`up -d`, `alembic upgrade head`, healthcheck, rollback (`--rollback`). Le bouton du dashboard declenche la meme logique via le Beekeeper, en streamant le log par SSE.
2. **Apps** — bump de la `version:` du manifest → livree par la couche 1 → badge « MAJ vX » → mise a jour opt-in par app.
3. **Packages Python** — skill Claude Code `python-update` (verification PyPI, backup, rebuild sans cache, tests, rollback si echec) → livre par la couche 1.

## CLI

```bash
# Installer les dependances CLI
pip install typer httpx rich

# Login par email/password
python channels/cli/cli.py login
# Email: user@example.com
# Password: ****
# -> Logged in as user@example.com
# -> Workspace: user (owner)

# Login par API key
python channels/cli/cli.py login --api-key hf_abc123...

# Lister les workspaces
python channels/cli/cli.py workspace list

# Changer de workspace
python channels/cli/cli.py workspace use mon-equipe

# Voir le statut de la session
python channels/cli/cli.py status

# Message unique
python channels/cli/cli.py chat "Bonjour, qui es-tu ?"

# Mode interactif (REPL)
python channels/cli/cli.py chat -i

# Streaming temps reel
python channels/cli/cli.py chat --stream "Explique les design patterns"

# Choisir un agent specifique
python channels/cli/cli.py chat --agent data-analyst --stream "Analyse ce dataset..."

# Deconnexion
python channels/cli/cli.py logout
```

## API Endpoints

### Auth

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| POST | `/auth/register` | Non | Inscription |
| POST | `/auth/login` | Non | Login → tokens JWT |
| POST | `/auth/refresh` | Non | Rafraichir access token |
| GET | `/auth/me` | Bearer | Info utilisateur courant |

### Chat

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| POST | `/chat` | Oui | Envoyer un message (sync) |
| POST | `/chat/stream` | Oui | Streaming SSE (temps reel) |
| POST | `/chat/{id}/resume` | Oui | Reprendre apres HITL |

### Agents

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/agents` | Oui | Lister agents (scopes par workspace) |
| POST | `/agents` | Oui | Creer un agent |
| GET | `/agents/{id}` | Oui | Detail d'un agent |
| PUT | `/agents/{id}` | Oui | Modifier un agent |
| DELETE | `/agents/{id}` | Oui | Desactiver un agent |

### Tasks

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| POST | `/tasks/run` | Oui | Soumettre un script (python/bash) |
| GET | `/tasks/{id}` | Oui | Etat d'une tache (sync Celery) |
| PATCH | `/tasks/{id}` | Oui | Mise a jour statut/progression (app tools) |
| POST | `/tasks/{id}/cancel` | Oui | Annuler une tache Celery |
| DELETE | `/tasks/cleanup` | Oui | Supprimer les taches terminees |

### Human Tools (File Manager)

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/api/w/{slug}/tools/available` | Cookie | Liste des tools (filtre par extension) |
| POST | `/api/w/{slug}/tools/execute` | Cookie | Executer un tool sur un fichier |
| GET | `/api/w/{slug}/tools/agents` | Cookie | Agents disponibles (selector) |
| GET | `/api/w/{slug}/tools/fabric/patterns` | Cookie | Catalogue Fabric (proxy container) |
| GET | `/api/w/{slug}/tools/fabric/pattern/{name}` | Cookie | System prompt d'un pattern |

### Activity Bus (SSE)

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/dashboard/w/{slug}/activity/stream` | Cookie | SSE bus d'activite unifie |

Canal Redis : `hive:ws:{workspace_id}:activity`
Events : `task_created`, `task_progress`, `task_done`, `task_error`, `task_cancelled`, `app_started`, `app_stopped`, `app_crashed`

### Workspaces

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/workspaces` | Bearer | Lister ses workspaces |
| POST | `/workspaces` | Bearer | Creer un workspace |
| PUT | `/workspaces/{id}` | Bearer | Modifier un workspace |
| DELETE | `/workspaces/{id}` | Bearer | Desactiver (owner) |
| GET | `/workspaces/{id}/members` | Bearer | Lister les membres |
| POST | `/workspaces/{id}/members` | Bearer | Ajouter un membre |
| PUT | `/workspaces/{id}/members/{uid}` | Bearer | Changer le role |
| DELETE | `/workspaces/{id}/members/{uid}` | Bearer | Retirer un membre |
| GET | `/workspaces/{id}/api-keys` | Bearer | Lister API keys |
| POST | `/workspaces/{id}/api-keys` | Bearer | Generer une API key |
| DELETE | `/workspaces/{id}/api-keys/{kid}` | Bearer | Revoquer une API key |
| GET | `/workspaces/{id}/audit` | Bearer | Journal d'audit |

### Apps (via Beekeeper)

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/apps/catalog` | Bearer | Catalogue d'apps disponibles |
| GET | `/w/{ws_id}/apps` | Bearer | Apps installees dans le workspace (+ statut MAJ) |
| POST | `/w/{ws_id}/apps/install` | Bearer | Installer une app |
| POST | `/w/{ws_id}/apps/{slug}/start` \| `/stop` \| `/restart` | Bearer | Lifecycle |
| DELETE | `/w/{ws_id}/apps/{slug}` | Bearer | Desinstaller |
| PUT | `/w/{ws_id}/apps/{slug}/env` | Bearer | Modifier les variables d'env |
| GET | `/w/{ws_id}/apps/{slug}/logs` | Bearer | Logs du container |

### Providers (Credential Vault)

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/providers` | Bearer | Lister les credentials configures |
| POST | `/providers` | Bearer | Ajouter un credential (chiffre Fernet) |
| DELETE | `/providers/{id}` | Bearer | Supprimer un credential |
| POST | `/providers/{id}/test` | Bearer | Tester la connexion au provider |

### API Server (proxy LLM par workspace)

OpenAI-compatible, pour connecter un outil externe (ex. un client de chat) au LLM configure d'un workspace. Deux modes d'auth : token `workspace_id` (reserve aux reseaux internes de confiance, `LLM_PROXY_TRUSTED_NETWORKS`) ou cle API `hf_...` du workspace (externe, revocable). Voir [docs/api_server.md](docs/api_server.md).

| Methode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/internal/v1/{workspace_id}/models` | Token/Cle | Lister les modeles disponibles |
| POST | `/internal/v1/{workspace_id}/chat/completions` | Token/Cle | Completion chat OpenAI-compatible (stream ou non) |
| POST | `/internal/v1/{workspace_id}/responses` | Token/Cle | Endpoint Responses API |

### Portal

Routes `/portal` (enrollment, pairing) et `/portal-devices` (registry, audit des devices) — voir `docs/portal.md` et `docs/portal_docs.md`.

## Agents — Format de configuration

```json
{
  "id": "mon-agent",
  "name": "Mon Agent",
  "description": "Description courte",
  "llm_config": {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
  "system_prompt": "Instructions de base...",
  "skills": ["exemple"],
  "tools": ["run_script", "run_skill_script", "message_agent"],
  "subagents": [
    {"name": "researcher", "description": "Expert en recherche", "system_prompt": "Tu es un expert..."}
  ],
  "memory_sources": ["/memories/AGENTS.md"],
  "interrupt_on": {"run_script": true},
  "backend_config": {"type": "filesystem", "root_dir": "/agent-workspace"}
}
```

### Agent-to-Agent Communication

Les agents peuvent communiquer entre eux via le tool `message_agent` :

```
Agent A (coordinator)                Agent B (analyst)
       |                                    |
       |  message_agent("analyst",          |
       |    "Analyse ces donnees...")        |
       |  ---------------------------------->
       |                                    |
       |                               Repond
       |  <----------------------------------
       |                                    |
       |  "Les ventes Q1 sont en hausse..." |
```

Restrictions : meme workspace uniquement, limite de hops (max 3).

## Skills

Les skills sont des modules reutilisables montes dans `/skills/` (magasin central, monte en volume dans core). `SkillLoader` scanne le dossier au build de l'agent et injecte les `skill.md` dans le system prompt.

```
skills/
  exemple/
    skill.md        # Description injectee dans le system prompt
    scripts/
      hello.py      # Script executable via run_skill_script
  tools/             # Modules Python appelables directement en tools agent
    fs/              # convert_file
    image/           # qrcode_reader
    nlp/             # translate, translate_file
    web/             # fetch_url, url2markdown, get_weather, playwright_browser,
                      #  piper_tts, whisper_transcribe
```

> Piege connu : Pydantic reserve `args` comme nom de parametre de tool → utiliser `script_args`.

## Tests

Tous les tests s'executent dans le container core (acces PostgreSQL + Redis + packages) :

```bash
# Tous les tests
docker compose exec core pytest /app/tests/ -v

# Tests unitaires seulement (model Task, activity_bus, human_tools)
docker compose exec core pytest /app/tests/test_tasks.py /app/tests/test_activity_bus.py -v

# Tests integration Phase 7 (flows tasks, SSE, cancel, cleanup)
docker compose exec core pytest /app/tests/test_integration_phase7.py -v
```

### Couverture des tests

| Fichier | Couvre |
|---------|--------|
| `test_tasks.py` | Model Task, schemas Pydantic, human_tools registry |
| `test_activity_bus.py` | build_event, activity_channel |
| `test_integration_phase7.py` | POST /tasks/run, POST /tools/execute, SSE stream, cancel, cleanup |
| `test_portal_*.py` | Enrollment, tunnel, migration, registry Portal |
| `test_llm_factory.py` | LLM provider factory |
| `test_credential_vault.py` | Chiffrement Fernet des credentials providers |
| `test_llm_proxy.py` | Proxy LLM OpenAI-compatible par workspace (auth token/cle, streaming) |

`core/tests/` couvre le service core (12 fichiers, execute via `docker compose exec core pytest /app/tests/`) ; `tests/` a la racine (`test_core/`, `test_cli/`, `test_workers/`) couvre les autres services.

## Structure du projet

```
hiveflows/
├── docker-compose.yml            # Prod : images immuables (code baké)
├── docker-compose.override.yml.example  # Dev : bind-mount + --reload (HIVE_DEV=1)
├── .env.example
├── core/                         # Service principal (FastAPI)
│   ├── main.py                   # Entrypoint + Jinja2 + StaticFiles
│   ├── config.py                 # Settings (pydantic-settings)
│   ├── database.py               # SQLAlchemy async
│   ├── auth.py                   # Auth middleware (JWT cookie)
│   ├── auth_service.py           # JWT + password hashing
│   ├── permissions.py            # RBAC (roles, permissions, dependencies)
│   ├── audit.py                  # Audit logging service
│   ├── credential_vault.py       # Chiffrement Fernet des credentials providers
│   ├── beekeeper_client.py       # Client HTTP vers le Beekeeper
│   ├── chat_persistence.py       # Conversation/message persistence
│   ├── api/                      # 18 routers (auth, oauth, chat, tasks, agents, audit,
│   │                              #  dashboard, workspaces, files, api_keys, admin,
│   │                              #  providers, apps, llm_proxy, portal, portal_devices,
│   │                              #  tunnel, tools) + schemas_*.py
│   ├── agents/
│   │   ├── manager.py            # AgentManager (cache + invoke + store)
│   │   ├── factory.py            # build_agent_from_config()
│   │   ├── llm_factory.py        # LLM factory multi-provider (dont openai_compatible)
│   │   ├── credential_resolver.py# Cascade agent → workspace → global → .env
│   │   ├── tools.py              # Agent tools (message_agent, run_script, etc.)
│   │   ├── agent_bus.py          # Agent-to-agent Redis pub/sub
│   │   ├── app_tool_registry.py  # Tools exposes par les apps
│   │   ├── app_instance_bus.py   # Bus events des instances d'app
│   │   └── skill_loader.py       # Chargement des skills depuis /skills/
│   ├── services/
│   │   ├── activity_bus.py       # Bus d'activite Redis unifie (SSE)
│   │   ├── human_tools.py        # Tools executes depuis le file manager
│   │   ├── portal_enrollment.py  # Enrollment/pairing Portal
│   │   └── portal_registry.py    # Registry des devices Portal
│   ├── models/                   # SQLAlchemy models (user, workspace, agent_config,
│   │                              #  conversation, task, api_key, audit_log,
│   │                              #  provider_credential, app, portal)
│   ├── alembic/                  # Migrations DB (23 : 001 → 023)
│   ├── templates/dashboard/      # activity, agents, api_server, apps, audit, chat,
│   │                              #  conversations, index, login, portal, profile,
│   │                              #  providers, register, system, workspaces
│   ├── static/
│   └── tests/                    # Tests co-localises au service (llm_proxy, portal, ...)
├── workers/                      # Service Celery (run_python, run_bash)
├── beekeeper/                    # Control plane Docker
│   ├── api/                      # routes_apps, routes_networks, routes_self
│   └── services/                 # container_manager, network_manager, event_listener,
│                                  #  ingress_manager, manifest_loader, metrics_publisher
├── apps/                         # Catalogue d'apps (manifest.yaml + Dockerfile optionnel)
│   └── {fabric,jupyter,node-red,piper,remote-fs,silverbullet,vscode-server,whisper,xterm.js,yt-dlp}/
├── clients/portal/                # Client Go du tunnel Portal (cmd, internal, examples)
├── channels/cli/cli.py           # Client CLI (login, workspace, chat, SSE)
├── skills/tools/{fs,image,nlp,web}/  # Magasin de skills (monte en volume dans core)
├── scripts/                      # bootstrap.sh, update.sh, deploy.sh
├── tests/                        # test_core, test_cli, test_workers
├── landing/                      # Landing page beta testeurs (independante, NerdRack)
└── docs/                         # Docs de pilotage (GOAL, PHASE*, Update, FlowersField, JOURNAL...)
```

## Stack technique

- **FastAPI** — API REST async + Server-Sent Events
- **deepagents** + **LangGraph** — framework agent IA multi-agent
- **ChatOpenAI + `base_url`** — LLM via OpenRouter (workaround : `langchain-openrouter` pas encore compatible `openrouter>=0.8.0`, retour prevu quand possible)
- **LangGraph Store (PostgreSQL)** — memoire persistante cross-conversation
- **Celery** + **Redis** — execution de taches async sandboxee + agent-to-agent pub/sub + bus d'activite
- **PostgreSQL** — persistance (SQLAlchemy + Alembic + LangGraph checkpointer + store)
- **Docker** (via **Beekeeper**) — control plane des apps workspace, isole du socket hote
- **Jinja2** + **HTMX** — Dashboard web
- **Typer** + **Rich** — CLI client
- **Go** — client Portal (tunnel WebSocket inverse)
- **python-jose** + **passlib** — JWT + password hashing
- **authlib** — OAuth2 (Google, GitHub)
- **cryptography (Fernet)** — chiffrement des credentials providers en DB
