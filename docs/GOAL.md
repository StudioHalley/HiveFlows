# HiveFlows

> Une plateforme modulaire d'agents AI collaboratifs — où chaque agent est une abeille spécialisée, et la ruche orchestre le tout.

---

## 1. Vision

Créer une plateforme complète d'agents AI multi-collaboratifs capable de coordonner, spécialiser et exécuter des tâches complexes de façon autonome. HiveFlows permet à des humains et des agents de travailler ensemble dans des workspaces partagés, en combinant la puissance des LLM avec l'exécution concrète de scripts et d'outils.

**Philosophie :** Modulaire avant tout. Chaque composant est interchangeable, chaque agent est configurable, chaque skill est réutilisable.

**Fondation technique :** HiveFlows s'appuie sur **Deep Agents** (`deepagents`), le framework open-source (MIT) de LangChain conçu pour construire des agents capables de gérer des tâches complexes et longues. Deep Agents fournit nativement les capacités essentielles dont nos agents ont besoin : planification de tâches, gestion de contexte via un filesystem backend, spawning de sous-agents, et mémoire persistante — le tout construit sur le runtime LangGraph pour l'exécution durable, le streaming, et le human-in-the-loop.

---

## 2. Architecture Générale

```
┌──────────────────────────────────────────────────────────┐
│                       CHANNELS                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Console  │  │   Web    │  │   API    │  │  Slack+  │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       └──────────────┴─────────────┴─────────────┘       │
│                           │                               │
├───────────────────────────┼───────────────────────────────┤
│                      CORE SERVICE                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Orchestrator│  │Agent Manager │  │Workspace Manager │  │
│  │             │  │(multi-provider)│ │                  │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬──────────┘  │
│         └────────────────┴──────────────────┘             │
│                           │                               │
├───────────────────────────┼───────────────────────────────┤
│                      BEEKEEPER                            │
│            Docker Control Plane (service séparé)          │
│  ┌────────────────┐  ┌───────────┐  ┌──────────────────┐  │
│  │Container Mgr   │  │Network Mgr│  │ Event Listener   │  │
│  │(start/stop/rm) │  │(subnets)  │  │ (docker events   │  │
│  │                │  │           │  │  → Redis pub)     │  │
│  └────────────────┘  └───────────┘  └──────────────────┘  │
│                           │                               │
├───────────────────────────┼───────────────────────────────┤
│                      EXECUTION                            │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Workers  │  │  Apps (Docker)│  │Skills (Docker)│        │
│  │ (Celery) │  │  persistent   │  │  oneshot/     │        │
│  │ stateless│  │  vscode,      │  │  stream       │        │
│  │          │  │  node-red...  │  │               │        │
│  └──────────┘  └──────────────┘  └──────────────┘         │
│                           │                               │
├───────────────────────────┼───────────────────────────────┤
│                      PERSISTANCE                          │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐   │
│  │  PostgreSQL  │  │    Redis     │  │   .hive/ FS    │   │
│  │  (mémoire    │  │  (système    │  │  (données      │   │
│  │   permanente)│  │   nerveux)   │  │   workspace)   │   │
│  └──────────────┘  └──────────────┘  └────────────────┘   │
└───────────────────────────────────────────────────────────┘
```

---

## 3. Composants Principaux

### 3.1 — Core Service (Le Cerveau)

Le service central qui coordonne l'ensemble de la plateforme.

**Responsabilités :**
- **Orchestrator** : Reçoit les requêtes, planifie l'exécution, délègue aux agents appropriés
- **Agent Manager** : Gère le cycle de vie des agents (création, configuration, activation, arrêt)
- **Workspace Manager** : Gère les espaces de travail personnels et partagés
- **Routing** : Dirige les messages entre channels, agents et workers
- **Contexte partagé** : Maintient le contexte de conversation et l'état des agents

**Stack technique :** Python, LangChain, Deep Agents, LangGraph, FastAPI

### 3.2 — Agents (Les Abeilles) — Propulsés par Deep Agents

Chaque agent HiveFlows est construit sur le SDK Deep Agents de LangChain. Un "deep agent" est plus qu'une simple boucle d'appels d'outils — c'est un agent capable de planifier, décomposer, déléguer, et se souvenir, comme un chef de projet humain.

**Pourquoi Deep Agents ?**
- **Planning intégré** : Outil `write_todos` natif — chaque agent peut décomposer une tâche complexe en étapes, suivre sa progression, et adapter son plan
- **Filesystem backend** : Outils `ls`, `read_file`, `write_file`, `edit_file` intégrés — les agents déchargent le contexte volumineux en mémoire ou sur disque, évitant le débordement de la fenêtre de contexte
- **Sous-agents (subagents)** : Outil `task` natif — un agent peut spawner des sous-agents spécialisés pour isoler le contexte et aller en profondeur sur des sous-tâches
- **Mémoire persistante** : Via LangGraph Memory Store, les agents conservent de l'information entre les conversations
- **Runtime LangGraph** : Exécution durable, streaming, checkpointing, et human-in-the-loop

**Caractéristiques HiveFlows :**
- Un agent = un profil (nom, rôle, personnalité, permissions) + un `create_deep_agent()` configuré
- Configurable via YAML (modèle LLM, tools, skills, prompts)
- Provider-agnostic : chaque agent peut utiliser un LLM différent (OpenAI, Claude, Ollama, etc.)
- Collabore avec d'autres agents via la message queue
- Accès aux skills de son workspace
- **Propriété** : chaque agent appartient à un **utilisateur** (son créateur). Il peut être invité dans plusieurs workspaces.
- **Adressage** : dans une conversation multi-agents, on interpelle un agent via `@slug` (ex: `@time-traveler comment ça va ?`). En 1-à-1, le @ est implicite.
- **Agent par défaut** : un agent plateforme configuré par le site-admin, présent automatiquement dans tous les workspaces

**Types de collaboration :**
- Agent-à-sous-agent : un agent spawne un subagent Deep Agents pour une sous-tâche isolée
- Agent-à-agent : collaboration via la message queue entre agents indépendants
- Agent-à-humain : human-in-the-loop natif via LangGraph — un agent peut demander une validation ou un input
- Multi-agents : plusieurs agents travaillent en parallèle sur un problème

**Exemple de création d'agent :**
```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="openai:gpt-4",           # ou "anthropic:claude-3", "ollama:llama3"
    tools=[skill_calendrier, skill_todo],
    system_prompt=load_prompt("data-analyst"),
    # LangGraph checkpointer pour la persistance
    checkpointer=postgres_checkpointer,
)
```

### 3.3 — Skills (Les Outils)

Les skills spécialisent les agents. Chaque skill est un module réutilisable et composable.

**Structure d'un skill :**
```
skills/
└── calendrier/
    ├── skill.md          # Documentation et instructions pour l'agent
    ├── config.yaml       # Configuration (nom, description, dépendances, permissions)
    ├── scripts/
    │   ├── list_events.py
    │   ├── create_event.py
    │   └── sync_calendar.sh
    └── prompts/
        └── templates.yaml  # Templates de prompts spécifiques au skill
```

**Config YAML d'un skill :**
```yaml
name: calendrier
version: 1.0.0
description: "Gestion du calendrier et des événements"
dependencies:
  - todo        # Ce skill peut utiliser le skill "todo"
permissions:
  - read_calendar
  - write_calendar
scripts:
  - name: list_events
    type: python
    entry: scripts/list_events.py
  - name: sync
    type: bash
    entry: scripts/sync_calendar.sh
```

**Inter-skills :** Un skill peut déclarer des dépendances vers d'autres skills. Par exemple, le skill `organiser-journee` peut invoquer les skills `calendrier` et `todo` pour construire un plan de journée.

### 3.4 — Beekeeper (Le Gardien)

Le Beekeeper est un service autonome qui détient l'accès exclusif au socket Docker. Il est le seul composant autorisé à créer, démarrer, arrêter et détruire des containers. Le Core, les agents et les Bees passent tous par le Beekeeper via son API REST interne.

**Responsabilités :**
- **Docker Control** : start, stop, restart, remove des containers (apps et skills Docker)
- **Network Isolation** : un subnet Docker par workspace (`172.20.{n}.0/24`)
- **Event Publishing** : écoute `docker events` et publie sur Redis en temps réel
- **Ingress Management** : génère la config Nginx pour router vers les apps
- **Security Gate** : valide le workspace_id, refuse les images hors catalogue, empêche le path traversal

**Stack technique :** Python, FastAPI, Docker SDK (`docker` PyPI), Redis Pub/Sub

### 3.5 — Apps (Les Extensions)

Les apps sont des containers Docker persistants qui s'exécutent dans le contexte d'un workspace. Chaque app est définie par un manifest YAML et installée via le Beekeeper.

**Types d'apps :**
- **Persistent + UI** : VS Code Server, Node-RED, Jupyter, SilverBullet — accessibles via iframe ou lien dans le dashboard
- **Backend** : Ollama, Chromium headless — pas d'UI, accessibles via API interne par les agents

**Caractéristiques :**
- Partage le filesystem `.hive/` du workspace avec les agents
- Isolée par réseau Docker (un subnet par workspace)
- Variables d'environnement configurables et chiffrées
- Installable depuis un catalogue curé (App Store)

### 3.6 — Workers (Les Ouvrières)

Les workers exécutent les tâches concrètes — scripts Python, commandes Bash, ou toute opération définie par un skill.

**Stack technique :** Celery + Redis comme broker

**Responsabilités :**
- Exécution de scripts Python et Bash (tâches stateless, sans dépendance au workspace filesystem)
- Suivi de l'état des tâches (pending, running, success, failed)
- Gestion des timeouts et retries
- Rapport de résultats au Core via la message queue
- Isolation des tâches (chaque tâche dans son contexte)

**Coexistence avec Docker skills :** Les tâches légères et stateless restent dans Celery (rapide, scalable). Les tâches lourdes, isolées ou nécessitant des dépendances spécifiques s'exécutent comme des containers Docker via le Beekeeper (Phase 7).

### 3.7 — Bees (Les Sous-Agents Éphémères)

Un Bee est un sub-agent spécialisé et éphémère, scoped à un workspace. Il naît pour accomplir une mission (chaîner des skills, piloter des apps, surveiller des fichiers) et meurt quand c'est fait.

**Caractéristiques :**
- Piloté par l'agent principal (la Reine) ou déclenché automatiquement
- Possède une identité (workspace_id + token) validée par le Beekeeper
- Peut lancer des skills Docker, interagir avec des apps, écrire dans `.hive/`
- Communique ses résultats et son progress via Redis

**Types de communication avec les skills :**
- `oneshot` : docker run --rm, résultat dans stdout
- `web` : appel HTTP vers une app/skill persistante
- `stdin` : pipe de données en entrée
- `stream` : output progressif avec progress events sur Redis
- `persistent` : interaction avec une app qui tourne déjà

### 3.8 — Channels (Les Portes d'Entrée)

Les channels sont les interfaces par lesquelles un humain (ou un système externe) communique avec HiveFlows.

**Channels prévus :**
- **Console (CLI)** : Interface terminal pour les power users
- **Web** : Interface web responsive (chat + dashboard)
- **API REST** : Pour les intégrations programmatiques
- **Extensible** : Slack, Discord, Telegram, email, etc. — chaque channel est un module indépendant

**Contrat d'interface :** Chaque channel implémente une interface standard (Protocol `IChannel`) pour envoyer/recevoir des messages vers le Core. Ajouter un nouveau channel = implémenter cette interface.

### 3.9 — Le Système Nerveux (Redis)

Redis est le backbone de communication temps réel entre tous les composants.

**Rôles :**
- **Message broker** : dispatch des tâches Celery
- **Pub/Sub événements** : le Beekeeper publie (status apps, docker events), le Core subscribe et forward au dashboard via SSE
- **Progress streaming** : les skills et Bees publient leur avancement, le dashboard l'affiche en temps réel
- **Métriques éphémères** : CPU/mémoire des apps, heartbeats

**Principe directeur :**
- Redis = "ce qui se passe maintenant" (éphémère)
- PostgreSQL = "ce qui s'est passé" (permanent)
- Filesystem (.hive/) = "ce qui existe" (données)

---

## 4. Multi-Usagers, Workspaces & Interactions

### 4.1 — Modèle Utilisateur

- Chaque utilisateur a un compte avec authentification
- **Slug unique** : chaque utilisateur a un slug (handle) qui partage le même namespace que les agents (pas de collision)
- **Rôle global `site-admin`** : configuration de la plateforme (agent par défaut, bans, logs globaux, etc.)
- **Phase 1 :** API keys
- **Phase 4 :** JWT + OAuth2 (Google, GitHub)

### 4.2 — Workspaces

- **Workspace personnel** : créé automatiquement à l'inscription
- **Workspace partagé** : plusieurs humains et agents collaborent dans un espace commun
  - Historique de conversation persistant et partagé — un utilisateur peut relire la discussion même s'il était absent
  - Pagination / scroll infini pour les longs historiques
  - Permissions granulaires (rôles : owner, admin, member, viewer)
- **Filesystem** : chaque workspace a un répertoire dédié sur le serveur (`/data/workspaces/{id}/`). Les agents peuvent lire et écrire des fichiers dans ce répertoire via leurs tools.

### 4.3 — Agents : Propriété et Invitations

Les agents n'appartiennent **pas** aux workspaces — ils appartiennent aux **utilisateurs**.

- Un agent est créé par un utilisateur et lui appartient
- L'utilisateur peut **ajouter/inviter** ses agents dans n'importe quel workspace où il est membre
- Un agent peut être présent dans **plusieurs workspaces** simultanément
- Les membres du workspace peuvent **retirer** un agent
- **Agent par défaut** : un agent plateforme, configuré par le `site-admin`, automatiquement présent dans tous les workspaces. Permet aux nouveaux utilisateurs d'avoir une expérience fonctionnelle dès l'inscription, avant même de comprendre la plateforme.

### 4.4 — @ Mentions et Adressage

Dans une conversation avec **plusieurs agents** :
- On interpelle un agent via `@slug` : *"Hey @time-traveler comment ça va ?"*
- Seul l'agent mentionné répond — les autres restent silencieux

Dans une conversation **1 humain + 1 agent** :
- Le @ est implicite, pas besoin de mentionner l'agent

**Agent par défaut comme orchestrateur** (évolution future) :
- L'agent par défaut écoute silencieusement la discussion
- Il peut dispatcher automatiquement au bon sous-agent (ex: une question météo → l'agent qui a le tool météo)
- Plus complexe que le @, prévu en évolution

### 4.5 — Slash Commands

Ambiance IRC/Discord. Commandes tapées directement dans le chat :
- `/invite @slug` — inviter un utilisateur ou un agent dans le workspace
- `/part` — quitter le workspace
- `/join #workspace` — rejoindre un workspace
- `/kick @slug` — retirer un membre ou un agent

Extensible : les utilisateurs pourront créer des **alias personnalisés** pour se définir des commandes custom.

### 4.6 — Gestion du Contexte Agent

Les conversations sont potentiellement infinies. Les agents ne peuvent pas envoyer l'intégralité de l'historique au LLM.

- **Cut-off** : fenêtre glissante des N derniers messages (configurable par agent)
- **Recherche** : quand l'agent a besoin de contexte ancien, il peut rechercher dans l'historique complet (full-text search ou embeddings)
- Transparent pour l'utilisateur : il voit tout l'historique, l'agent gère son contexte intelligemment

### 4.7 — Sécurité

- Authentification sur toutes les routes API
- Isolation des données entre workspaces
- Permissions par workspace (rôles : owner, admin, member, viewer)
- **Namespace unifié** : les slugs utilisateurs et agents ne peuvent pas se chevaucher
- Chiffrement des données sensibles (clés API des LLM, tokens)
- Audit log des actions importantes

---

## 5. LLM et Providers

**Approche multi-provider :**
- Abstraction via LangChain / Deep Agents pour supporter plusieurs fournisseurs
- Chaque agent peut être configuré avec un modèle différent
- **Phase 1 :** API compatible OpenAI (OpenAI, Ollama, vLLM, etc.)
- **Phase 2 :** Anthropic, Google, et autres providers natifs
- Configuration par agent dans le YAML :

```yaml
agent:
  name: "data-analyst"
  llm:
    provider: openai
    model: gpt-4
    temperature: 0.3
    fallback:
      provider: ollama
      model: llama3
```

---

## 6. Stack Technique

| Composant         | Technologie                          |
|-------------------|--------------------------------------|
| Langage           | Python (principal), Bash (scripts)   |
| Framework AI      | LangChain + Deep Agents + LangGraph  |
| API               | FastAPI (Core + Beekeeper)           |
| Task Queue        | Celery (tâches stateless)            |
| Docker Control    | Docker SDK Python (Beekeeper)        |
| Message Broker    | Redis                                |
| Base de données   | PostgreSQL                           |
| Event Bus         | Redis Pub/Sub                        |
| Conteneurisation  | Docker + Docker Compose              |
| Ingress           | Nginx (reverse proxy, géré par Beekeeper) |
| Auth              | API Keys → JWT + OAuth2              |
| Web Frontend      | HTMX + Jinja2 (intégré dans Core)    |
| LLM Providers     | OpenRouter, Anthropic, Google, Ollama |

---

## 7. Déploiement

**Docker Compose** comme orchestrateur principal :

```yaml
# Structure des services
services:
  core:        # Service principal (FastAPI, dashboard, auth, agents)
  beekeeper:   # Docker control plane (seul accès au docker.sock)
  worker:      # Workers Celery (scalable, tâches stateless)
  nginx:       # Reverse proxy / ingress pour les apps
  redis:       # Message broker + event bus (pub/sub)
  postgres:    # Base de données permanente
  # + containers dynamiques gérés par le Beekeeper (apps, skills Docker)
```

- Tout roule en conteneurs
- Configuration via variables d'environnement et fichiers YAML
- Scalabilité horizontale des workers (`docker compose scale worker=N`)
- Les apps workspace sont des containers gérés dynamiquement par le Beekeeper (pas dans le compose)

---

## 8. Phases du Projet

> ⚠️ **Mise à jour 2026-07-10** : les Phases 5 et 6 sont livrées (cases cochées ci-dessous). Les Phases 7 et 8 ont aussi été livrées *dans les commits*, mais sous un contenu différent de ce qui était planifié ici — voir les notes dans chaque section. Détail complet : [status_20260710.md](status_20260710.md) §2.

### Phase 1 — Fondation (MVP) ✅
- [x] Structure du projet et repo Git
- [x] Core service minimal (FastAPI + routing basique)
- [x] Un agent Deep Agents fonctionnel (`create_deep_agent`) avec LangGraph runtime
- [x] Worker Celery capable d'exécuter un script Python/Bash
- [x] Message queue Redis
- [x] Channel CLI (console)
- [x] PostgreSQL pour la persistance
- [x] Docker Compose fonctionnel
- [x] Auth par API key

### Phase 2 — Système de Skills ✅
- [x] Structure de dossier d'un skill (skill.md, config.yaml, scripts/, prompts/)
- [x] Chargement et enregistrement dynamique des skills
- [x] Intégration des skills comme tools dans `create_deep_agent`
- [x] Inter-skills : déclaration de dépendances entre skills
- [x] Skills de base : `shell`, `fichiers`, `web-search`
- [x] Validation et versioning des skills

### Phase 3 — Multi-Agents & Streaming ✅
- [x] Agent Registry — AgentConfig en DB + API CRUD
- [x] Collaboration agent-à-sous-agent via `task` tool de Deep Agents
- [x] Mémoire persistante via LangGraph Store (PostgreSQL)
- [x] Gestion du contexte partagé (backends de Deep Agents)
- [x] Human-in-the-loop via LangGraph
- [x] Streaming (SSE) — API + CLI
- [x] Dashboard web minimal (HTMX + Jinja)

### Phase 4 — Multi-Usagers & Interactions
- [x] Comptes utilisateurs (inscription, login, profil, slug)
- [x] Auth JWT + OAuth2 (Google, GitHub)
- [x] Rôle site-admin
- [x] Workspaces (perso + partagés + filesystem)
- [x] Agents : propriété utilisateur + invitation dans workspaces
- [x] Agent par défaut plateforme
- [x] @ mentions et adressage multi-agent
- [x] Slash commands (/invite, /join, /part, /kick)
- [x] Namespace unifié slugs (users + agents)
- [x] Permissions granulaires (RBAC + permissions skills)
- [x] Conversation : pagination / scroll infini
- [x] Gestion du contexte agent (cut-off configurable)
- [x] Collaboration agent-à-agent via message queue
- [x] Audit logging
- [x] Interface web complète

### Phase 5 — Multi-Provider LLM & Credential Vault ✅
- [x] LLM Factory : `create_llm(provider, model, api_key)` → BaseChatModel
- [x] Coffre-fort de credentials (chiffrement Fernet en DB)
- [x] Configuration provider par agent (avec cascade : agent → workspace → global → .env)
- [x] Dashboard : page gestion des providers (ajout, test, suppression)
- [x] Rétro-compatibilité avec OPENROUTER_API_KEY existant
- [x] Fallback provider optionnel par agent

Livrée commit `9bca252` (2026-04-03). Note : le test C4 (clé Anthropic réelle) n'a jamais été fait faute de clé — reste théorique.

### Phase 6 — Beekeeper & Apps ✅
- [x] Service Beekeeper : FastAPI interne, accès exclusif au socket Docker
- [x] Isolation réseau : un subnet Docker par workspace
- [x] App manifest YAML : format standardisé pour décrire une app
- [x] Cycle de vie des apps : install, start, stop, restart, uninstall
- [x] App Catalog : 10 apps (fabric, jupyter, node-red, piper, remote-fs, silverbullet, vscode-server, whisper, xterm.js, yt-dlp) — largement au-delà des 3 apps de base prévues
- [x] Ingress Nginx : routing par path vers les apps
- [x] Redis events : status, logs, métriques en temps réel
- [x] Docker events listener → Redis pub/sub
- [x] API Core proxy vers le Beekeeper
- [ ] CLI : `workflow app start|stop|list|logs|install|env` — jamais fait, la gestion des apps se fait uniquement depuis le dashboard
- [x] Dashboard : gestion des apps par workspace (status, logs, env, iframe)

Livrée commit `83efcb3` (2026-04-06). Les 8 cases « Test » du détail `PHASE6.md` ne sont pas cochées formellement, mais la fonctionnalité est validée depuis en usage réel (petite dette de vérification E2E).

### Phase 7 — Bees & Skill Evolution
⚠️ **Divergence de plan** : le commit `9241c79` (2026-04-16) est étiqueté « Phase 7 » mais livre un contenu différent de ce qui suit — Activity Bus (bus Redis unifié), Human Tools (exécution de tools depuis le file manager), et le refactor de la table `tasks` (cancel/cleanup). Aucun des items ci-dessous (Bees en containers Docker, skill chaining, `.hive/`) n'a été construit ; ils restent des idées ouvertes pour une itération future.
- [ ] Skills comme containers Docker (oneshot, stream, stdin)
- [ ] Bees : sub-agents éphémères scoped à un workspace
- [ ] Bee identity : token workspace-scoped, validé par le Beekeeper
- [ ] Skill chaining : un Bee orchestre plusieurs skills séquentiellement
- [ ] Progress/streaming via Redis → SSE → dashboard *(livré, mais dans le cadre de l'Activity Bus ci-dessus, pas des Bees)*
- [ ] Coexistence Celery (stateless) + Docker (isolé)
- [ ] Skill manifest YAML (type, input, output, progress)
- [ ] Conventions filesystem `.hive/` (inbox, outputs, memory...)

### Phase 8 — Channels & Intégrations Externes
⚠️ **Divergence de plan** : le commit `8c58620` (2026-04-17) est étiqueté « Phase 8 » mais livre du TTS/STT (apps Piper + Whisper, app bus, extensions de tools) — pas les channels externes listés ci-dessous. Les channels (Slack/Discord/Telegram) restent entièrement à faire ; seul le channel CLI existe.
- [ ] Abstraction IChannel (Protocol Python) + ChannelMessage
- [ ] Webhook ingress : `/webhooks/{channel_type}/{workspace_slug}`
- [ ] Channel Slack (slack-bolt, Events API + Socket Mode)
- [ ] Channel Discord (discord.py, background task)
- [ ] Channel Telegram (python-telegram-bot, webhook mode)
- [ ] Mapping utilisateurs externes → HiveFlows
- [ ] Smart Dispatch : l'agent par défaut route les messages sans @mention

### Phase 9 — Observabilité & Polish
- [ ] Monitoring : Prometheus + Grafana (métriques HTTP, LLM, tasks)
- [ ] Structured logging (structlog JSON)
- [ ] Deep health checks (`/health/deep`)
- [ ] Quotas et rate limiting par workspace/utilisateur
- [ ] Recherche sémantique (FTS PostgreSQL)
- [ ] Skill Marketplace (registre, versioning, import par workspace)
- [ ] Alias slash commands personnalisés
- [ ] Documentation complète (API, admin, utilisateur, développeur)

---

## 9. Structure du Repo (Préliminaire)

```
hiveflows/
├── docker-compose.yml
├── README.md
├── GOAL.md
├── core/                          # Service principal
│   ├── Dockerfile
│   ├── main.py
│   ├── config.py
│   ├── agents/                    # Agent Manager, LLM Factory
│   ├── api/                       # Routes REST (chat, agents, workspaces, apps proxy)
│   ├── templates/                 # Dashboard HTMX + Jinja2
│   └── static/
├── beekeeper/                     # Docker Control Plane (Phase 6)
│   ├── Dockerfile
│   ├── main.py
│   ├── api/                       # Routes internes (apps, networks, health)
│   └── services/                  # container_manager, network_manager, event_listener, ingress_manager
├── workers/                       # Celery workers (tâches stateless)
│   ├── Dockerfile
│   ├── celery_app.py
│   └── tasks/
├── apps/                          # Catalogue des apps (manifests YAML)
│   ├── vscode-server/
│   ├── node-red/
│   ├── jupyter/
│   └── ollama/
├── skills/                        # Magasin de skills
│   ├── tools/
│   └── ...
├── nginx/                         # Config Nginx de base (ingress)
│   └── nginx.conf
├── cli/                           # Channel CLI (Typer + Rich)
├── shared/
│   ├── models/
│   ├── schemas/
│   └── utils/
└── tests/
```

---

## 10. Critères de Succès

1. **Modulaire** : Chaque composant (channel, skill, agent, worker) peut être ajouté, retiré, ou remplacé indépendamment
2. **Fonctionnel** : Un humain peut "jaser" avec un agent via la console, lui demander une tâche, et l'agent l'exécute via un worker
3. **Collaboratif** : Deux agents peuvent se passer du contexte et collaborer sur une tâche
4. **Extensible** : Ajouter un nouveau skill = créer un dossier avec la bonne structure
5. **Sécuritaire** : Isolation des workspaces, auth sur toutes les routes, données chiffrées
6. **Déployable** : `docker compose up` et c'est parti

---

## 11. Questions Ouvertes

- [x] ~~Choix du framework frontend web~~ → HTMX + Jinja2 (intégré dans Core)
- [ ] Convention de slugs : suffixe `_bot` (Telegram-style) vs namespace unifié libre
- [ ] Orchestration automatique par l'agent par défaut : Phase 4 ou Phase 5 ?
- [ ] Stratégie de recherche dans l'historique : full-text PostgreSQL vs embeddings/vector store
- [ ] Stratégie de versioning des skills
- [ ] Mécanisme exact de "mémoire longue" — LangGraph Memory Store vs vector store vs RAG (ou combinaison)
- [ ] Stratégie de monitoring et logging centralisé
- [ ] Gestion des secrets et credentials des services externes
- [ ] Rate limiting et quotas par utilisateur/workspace

---

## 12. Références et Ressources

- **Deep Agents** : https://github.com/langchain-ai/deepagents (MIT License)
- **Deep Agents Docs** : https://docs.langchain.com/oss/python/deepagents/overview
- **LangGraph** : https://langchain-ai.github.io/langgraph/
- **LangChain** : https://www.langchain.com/
- **Celery** : https://docs.celeryq.dev/
- **FastAPI** : https://fastapi.tiangolo.com/

---

*Projet initié le 6 mars 2026 par Patrick — avec la complicité de Claude.*
*On va révolutionner l'IA, une abeille à la fois.* 🐝
