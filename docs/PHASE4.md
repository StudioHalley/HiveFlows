# HiveFlows — Phase 4 : Multi-Usagers & Workspaces

> Objectif : transformer HiveFlows d'un outil mono-utilisateur en une plateforme multi-usagers avec authentification, workspaces isolés, permissions granulaires, collaboration agent-à-agent, et audit complet.

---

## 1. Perimetre de la Phase 4

### Dans le scope
- Comptes utilisateurs — inscription, connexion, profil
- Authentification JWT + OAuth2 (Google, GitHub)
- Workspaces — espaces personnels et partages
- Permissions granulaires (RBAC) — roles owner/admin/member/viewer
- Permissions skills par agent et par workspace
- Collaboration agent-a-agent via message queue Redis
- Audit logging — journal de toutes les actions sensibles
- Interface web complete — pages manquantes + polish du dashboard
- @ mentions — adressage multi-agent via @slug dans le chat
- Agent par defaut plateforme — configurable par site-admin, present dans tous les workspaces
- Agents : propriete utilisateur + invitation dans workspaces (changement de modele)
- Slash commands dans le chat (/invite, /join, /part, /kick)
- Workspace filesystem — repertoire par workspace sur le serveur
- Pagination des conversations — scroll infini pour les longs historiques
- Gestion du contexte agent — cut-off configurable + recherche dans l'historique
- Namespace unifie slugs — users et agents dans le meme espace, pas de collision
- Role site-admin — configuration plateforme globale

### Hors scope (phases suivantes)
- Channels externes Slack/Discord/Telegram (Phase 5)
- Multi-provider LLM natif (Phase 5)
- Marketplace de skills (Phase 5)
- Monitoring et observabilite (Phase 5)
- Orchestration automatique par l'agent par defaut (dispatching intelligent sans @) — Phase 5
- Alias personnalises pour slash commands — Phase 5
- Recherche semantique dans l'historique (embeddings/vector store) — Phase 5

---

## 2. Architecture Phase 4

```
                   +-----------------------------+
                   |      DASHBOARD WEB          |
                   |  HTMX + Jinja2 (dans Core)  |
                   |  Login / Workspaces / Admin |
                   +-------------+---------------+
                                 |
+----------------+               |               +----------------+
|  CHANNEL CLI   |               |               |   API REST     |
|  (login JWT)   |               |               |  Bearer token  |
+-------+--------+               |               +-------+--------+
        |                        |                        |
        +------------------------+------------------------+
                                 | HTTP + JWT / API Key
                                 v
+----------------------------------------------------------------+
|                    CORE SERVICE (FastAPI)                      |
|                                                                |
|  POST /auth/register          -> Inscription                   |
|  POST /auth/login             -> JWT access + refresh          |
|  POST /auth/oauth/{provider}  -> OAuth2 callback               |
|  GET/POST /workspaces         -> CRUD workspaces               |
|  POST /workspaces/{id}/invite -> Inviter un membre             |
|  *Toutes les routes existantes* -> scopees par workspace       |
|                                                                |
|  +----------------------------------------------------------+  |
|  |              AUTH MIDDLEWARE (JWT + API Key)              | |
|  |                                                           | |
|  |  Bearer token -> decode JWT -> user_id + workspace_id     | |
|  |  X-API-Key    -> lookup API key -> workspace_id           | |
|  +----------------------------------------------------------+  |
|                                                                |
|  +----------------------------------------------------------+  |
|  |              AGENT MANAGER (multi-agent)                  | |
|  |                                                           | |
|  |  Agents scopes par workspace                              | |
|  |  message_agent() tool -> Redis pub/sub -> Agent B         | |
|  |  Audit logging sur chaque action                          | |
|  +----------------------------------------------------------+  |
|                    |                    |                      |
|            +-------+-------+    +------+------+                |
|            | LangGraph     |    | LangGraph   |                |
|            | Checkpointer  |    | Store       |                |
|            +-------+-------+    +------+------+                |
|                    |                    |                      |
+--------------------+--------------------+---------+------------+
                     |                              |
                     v                              v
              +------+------+             +---------+---------+
              |  PostgreSQL |             |   REDIS           |
              |  - users    |             |  - broker Celery  |
              |  - workspace|             |  - agent-to-agent |
              |  - configs  |             |    pub/sub        |
              |  - threads  |             +---------+---------+
              |  - store    |                       |
              |  - audit    |                       v
              +-------------+             +---------+---------+
                                          |   WORKER Celery   |
                                          +-------------------+
```

---

## 3. Decisions Techniques

| Decision | Choix | Justification |
|----------|-------|---------------|
| Auth primaire | JWT (access + refresh tokens) | Stateless, standard, compatible API + dashboard |
| OAuth2 | Google + GitHub (optionnel) | Friction reduite pour les utilisateurs, desactivable via .env |
| Hashing passwords | bcrypt via passlib | Standard industrie, lent par design |
| API keys | Conservees, scopees par workspace | Retro-compatibilite CLI + usage programmatique |
| Workspace isolation | FK workspace_id sur agents, conversations, tasks | Isolation au niveau DB, queries filtrees |
| RBAC | 4 roles : owner, admin, member, viewer | Assez granulaire sans etre complexe |
| Agent-to-agent | Tool `message_agent()` + Redis pub/sub | Async natif, decouple les agents, pas de polling |
| Audit | Table audit_logs + middleware FastAPI | Centralise, consultable via dashboard |
| Skills permissions | Champ `allowed_skills` sur WorkspaceMember | L'admin controle quels skills sont disponibles |
| Sessions dashboard | JWT dans httponly cookie (remplace API key cookie) | Coherent avec le nouveau systeme auth |
| Migration API keys | API keys liees a un workspace (pas globales) | Transition douce, CLI existant continue de fonctionner |
| URLs dashboard | Slug workspace dans l'URL : `/dashboard/w/{slug}/...` | Bookmarkable, partageable, contexte explicite dans l'URL |
| Agent ownership | Agents propriete des users, invites dans workspaces | Junction table `workspace_agents`. Un agent peut etre dans N workspaces |
| @ mentions | Parser `@slug` dans les messages chat | Multi-agent: seul l'agent mentionne repond. 1:1: implicite |
| Agent par defaut | Flag `is_default` + site-admin configure | Auto-present dans tous les workspaces. UX immediate pour nouveaux users |
| Slugs namespace | Unique global (users + agents) | Contrainte cross-table ou table de slugs unifiee. Convention a definir (suffixe `_bot` ?) |
| Slash commands | Parser `/commande` dans les messages chat | /invite, /join, /part, /kick. Extensible via alias utilisateur |
| Workspace filesystem | Repertoire `/data/workspaces/{workspace_id}/` | Monte en volume Docker. Agents y accedent via tools |
| Conversation pagination | Curseur-based (pas offset) | Performant pour longs historiques. Frontend: infinite scroll HTMX |
| Agent context window | Rolling window (N derniers messages) | Cut-off configurable par agent. Recherche historique via FTS/embeddings plus tard |
| Site-admin | Champ `is_site_admin` sur User | Role global (pas workspace). Configure agent par defaut, ban, logs globaux |

---

## 4. Schemas de Donnees (Nouveaux + Modifies)

### Nouvelle table `users`

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    slug            VARCHAR(100) UNIQUE NOT NULL,   -- handle unique (@slug), meme namespace que les agents
    display_name    VARCHAR(200) NOT NULL,
    hashed_password VARCHAR(255),           -- NULL si OAuth uniquement
    oauth_provider  VARCHAR(50),            -- 'google', 'github', NULL
    oauth_id        VARCHAR(255),           -- ID chez le provider
    is_active       BOOLEAN DEFAULT true,
    is_site_admin   BOOLEAN DEFAULT false,  -- role global : config agent par defaut, ban, logs
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
```

### Nouvelle table `workspaces`

```sql
CREATE TABLE workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(200) NOT NULL,
    slug        VARCHAR(100) UNIQUE NOT NULL,   -- URL-friendly: "mon-workspace"
    description TEXT,
    owner_id    UUID REFERENCES users(id) NOT NULL,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
```

### Nouvelle table `workspace_members`

```sql
CREATE TABLE workspace_members (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE NOT NULL,
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'member',  -- owner, admin, member, viewer
    allowed_skills  JSONB DEFAULT NULL,     -- NULL = tous, [] = aucun, ["skill-a"] = filtre
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(workspace_id, user_id)
);
```

### Nouvelle table `workspace_agents`

```sql
CREATE TABLE workspace_agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE NOT NULL,
    agent_id        VARCHAR(100) NOT NULL,       -- ref vers agent_configs.id
    added_by        UUID REFERENCES users(id) NOT NULL,
    added_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE(workspace_id, agent_id)
);
```
> Junction table : lie les agents aux workspaces. Un agent (propriete d'un user) peut etre dans plusieurs workspaces.
> L'agent par defaut (`is_default=true`) est auto-ajoute a tout nouveau workspace.

### Nouvelle table `api_keys`

```sql
CREATE TABLE api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash        VARCHAR(255) NOT NULL,      -- hash bcrypt de la cle
    key_prefix      VARCHAR(10) NOT NULL,       -- "hf-abc..." pour identification
    name            VARCHAR(200) NOT NULL,       -- "CLI dev", "CI/CD"
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE NOT NULL,
    created_by      UUID REFERENCES users(id) NOT NULL,
    permissions     JSONB DEFAULT '["chat", "tasks"]',  -- actions autorisees
    is_active       BOOLEAN DEFAULT true,
    last_used_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### Nouvelle table `audit_logs`

```sql
CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id),
    workspace_id    UUID REFERENCES workspaces(id),
    action          VARCHAR(50) NOT NULL,       -- 'agent.create', 'chat.invoke', 'task.run', etc.
    resource_type   VARCHAR(50),                -- 'agent', 'conversation', 'task', 'workspace'
    resource_id     VARCHAR(255),               -- ID de la ressource concernee
    details         JSONB DEFAULT '{}',         -- payload contextuel
    ip_address      VARCHAR(45),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_audit_workspace ON audit_logs(workspace_id, created_at DESC);
CREATE INDEX idx_audit_user ON audit_logs(user_id, created_at DESC);
```

### Modifications aux tables existantes

```sql
-- agent_configs : propriete utilisateur (pas workspace)
ALTER TABLE agent_configs
    ADD COLUMN owner_id UUID REFERENCES users(id),       -- le user qui a cree l'agent
    ADD COLUMN is_default BOOLEAN DEFAULT false;          -- agent par defaut plateforme
-- Note: le scoping par workspace passe par la table workspace_agents (pas par un FK direct)

-- conversations : lier a un user
ALTER TABLE conversations
    ADD COLUMN user_id UUID REFERENCES users(id),
    ADD COLUMN workspace_id UUID REFERENCES workspaces(id);

-- tasks : lier a un user
ALTER TABLE tasks
    ADD COLUMN user_id UUID REFERENCES users(id),
    ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
```

### Migration Alembic
- `005_users_workspaces_phase4.py` — tables users, workspaces, workspace_members, api_keys, audit_logs
- `006_scope_existing_tables_phase4.py` — ajouter workspace_id/user_id aux tables existantes + migration des donnees

---

## 5. Roles et Permissions (RBAC)

### Matrice des permissions

| Action | owner | admin | member | viewer |
|--------|-------|-------|--------|--------|
| Gerer le workspace (nom, settings) | x | x | | |
| Inviter/retirer des membres | x | x | | |
| Changer les roles | x | | | |
| Supprimer le workspace | x | | | |
| Creer/modifier un agent | x | x | x | |
| Supprimer un agent | x | x | | |
| Chatter avec un agent | x | x | x | |
| Voir les conversations | x | x | x | x |
| Executer des tasks | x | x | x | |
| Voir les tasks | x | x | x | x |
| Voir l'audit log | x | x | | |
| Gerer les API keys | x | x | | |

### Implementation

```python
from enum import Enum

class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

ROLE_HIERARCHY = {
    Role.OWNER: 4,
    Role.ADMIN: 3,
    Role.MEMBER: 2,
    Role.VIEWER: 1,
}

class Permission(str, Enum):
    WORKSPACE_MANAGE = "workspace.manage"
    WORKSPACE_DELETE = "workspace.delete"
    MEMBERS_MANAGE = "members.manage"
    ROLES_MANAGE = "roles.manage"
    AGENT_CREATE = "agent.create"
    AGENT_DELETE = "agent.delete"
    CHAT = "chat"
    CHAT_VIEW = "chat.view"
    TASK_RUN = "task.run"
    TASK_VIEW = "task.view"
    AUDIT_VIEW = "audit.view"
    APIKEY_MANAGE = "apikey.manage"

# Mapping role -> permissions
ROLE_PERMISSIONS = {
    Role.OWNER:  set(Permission),  # toutes
    Role.ADMIN:  {Permission.WORKSPACE_MANAGE, Permission.MEMBERS_MANAGE,
                  Permission.AGENT_CREATE, Permission.AGENT_DELETE,
                  Permission.CHAT, Permission.CHAT_VIEW,
                  Permission.TASK_RUN, Permission.TASK_VIEW,
                  Permission.AUDIT_VIEW, Permission.APIKEY_MANAGE},
    Role.MEMBER: {Permission.AGENT_CREATE, Permission.CHAT, Permission.CHAT_VIEW,
                  Permission.TASK_RUN, Permission.TASK_VIEW},
    Role.VIEWER: {Permission.CHAT_VIEW, Permission.TASK_VIEW},
}
```

### FastAPI Dependency

```python
def require_permission(permission: Permission):
    async def checker(
        user: User = Depends(get_current_user),
        workspace_id: UUID = Depends(get_workspace_id),
        db: AsyncSession = Depends(get_db),
    ):
        member = await get_workspace_member(db, user.id, workspace_id)
        if not member or permission not in ROLE_PERMISSIONS[member.role]:
            raise HTTPException(403, "Permission denied")
        return member
    return checker
```

---

## 6. Contrats d'Interface (Nouveaux Endpoints)

### Auth

#### `POST /auth/register`
```json
// Request
{"email": "patrick@example.com", "display_name": "Patrick", "password": "secret123"}
// Response 201
{"user_id": "uuid", "email": "patrick@example.com", "workspace": {"id": "uuid", "slug": "patrick"}}
```
> Cree l'utilisateur + un workspace personnel automatiquement.

#### `POST /auth/login`
```json
// Request
{"email": "patrick@example.com", "password": "secret123"}
// Response 200
{"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer", "expires_in": 3600}
```

#### `POST /auth/refresh`
```json
// Request
{"refresh_token": "eyJ..."}
// Response 200
{"access_token": "eyJ...", "expires_in": 3600}
```

#### `GET /auth/me`
```json
// Response 200
{"id": "uuid", "email": "patrick@example.com", "display_name": "Patrick",
 "workspaces": [{"id": "uuid", "slug": "patrick", "name": "Patrick", "role": "owner"}]}
```

#### `GET /auth/oauth/{provider}` (Google, GitHub)
> Redirige vers le provider OAuth2.

#### `GET /auth/oauth/{provider}/callback`
> Callback OAuth2, cree/lie le compte, retourne les tokens.

### Workspaces

#### `GET /workspaces`
Liste les workspaces de l'utilisateur courant.

#### `POST /workspaces`
```json
{"name": "Mon Equipe", "slug": "mon-equipe", "description": "Workspace partage"}
```

#### `PUT /workspaces/{workspace_id}`
Modifier nom/description (owner ou admin).

#### `DELETE /workspaces/{workspace_id}`
Desactiver le workspace (owner uniquement).

#### `GET /workspaces/{workspace_id}/members`
Liste des membres et leurs roles.

#### `POST /workspaces/{workspace_id}/invite`
```json
{"email": "collegue@example.com", "role": "member"}
```
> Si l'utilisateur n'existe pas, envoie une invitation (ou le cree avec un mot de passe temporaire).

#### `PUT /workspaces/{workspace_id}/members/{user_id}`
```json
{"role": "admin"}
```

#### `DELETE /workspaces/{workspace_id}/members/{user_id}`
Retirer un membre.

### API Keys (par workspace)

#### `GET /workspaces/{workspace_id}/api-keys`
Liste les API keys du workspace (prefixe seulement, pas la cle complete).

#### `POST /workspaces/{workspace_id}/api-keys`
```json
{"name": "CLI dev", "permissions": ["chat", "tasks"]}
// Response 201
{"id": "uuid", "key": "hf-abc123...", "name": "CLI dev"}
```
> La cle complete est retournee UNE SEULE FOIS a la creation.

#### `DELETE /workspaces/{workspace_id}/api-keys/{key_id}`
Revoquer une API key.

### Routes existantes — scopees par workspace

Toutes les routes existantes sont prefixees ou filtrees :

| Route actuelle | Route Phase 4 | Changement |
|---------------|---------------|------------|
| `GET /agents` | `GET /agents` | Filtre par workspace du token |
| `POST /agents` | `POST /agents` | Ajoute workspace_id automatiquement |
| `POST /chat` | `POST /chat` | Lie conversation au user + workspace |
| `POST /chat/stream` | `POST /chat/stream` | Idem |
| `GET /tasks/{id}` | `GET /tasks/{id}` | Verifie que la task appartient au workspace |
| `POST /tasks/run` | `POST /tasks/run` | Lie task au user + workspace |

> Les routes ne changent pas de chemin — le scope est derive du token JWT ou de l'API key.

### Agent-to-Agent

#### `POST /agents/{agent_id}/message`
```json
{"from_agent_id": "assistant-principal", "message": "Analyse ces donnees...",
 "conversation_id": "uuid-optionnel"}
```
> Usage interne par le tool `message_agent()`. Pas expose directement aux utilisateurs.

### Audit

#### `GET /workspaces/{workspace_id}/audit`
```json
// Query params: ?action=chat.invoke&limit=50&offset=0
// Response 200
{
  "items": [
    {"id": "uuid", "user_id": "uuid", "action": "chat.invoke",
     "resource_type": "conversation", "resource_id": "uuid",
     "details": {"agent_id": "assistant-principal", "message_preview": "Ecris un..."},
     "created_at": "2026-03-15T10:00:00Z"}
  ],
  "total": 142
}
```

---

## 7. Agent-to-Agent — Details Techniques

### Nouveau tool `message_agent()`

```python
@tool
def message_agent(
    target_agent_id: str,
    message: str,
    wait_for_response: bool = True,
) -> str:
    """Envoie un message a un autre agent et retourne sa reponse.

    Utilise pour deleguer une tache a un agent specialise
    (ex: demander a l'agent 'data-analyst' d'analyser un dataset).
    """
    if wait_for_response:
        # Invocation synchrone via AgentManager
        result = agent_manager.invoke(target_agent_id, message)
        return result["response"]
    else:
        # Async via Redis pub/sub
        redis.publish(f"agent:{target_agent_id}:inbox", json.dumps({
            "from": current_agent_id,
            "message": message,
            "conversation_id": conversation_id,
        }))
        return "Message envoye."
```

### Difference avec les sous-agents (task tool)

| | Sous-agent (task) | Agent-to-agent (message_agent) |
|---|---|---|
| Relation | Parent → enfant ephemere | Pair a pair, agents independants |
| Contexte | Isole, cree a la volee | Chaque agent garde son propre contexte |
| Persistance | Disparait apres la tache | Les deux agents persistent |
| Config | Defini dans subagents[] du parent | Agents configures independamment en DB |
| Cas d'usage | Sous-tache isolee | Collaboration entre specialistes |

### Workflow

```
Agent A (assistant-principal)             Agent B (data-analyst)
        |                                         |
        |  message_agent("data-analyst",           |
        |    "Analyse le fichier sales.csv")       |
        |  ---------------------------------------->
        |                                         |
        |                                    Recoit le message
        |                                    Analyse le fichier
        |                                    Retourne le resultat
        |  <----------------------------------------
        |                                         |
        |  "Les ventes Q1 sont en hausse de 15%"  |
        |                                         |
```

### Restrictions
- Un agent ne peut communiquer qu'avec les agents du meme workspace
- Le nombre de hops (A→B→C) est limite (defaut: 3) pour eviter les boucles
- Chaque `message_agent()` est logge dans l'audit

---

## 8. OAuth2 — Details Techniques

### Providers supportes

| Provider | Client ID env var | Scopes |
|----------|-------------------|--------|
| Google | `OAUTH_GOOGLE_CLIENT_ID` | `openid email profile` |
| GitHub | `OAUTH_GITHUB_CLIENT_ID` | `user:email` |

### Flux OAuth2 (Authorization Code)

```
1. User clique "Se connecter avec Google"
   GET /auth/oauth/google
        |
2. Redirect vers Google avec client_id + scopes
        |
3. User autorise l'app chez Google
        |
4. Google redirige vers /auth/oauth/google/callback?code=xxx
        |
5. Core echange le code contre un access_token Google
        |
6. Core recupere le profil (email, nom)
        |
7. Core cree/lie le User en DB
        |
8. Core genere JWT HiveFlows (access + refresh)
        |
9. Redirect vers /dashboard avec le JWT en cookie
```

### Configuration (desactivable)

```env
# OAuth2 — laisser vide pour desactiver
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
OAUTH_GITHUB_CLIENT_ID=
OAUTH_GITHUB_CLIENT_SECRET=
OAUTH_REDIRECT_BASE_URL=http://192.168.1.10:8002
```

Si les client IDs sont vides, les boutons OAuth n'apparaissent pas sur la page login.

---

## 9. Dashboard — Pages Ajoutees/Modifiees

### Schema d'URLs

Les pages scopees par workspace utilisent le slug dans l'URL : `/dashboard/w/{slug}/...`.
Cela rend les pages bookmarkables, partageables entre membres, et le contexte workspace est toujours explicite.
`/dashboard` sans slug redirige vers le workspace par defaut de l'utilisateur.
Le selecteur workspace dans la sidebar change l'URL (pas un etat cache en session).

### Nouvelles pages

| Route | Page | Description |
|-------|------|-------------|
| `/dashboard/login` | Login | Email/password + boutons OAuth (remplace l'ancien login API key) |
| `/dashboard/register` | Inscription | Formulaire email + password |
| `/dashboard/profile` | Profil | Modifier nom, email, mot de passe, lier OAuth |
| `/dashboard/workspaces` | Workspaces | Liste des workspaces, creer un nouveau |
| `/dashboard/workspaces/{slug}` | Detail | Membres, roles, invitations, API keys |
| `/dashboard/workspaces/{slug}/audit` | Audit | Journal des actions (filtrable) |
| `/dashboard/w/{slug}/conversations` | Historique | Toutes les conversations du workspace (recherche, filtre par agent) |
| `/dashboard/w/{slug}/conversations/{id}` | Conversation | Relire une conversation passee |

### Pages modifiees (scopees par workspace)

| Route | Changement |
|-------|-----------|
| `/dashboard` | Redirige vers `/dashboard/w/{slug-par-defaut}/` |
| `/dashboard/w/{slug}/` | Dashboard workspace : stats scopes par workspace |
| `/dashboard/w/{slug}/chat` | Conversations liees au user, historique dans sidebar |
| `/dashboard/w/{slug}/agents` | Agents filtres par workspace, bouton "Discuter avec" autre agent |
| `/dashboard/w/{slug}/tasks` | Tasks filtrees par workspace + user |

### Navigation

```
Sidebar:
  [Logo HiveFlows]
  [Selecteur workspace ▼]      <- change l'URL vers /dashboard/w/{slug}/...
  ─────────────
  Dashboard                     -> /dashboard/w/{slug}/
  Chat                          -> /dashboard/w/{slug}/chat
  Conversations    <- NOUVEAU   -> /dashboard/w/{slug}/conversations
  Agents                        -> /dashboard/w/{slug}/agents
  Tasks                         -> /dashboard/w/{slug}/tasks
  ─────────────
  Workspaces       <- NOUVEAU   -> /dashboard/workspaces
  Audit            <- NOUVEAU   -> /dashboard/workspaces/{slug}/audit
  ─────────────
  [Profil]         <- NOUVEAU   -> /dashboard/profile
  [Deconnexion]
```

---

## 10. CLI — Changements

### Nouvelle commande `login`

```bash
# Login par email/password (stocke le JWT localement)
hiveflows login
# Email: patrick@example.com
# Password: ****
# Connecte! Workspace: patrick (owner)

# Login par API key (retro-compatible)
hiveflows login --api-key hf-abc123...

# Changer de workspace
hiveflows workspace use mon-equipe

# Lister les workspaces
hiveflows workspace list
```

### Session locale

```json
// ~/.hiveflows/session.json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "workspace_id": "uuid",
  "workspace_slug": "patrick",
  "conversation_id": "uuid",
  "agent_id": "assistant-principal"
}
```

Le CLI auto-refresh le token quand il expire.

---

## 11. Plan de Taches (Ordonne par Dependances)

### Bloc A — Users & Auth JWT
```
Depend de : Phase 3
A1. [x] Modele SQLAlchemy User + migration Alembic 005
A2. [x] Dependances : passlib[bcrypt], python-jose[cryptography]
A3. [x] Service auth : hash_password(), verify_password(), create_jwt(), decode_jwt()
A4. [x] Routes POST /auth/register, POST /auth/login, POST /auth/refresh, GET /auth/me
A5. [x] Middleware auth : get_current_user() — supporte Bearer JWT ET X-API-Key (retro-compat)
A6. [x] Mise a jour des routes existantes pour injecter le user courant
A7. [x] Dashboard : nouvelles pages login (email/password) et register
A8. [x] Test : register, login, access route protegee, refresh token
```

### Bloc B — Workspaces & Membres
```
Depend de : A8
B1. [x] Modeles Workspace + WorkspaceMember + migration Alembic 006
B2. [x] Auto-creation du workspace personnel a l'inscription (dans register)
B3. [x] Routes CRUD workspaces : GET/POST/PUT/DELETE /workspaces
B4. [x] Routes membres : GET/POST/PUT/DELETE /workspaces/{id}/members
B5. [x] Route POST /workspaces/{id}/invite (cree le user si inexistant)
B6. [x] Middleware get_workspace_id() — extrait du token ou du header
B7. [x] Dashboard : page workspaces (liste, creer, detail, membres)
B8. [x] Test : creer workspace, inviter membre, lister membres, changer role
```

### Bloc C — Isolation par Workspace (Scoping)
```
⚠️  REWORK PARTIEL NECESSAIRE : le modele initial scopait les agents par workspace_id direct.
Le nouveau modele (voir Bloc L) fait des agents une propriete utilisateur, lies aux workspaces
via la table workspace_agents. Les items C1/C3/C4/C5 doivent etre revus dans Bloc L.
Les conversations et tasks gardent workspace_id (pas de changement).

Depend de : B8
C1. [x] Ajouter workspace_id + user_id aux tables agent_configs, conversations, tasks
C2. [x] Migration Alembic 007 : nouvelles colonnes + migration des donnees existantes
C3. [x] Modifier toutes les queries SELECT pour filtrer par workspace_id
C4. [x] Modifier les INSERT pour injecter workspace_id et user_id automatiquement
C5. [x] AgentManager : cache scoped par workspace (cle = (workspace_id, agent_id))
C6. [x] Dashboard : URLs /dashboard/w/{slug}/... — selecteur workspace dans la sidebar change l'URL, /dashboard redirige vers le workspace par defaut
C7. [x] Test : un user ne voit pas les agents/conversations d'un autre workspace
```

### Bloc D — Permissions (RBAC)
```
Depend de : C7
D1. [x] Module permissions.py : roles, permissions, matrice, dependency require_permission()
D2. [x] Appliquer require_permission() sur toutes les routes API
D3. [x] Appliquer les permissions dans le dashboard (masquer boutons, bloquer actions)
D4. [x] Skills permissions : allowed_skills sur WorkspaceMember, filtre dans build_agent_from_config()
D5. [x] Test : viewer ne peut pas creer d'agent, member ne peut pas supprimer, admin peut tout sauf supprimer workspace
```

### Bloc E — API Keys Workspace-Scoped
```
Depend de : C7
E1. [x] Modele ApiKey + migration Alembic (dans 006 ou 008)
E2. [x] Routes CRUD : GET/POST/DELETE /workspaces/{id}/api-keys
E3. [x] Modifier le middleware auth pour resoudre API key → workspace_id depuis la DB
E4. [x] Migration : convertir les API keys existantes (.env) en entrees DB pour le workspace par defaut
E5. [x] Dashboard : page gestion des API keys par workspace
E6. [x] Test : creer API key, l'utiliser pour auth, la revoquer
```

### Bloc F — Agent-to-Agent Communication
```
Depend de : C5
F1. [x] Tool message_agent() : invocation synchrone d'un autre agent du meme workspace
F2. [x] Restriction : meme workspace, limite de hops (max 3)
F3. [x] Ajouter message_agent aux tools disponibles dans build_agent_from_config()
F4. [x] Option async via Redis pub/sub (fire-and-forget)
F5. [x] Dashboard : visualisation des echanges agent-to-agent dans le chat
F6. [x] Test : Agent A envoie un message a Agent B, recoit la reponse
```

### Bloc G — Audit Logging
```
Depend de : C7
G1. [x] Modele AuditLog + migration 010
G2. [x] Service audit : log_action(user_id, workspace_id, action, resource_type, resource_id, details)
G3. [x] Middleware FastAPI pour logger automatiquement les actions API
G4. [x] Logger les actions critiques : login, register, agent CRUD, chat, task run, HITL decisions
G5. [x] Route GET /workspaces/{id}/audit (pagination, filtres)
G6. [x] Dashboard : page audit log (tableau filtrable par action/user/date)
G7. [x] Test : verifier que les actions sont loggees correctement
```

### Bloc H — OAuth2 (Google + GitHub)
```
Depend de : A8
H1. [x] Dependance : authlib + itsdangerous
H2. [x] Config : OAUTH_GOOGLE_CLIENT_ID/SECRET, OAUTH_GITHUB_CLIENT_ID/SECRET dans .env
H3. [x] Routes GET /auth/oauth/{provider} (redirect) + GET /auth/oauth/{provider}/callback
H4. [x] Lier un compte OAuth a un User existant (par email) ou creer un nouveau User
H5. [x] Dashboard : boutons "Se connecter avec Google/GitHub" sur la page login
H6. [x] Desactivable : si les env vars sont vides, les boutons n'apparaissent pas
H7. [x] Test : mock OAuth flow, verifier creation/linkage du user
```

### Bloc I — Dashboard Complet & Polish
```
Depend de : G6, H5
I1. [x] Page /dashboard/profile — modifier nom, email, mot de passe, lier OAuth
I2. [x] Page /dashboard/conversations — historique complet, recherche, filtre par agent
I3. [x] Page /dashboard/conversations/{id} — relire une conversation
I4. [x] Ameliorer /dashboard/chat — sidebar historique conversations, nouveau chat
I5. [x] Ameliorer /dashboard/agents — badge workspace, bouton "message_agent" test
I6. [x] Navigation : selecteur workspace dans sidebar, breadcrumbs
I7. [x] Responsive : adapter pour tablette et mobile
I8. [x] Test : navigation complete (toutes les pages retournent 200)
```

### Bloc J — CLI Login & Workspaces
```
Depend de : E6
J1. [x] Commande `hiveflows login` (email/password ou --api-key)
J2. [x] Commande `hiveflows workspace list` + `hiveflows workspace use <slug>`
J3. [x] Auto-refresh du JWT quand il expire
J4. [x] Stocker workspace_id dans session.json
J5. [x] Test : login, switch workspace, chat dans le bon workspace
```

### Bloc K — Finition et Validation
```
Depend de : tous les blocs
K1. [x] Tests unitaires : auth register/login/refresh (TestAuth)
K2. [x] Tests unitaires : workspace CRUD + membres (TestWorkspaces)
K3. [x] Tests unitaires : permissions RBAC (TestPermissions)
K4. [x] Tests integration : isolation workspace end-to-end (TestIsolation)
K5. [x] Tests integration : agent-to-agent communication (TestAgentComm)
K6. [x] Tests integration : audit log complet (TestAudit)
K7. [x] Mettre a jour README.md
K8. [x] Mettre a jour .env.example avec les nouvelles variables
K9. [x] Verifier que docker compose up part proprement de zero
K10. [x] Revue securite : JWT secrets, password hashing, CORS, rate limiting login
```

---

### Nouveaux Blocs (ajoutes suite a QUESTION.md — 2026-03-15)

> Ces blocs s'inserent dans le plan apres Bloc C. Le Bloc L rework partiellement Bloc C
> pour changer le modele de propriete des agents (workspace → user).

### Bloc L — Agent Ownership + Agent Par Defaut
```
Depend de : C6
L1. [x] Modifier agent_configs : ajouter owner_id (FK users) + is_default — migration 011
L2. [x] Creer table workspace_agents (junction agent <-> workspace) — migration 011
L3. [x] Ajouter slug et is_site_admin a la table users — migration 011
L4. [x] Migrer les donnees : deplacer la relation agent<->workspace de agent_configs.workspace_id vers workspace_agents
L5. [x] Modifier queries agents pour utiliser workspace_agents (JOIN au lieu de WHERE workspace_id)
L6. [x] Routes API : POST /workspaces/{id}/agents/{agent_id} (ajouter un agent au workspace)
L7. [x] Routes API : DELETE /workspaces/{id}/agents/{agent_id} (retirer un agent du workspace)
L8. [x] Agent par defaut : flag is_default, auto-ajoute a tout nouveau workspace a sa creation
L9. [x] Route site-admin : PUT /admin/default-agent (configurer quel agent est le defaut)
L10. [x] Dashboard : liste des agents du workspace, bouton "Ajouter un de mes agents"
L11. [x] Test : agent dans N workspaces, retirer != supprimer, agent par defaut present partout
```

### Bloc M — @ Mentions et Adressage Multi-Agent
```
Depend de : L11
M1. [x] Parser les messages chat pour detecter @slug (regex simple)
M2. [x] Router le message vers l'agent mentionne uniquement (pas les autres)
M3. [x] Mode 1:1 : routing implicite quand un seul agent dans la conversation
M4. [x] Gerer le cas "aucun @ et plusieurs agents" : utilise l'agent selectionne, sinon message d'aide
M5. [x] Dashboard : autocomplete @slug dans le champ de saisie (liste agents du workspace)
M6. [x] Dashboard : affichage visuel des @mentions dans les messages
M7. [x] Test : 14 unit tests mention_parser + 2 integration tests routing
```

### Bloc N — Slash Commands
```
Depend de : B8
N1. [x] Parser les messages chat pour detecter /commande (avant envoi au LLM)
N2. [x] Registry de commandes : dict commande → handler async
N3. [x] Commandes de base : /invite @slug, /kick @slug, /join #workspace, /part + /members + /help
N4. [x] Feedback visuel : message systeme dans le chat (pas reponse agent)
N5. [x] Gestion d'erreurs : commande inconnue, permissions insuffisantes
N6. [ ] Future : alias personnalises par utilisateur (hors scope initial)
N7. [x] Test : 18 parser + 22 execution tests (40 total) — /invite, /kick, /part, permissions, erreurs
```

### Bloc O — Workspace Filesystem
```
Depend de : B8
O1. [x] Creer repertoire /data/workspaces/{workspace_id}/ a la creation du workspace
O2. [x] Supprimer/archiver le repertoire quand le workspace est desactive
O3. [x] docker-compose : monter /data/workspaces/ en volume
O4. [x] Configurer les tools agents (read_file, write_file, ls) pour etre scopes au repertoire du workspace
O5. [x] Isolation : un agent ne peut PAS acceder au repertoire d'un autre workspace (chroot ou validation de path)
O6. [x] Test : agent ecrit un fichier, autre agent du meme workspace peut le lire, agent d'un autre workspace ne peut pas
```

### Bloc P — Conversation Pagination & Contexte Agent
```
Depend de : C7
P1. [x] API GET /dashboard/w/{slug}/chat/history?before=xxx&limit=50 (pagination curseur — chat_persistence.get_recent_messages)
P2. [x] Dashboard : infinite scroll HTMX — charger les anciens messages en scrollant vers le haut (loadOlderMessages + "Charger plus ancien...")
P3. [x] Stocker les messages individuellement en DB (pas dans un JSONB blob — table messages avec FK conversation)
P4. [x] Agent context window : champ context_window_size sur agent_configs (defaut: 50 messages) — migration 013
P5. [x] Envoyer seulement les N derniers messages au LLM lors de l'invocation — ContextWindowChatModel wrapper (factory.py)
P6. [ ] Future : recherche dans l'historique complet via FTS PostgreSQL (hors scope initial)
P7. [x] Test : tests/test_core/test_pagination.py — ContextWindowChatModel trimming + pagination curseur
```

---

## 12. Dependances Python (Ajouts Phase 4)

### core/requirements.txt — ajouts
```
# Auth JWT
python-jose[cryptography]>=3.3
passlib[bcrypt]>=1.7

# OAuth2
authlib>=1.3                  # OAuth2 client (Google, GitHub)
httpx>=0.27                   # deja present, pour les appels OAuth

# Rate limiting (protection brute-force login)
slowapi>=0.1
```

### channels/cli/requirements.txt — ajouts
```
# Aucun ajout — httpx suffit pour le login JWT
```

---

## 13. Configuration (Variables d'Environnement — Ajouts)

```env
# JWT
JWT_SECRET_KEY=change-me-to-a-random-64-char-string
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# OAuth2 (optionnel — laisser vide pour desactiver)
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
OAUTH_GITHUB_CLIENT_ID=
OAUTH_GITHUB_CLIENT_SECRET=
OAUTH_REDIRECT_BASE_URL=http://192.168.1.10:8002

# Rate limiting
LOGIN_RATE_LIMIT=5/minute
```

---

## 14. Docker Compose (Changements)

```yaml
# Pas de nouveau service necessaire.
# Seul changement : ajouter les nouvelles env vars au .env

# Optionnel futur : service de mail pour les invitations
# (en Phase 4 les invitations sont "directes" — pas de mail)
```

---

## 15. Angles Morts — Points d'Attention

| # | Risque | Mitigation |
|---|--------|------------|
| 1 | **Migration donnees existantes** : les agents/conversations existants n'ont pas de workspace_id | Migration 007 cree un workspace "default" et y rattache tout |
| 2 | **JWT secret rotation** : si le secret change, tous les tokens sont invalides | Supporter un old_secret temporaire pour la rotation |
| 3 | **API key retro-compat** : le CLI existant utilise des API keys statiques (.env) | Migration cree des API keys DB pour le workspace default |
| 4 | **Agent-to-agent boucles** : Agent A message Agent B qui message Agent A | Compteur de hops dans le contexte, max 3 par defaut |
| 5 | **Performance audit logs** : beaucoup d'ecritures (chaque token stream?) | Logger par action (chat.invoke), pas par token. Cleanup periodique |
| 6 | **OAuth2 sur reseau local** : Google/GitHub necessitent un redirect URI accessible | Supporter un tunnel (ngrok) ou desactiver OAuth en local |
| 7 | **Workspace slug collision** : deux users veulent "mon-projet" | Slug unique global, proposer des alternatives |
| 8 | **Multi-workspace agent cache** : le cache AgentManager est global | Cle de cache = (workspace_id, agent_id) au lieu de agent_id seul |
| 9 | **Rate limiting login** : brute-force sur /auth/login | slowapi avec limite 5/minute par IP |
| 10 | **Sessions dashboard** : migration de cookie API key vers cookie JWT | Page login detecte l'ancien cookie et force re-login |
| 11 | **Slug collision cross-table** : users et agents partagent le meme namespace | Contrainte unique cross-table (table de slugs ou check avant insert) |
| 12 | **Agent ownership migration** : les agents ont actuellement workspace_id (Bloc C) | Bloc L rework : migrer vers owner_id + workspace_agents junction |
| 13 | **Context window cut-off** : couper l'historique peut perdre du contexte important | Configurable par agent, valeur par defaut conservative (50 messages) |
| 14 | **Workspace filesystem isolation** : un path traversal pourrait exposer d'autres workspaces | Validation stricte des paths, chroot ou prefix check obligatoire |

---

## 16. Definition de "Phase 4 terminee"

La Phase 4 est complete quand on peut faire ceci **de bout en bout** :

```bash
# 1. Demarrer tous les services
docker compose up

# 2. S'inscrire via le dashboard
# Naviguer vers http://192.168.1.10:8002/dashboard/register
# Creer un compte : patrick@example.com / password
# -> Redirige vers /dashboard avec un workspace personnel "patrick"

# 3. Creer un workspace partage
# /dashboard/workspaces -> "Nouveau workspace"
# Nom: "Mon Equipe", Slug: "mon-equipe"

# 4. Inviter un collegue
# /dashboard/workspaces/mon-equipe -> "Inviter"
# Email: alice@example.com, Role: member

# 5. Creer un agent dans le workspace
curl -X POST http://192.168.1.10:8002/agents \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"id": "analyste", "name": "Analyste", ...}'

# 6. Verifier l'isolation : Alice ne voit pas les agents du workspace "patrick"
# Se connecter en tant qu'Alice -> workspace "mon-equipe" -> voir l'agent "analyste"
# Aller dans le workspace personnel d'Alice -> ne voit PAS "analyste"

# 7. Agent-to-agent
curl -X POST http://192.168.1.10:8002/chat \
  -H "Authorization: Bearer eyJ..." \
  -d '{"agent_id": "assistant-principal",
       "message": "Demande a l agent analyste de resumer ce texte: ..."}'
# -> L'assistant utilise message_agent("analyste", "Resume ce texte: ...")
# -> L'analyste repond, l'assistant integre la reponse

# 8. Verifier l'audit log
# /dashboard/workspaces/mon-equipe/audit
# -> Voir toutes les actions : login, agent.create, chat.invoke, etc.

# 9. Generer une API key pour le CLI
# /dashboard/workspaces/mon-equipe -> "API Keys" -> "Generer"
# -> hf-abc123...

# 10. CLI avec la nouvelle auth
hiveflows login --api-key hf-abc123...
hiveflows workspace use mon-equipe
hiveflows chat "Bonjour, qui es-tu?"
# -> L'agent repond dans le contexte du workspace "mon-equipe"
```

---

*Phase 4 planifiee le 12 mars 2026.*
