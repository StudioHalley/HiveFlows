# HiveFlows — Phase 6 : Beekeeper — Docker Control Plane

> Objectif : creer le **Beekeeper**, un service autonome qui controle Docker, isole les workspaces par reseau, gere le cycle de vie des apps, et publie les evenements sur Redis. Le Beekeeper est le seul composant qui touche au socket Docker.

---

## 1. Perimetre

### Dans le scope
- Service Beekeeper : API REST interne, acces exclusif au socket Docker
- Isolation reseau : un subnet Docker par workspace
- App manifest : format YAML standardise pour decrire une app
- Cycle de vie des apps : install, start, stop, restart, uninstall
- Variables d'environnement par app/instance
- Montage filesystem : `.hive/` du workspace monte dans chaque app
- Publication d'evenements sur Redis (status, logs, metriques)
- Ecoute de `docker events` en temps reel
- API Core (proxy) : routes qui relaient vers le Beekeeper
- CLI : `workflow app start|stop|list|logs|install|env`
- Dashboard : gestion des apps par workspace (status, logs, env, restart)
- Ingress : routing des requetes vers les apps (Nginx ou path-based)

### Hors scope (Phase 7)
- Bees (sub-agents orchestrant les apps/skills)
- Skills comme containers Docker (evolution du skill system)
- Chaining de skills / progress streaming
- App Store browse UI (la Phase 6 gere l'installation, pas le catalogue visuel)

### Hors scope (futur)
- Multi-serveur (plusieurs Beekeeper sur differents hosts)
- Kubernetes migration
- GPU scheduling

---

## 2. Architecture

```
                    Utilisateur
                    │
         ┌──────────┼───────────┐
         │ CLI      │ Dashboard │
         └──────────┼───────────┘
                    │
         ┌──────────▼───────────┐
         │       CORE           │
         │  FastAPI             │
         │  Auth + Sessions     │
         │  Workspace ownership │
         │                      │
         │  POST /apps/start ──────REST──┐
         │  GET  /apps/list  ──────REST──┤
         │  GET  /apps/logs  ──────REST──┤
         │                      │        │
         │  SSE ◄── Redis sub   │        │
         └──────────────────────┘        │
                                         │
         ┌───────────────────────────────▼──┐
         │           BEEKEEPER              │
         │                                  │
         │  FastAPI (interne, port 8010)    │
         │  Docker SDK (docker.sock)        │
         │  Redis publisher                 │
         │  docker events listener          │
         │                                  │
         │  Responsabilites :               │
         │  - Creer/detruire des networks   │
         │  - Pull/start/stop des containers│
         │  - Monter les volumes .hive/     │
         │  - Publier events sur Redis      │
         │  - Streamer les logs             │
         └──────────────┬───────────────────┘
                        │
                  docker.sock
                        │
         ┌──────────────▼───────────────────┐
         │         DOCKER DAEMON            │
         │                                  │
         │  ┌─────────────────────────┐     │
         │  │ net: hive-ws-abc123     │     │
         │  │ subnet: 172.20.1.0/24  │     │
         │  │                         │     │
         │  │  ┌─────────┐ ┌───────┐ │     │
         │  │  │ vscode  │ │node-  │ │     │
         │  │  │ :8080   │ │red    │ │     │
         │  │  │         │ │:1880  │ │     │
         │  │  └─────────┘ └───────┘ │     │
         │  └─────────────────────────┘     │
         │                                  │
         │  ┌─────────────────────────┐     │
         │  │ net: hive-ws-xyz789     │     │
         │  │ subnet: 172.20.2.0/24  │     │
         │  │                         │     │
         │  │  ┌─────────┐           │     │
         │  │  │ jupyter  │           │     │
         │  │  │ :8888    │           │     │
         │  │  └─────────┘           │     │
         │  └─────────────────────────┘     │
         └──────────────────────────────────┘
```

---

## 3. Decisions Techniques

| Decision | Choix | Justification |
|----------|-------|---------------|
| Langage Beekeeper | Python + FastAPI | Coherent avec le reste de la stack, acces au Docker SDK Python |
| Docker SDK | `docker` (PyPI) — SDK officiel Python | Mature, maintenu, API complete |
| Communication Core→Beekeeper | REST HTTP interne (port 8010) | Simple, debuggable, pas d'overhead inutile |
| Auth interne | API key partagee (`BEEKEEPER_API_KEY` en env) | Le Core et le Beekeeper partagent un secret. Simple, securise sur reseau Docker interne |
| Evenements | Redis Pub/Sub | Push, pas poll. Le Core subscribe, le dashboard recoit via SSE |
| Logs | `docker logs` stream + Redis publish | Les logs sont ephemeres (Redis), l'historique est dans Docker |
| Reseau workspace | Un Docker network par workspace, subnet `/24` | Isolation native, DNS Docker entre les apps |
| Naming convention | `hive-ws-{workspace_id}` (network), `hive-app-{workspace_id}-{app_name}` (container) | Predictable, grepable, pas de collision |
| Subnet allocation | `172.20.{n}.0/24`, n incremente par workspace | Simple, supporte 250+ workspaces |
| Volume mount | `{WORKSPACE_DATA_PATH}/{workspace_id}/.hive/` | Chaque app voit le .hive/ de son workspace |
| Ingress | Nginx reverse proxy (un seul, gere par Beekeeper) | Route vers les apps par path : `/app/{workspace_id}/{app_name}/` |
| Port interne apps | Chaque app utilise son port naturel dans son network | Pas de remapping — vscode:8080, node-red:1880, etc. |
| Instanciation | Une app peut etre instanciee N fois (une par workspace) | Chaque instance = un container distinct dans un network distinct |
| Persistance config | PostgreSQL (table app_instances) | L'etat desire est en DB, le Beekeeper reconcilie |

---

## 4. Schemas de Donnees

### Nouvelle table `app_catalog`

Le catalogue des apps disponibles. Chaque entree correspond a un manifest YAML.

```sql
CREATE TABLE app_catalog (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,           -- "VS Code Server"
    slug            VARCHAR(100) UNIQUE NOT NULL,    -- "vscode-server"
    version         VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    description     TEXT,
    image           VARCHAR(500) NOT NULL,           -- "ghcr.io/coder/code-server:latest"
    app_type        VARCHAR(20) NOT NULL,            -- 'persistent', 'backend'
    ingress_mode    VARCHAR(20) DEFAULT 'iframe',    -- 'iframe', 'link', 'none'
    default_port    INTEGER,                         -- 8080
    env_schema      JSONB DEFAULT '[]',              -- [{key: "PASSWORD", required: true, secret: true}]
    default_mounts  JSONB DEFAULT '[]',              -- [{host: ".hive/code", container: "/workspace"}]
    resources       JSONB DEFAULT '{}',              -- {memory: "512m", cpus: "1.0"}
    icon            VARCHAR(100),                    -- MDI icon name ou URL
    readme_md       TEXT,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
```

### Nouvelle table `app_instances`

Une instance = une app installee et/ou running dans un workspace.

```sql
CREATE TABLE app_instances (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE NOT NULL,
    app_id          UUID REFERENCES app_catalog(id) NOT NULL,
    container_name  VARCHAR(200) NOT NULL,           -- "hive-app-abc123-vscode"
    status          VARCHAR(20) DEFAULT 'stopped',   -- 'pulling', 'starting', 'running', 'stopped', 'crashed', 'error'
    env_values      TEXT,                            -- JSON chiffre (Fernet) des variables configurees
    custom_mounts   JSONB DEFAULT '[]',              -- mounts additionnels
    container_ip    VARCHAR(50),                     -- IP dans le subnet du workspace
    installed_by    UUID REFERENCES users(id) NOT NULL,
    started_at      TIMESTAMP,
    stopped_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(workspace_id, app_id)                     -- une seule instance par app par workspace
);
```

### Nouvelle table `workspace_networks`

Tracking des networks Docker par workspace.

```sql
CREATE TABLE workspace_networks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE NOT NULL UNIQUE,
    network_name    VARCHAR(200) NOT NULL,           -- "hive-ws-abc123"
    subnet          VARCHAR(50) NOT NULL,            -- "172.20.1.0/24"
    gateway         VARCHAR(50) NOT NULL,            -- "172.20.1.1"
    docker_network_id VARCHAR(100),                  -- ID Docker du network
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### Migrations Alembic
- `016_app_catalog.py` — table app_catalog
- `017_workspace_networks.py` — table workspace_networks
- `018_app_instances.py` — table app_instances

---

## 5. App Manifest (YAML)

Chaque app est definie par un fichier manifest dans le catalogue :

```
apps/
  vscode-server/
    manifest.yaml
    icon.svg          # optionnel
    README.md         # optionnel
  node-red/
    manifest.yaml
    README.md
  jupyter/
    manifest.yaml
```

### Format manifest.yaml

```yaml
name: VS Code Server
slug: vscode-server
version: "1.0"
description: "Editeur de code complet dans le navigateur"
image: ghcr.io/coder/code-server:latest
type: persistent           # persistent | backend
ingress: iframe            # iframe | link | none

port: 8080                 # port interne du container
health_check: /healthz     # endpoint de health (optionnel)

env:
  - key: PASSWORD
    description: "Mot de passe d'acces"
    required: true
    secret: true
    default: ""
  - key: EXTENSIONS_GALLERY
    description: "URL du marketplace d'extensions"
    required: false
    default: ""

mounts:
  - host: ".hive/code"
    container: "/home/coder/workspace"
    description: "Repertoire de code du workspace"

resources:
  memory: 512m
  cpus: "1.0"

icon: mdi-microsoft-visual-studio-code
```

### Exemples de manifests

**Node-RED :**
```yaml
name: Node-RED
slug: node-red
version: "1.0"
description: "Plateforme de flow-based programming"
image: nodered/node-red:latest
type: persistent
ingress: iframe
port: 1880
env:
  - key: NODE_RED_CREDENTIAL_SECRET
    required: true
    secret: true
mounts:
  - host: ".hive/nodered"
    container: "/data"
resources:
  memory: 256m
icon: mdi-sitemap
```

**Ollama (backend) :**
```yaml
name: Ollama
slug: ollama
version: "1.0"
description: "LLM local — inference privee"
image: ollama/ollama:latest
type: backend
ingress: none
port: 11434
health_check: /api/tags
mounts:
  - host: ".hive/ollama/models"
    container: "/root/.ollama"
resources:
  memory: 4g
  cpus: "4.0"
icon: mdi-brain
```

**Jupyter :**
```yaml
name: Jupyter Lab
slug: jupyter
version: "1.0"
description: "Notebooks Python interactifs"
image: jupyter/minimal-notebook:latest
type: persistent
ingress: iframe
port: 8888
env:
  - key: JUPYTER_TOKEN
    required: true
    secret: true
mounts:
  - host: ".hive/notebooks"
    container: "/home/jovyan/work"
resources:
  memory: 1g
icon: mdi-notebook
```

---

## 6. API Beekeeper (interne — port 8010)

Le Beekeeper expose une API REST accessible uniquement depuis le reseau Docker interne. Authentifiee par `BEEKEEPER_API_KEY` dans le header `X-Beekeeper-Key`.

### Networks

#### `POST /networks`
Creer le network Docker pour un workspace.
```json
// Request
{"workspace_id": "abc123"}

// Response
{"network_name": "hive-ws-abc123", "subnet": "172.20.3.0/24",
 "gateway": "172.20.3.1", "docker_network_id": "sha256:..."}
```

#### `DELETE /networks/{workspace_id}`
Supprimer le network d'un workspace (et tous ses containers).

#### `GET /networks`
Lister tous les networks workspace actifs.

### Apps

#### `POST /apps/pull`
Pull l'image Docker d'une app (async, publie progress sur Redis).
```json
{"image": "ghcr.io/coder/code-server:latest"}
```

#### `POST /apps/start`
Demarrer une instance d'app dans un workspace.
```json
// Request
{
  "workspace_id": "abc123",
  "app_slug": "vscode-server",
  "image": "ghcr.io/coder/code-server:latest",
  "container_name": "hive-app-abc123-vscode",
  "port": 8080,
  "env": {"PASSWORD": "secret123"},
  "mounts": [
    {"host": "/data/workspaces/abc123/.hive/code", "container": "/home/coder/workspace"}
  ],
  "resources": {"memory": "512m", "cpus": "1.0"},
  "network": "hive-ws-abc123"
}

// Response
{"container_id": "sha256:...", "container_ip": "172.20.3.2", "status": "running"}
```

#### `POST /apps/stop`
Arreter un container.
```json
{"container_name": "hive-app-abc123-vscode"}
```

#### `POST /apps/restart`
Restart un container.
```json
{"container_name": "hive-app-abc123-vscode"}
```

#### `DELETE /apps/{container_name}`
Supprimer un container (stop + rm).

#### `GET /apps/status`
Statut de tous les containers d'un workspace.
```json
// Query: ?workspace_id=abc123
[
  {"container_name": "hive-app-abc123-vscode", "status": "running",
   "ip": "172.20.3.2", "port": 8080, "uptime": "2h 34m",
   "cpu_percent": 2.3, "memory_mb": 145},
  {"container_name": "hive-app-abc123-nodered", "status": "stopped"}
]
```

#### `GET /apps/logs`
Streamer les logs d'un container.
```json
// Query: ?container_name=hive-app-abc123-vscode&lines=100&follow=true
// Response: text/event-stream (SSE) ou text/plain
```

#### `PUT /apps/env`
Modifier une variable d'environnement (necessite restart).
```json
{"container_name": "hive-app-abc123-vscode", "key": "PASSWORD", "value": "new-pass"}
```

### Health

#### `GET /health`
Health du Beekeeper lui-meme.
```json
{"status": "healthy", "docker_version": "24.0.7", "networks_count": 5,
 "containers_count": 12, "uptime": "48h"}
```

---

## 7. API Core (proxy vers Beekeeper)

Le Core expose des routes "user-facing" qui valident l'auth, verifient les permissions workspace, puis relaient au Beekeeper.

### Routes Core

```
GET    /w/{workspace_id}/apps              → liste les apps installees + status
POST   /w/{workspace_id}/apps/install      → installe une app depuis le catalogue
POST   /w/{workspace_id}/apps/{slug}/start → demarre l'app
POST   /w/{workspace_id}/apps/{slug}/stop  → arrete l'app
POST   /w/{workspace_id}/apps/{slug}/restart
GET    /w/{workspace_id}/apps/{slug}/logs  → stream les logs (SSE)
PUT    /w/{workspace_id}/apps/{slug}/env   → modifie les variables
DELETE /w/{workspace_id}/apps/{slug}       → desinstalle

GET    /apps/catalog                       → liste les apps disponibles dans le catalogue
GET    /apps/catalog/{slug}                → detail d'une app (manifest + readme)
```

### Flux d'installation complet

```
1. User clique "Installer VS Code" dans le dashboard
2. Core recoit POST /w/{workspace_id}/apps/install {slug: "vscode-server"}
3. Core verifie : user est membre du workspace ? workspace existe ?
4. Core lit le manifest depuis app_catalog en DB
5. Core verifie : le workspace a-t-il un network ? sinon, cree-le via Beekeeper
6. Core cree l'entree app_instances en DB (status: "pulling")
7. Core appelle Beekeeper POST /apps/pull {image: "ghcr.io/coder/code-server:latest"}
8. Beekeeper pull l'image, publie progress sur Redis
   → redis: hive:app:abc123:vscode:pull_progress → "23%"... "67%"... "100%"
   → Dashboard affiche la barre de progression via SSE
9. Core appelle Beekeeper POST /apps/start {...}
10. Beekeeper cree le container, le connecte au network workspace
11. Beekeeper publie sur Redis : hive:app:abc123:vscode:status → "running"
12. Core met a jour app_instances (status: "running", container_ip, started_at)
13. Dashboard affiche l'app avec un bouton "Ouvrir" (iframe ou lien)
```

---

## 8. Reseau Docker — Isolation Workspace

### Creation d'un network workspace

```python
# beekeeper/network_manager.py

import docker

client = docker.from_env()

def create_workspace_network(workspace_id: str, subnet_index: int) -> dict:
    """Cree un network Docker isole pour un workspace."""
    network_name = f"hive-ws-{workspace_id}"
    subnet = f"172.20.{subnet_index}.0/24"
    gateway = f"172.20.{subnet_index}.1"

    ipam_pool = docker.types.IPAMPool(subnet=subnet, gateway=gateway)
    ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool])

    network = client.networks.create(
        name=network_name,
        driver="bridge",
        ipam=ipam_config,
        labels={
            "hive.workspace_id": workspace_id,
            "hive.managed": "true",
        },
    )

    return {
        "network_name": network_name,
        "subnet": subnet,
        "gateway": gateway,
        "docker_network_id": network.id,
    }
```

### Pourquoi un subnet par workspace

- Les apps du meme workspace se voient par nom DNS Docker (ex: `vscode`, `node-red`)
- Les apps de workspaces differents sont **invisibles** les unes aux autres
- Chaque app peut utiliser son port naturel sans collision (8080, 1880, etc.)
- Le Beekeeper peut connecter ses propres containers au network workspace quand il faut interagir

### Le network `hiveflows` (existant) vs les networks workspace

```
hiveflows (network existant)     → core, redis, postgres, beekeeper, workers
hive-ws-abc123 (nouveau)         → apps du workspace abc123
hive-ws-xyz789 (nouveau)         → apps du workspace xyz789
```

Le Beekeeper est connecte a `hiveflows` ET peut se connecter aux networks workspace au besoin (pour health checks, log streaming, etc.).

---

## 9. Redis — Canaux d'Evenements

Le Beekeeper publie sur Redis. Le Core subscribe et forward au dashboard via SSE.

### Canaux

```
# Status d'une app (change d'etat)
PUBLISH hive:app:{workspace_id}:{app_slug}:status
  → {"status": "running", "container_ip": "172.20.3.2", "timestamp": "..."}
  → {"status": "crashed", "exit_code": 137, "timestamp": "..."}
  → {"status": "stopped", "timestamp": "..."}

# Progress d'un pull d'image
PUBLISH hive:app:{workspace_id}:{app_slug}:pull
  → {"progress": 45, "layer": "sha256:abc...", "status": "downloading"}
  → {"progress": 100, "status": "complete"}

# Logs en temps reel
PUBLISH hive:app:{workspace_id}:{app_slug}:logs
  → {"line": "Server started on port 8080", "stream": "stdout", "timestamp": "..."}
  → {"line": "Error: connection refused", "stream": "stderr", "timestamp": "..."}

# Metriques periodiques (toutes les 30s)
PUBLISH hive:app:{workspace_id}:{app_slug}:metrics
  → {"cpu_percent": 2.3, "memory_mb": 145, "memory_limit_mb": 512, "timestamp": "..."}

# Events workspace-level (aggreges)
PUBLISH hive:ws:{workspace_id}:events
  → {"event": "app_started", "app": "vscode-server", "timestamp": "..."}
  → {"event": "app_crashed", "app": "node-red", "exit_code": 1, "timestamp": "..."}
  → {"event": "network_created", "subnet": "172.20.3.0/24", "timestamp": "..."}
```

### Docker Events Listener

Le Beekeeper ecoute `docker events` en continu et traduit en events Redis :

```python
# beekeeper/event_listener.py

import docker
import redis.asyncio as aioredis

async def listen_docker_events(docker_client, redis_client):
    """Ecoute les events Docker et les republish sur Redis."""
    for event in docker_client.events(decode=True):
        # Filtrer : seulement les containers geres par HiveFlows
        labels = event.get("Actor", {}).get("Attributes", {})
        if labels.get("hive.managed") != "true":
            continue

        workspace_id = labels.get("hive.workspace_id")
        app_slug = labels.get("hive.app_slug")
        action = event.get("Action")  # start, stop, die, kill, etc.

        if action == "die":
            exit_code = labels.get("exitCode", "unknown")
            status = "crashed" if exit_code != "0" else "stopped"
            await redis_client.publish(
                f"hive:app:{workspace_id}:{app_slug}:status",
                json.dumps({"status": status, "exit_code": exit_code})
            )
            await redis_client.publish(
                f"hive:ws:{workspace_id}:events",
                json.dumps({"event": f"app_{status}", "app": app_slug})
            )
```

---

## 10. Ingress & Routing

### Strategie : Nginx reverse proxy gere par le Beekeeper

Le Beekeeper genere et recharge la config Nginx quand une app demarre ou s'arrete.

### Routing par path

```
https://hiveflows.example.com/app/{workspace_id}/{app_slug}/
  → proxy_pass http://{container_ip}:{port}/
```

### Config Nginx generee

```nginx
# /etc/nginx/conf.d/hive-apps.conf
# Genere automatiquement par Beekeeper — NE PAS EDITER

# Workspace abc123 — VS Code Server
location /app/abc123/vscode-server/ {
    proxy_pass http://172.20.3.2:8080/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # WebSocket support (requis pour VS Code, Node-RED, Jupyter)
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}

# Workspace abc123 — Node-RED
location /app/abc123/node-red/ {
    proxy_pass http://172.20.3.3:1880/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

### Lifecycle Nginx

```
1. App demarre → Beekeeper recoit l'IP du container
2. Beekeeper genere/met a jour /etc/nginx/conf.d/hive-apps.conf
3. Beekeeper envoie `nginx -s reload` (zero downtime)
4. App s'arrete → Beekeeper retire le bloc location, reload
```

### Alternative : Traefik avec labels Docker

Si Nginx devient trop rigide, on pourrait migrer vers Traefik qui detecte automatiquement les containers via les labels Docker :

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.vscode-abc123.rule=PathPrefix(`/app/abc123/vscode-server`)"
  - "traefik.http.services.vscode-abc123.loadbalancer.server.port=8080"
```

Decision : commencer avec Nginx (plus de controle), migrer si le besoin se fait sentir.

---

## 11. Securite

### Principe : defense en profondeur

```
Layer 1 — Auth Core     : l'utilisateur est authentifie, est membre du workspace
Layer 2 — API interne    : le Core envoie le workspace_id verifie au Beekeeper
Layer 3 — Beekeeper key  : BEEKEEPER_API_KEY empeche les appels directs non autorises
Layer 4 — Network Docker : les apps sont isolees par network, invisibles entre workspaces
Layer 5 — Labels Docker  : chaque container porte des labels hive.workspace_id, hive.managed
```

### Ce que le Beekeeper refuse

- Demarrer un container sans workspace_id valide
- Monter un volume hors du repertoire `.hive/` du workspace concerne
- Connecter un container a un network qui ne correspond pas a son workspace
- Exposer un port sur l'hote (tout passe par l'ingress Nginx)
- Executer une image qui n'est pas dans le catalogue (`app_catalog`)

### Variables d'environnement sensibles

Les env values des apps (mots de passe, tokens) sont chiffrees en DB (meme Fernet que les provider credentials de Phase 5). Le Beekeeper dechiffre au moment du `docker run` et les injecte — elles ne sont jamais en clair dans la DB.

---

## 12. CLI

Toutes les commandes passent par le Core (qui relay au Beekeeper). L'auth est geree par la session CLI existante.

```bash
# Lister les apps disponibles dans le catalogue
workflow app catalog

# Lister les apps installees dans le workspace actif
workflow app list

# Installer une app
workflow app install vscode-server

# Demarrer / arreter / restart
workflow app start vscode-server
workflow app stop vscode-server
workflow app restart vscode-server

# Voir les logs (follow mode)
workflow app logs vscode-server
workflow app logs vscode-server --lines 50 --follow

# Configurer une variable d'environnement
workflow app env vscode-server PASSWORD=secret123
workflow app env vscode-server --list

# Desinstaller
workflow app uninstall vscode-server

# Statut detaille
workflow app status vscode-server
# → Status: running
# → IP: 172.20.3.2
# → Port: 8080
# → URL: /app/abc123/vscode-server/
# → Uptime: 2h 34m
# → CPU: 2.3%  Memory: 145/512 MB
```

---

## 13. Dashboard UI

### Page `/dashboard/w/{workspace}/apps`

**Vue grille des apps installees :**
- Carte par app : icone (MDI), nom, status (pastille verte/rouge/jaune)
- Boutons : Ouvrir (iframe/lien), Restart, Stop, Logs, Config
- Bouton "+ Ajouter une app" → modal avec le catalogue

**Detail d'une app (expandable ou page) :**
- Status en temps reel (via SSE ← Redis)
- Metriques : CPU, memoire (barres de progression)
- Logs live (scrollable, auto-scroll, couleurs stderr/stdout)
- Variables d'environnement (editable, masquees pour les secrets)
- Bouton Desinstaller (confirmation modale)

### Page `/dashboard/w/{workspace}/apps/catalog`

**Liste des apps disponibles :**
- Grille d'apps avec icone, nom, description courte
- Badge "installe" si deja present dans le workspace
- Bouton "Installer" → formulaire des variables requises → lancement

### Iframe integration

Pour les apps `ingress: iframe` :
```html
<div class="app-iframe-container">
    <iframe
        src="/app/{{ workspace_id }}/{{ app_slug }}/"
        class="app-iframe"
        allow="clipboard-read; clipboard-write"
    ></iframe>
</div>
```

Le dashboard a un "app switcher" — onglets ou sidebar — pour naviguer entre les apps ouvertes.

---

## 14. Docker Compose

### Nouveau service `beekeeper`

```yaml
# docker-compose.yml

  beekeeper:
    build:
      context: ./beekeeper
      dockerfile: Dockerfile
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock    # SEUL service avec acces Docker
      - ./apps:/apps:ro                               # Catalogue des manifests
      - workspace_data:/data/workspaces               # Pour monter les .hive/ dans les apps
    environment:
      - BEEKEEPER_API_KEY=${BEEKEEPER_API_KEY}
      - REDIS_URL=redis://redis:6379/0
      - DATABASE_URL=${DATABASE_URL}
      - CREDENTIAL_ENCRYPTION_KEY=${CREDENTIAL_ENCRYPTION_KEY}
      - WORKSPACE_DATA_PATH=/data/workspaces
    depends_on:
      - redis
      - postgres
    networks:
      - hiveflows
    # Pas de port expose ! Accessible uniquement via le reseau Docker interne.
    # Le Core appelle http://beekeeper:8010 en interne.
```

### Nouveau service `nginx` (ingress)

```yaml
  nginx:
    image: nginx:alpine
    ports:
      - "8080:80"                                     # Point d'entree pour les apps
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - nginx_conf:/etc/nginx/conf.d                  # Genere par le Beekeeper
    depends_on:
      - beekeeper
      - core
    networks:
      - hiveflows
    # Le Beekeeper genere la config dans nginx_conf et reload nginx
```

### Structure du repertoire `beekeeper/`

```
beekeeper/
  Dockerfile
  requirements.txt
  main.py                    # FastAPI app, startup events
  config.py                  # Settings (env vars)
  api/
    routes_apps.py           # POST /apps/start, stop, restart, logs, status
    routes_networks.py       # POST /networks, DELETE /networks/{id}
    routes_health.py         # GET /health
  services/
    container_manager.py     # Docker SDK : create, start, stop, rm, logs
    network_manager.py       # Docker SDK : create/delete networks
    event_listener.py        # docker events → Redis pub/sub
    ingress_manager.py       # Generer config Nginx, reload
    manifest_loader.py       # Lire les manifests YAML depuis /apps/
  models.py                  # Pydantic schemas (request/response)
```

### Structure du repertoire `apps/`

```
apps/
  vscode-server/
    manifest.yaml
    README.md
  node-red/
    manifest.yaml
    README.md
  jupyter/
    manifest.yaml
    README.md
  ollama/
    manifest.yaml
    README.md
```

### Volumes

```yaml
volumes:
  workspace_data:       # /data/workspaces/ — partage entre core, beekeeper, et les apps
  nginx_conf:           # /etc/nginx/conf.d/ — genere par beekeeper, lu par nginx
```

### .env (ajouts)

```env
# Beekeeper
BEEKEEPER_API_KEY=change-me-random-secret
WORKSPACE_DATA_PATH=/data/workspaces

# Nginx ingress
NGINX_PORT=8080
```

---

## 15. Plan de Taches

### Bloc A — Beekeeper Service (squelette)
```
Depend de : Phase 5 (credential vault pour chiffrer les env values)
A1. [x] Creer le repertoire beekeeper/ avec la structure
A2. [x] beekeeper/Dockerfile (Python 3.11, FastAPI, docker SDK)
A3. [x] beekeeper/requirements.txt (fastapi, uvicorn, docker, redis, cryptography)
A4. [x] beekeeper/config.py (Settings : BEEKEEPER_API_KEY, REDIS_URL, etc.)
A5. [x] beekeeper/main.py (FastAPI app, lifespan, auth middleware X-Beekeeper-Key)
A6. [x] Route GET /health (docker version, uptime)
A7. [x] Ajouter beekeeper au docker-compose.yml
A8. [ ] Test : docker compose up, beekeeper repond sur /health
```

### Bloc B — Network Manager
```
Depend de : A8
B1. [x] beekeeper/services/network_manager.py : create_workspace_network(), delete_workspace_network()
B2. [x] Route POST /networks, DELETE /networks/{workspace_id}, GET /networks
B3. [x] Migration Alembic 016_workspace_networks (renumerote — 017 sera app_catalog)
B4. [x] Allocation automatique de subnet (10.20.{n}.0/24 — evite conflits 172.x existants)
B5. [x] Labels Docker sur les networks (hive.workspace_id, hive.managed)
B6. [x] Test : creer un network, verifier l'isolation, supprimer
```

### Bloc C — Container Manager (coeur)
```
Depend de : B6
C1. [x] beekeeper/services/container_manager.py : start_app(), stop_app(), restart_app(), remove_app()
C2. [x] Route POST /apps/start — creer et demarrer un container
C3. [x] Route POST /apps/stop — arreter un container
C4. [x] Route POST /apps/restart
C5. [x] Route DELETE /apps/{container_name}
C6. [x] Route GET /apps/status — lister les containers d'un workspace
C7. [x] Route POST /apps/pull — pull une image (async, background thread)
C8. [x] Labels Docker sur les containers (hive.workspace_id, hive.app_slug, hive.managed)
C9. [x] Montage des volumes .hive/ avec validation (pas de path traversal)
C10. [x] Injection des env vars (plaintext dict ou dechiffrement Fernet si encrypted_env fourni)
C11. [x] Resources limits (memory, cpus) via Docker SDK
C12. [x] Test : start vscode-server dans un network workspace, verifier que le container tourne
```

### Bloc D — Events & Redis
```
Depend de : C12
D1. [x] beekeeper/services/event_listener.py : listen_docker_events()
D2. [x] Background task au startup : ecouter docker events en continu
D3. [x] Publier les changements de status sur Redis (start, stop, die, kill)
D4. [x] Publier le progress du pull d'image sur Redis
D5. [x] Route GET /apps/logs — streamer les logs via SSE
D6. [x] Publier les logs sur Redis channel (optionnel, pour le dashboard)
D7. [x] Metriques periodiques : task background qui publie CPU/memory toutes les 30s
D8. [ ] Test : demarrer une app, verifier les events Redis, crasher une app, verifier le "die" event : redis-cli SUBSCRIBE hive:app:*
```

### Bloc E — Ingress (Nginx)
```
Depend de : C12
E1. [x] beekeeper/services/ingress_manager.py : generate_nginx_config(), reload_nginx()
E2. [x] Template Nginx avec WebSocket support
E3. [x] Ajouter nginx au docker-compose.yml
E4. [x] nginx/nginx.conf de base (upstream core, includes conf.d/)
E5. [x] Au start/stop d'une app : regenerer la config, nginx -s reload
E6. [x] Volume partage nginx_conf entre beekeeper et nginx
E7. [ ] Test : demarrer vscode, acceder via /app/{workspace_id}/vscode-server/
```

### Bloc F — App Catalog & DB
```
Depend de : A8
F1. [x] Migration Alembic 017_app_catalog
F2. [x] Migration Alembic 018_app_instances
F3. [x] beekeeper/services/manifest_loader.py : lire les manifests YAML, sync vers DB
F4. [x] Sync au startup : scanner /apps/, inserer/update dans app_catalog
F5. [x] Modeles SQLAlchemy : AppCatalog, AppInstance (dans core/ — partages)
F6. [x] Creer 3 manifests de base : vscode-server, node-red, jupyter
F7. [ ] Test : au demarrage, le catalogue contient les 3 apps
```

### Bloc G — API Core (proxy)
```
Depend de : C12, F7
G1. [x] core/api/routes_apps.py : routes proxy vers le Beekeeper
G2. [x] GET /w/{workspace_id}/apps — liste apps installees + status
G3. [x] POST /w/{workspace_id}/apps/install — install workflow complet
G4. [x] POST /w/{workspace_id}/apps/{slug}/start
G5. [x] POST /w/{workspace_id}/apps/{slug}/stop
G6. [x] POST /w/{workspace_id}/apps/{slug}/restart
G7. [x] GET /w/{workspace_id}/apps/{slug}/logs — proxy SSE
G8. [x] PUT /w/{workspace_id}/apps/{slug}/env
G9. [x] DELETE /w/{workspace_id}/apps/{slug} — desinstall
G10. [x] GET /apps/catalog — liste le catalogue
G11. [x] Verification des permissions workspace sur chaque route
G12. [x] Client HTTP interne (httpx) vers beekeeper:8010
G13. [x] SSE endpoint qui subscribe aux Redis events du workspace
G14. [ ] Test : installer une app via Core API, verifier le flux complet
```

### Bloc H — CLI
```
Depend de : G14
H1. [x] Commandes CLI : workflow app catalog
H2. [x] Commandes CLI : workflow app list
H3. [x] Commandes CLI : workflow app install {slug}
H4. [x] Commandes CLI : workflow app start/stop/restart {slug}
H5. [x] Commandes CLI : workflow app logs {slug} [--follow] [--lines N]
H6. [x] Commandes CLI : workflow app env {slug} [KEY=VALUE | --list]
H7. [x] Commandes CLI : workflow app status {slug}
H8. [x] Commandes CLI : workflow app uninstall {slug}
H9. [ ] Test : installer et demarrer une app via CLI
```

### Bloc I — Dashboard
```
Depend de : G14
I1. [x] Template : /dashboard/w/{ws}/apps — grille des apps installees
I2. [x] Template : /dashboard/w/{ws}/apps/catalog — catalogue des apps disponibles
I3. [x] Formulaire d'installation (env vars requises)
I4. [x] Pastilles de status en temps reel (HTMX SSE)
I5. [x] Boutons start/stop/restart (HTMX swap)
I6. [x] Panel logs live (SSE stream, auto-scroll)
I7. [x] Panel metriques (CPU/memory barres)
I8. [x] Edition des variables d'environnement
I9. [x] Iframe integration pour les apps de type 'iframe'
I10. [x] App switcher (onglets ou sidebar)
I11. [ ] Test : parcours complet dans le dashboard
```

### Bloc J — Finition
```
Depend de : tous les blocs
J1. [x] Mettre a jour .env.example avec BEEKEEPER_API_KEY, WORKSPACE_DATA_PATH
J2. [x] Mettre a jour docker-compose.yml final
J3. [x] Verifier que docker compose up demarre tout proprement
J4. [x] Revue securite : pas de path traversal, pas de port expose, auth sur toutes les routes
J5. [ ] Test E2E : installer vscode, ouvrir dans le dashboard, editer un fichier dans .hive/
J6. [x] Documenter le format manifest.yaml
J7. [x] Sync + deploy sur le serveur
```

---

## 16. Dependances Python

### beekeeper/requirements.txt
```
fastapi>=0.115
uvicorn>=0.34
docker>=7.0           # Docker SDK Python
redis>=5.0            # Redis async (pub/sub)
pyyaml>=6.0           # Manifest parsing
cryptography>=42.0    # Fernet (dechiffrement env values)
httpx>=0.27           # HTTP client (optionnel, health checks apps)
pydantic>=2.0
pydantic-settings>=2.0
```

### core/requirements.txt — ajouts
```
httpx>=0.27           # Client HTTP vers le Beekeeper
```

---

## 17. Definition de "Phase 6 terminee"

La Phase 6 est complete quand on peut faire ceci de bout en bout :

```
1. docker compose up
   → core, beekeeper, redis, postgres, nginx, workers demarrent

2. Le catalogue contient 3 apps (vscode-server, node-red, jupyter)
   → GET /apps/catalog retourne les 3

3. Installer VS Code Server dans un workspace via le dashboard
   → Le pull de l'image affiche un progress
   → Le container demarre dans le network isole du workspace
   → L'app est accessible dans un iframe dans le dashboard

4. Ouvrir VS Code dans le dashboard
   → L'editeur fonctionne, les fichiers sont dans .hive/code/
   → Creer un fichier dans VS Code → le fichier existe dans le workspace filesystem

5. Installer Node-RED dans le meme workspace
   → Node-RED tourne sur son propre IP dans le meme network
   → Node-RED peut "voir" VS Code par DNS Docker (meme workspace)

6. Verifier l'isolation
   → Un deuxieme workspace installe VS Code
   → Son VS Code est dans un network different, IP differente
   → Il ne peut PAS voir les apps du premier workspace

7. Crash recovery
   → Tuer un container manuellement (docker kill)
   → Le dashboard affiche "crashed" en temps reel (via Redis event)
   → Le Beekeeper publie l'event, le dashboard le recoit via SSE

8. CLI fonctionne
   → workflow app list → affiche les apps du workspace
   → workflow app logs vscode-server --follow → stream les logs

9. Variables d'environnement
   → workflow app env vscode-server PASSWORD=newpass
   → Restart l'app → le nouveau mot de passe est actif

10. Desinstallation propre
    → workflow app uninstall vscode-server
    → Container supprime, entree DB nettoyee, config Nginx retiree
    → Les fichiers dans .hive/ sont preserves (les donnees restent)
```

---

## 18. Angles Morts

| # | Risque | Mitigation |
|---|--------|------------|
| 1 | **Docker socket = root access** : le Beekeeper avec docker.sock peut tout faire sur l'hote | Le Beekeeper est le seul avec docker.sock. Valider toutes les images contre le catalogue. Ne jamais executer d'images arbitraires |
| 2 | **Nginx reload race** : deux apps demarrent en meme temps, deux reloads concurrents | Mutex/lock dans ingress_manager. Regenerer toute la config a chaque fois (idempotent) |
| 3 | **Subnet exhaustion** : 172.20.0.0/16 = 255 subnets max | Suffisant pour des centaines de workspaces. Si on depasse, passer a 10.0.0.0/8 |
| 4 | **Image pull lent** : certaines images font plusieurs GB | Pull async + progress sur Redis. Le dashboard montre l'avancement |
| 5 | **Apps qui base_url-aware** : certaines apps (Jupyter, Node-RED) ont besoin de savoir leur base URL pour fonctionner derriere un reverse proxy | Passer la base URL via env var (ex: `NODE_RED_BASE_URL=/app/abc123/node-red/`) |
| 6 | **WebSocket derriere Nginx** : VS Code, Node-RED, Jupyter utilisent tous WebSocket | Config Nginx avec `Upgrade` et `Connection` headers — deja prevu dans le template |
| 7 | **Donnees orphelines** : si un workspace est supprime, les containers et networks doivent etre nettoyes | Cascade : supprimer le workspace → Core appelle Beekeeper → stop all + delete network |
| 8 | **Beekeeper down** : les apps tournent toujours, mais plus de controle | Les containers sont autonomes. Au restart du Beekeeper, il reconcilie l'etat DB vs Docker |
| 9 | **Limites de ressources** : un workspace qui lance trop d'apps peut consommer tout le serveur | Limite configurable par workspace (max_apps, max_memory_total). A implementer en Phase 7 ou 9 |
| 10 | **SilverBullet et apps qui ne font pas d'iframe** : certaines apps refusent d'etre embedees (X-Frame-Options) | Le manifest declare `ingress: link` pour ces apps. Le dashboard ouvre dans un nouvel onglet |

---

## 19. Format manifest.yaml

Chaque app est decrite par un fichier `apps/{slug}/manifest.yaml`.

### Champs

| Champ | Type | Requis | Description |
|-------|------|--------|-------------|
| `name` | string | oui | Nom affiché dans le catalogue |
| `slug` | string | oui | Identifiant unique, alphanumérique + tirets (ex: `vscode-server`) |
| `version` | string | non | Version de la définition (défaut: `"1.0.0"`) |
| `description` | string | non | Description courte |
| `image` | string | oui | Image Docker complète (ex: `ghcr.io/coder/code-server:latest`) |
| `type` | enum | non | `persistent` (défaut) ou `ephemeral` |
| `ingress` | enum | non | `iframe` (embed dans le dashboard) ou `link` (nouvel onglet). Défaut: `iframe` |
| `port` | int | non | Port interne exposé par le container |
| `health_check` | string | non | Path HTTP pour le health check (ex: `/healthz`) |
| `env` | list | non | Variables d'environnement requises (voir ci-dessous) |
| `mounts` | list | non | Volumes montés depuis `.hive/` (voir ci-dessous) |
| `resources` | dict | non | Limites mémoire/CPU (voir ci-dessous) |
| `icon` | string | non | Nom d'icône MDI (ex: `mdi-microsoft-visual-studio-code`) |

### env[] — schéma des variables

```yaml
env:
  - key: PASSWORD          # Nom de la variable d'environnement dans le container
    description: "..."     # Affichée dans le formulaire d'installation
    required: true         # Si true, l'installation échoue sans cette var
    secret: true           # Si true, masqué dans l'UI + chiffré en DB
    default: ""            # Valeur par défaut (optionnel)
```

### mounts[] — montages depuis .hive/

```yaml
mounts:
  - host: ".hive/code"           # Chemin relatif dans le répertoire du workspace
    container: "/home/coder/workspace"  # Chemin dans le container
    description: "..."           # Optionnel, documentation
```

Le chemin `host` est relatif à `{WORKSPACE_DATA_PATH}/{workspace_id}/`.
Les tentatives de path traversal (ex: `../../etc`) sont rejetées par le Beekeeper.

### resources — limites

```yaml
resources:
  memory: 512m     # Format Docker : 128m, 1g, etc.
  cpus: "1.0"      # Float en string : "0.5", "2.0"
```

### Exemple complet

```yaml
name: Mon App
slug: mon-app
version: "1.0"
description: "Description de l'app"
image: myimage:latest
type: persistent
ingress: iframe
port: 8080
health_check: /health

env:
  - key: SECRET_KEY
    description: "Clé secrète"
    required: true
    secret: true
    default: ""

mounts:
  - host: ".hive/mon-app"
    container: "/data"
    description: "Données persistées"

resources:
  memory: 256m
  cpus: "0.5"

icon: mdi-application
```

---

*Phase 6 planifiee le 3 avril 2026.*
*Le Beekeeper veille sur la ruche — aucun container ne nait sans sa permission.*
