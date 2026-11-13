# HiveFlows — Phase 7 : Unified Task Bus & Human Tools

> Objectif : unifier tous les mecanismes d'execution (Celery, tools sync, Beekeeper) sous un **bus d'activite unique** avec un dashboard temps reel, et permettre a l'humain d'invoquer les tools **directement depuis le file manager** sans passer par un agent.

---

## 1. Perimetre

### Dans le scope
- Refactoring du model `Task` en enveloppe de suivi unifiee (hybride)
- Bus Redis unifie (`hive:ws:{id}:activity`) pour tous les evenements
- Consolidation des endpoints SSE (suppression des obsoletes)
- Dashboard "Activity" temps reel (remplace la page "Tasks" Celery-only)
- Human Tool Registry — tools invocables par l'humain depuis le file manager
- Menu contextuel file manager (bouton par ligne + bouton dans le viewer)
- Annulation des tasks Celery depuis le dashboard
- Nettoyage manuel des tasks terminees (bouton "Nettoyer")
- Conventions filesystem `.hive/outputs/`

### Hors scope (Phase 8+)
- Taches planifiees (cron/scheduler) — les fondations sont pretes (`triggered_by: "cron:{id}"`)
- Bees (sub-agents ephemeres LLM) — seront des Tasks de type `agent_task`
- Slash commands tools (`/translate`, `/convert`) dans le chat
- Annulation via Redis pour tools non-Celery
- Portal comme source de tools
- DeepAgents v0.5 migration
- Channels externes (Slack, Discord, Telegram)
- Skill marketplace

---

## 2. Architecture Phase 7

```
                Utilisateur
                │
     ┌──────────┼───────────┐
     │ CLI      │ Dashboard │
     └──────────┼───────────┘
                │
     ┌──────────▼───────────────────────────────────────────┐
     │                    CORE (FastAPI)                     │
     │                                                      │
     │  POST /tasks/run           → Task unifiee            │
     │  GET  /tasks/{id}          → Status unifie           │
     │  POST /tasks/{id}/cancel   → Revoke Celery           │
     │  DELETE /tasks/cleanup     → Purge terminees          │
     │                                                      │
     │  GET  /tools/available     → Human Tool Registry     │
     │  POST /tools/execute       → Exec tool (user)        │
     │                                                      │
     │  GET  /activity/stream     → SSE unifie (NOUVEAU)    │
     │                                                      │
     │  ┌────────────────────────────────────────────────┐  │
     │  │            ACTIVITY BUS (Redis)                 │  │
     │  │                                                │  │
     │  │  publish_activity(ws_id, event)                 │  │
     │  │  Canal : hive:ws:{id}:activity                  │  │
     │  │                                                │  │
     │  │  Events : task_created, task_progress,          │  │
     │  │           task_done, task_error,                │  │
     │  │           task_cancelled, app_status            │  │
     │  └────────────────────────────────────────────────┘  │
     │            │                    │                     │
     │     ┌──────┘                    └──────┐              │
     │     ▼                                  ▼              │
     │  Celery worker                    Beekeeper           │
     │  (run_python, run_bash,           (app lifecycle)     │
     │   run_translate)                                      │
     └──────────────────────────────────────────────────────┘
```

### Flow Human Tool (file manager)

```
Humain clique "Traduire" sur report.md
        │
        ▼
Menu dropdown (style LangChain docs, MDI neon)
        │
        ▼
Modal parametres (langue cible, modele)
        │
        ▼
POST /tools/execute  →  Task creee (triggered_by: "user:{id}")
        │                    │
        ▼                    ▼
Toast "en cours..."     Bus activity (task_created)
        │                    │
        ▼                    ▼
Celery worker execute   Dashboard Activity (temps reel)
        │
        ▼
Toast "termine" + lien vers .hive/outputs/report_fr.md
```

---

## 3. Decisions Techniques

| Decision | Choix | Justification |
|----------|-------|---------------|
| Task model | Hybride (Option C) — enveloppe de suivi | Source de verite unique, pas de refactoring lourd des backends |
| Nom de la table | `tasks` (inchange) | Continuite avec l'existant |
| Human Tools UI | Bouton par ligne + bouton dans le viewer | Pas de panneau Actions, pas de slash commands pour l'instant |
| Style menu | LangChain docs : fond sombre, MDI neon `var(--cyan)`, sous-titre | Voir `docs/menu_tools.png` pour reference |
| Feedback execution | Toast notification + Activity dashboard | Double feedback : immediat (toast) + persistant (dashboard) |
| Annulation | Celery `revoke()` seulement | Les autres backends plus tard via Redis publish |
| Nettoyage tasks | Manuel — bouton "Nettoyer" | Pas de TTL automatique |
| Bus Redis | Canal unique `hive:ws:{id}:activity` | Remplace les canaux fragmentes |
| SSE Activity | Endpoint dedie `/w/{id}/activity/stream` | Separation des concerns, les SSE chat restent a part |
| Outputs Human Tools | `.hive/outputs/` par defaut | Convention filesystem standardisee |

---

## 4. Etat actuel du code (avant Phase 7)

| Composant | Fichier | Notes |
|---|---|---|
| Model Task | `core/models/task.py` | Champs actuels : id, celery_id, type, payload, status (string), result, error, user_id, workspace_id |
| API Tasks | `core/api/routes_tasks.py` | `POST /tasks/run`, `GET /tasks/{task_id}` |
| Dashboard Tasks | `core/templates/dashboard/tasks/list.html` | Polling 5s (auto-refresh), pas de SSE |
| File Manager | `core/static/js/file-manager.js` | Table avec actions (rename, move, download, delete) |
| SSE task progress | `routes_dashboard.py:2187` | `/tasks/{id}/progress/stream` — canal `hiveflows:tasks:{id}:progress` |
| SSE workspace events | `routes_dashboard.py:2111` | `/w/{id}/events/stream` — canal `hive:ws:{id}:events` |
| SSE app metrics | `routes_dashboard.py:2145` | `/w/{id}/apps/metrics/stream` — canal `hive:app:{id}:*:metrics` |
| SSE app logs | `routes_apps.py:470` | `/w/{id}/apps/{slug}/logs` |
| SSE files | `routes_files.py:157` | `/files/{slug}/files/stream` |
| SSE chat | `routes_chat.py:122` | `/chat/stream` |
| Redis chat | `chat_broadcaster.py:18` | Canal `hiveflows:ws:{id}:chat` |
| Redis agent bus | `agent_bus.py` | Canal `hiveflows:agent_bus` |

---

## 5. Schema de Donnees

### Table `tasks` (refactoree)

```sql
-- Migration Alembic : ALTER TABLE tasks
-- Les donnees existantes ne sont pas conservees (truncate avant alter)

ALTER TABLE tasks
    ADD COLUMN name             VARCHAR(200) NOT NULL DEFAULT '',
    ADD COLUMN progress         INTEGER,
    ADD COLUMN progress_detail  VARCHAR(500),
    ADD COLUMN triggered_by     VARCHAR(200) NOT NULL DEFAULT 'system',
    ADD COLUMN backend_ref      VARCHAR(200),
    ADD COLUMN params           JSONB DEFAULT '{}',
    ADD COLUMN started_at       TIMESTAMP,
    ADD COLUMN completed_at     TIMESTAMP;

-- Renommer celery_id → backend_ref (si migration propre)
-- Sinon : drop celery_id, la nouvelle colonne backend_ref le remplace

ALTER TABLE tasks DROP COLUMN IF EXISTS celery_id;
ALTER TABLE tasks DROP COLUMN IF EXISTS payload;

-- Index pour le dashboard Activity
CREATE INDEX idx_tasks_workspace_status ON tasks(workspace_id, status);
CREATE INDEX idx_tasks_workspace_created ON tasks(workspace_id, created_at DESC);
```

### Colonnes finales de `tasks`

```python
class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[UUID]                    # PK (existant)
    workspace_id: Mapped[UUID]          # FK → workspaces (existant)
    user_id: Mapped[UUID]               # FK → users (existant)

    type: Mapped[str]                   # "python", "bash", "translate", "tool_exec", "app_action" (existant, elargi)
    name: Mapped[str]                   # NOUVEAU — nom lisible : "Traduire report.md → fr"
    status: Mapped[str]                 # "pending", "running", "success", "failed", "cancelled" (existant, + cancelled)

    progress: Mapped[int | None]        # NOUVEAU — 0-100
    progress_detail: Mapped[str | None] # NOUVEAU — "chunk 3/7", "pulling layer 4/12"

    triggered_by: Mapped[str]           # NOUVEAU — "user:{user_id}", "agent:{agent_id}", "system"
    backend_ref: Mapped[str | None]     # NOUVEAU (remplace celery_id) — "celery:{task_id}", "beekeeper:{container}"

    params: Mapped[dict]                # NOUVEAU (remplace payload) — JSONB
    result: Mapped[dict | None]         # Existant — JSONB
    error: Mapped[str | None]           # Existant

    created_at: Mapped[datetime]        # Existant
    started_at: Mapped[datetime | None] # NOUVEAU
    completed_at: Mapped[datetime | None] # NOUVEAU
```

---

## 6. Bus Redis Unifie

### Canaux apres Phase 7

| Canal | Usage | Changement |
|---|---|---|
| `hive:ws:{id}:activity` | **NOUVEAU** — tous les evenements task/app | Remplace les canaux fragmentes |
| `hiveflows:ws:{id}:chat` | Messages chat | Inchange |
| `hive:app:{id}:{slug}:logs` | Logs apps (volume eleve) | Inchange |
| `hive:app:{id}:*:metrics` | Metriques apps (volume eleve) | Inchange |
| `hiveflows:agent_bus` | Agent-to-agent messages | Inchange |

### Canaux supprimes (fusionnes dans `activity`)
- `hiveflows:tasks:{task_id}:progress` → event `task_progress`
- `hive:ws:{id}:events` → event `app_status`

### Format d'evenement unifie

```json
{
  "type": "task_created | task_progress | task_done | task_error | task_cancelled | app_status",
  "task_id": "uuid",
  "source": "celery | tool | app",
  "timestamp": "2026-04-13T10:30:00Z",
  "data": {
    "name": "Traduire report.md → fr",
    "progress": 42,
    "detail": "chunk 3/7"
  }
}
```

### Module `activity_bus.py`

```python
# core/services/activity_bus.py

async def publish_activity(workspace_id: str, event: dict):
    """Publie un evenement sur le bus d'activite du workspace."""
    channel = f"hive:ws:{workspace_id}:activity"
    await redis.publish(channel, json.dumps(event))

async def subscribe_activity(workspace_id: str):
    """Generateur async qui yield les evenements d'activite."""
    channel = f"hive:ws:{workspace_id}:activity"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    async for message in pubsub.listen():
        if message["type"] == "message":
            yield json.loads(message["data"])
```

---

## 7. Contrats d'Interface (Endpoints)

### Endpoints modifies

#### `POST /api/w/{slug}/tasks/run`
Cree une Task unifiee et dispatch vers le backend.

**Request :**
```json
{
  "type": "translate",
  "name": "Traduire report.md → fr",
  "params": {"file_path": "documents/report.md", "target_lang": "fr-fr"},
  "triggered_by": "agent:assistant-principal"
}
```

**Response :**
```json
{
  "task_id": "uuid",
  "status": "pending",
  "name": "Traduire report.md → fr",
  "backend_ref": "celery:abc123"
}
```

#### `GET /api/w/{slug}/tasks/{task_id}`
Retourne le status unifie d'une task.

**Response :**
```json
{
  "id": "uuid",
  "type": "translate",
  "name": "Traduire report.md → fr",
  "status": "running",
  "progress": 42,
  "progress_detail": "chunk 3/7",
  "triggered_by": "user:uuid",
  "params": {"file_path": "documents/report.md", "target_lang": "fr-fr"},
  "result": null,
  "started_at": "2026-04-13T10:30:00Z",
  "created_at": "2026-04-13T10:29:58Z"
}
```

### Nouveaux endpoints

#### `POST /api/w/{slug}/tasks/{task_id}/cancel`
Annule une task Celery (status `pending` ou `running`).

**Response :** `{"status": "cancelled"}`

**Erreur :** `409 Conflict` si la task n'est pas annulable (non-Celery ou deja terminee).

#### `DELETE /api/w/{slug}/tasks/cleanup`
Supprime toutes les tasks en status `success`, `failed`, ou `cancelled` du workspace.

**Response :** `{"deleted_count": 12}`

#### `GET /api/w/{slug}/tools/available`
Retourne les tools invocables par l'humain, filtrables par extension de fichier.

**Query params :** `?file_ext=.md` (optionnel)

**Response :**
```json
[
  {
    "key": "translate_file",
    "name": "Traduire",
    "description": "Traduit le fichier dans la langue cible",
    "icon": "mdi-translate",
    "file_extensions": [".md", ".txt", ".html", ".json", ".csv"],
    "params": [
      {"key": "target_lang", "type": "select", "label": "Langue cible", "options": ["fr-fr", "en-us", "es-es", "de-de"], "default": "fr-fr"},
      {"key": "model", "type": "select", "label": "Modele", "options": ["deepseek/deepseek-chat-v3.1", "openai/gpt-4o-mini"], "default": "deepseek/deepseek-chat-v3.1"}
    ],
    "executor": "celery"
  }
]
```

#### `POST /api/w/{slug}/tools/execute`
Execute un Human Tool — cree une Task avec `triggered_by: "user:{id}"`.

**Request :**
```json
{
  "tool_key": "translate_file",
  "file_path": "documents/report.md",
  "params": {"target_lang": "fr-fr", "model": "deepseek/deepseek-chat-v3.1"}
}
```

**Response :**
```json
{
  "task_id": "uuid",
  "status": "pending",
  "name": "Traduire report.md → fr"
}
```

#### `GET /w/{workspace_id}/activity/stream`
SSE unifie pour toute l'activite du workspace.

**Events SSE :**
```
event: task_created
data: {"task_id": "uuid", "name": "Traduire report.md → fr", "source": "celery"}

event: task_progress
data: {"task_id": "uuid", "progress": 42, "detail": "chunk 3/7"}

event: task_done
data: {"task_id": "uuid", "result": {"output_path": ".hive/outputs/report_fr.md"}}

event: task_error
data: {"task_id": "uuid", "error": "Connection timeout"}

event: task_cancelled
data: {"task_id": "uuid"}

event: app_status
data: {"task_id": "uuid", "app_slug": "vscode-server", "status": "running"}
```

### Endpoints SSE supprimes
- `/tasks/{task_id}/progress/stream` → remplace par `/w/{id}/activity/stream`
- `/w/{workspace_id}/events/stream` → fusionne dans `/w/{id}/activity/stream`

### Endpoints SSE conserves
- `/chat/stream` — streaming chat (inchange)
- `/w/{id}/apps/{slug}/logs` — logs apps volume eleve (inchange)
- `/w/{id}/apps/metrics/stream` — metriques apps (inchange)
- `/files/{slug}/files/stream` — evenements filesystem (inchange)

---

## 8. Human Tool Registry

### Structure du registre

```python
# core/services/human_tools.py

@dataclass
class ToolParam:
    key: str            # "target_lang"
    type: str           # "select" | "autocomplete" | "text" | "number" | "checkbox"
    label: str          # "Langue cible"
    options: list | None       # ["fr-fr", "en-us"] (pour select)
    options_endpoint: str | None  # "/api/tools/fabric/patterns" (pour autocomplete — catalogue dynamique)
    default: Any        # "fr-fr"

@dataclass
class HumanTool:
    key: str                    # "translate_file"
    name: str                   # "Traduire"
    description: str            # "Traduit le fichier dans la langue cible"
    icon: str                   # "mdi-translate"
    file_extensions: list[str]  # [".md", ".txt"] — vide = tous les fichiers
    params: list[ToolParam]     # Parametres a remplir par l'humain
    executor: str               # "celery" | "sync"
    ui: str                     # "inline" | "modal" — rendu popover leger ou modal dedie
    pinned: bool                # True = apparait direct dans le menu contextuel
    group: str | None           # "fabric" — regroupe les tools lies dans le menu

# Registre — les tools s'enregistrent ici
_registry: dict[str, HumanTool] = {}

def register_tool(tool: HumanTool): ...
def get_available_tools(file_ext: str | None = None) -> list[HumanTool]: ...
def execute_tool(key: str, workspace_id: str, user_id: str, file_path: str, params: dict) -> Task: ...
```

### Tools initiaux (Phase 7)

| Tool | Icon | Extensions | Executor | UI | Pinned | Params |
|---|---|---|---|---|---|---|
| `translate_file` | `mdi-translate` | .md, .txt, .html, .json, .csv | celery | inline | ✓ | target_lang, model |
| `fabric_extract_wisdom` | `mdi-star-four-points` | .md, .txt | celery | inline | ✓ | model |
| `fabric_summarize` | `mdi-text-short` | .md, .txt | celery | inline | ✓ | model |
| `fabric_extract_references` | `mdi-format-quote-close` | .md, .txt | celery | inline | ✓ | model |
| `fabric_run` | `mdi-flash` | .md, .txt | celery | modal | ✗ | pattern (autocomplete), model, output |

**Rationale Fabric** — Fabric propose ~200 patterns. Les 3 plus utilises (`extract_wisdom`, `summarize`, `extract_references`) sont epingles comme tools distincts (acces 1-clic). Le reste passe par le tool generique `fabric_run` qui ouvre un modal avec un champ `pattern` de type `autocomplete` (recherche fuzzy alimentee via `options_endpoint`). D'autres tools a catalogue dynamique pourront suivre la meme pattern.

### Menu contextuel file manager

Style inspire de `docs/menu_tools.png` et `docs/mockups/fabric_ui_preview.svg` — fond sombre, icones MDI monochromes couleur neon `var(--cyan)`, sous-titre descriptif. Les tools `pinned` apparaissent directement, les tools non-pinned d'un meme `group` sont accessibles via une entree generique (ex: « Fabric… » → ouvre le modal `fabric_run`).

```
┌────────────────────────────────────────┐
│  EDITION                               │
│    Renommer · Dupliquer · Supprimer    │
│  ──────────────────────────────────    │
│  FABRIC · EPINGLES                     │
│    ✦ Extract Wisdom         ⌘⇧W        │
│    ✦ Summarize              ⌘⇧S        │
│    ✦ Extract References                │
│  ──────────────────────────────────    │
│    ⚡ Fabric…  (tous les patterns) ▸   │
│  ──────────────────────────────────    │
│    🌐 Traduire…                        │
└────────────────────────────────────────┘
```

Le menu est dynamique : seuls les tools compatibles avec l'extension du fichier apparaissent. Les tools `ui: "inline"` ouvrent un popover leger pour remplir les params ; les tools `ui: "modal"` ouvrent un modal dedie (recherche + preview + actions).

### Modal `fabric_run` (pattern reference pour tools avec catalogue externe)

Mockup : [docs/mockups/fabric_ui_preview.svg](mockups/fabric_ui_preview.svg).

Elements :
- **Champ `pattern`** (autocomplete) — recherche fuzzy, liste alimentee par `GET /api/tools/fabric/patterns`, badge `★ PINNED` sur les favoris, navigation clavier ↑↓↵
- **Select `model`** — modeles LLM disponibles
- **Select `output`** — destination (par defaut : `<nom>.{pattern}.md` dans `.hive/outputs/`)
- **Preview du system prompt** du pattern selectionne (aide a la decision avant lancement)
- **Footer** — Entrée pour lancer, Esc pour annuler, bouton "Lancer" degrade cyan→purple

Endpoint associe : `GET /api/w/{slug}/tools/fabric/patterns` → liste `{key, description, pinned}` lue depuis le catalogue Fabric monte dans le container.

---

## 9. Dashboard Activity

### Remplacement
- La page **"Tasks"** (`/dashboard/tasks`) disparait
- Remplacee par **"Activity"** (`/dashboard/activity`)
- Le menu sidebar passe de "Tasks" a "Activity"

### Contenu de la page

| Element | Description |
|---|---|
| Table des tasks | Toutes les tasks du workspace (tous backends) |
| Colonnes | Nom, Type (badge), Status (badge couleur), Source (user/agent/system), Progress (barre), Duree, Date |
| Progress temps reel | SSE depuis `/w/{id}/activity/stream` — les barres se mettent a jour live |
| Filtres | Dropdowns : par type, status, source |
| Bouton "Annuler" | Sur les tasks Celery en `pending`/`running` |
| Bouton "Reexecuter" | Sur les tasks `failed` — relance avec les memes params |
| Bouton "Nettoyer" | Global — supprime toutes les tasks `success` + `failed` + `cancelled` |
| Resultat clickable | Lien vers le fichier output, log d'erreur expandable |

### Badges de status

| Status | Couleur | Icone |
|---|---|---|
| `pending` | gris | `mdi-clock-outline` |
| `running` | cyan (neon) | `mdi-loading` (spin) |
| `success` | vert | `mdi-check-circle` |
| `failed` | rouge | `mdi-alert-circle` |
| `cancelled` | jaune | `mdi-cancel` |

---

## 10. Plan de Taches

### Bloc A — Task Model & Migration
```
Depend de : Phase 6
A1. [x] Refactorer le model Task dans core/models/task.py (nouveaux champs : name, progress, progress_detail, triggered_by, backend_ref, params, started_at, completed_at)
A2. [x] Ajouter le status "cancelled" au model
A3. [x] Supprimer les champs obsoletes (celery_id, payload)
A4. [x] Migration Alembic : alter table tasks (truncate + alter)
A5. [x] Index : idx_tasks_workspace_status, idx_tasks_workspace_created
A6. [x] Schemas Pydantic : TaskCreate, TaskRead, TaskProgress
A7. [x] Adapter POST /tasks/run pour creer la Task unifiee (name, triggered_by, backend_ref, params)
A8. [x] Adapter GET /tasks/{task_id} pour retourner le format unifie
A9. [x] Nouveau endpoint POST /tasks/{task_id}/cancel — revoke Celery, met status "cancelled"
A10. [x] Nouveau endpoint DELETE /tasks/cleanup — supprime tasks success + failed + cancelled
A11. [x] Verifier que le Celery worker recoit toujours les tasks correctement apres refactoring
A12. [x] Test : CRUD Task + cancel + cleanup
```

### Bloc B — Bus Redis Unifie & SSE
```
Depend de : A7
B1. [x] Creer core/services/activity_bus.py — publish_activity(workspace_id, event)
B2. [x] Definir le format d'evenement unifie (type, task_id, source, timestamp, data)
B3. [x] Refactorer les Celery tasks pour publier task_created / task_progress / task_done / task_error sur le bus via activity_bus
B4. [x] Refactorer les app events (Beekeeper) pour publier app_status sur le bus via activity_bus
B5. [x] Mettre a jour routes_tasks.py : publier task_created a la creation d'une task
B6. [x] Mettre a jour routes_tasks.py : publier task_cancelled a l'annulation
B7. [x] Nouveau endpoint SSE GET /w/{workspace_id}/activity/stream — subscribe au canal hive:ws:{id}:activity
B8. [x] Supprimer l'ancien canal hiveflows:tasks:{task_id}:progress et le code qui publie dessus
B9. [x] Fusionner hive:ws:{id}:events dans le bus activity, supprimer l'ancien canal
B10. [x] Supprimer l'endpoint SSE /tasks/{task_id}/progress/stream (routes_dashboard.py:2187)
B11. [x] Supprimer l'endpoint SSE /w/{workspace_id}/events/stream (routes_dashboard.py:2111)
B12. [x] Verifier que les pages apps (logs, metrics) fonctionnent toujours (canaux separees inchanges)
B13. [x] Test : publish activity event → subscribe → recevoir l'event via SSE
```

### Bloc C — Dashboard Activity
```
Depend de : B7
C1. [x] Creer le template core/templates/dashboard/activity/list.html
C2. [x] Table des tasks : colonnes Nom, Type, Status, Source, Progress, Duree, Date
C3. [x] Badges de status avec couleurs et icones MDI (pending=gris, running=cyan, success=vert, failed=rouge, cancelled=jaune)
C4. [x] Connexion SSE au endpoint /w/{id}/activity/stream pour les mises a jour temps reel
C5. [x] Progress bars temps reel (mise a jour via SSE task_progress)
C6. [x] Filtres dropdown : par type, par status, par source (user/agent/system)
C7. [x] Bouton "Annuler" sur les tasks Celery en pending/running → POST /tasks/{id}/cancel
C8. [x] Bouton "Reexecuter" sur les tasks failed → POST /tasks/run avec les memes params
C9. [x] Bouton "Nettoyer" global → DELETE /tasks/cleanup + rafraichir la table
C10. [x] Resultat clickable : lien vers le fichier output (si applicable)
C11. [x] Erreur expandable : cliquer sur une task failed montre le message d'erreur complet
C12. [x] Supprimer l'ancienne page Tasks (core/templates/dashboard/tasks/list.html)
C13. [x] Mettre a jour la route /dashboard/tasks → rediriger vers /dashboard/activity
C14. [x] Mettre a jour la sidebar navigation : "Tasks" → "Activity"
C15. [x] Test : navigation dashboard Activity (200 OK), filtres, boutons
```

### Bloc D — Human Tool Registry & File Manager
```
Depend de : A7, B7, C1
D1. [x] Creer core/services/human_tools.py — classes HumanTool, ToolParam (types: select/autocomplete/text/number/checkbox), registre _registry
D2. [x] Fonctions : register_tool(), get_available_tools(file_ext), execute_tool() — support des champs ui, pinned, group, options_endpoint
D3. [x] Enregistrer le tool translate_file (ui=inline, pinned=True)
D4. [x] Enregistrer les tools Fabric epingles : fabric_extract_wisdom, fabric_summarize, fabric_extract_references (ui=inline, pinned=True, group=fabric)
D5. [x] Enregistrer le tool generique fabric_run (ui=modal, pinned=False, group=fabric, param pattern avec options_endpoint)
D6. [x] Endpoint GET /api/w/{slug}/tools/available?file_ext=.md — retourne les tools compatibles + flags ui/pinned/group
D7. [x] Endpoint GET /api/w/{slug}/tools/fabric/patterns — liste du catalogue Fabric {key, description, pinned}
D8. [x] Endpoint POST /api/w/{slug}/tools/execute — cree une Task triggered_by "user:{id}", dispatch vers le backend
D9. [x] execute_tool() publie task_created sur le bus activity
D10. [x] File manager (file-manager.js) : ajouter un bouton actions "..." sur chaque ligne de fichier
D11. [x] File manager : menu dropdown — tools pinned affiches direct, regroupement par `group` + entree generique pour les tools non-pinned du meme groupe (ex: "Fabric…")
D12. [x] File manager : style du menu — fond sombre, icones MDI neon var(--cyan), sections avec titres (ref: docs/mockups/fabric_ui_preview.svg)
D13. [x] File viewer : ajouter un bouton actions en haut du viewer
D14. [x] File viewer : meme menu dropdown que le file manager
D15. [x] Popover inline (ui=inline) : popup leger ancre sur le fichier avec les params (dropdowns, inputs) + bouton Lancer
D16. [x] Modal dedie (ui=modal) : full modal avec header, champs params, footer actions — reutilisable pour tout tool ui=modal
D17. [x] Composant autocomplete : input + dropdown filtrable, alimente via options_endpoint, navigation clavier ↑↓↵, badge pinned
D18. [x] Modal fabric_run : integre l'autocomplete pattern + select model + select output + preview du system prompt du pattern selectionne
D19. [x] Toast notification au lancement / completion / erreur avec lien vers le fichier output
D20. [x] Les fichiers output sont ecrits dans .hive/outputs/ par defaut (nom par defaut : <source>.{tool|pattern}.md)
D21. [x] Test : GET /tools/available retourne les tools filtres par extension avec flags ui/pinned/group
D22. [x] Test : GET /tools/fabric/patterns retourne la liste du catalogue
D23. [x] Test : POST /tools/execute cree une Task et publie sur le bus (tool pinned et tool via fabric_run)
D24. [x] Test : flow complet inline (translate) et modal (fabric_run avec pattern custom) → task created → progress → done → toast
```

### Bloc E — Tests & Nettoyage
```
Depend de : D19
E1. [x] Tests unitaires : model Task (creation, status transitions, serialisation)
E2. [x] Tests unitaires : activity_bus (publish, format d'evenement)
E3. [x] Tests unitaires : human_tools registry (register, filter, execute)
E4. [x] Tests integration : flow agent → POST /tasks/run → Celery → task_done (non-regression)
E5. [x] Tests integration : flow human tool → execute → task → progress → done
E6. [x] Tests integration : SSE activity stream — connexion, reception d'events
E7. [x] Tests integration : cancel task Celery → task_cancelled event
E8. [x] Tests integration : cleanup tasks → tasks supprimees
E9. [x] Nettoyer le code mort : anciens endpoints SSE supprimes en Bloc B
E10. [x] Nettoyer le code mort : ancienne page Tasks supprimee en Bloc C
E11. [x] Nettoyer les imports et references aux anciens canaux Redis
E12. [x] Verifier que docker compose up demarre proprement (200 OK /health)
E13. [x] Mettre a jour README.md (nouveaux endpoints, architecture)
E14. [x] Verifier le dashboard complet sur le serveur distant (Activity, File Manager, Apps)
```

---

## 11. Dependances Python (Ajouts Phase 7)

### core/requirements.txt — ajouts

```
# Aucun nouveau package prevu — tout est deja dans la stack :
# - Redis (aioredis / redis-py) pour le bus
# - FastAPI StreamingResponse pour le SSE
# - SQLAlchemy pour le model Task
# - Celery pour les workers
```

Si un besoin emerge en cours de route (ex: un meilleur helper SSE), on ajoutera au fur et a mesure.

---

## 12. Docker Compose (Changements)

Aucune modification prevue au `docker-compose.yml` pour Phase 7.

Les changements sont exclusivement dans le code Python (model, routes, services) et les templates/static (dashboard, file manager). Les volumes existants suffisent :
- `./core:/app` — code Python
- `./core/static:/app/static` — JS, CSS
- `./core/templates:/app/templates` — templates Jinja2

---

## 13. Angles Morts — Points d'Attention

| # | Risque | Impact | Mitigation |
|---|--------|--------|------------|
| 1 | Migration table `tasks` casse les flows agents existants | Agents ne peuvent plus creer de tasks | Bloc A adapte `POST /tasks/run` — l'API externe ne change pas pour les agents |
| 2 | Suppression SSE endpoints casse le dashboard | Pages apps cassees | Bloc B verifie que les SSE apps (logs, metrics) restent intacts |
| 3 | Bus Redis : beaucoup d'events sur un seul canal | Performance Redis | Events legers (~200 bytes JSON), pas de persistence Redis, filtrage SSE cote serveur si necessaire |
| 4 | Human Tools : execution sans agent = sans garde-fou | Abus possible | Permissions workspace (viewer=lecture seule), whitelist de tools dans le registre |
| 5 | Tools sync bloquants dans le thread request | Timeout FastAPI | Les tools lourds passent par Celery, seuls les tools rapides (<2s) en sync |
| 6 | File manager JS : ajout de logique complexe | Maintenabilite | Garder le code JS minimal, logique cote serveur |
| 7 | Toast notifications : gestion du multi-task | UX confuse si 5 toasts en meme temps | Stack de toasts avec auto-dismiss, max 3 visibles |

---

## 14. Definition de "Phase 7 terminee"

La Phase 7 est complete quand on peut faire ceci **de bout en bout** :

```bash
# 1. Demarrer tous les services
docker compose up

# 2. Un agent cree une task (comme avant — non-regression)
curl -X POST http://localhost:8002/api/w/demo/tasks/run \
  -H "X-API-Key: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "python",
    "name": "Test script agent",
    "params": {"script": "print(42)"},
    "triggered_by": "agent:assistant-principal"
  }'
# → Task creee, Celery execute, status "success"

# 3. Ouvrir le dashboard Activity
# http://localhost:8002/dashboard/activity
# → La task apparait avec status, progress, resultat
# → Les filtres fonctionnent (type, status, source)

# 4. Un humain utilise un Human Tool depuis le file manager
# → Naviguer vers le file manager
# → Cliquer "..." sur un fichier .md
# → Menu dropdown avec "Traduire" (icone MDI neon)
# → Cliquer → modal avec params (langue cible)
# → Valider → toast "en cours..." → progress dans Activity
# → Fichier traduit dans .hive/outputs/ → toast "termine"

# 5. Annuler une task en cours
# → Lancer une task longue
# → Dashboard Activity → bouton "Annuler"
# → Status passe a "cancelled"

# 6. Nettoyer les tasks
# → Bouton "Nettoyer" dans Activity
# → Toutes les tasks success/failed/cancelled disparaissent

# 7. SSE temps reel
# → Ouvrir Activity dans un onglet
# → Lancer une task depuis un autre onglet
# → La task apparait en temps reel dans le premier onglet
# → La progress bar se met a jour live
```

---

*Phase 7 — valide le 13 avril 2026*
*Pret pour implementation — commencer par Bloc A.*
