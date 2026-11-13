# HiveFlows Portal — Documentation technique (Phase 1)

> **Statut** : document de conception — Phase 1
> **Date** : 2026-04-10
> **Scope** : poser les fondations d'un reverse tunnel HTTP sécurisé entre HiveFlows et des services self-hosted situés hors du réseau Docker interne.

---

## 1. Contexte & objectif

### 1.1 Problème

Les agents HiveFlows vivent dans le réseau Docker interne du core. Aujourd'hui ils peuvent :
- Appeler les apps Beekeeper (Jupyter, Node-RED, VSCode Server, etc.) via le réseau Docker.
- Appeler n'importe quel service public sur Internet.

Ce qu'ils **ne peuvent pas** faire :
- Atteindre un service self-hosted chez l'utilisateur (Home Assistant sur un Raspberry Pi, une instance Plex sur le NAS, une API développée sur le laptop, etc.) sans exposer ces services publiquement.

### 1.2 Solution : Portal

Un **reverse tunnel WebSocket persistant initié par le client** — même pattern que Cloudflare Tunnel, ngrok, Tailscale Funnel. Le client (appelé *gateway*) tourne sur la machine de l'utilisateur, ouvre une connexion WebSocket sortante vers HiveFlows, et reste en écoute. Quand HiveFlows veut atteindre un service local, il envoie une "requête" dans ce WebSocket, le client l'exécute localement et renvoie la réponse.

**Bénéfices** :
- Contourne NAT, pare-feu, IP dynamiques — aucun port à ouvrir côté utilisateur.
- Aucune exposition publique des services locaux.
- L'utilisateur contrôle ce qui est exposé via un fichier de config local.

### 1.3 Objectifs de la Phase 1

- Établir un protocole de communication **HTTP-only** entre HiveFlows (serveur) et un client Go portable.
- Permettre à n'importe quel caller authentifié à l'intérieur du core (agents, apps Beekeeper, requêtes dashboard) d'appeler un service distant via une URL unifiée.
- Enrôler, révoquer, auditer des devices depuis le dashboard.
- Offrir un client CLI Go simple, installable en tant que service Linux/macOS/Windows.

### 1.4 Hors scope de la Phase 1

- Streaming bidirectionnel (SSE, WebSocket imbriqué, chunked transfer).
- Tunneling TCP générique (SSH, Postgres, raw TCP).
- Exécution de commandes système sur le device distant — Portal ne contrôle pas la machine, il proxifie uniquement des services HTTP déjà existants.
- App Desktop GUI (tray icon, panneau de contrôle) — ce sera une surcouche ultérieure, le binaire Go de la Phase 1 reste réutilisable tel quel.
- Permissions fines agents → devices (tout agent du workspace peut utiliser tout device du workspace en Phase 1).
- Allowlist hybride poussée par le serveur (le client garde 100% du contrôle via son yaml local — option A simple).
- Rotation automatique des tokens (permanent + révocable).
- Pipeline CI/CD de build Go multi-arch (build manuel acceptable en Phase 1).

---

## 2. Vue d'ensemble architecturale

```
┌─────────────────────────────────────────┐          ┌──────────────────────────┐
│           HiveFlows (VPS)               │          │   Device utilisateur     │
│                                         │          │                          │
│  ┌───────────────────────────────────┐  │          │  ┌────────────────────┐  │
│  │          Core (FastAPI)           │  │          │  │  portal-client     │  │
│  │                                   │  │          │  │  (Go binary)       │  │
│  │  ┌─────────────────────────────┐  │  │          │  │                    │  │
│  │  │  Tunnel endpoint (HTTP)     │  │  │          │  │  portal.yaml       │  │
│  │  │  /tunnel/{ws}/{dev}/{svc}/* │  │  │          │  │  ┌──────────────┐  │  │
│  │  └─────────────┬───────────────┘  │  │          │  │  │ services:    │  │  │
│  │                │                  │  │          │  │  │  - homeass.  │  │  │
│  │                ▼                  │  │          │  │  │  - vlc       │  │  │
│  │  ┌─────────────────────────────┐  │  │          │  │  └──────────────┘  │  │
│  │  │   PortalRegistry (memory)   │  │  │          │  │                    │  │
│  │  │   {device_id: Connection}   │  │  │          │  │  HTTP client local │  │
│  │  └─────────────┬───────────────┘  │  │          │  │   ↓                │  │
│  │                │                  │  │          │  │  127.0.0.1:8004    │  │
│  │                ▼                  │  │          │  │  192.168.1.20     │  │
│  │  ┌─────────────────────────────┐  │  │  wss://  │  │                    │  │
│  │  │   WebSocket /portal/ws      │◄─┼──┼──────────┼──┤ WebSocket client   │  │
│  │  └─────────────────────────────┘  │  │          │  │ (persistent)       │  │
│  │                                   │  │          │  └────────────────────┘  │
│  │  ┌─────────────────────────────┐  │  │          │                          │
│  │  │  portal_audit_log (DB)      │  │  │          └──────────────────────────┘
│  │  │  portal_devices (DB)        │  │  │
│  │  │  portal_enroll_tokens (DB)  │  │  │
│  │  └─────────────────────────────┘  │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘

Flow d'une requête agent → service distant :

  Agent deepagents
      │
      ▼ http.get("http://core:8000/tunnel/acme/nas-maison/homeassistant/api/states")
  Tunnel endpoint
      │
      ▼ lookup PortalRegistry[nas-maison]
  PortalConnection.send_request(method, path, body) → génère req_id, crée Future
      │
      ▼ WebSocket REQUEST {id, method, path, headers, body}
  portal-client (Go)
      │
      ▼ valide que "homeassistant" est dans portal.yaml
      │
      ▼ net/http.Do() → http://192.168.1.20:8123/api/states
      │
      ▼ WebSocket RESPONSE {id, status, headers, body}
  PortalConnection reçoit → résout Future[req_id]
      │
      ▼ Tunnel endpoint construit la Response HTTP
      │
      ▼ insert portal_audit_log (async)
  Agent reçoit la réponse
```

---

## 3. Distinction Serveur / Client

### 3.1 Partie Serveur (core)

Vit dans [core/](core/) (FastAPI existant). Aucune nouvelle app Beekeeper — décision architecturale : le Portal Manager est intégré au core parce que :
- Le registry WebSocket doit être colocalisé avec les agents qui l'utilisent (éviter un hop réseau Docker par requête).
- L'auth, la session DB, les modèles workspace/user sont déjà dans le core.
- La surface de code est modeste (~1 router FastAPI + 1 service de registry + 1 service d'enrôlement).

Composants :
- Modèles SQLAlchemy + migration Alembic
- `PortalRegistry` (service in-memory)
- Route `WebSocket /portal/ws` (connexion des clients)
- Route `HTTP /tunnel/...` (endpoint unifié utilisé par agents, tools, apps)
- Routes `HTTP /api/portal/...` (management : CRUD devices, audit log)
- Templates Jinja2 pour le dashboard

### 3.2 Partie Client (gateway)

Vit dans [clients/portal/](clients/portal/) (nouveau dossier dans le repo BoBot). Binaire Go unique, sans dépendances runtime, cross-compilé pour Linux amd64/arm64, macOS, Windows.

Composants :
- `cmd/portal-client/main.go` — entrypoint
- `internal/config` — parsing yaml
- `internal/ws` — boucle WebSocket, reconnect, dispatch
- `internal/proxy` — exécution HTTP locale avec validation allowlist
- `portal.yaml` — config utilisateur (template fourni)

Le client est explicitement **non-trusted** du point de vue du serveur : le serveur ne lui fait pas confiance, valide tout, et n'exécute rien qu'il n'a pas prévu. Inversement, le client ne forwarde jamais vers une route qui n'est pas déclarée dans son yaml local.

---

## 4. Modèle d'identité

### 4.1 Device

Un Device représente **une instance de gateway client connectée à un workspace**. Si une même machine physique sert 3 workspaces, elle héberge 3 Devices distincts (3 instances de `portal-client` avec 3 configs yaml et 3 tokens). C'est volontairement redondant mais simple et cohérent avec le modèle workspace-scoped d'HiveFlows.

**Slug** : unique par workspace. Exemples : `nas-maison`, `laptop-bureau`, `rpi-salon`.

### 4.2 Token

- **Type dédié** : `portal` — pas de réutilisation des tokens workspace existants. Cycle de vie propre, révocation indépendante, audit isolé.
- **Permanent + révocable** en Phase 1 (pas de rotation automatique, pas d'expiration par défaut).
- **Stocké hashé** en DB (`token_hash`), jamais en clair.
- **Affiché une seule fois** à l'utilisateur lors de la création du device (via la commande `init` à copier).

### 4.3 Permissions (Phase 1)

Tous les agents d'un workspace ont accès à tous les devices du workspace. Pas de notion `allowed_devices` dans `AgentConfig` pour l'instant — ce sera adressé dans la phase "Permissions" globale du projet.

---

## 5. Schéma de base de données

### 5.1 `portal_devices`

| Colonne | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `workspace_id` | FK → workspaces.id | cascade delete |
| `slug` | str | unique par workspace |
| `name` | str | libre, affiché dashboard |
| `token_hash` | str | sha256 du token permanent ; NULL si révoqué |
| `services` | JSONB | liste `[{name, route}]` envoyée par le client au HELLO |
| `status` | enum | `pending`, `connected`, `disconnected`, `revoked` |
| `last_seen_at` | timestamp | update à chaque PING ou activité |
| `created_at` | timestamp | |

Contrainte : `UNIQUE(workspace_id, slug)`.

### 5.2 `portal_enroll_tokens`

Tokens one-shot à TTL court (15 min) utilisés uniquement pour l'enrôlement initial.

| Colonne | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `workspace_id` | FK → workspaces.id | |
| `device_name` | str | pré-rempli par l'utilisateur au dashboard |
| `token_hash` | str | sha256 du one-shot |
| `expires_at` | timestamp | par défaut `now() + 15 min` |
| `used_at` | timestamp NULL | marqué une fois consommé |
| `created_at` | timestamp | |

### 5.3 `portal_audit_log`

Toute requête passant par `/tunnel/...` génère une ligne.

| Colonne | Type | Notes |
|---|---|---|
| `id` | bigint PK | |
| `device_id` | FK → portal_devices.id | |
| `agent_id` | FK → agents.id NULL | parsed depuis `X-HiveFlows-Agent-ID` |
| `task_id` | FK → tasks.id NULL | parsed depuis `X-HiveFlows-Task-ID` |
| `source` | enum | `agent`, `app`, `dashboard`, `unknown` |
| `service_slug` | str | ex: `homeassistant` |
| `method` | str | GET/POST/... |
| `path` | str | chemin après le service_slug |
| `status_code` | int | retour du service distant |
| `duration_ms` | int | round-trip complet |
| `bytes_out` | int | taille body envoyé au client |
| `bytes_in` | int | taille body reçu du client |
| `error` | str NULL | si échec (timeout, service non exposé, 413, etc.) |
| `ts` | timestamp | indexé |

**Index** : `(device_id, ts DESC)`, `(agent_id, ts DESC)`.

**Note opérationnelle** : le volume peut exploser si un agent tape en boucle un service verbeux. Prévoir une tâche de nettoyage planifiée (rétention 30/60/90 jours selon besoin) dans une phase ultérieure. Pour la Phase 1, on logge tout sans purge.

---

## 6. Protocole WebSocket

### 6.1 Connexion

```
Client → Server : WSS /portal/ws
  Authorization: Bearer <portal_token>
```

Le token est transmis en header lors du handshake. Le serveur :
1. Parse le token, calcule le hash, cherche en DB.
2. Si invalide ou révoqué → close (1008).
3. Charge le `portal_devices` correspondant, vérifie `status != 'revoked'`.
4. Attend le premier message `HELLO`.
5. Enregistre la connexion dans `PortalRegistry[device_id] = PortalConnection(ws)`.
6. Met à jour `status = 'connected'`, `last_seen_at = now()`, `services = hello.services`.

### 6.2 Messages

Tous les messages sont du JSON texte (UTF-8) sur la WebSocket. Le body HTTP, pouvant être binaire, est encodé en **base64**.

**Client → Serveur** :

```json
// HELLO — premier message obligatoire après connect
{
  "type": "hello",
  "version": "1",
  "device_info": {
    "os": "linux",
    "arch": "amd64",
    "client_version": "0.1.0"
  },
  "services": [
    {"name": "homeassistant", "route": "http://192.168.1.20:8123"},
    {"name": "vlc", "route": "http://127.0.0.1:8004"}
  ]
}

// RESPONSE — réponse à une REQUEST serveur
{
  "type": "response",
  "id": "req_abc123",
  "status": 200,
  "headers": {"Content-Type": "application/json"},
  "body_b64": "eyJoZWxsbyI6ICJ3b3JsZCJ9"
}

// ERROR — erreur côté client (service non exposé, unreachable, timeout local)
{
  "type": "error",
  "id": "req_abc123",
  "code": "service_not_allowed" | "upstream_unreachable" | "upstream_timeout" | "body_too_large",
  "message": "human readable"
}

// PING — keepalive
{"type": "ping"}
```

**Serveur → Client** :

```json
// REQUEST — demande de forward
{
  "type": "request",
  "id": "req_abc123",
  "service": "homeassistant",
  "method": "GET",
  "path": "/api/states",
  "headers": {"Accept": "application/json"},
  "body_b64": ""
}

// PONG
{"type": "pong"}

// REVOKE — le token vient d'être révoqué, ferme la connexion
{"type": "revoke"}
```

### 6.3 Mécanique de corrélation requête/réponse

Côté serveur, `PortalConnection` maintient :

```python
pending_requests: dict[str, asyncio.Future]
```

- `send_request(method, path, body) -> Future` :
  1. Génère `req_id = secrets.token_urlsafe(16)`
  2. `fut = asyncio.Future()` ; `pending_requests[req_id] = fut`
  3. Sérialise REQUEST, envoie via WS
  4. `await asyncio.wait_for(fut, timeout=30)`
  5. Sur timeout : pop du dict, raise `PortalTimeoutError`
- Tâche `_read_loop` : pour chaque message entrant de type RESPONSE/ERROR, pop le `req_id`, appelle `fut.set_result(...)`.

### 6.4 Reconnexion

Le client Go implémente un reconnect avec **backoff exponentiel plafonné** :
- Initial : 1s
- Doublement à chaque échec : 1, 2, 4, 8, 16, 32, 60s
- Plafond : 60s
- Reset après une connexion stable ≥ 30s

Le serveur, au disconnect, met `status = 'disconnected'` et vide le slot du registry. Toutes les `pending_requests` restantes échouent avec `PortalConnectionLostError` → le endpoint HTTP retourne `502 Bad Gateway`.

### 6.5 Keepalive

- Client envoie un PING toutes les 20s.
- Serveur répond PONG.
- Si le serveur ne reçoit aucun message (PING ou RESPONSE) pendant 60s → close.

---

## 7. Endpoint HTTP unifié

### 7.1 Route

```
ANY /tunnel/{workspace_slug}/{device_slug}/{service_slug}/{path:path}
```

Le segment `{path:path}` est une capture FastAPI qui absorbe tout le reste de l'URL, permettant un forward fidèle de n'importe quel chemin arbitraire.

### 7.2 Authentification & source

Trois sources légitimes :

| Source | Auth | Header attendu |
|---|---|---|
| Agent (core interne) | Token agent | `X-HiveFlows-Agent-ID: <uuid>`, optionnel `X-HiveFlows-Task-ID` |
| App Beekeeper | Token interne app (mécanisme à définir/aligner avec l'existant) | `X-HiveFlows-App-ID: <slug>` |
| Dashboard (user direct) | Session cookie | aucun header supplémentaire |

Le endpoint détermine `source` et `agent_id`/`task_id` à partir des headers et/ou de la session. Ces valeurs sont persistées dans `portal_audit_log`.

**À l'intérieur du réseau Docker** : le endpoint est accessible via `http://core:8000/tunnel/...` depuis n'importe quelle app Beekeeper ou agent. **Publiquement** : non exposé (sauf si on décide d'ajouter un accès dashboard direct, auquel cas l'auth session suffit).

### 7.3 Logique du handler

```
1. Parse workspace_slug, device_slug, service_slug, path
2. Auth + détermine source/agent_id/task_id
3. Charge workspace, vérifie que l'utilisateur/agent y a accès
4. Charge device via (workspace_id, device_slug) ; 404 sinon
5. Vérifie device.status == 'connected' ; 503 sinon
6. Vérifie que service_slug est dans device.services ; 404 sinon
7. Récupère PortalConnection depuis le registry ; 503 si absent
8. Construit REQUEST {method, path, headers filtrés, body_b64}
9. await connection.send_request(...) avec timeout 30s
10. Parse RESPONSE → Response FastAPI (status, headers filtrés, body)
11. Insert portal_audit_log (async, fire-and-forget)
12. Return
```

### 7.4 Filtrage des headers

**Headers retirés avant forward vers le client** (headers hop-by-hop ou sensibles) :
- `Host`, `Connection`, `Upgrade`, `Proxy-Authenticate`, `Proxy-Authorization`, `TE`, `Trailer`, `Transfer-Encoding`, `Keep-Alive`
- `Authorization` du caller (on ne leak pas les tokens internes vers le device)
- Les headers `X-HiveFlows-*` (consommés par le endpoint, pas transmis)

**Headers retirés dans la réponse avant retour au caller** :
- Hop-by-hop identiques
- `Set-Cookie` (évite les cross-site cookies parasites)

### 7.5 Limites

- **Taille body** : 10 MB max en request ET response. Au-delà → `413 Payload Too Large`. Pas de streaming en Phase 1, buffer complet des deux côtés.
- **Timeout** : 30s par défaut. Configuration ultérieure via header ou config serveur si besoin. Dépassement → `504 Gateway Timeout`.

### 7.6 Codes d'erreur

| Code | Cause |
|---|---|
| 401 | Auth manquante ou invalide |
| 403 | Caller n'a pas accès au workspace |
| 404 | Workspace, device ou service introuvable |
| 413 | Body > 10 MB |
| 502 | Connexion WS perdue pendant l'attente, ou client a répondu ERROR `upstream_unreachable` |
| 503 | Device non connecté |
| 504 | Timeout (30s) dépassé |

---

## 8. Flow d'enrôlement

### 8.1 Création du device (dashboard)

1. User va dans **Dashboard → Workspace → Portal Devices**.
2. Clique "Add Device", saisit un nom (`NAS Maison`).
3. Front appelle `POST /api/portal/devices` avec `{name}`.
4. Backend :
   - Génère un slug unique (`nas-maison`, `nas-maison-2` si collision)
   - Crée une ligne `portal_devices` avec `status = 'pending'`, `token_hash = null`
   - Génère un **one-shot token** (32 bytes base64url), le hash, insère dans `portal_enroll_tokens` avec `expires_at = now() + 15min`
   - Retourne `{device_id, slug, enroll_token, init_command}`
5. Dashboard affiche la commande à copier **une seule fois** :
   ```
   portal-client init \
     --server wss://hiveflows.example.com/portal/ws \
     --token <enroll_token> \
     --device-slug nas-maison
   ```

### 8.2 Enrôlement depuis le client

1. User installe le binaire Go, lance `portal-client init ...`
2. Le client `POST https://hiveflows.example.com/portal/enroll` avec `{enroll_token, device_slug, device_info}`
3. Backend :
   - Lookup `portal_enroll_tokens` par hash, vérifie non expiré et non utilisé
   - Lookup `portal_devices` par `(workspace_id, slug)` et `status = 'pending'`
   - Génère un **permanent token** (32 bytes base64url)
   - Update `portal_devices` : `token_hash = sha256(perm_token)`, `status = 'disconnected'`
   - Marque `portal_enroll_tokens.used_at = now()`
   - Retourne `{permanent_token, server_url, workspace_slug}`
4. Le client écrit `portal.yaml` dans le dossier de config (`~/.config/portal-client/portal.yaml` sur Linux/macOS, `%APPDATA%\portal-client\portal.yaml` sur Windows) :
   ```yaml
   server_url: wss://hiveflows.example.com/portal/ws
   workspace: acme
   device_slug: nas-maison
   token: <permanent_token>
   services: []
   ```
5. User édite le fichier pour ajouter ses services.
6. User lance `portal-client run` → connexion WS + HELLO.

### 8.3 Révocation

1. User clique "Revoke" dans le dashboard, ou appelle `DELETE /api/portal/devices/{id}`.
2. Backend :
   - `token_hash = null`, `status = 'revoked'`
   - Si `PortalRegistry[device_id]` existe : envoie message `REVOKE`, close la WS.
3. Le client reçoit REVOKE ou un close, arrête sa boucle, log l'événement, exit avec code non-zero.

---

## 9. Client Go — structure

### 9.1 Arborescence

```
clients/portal/
├── cmd/
│   └── portal-client/
│       └── main.go              # CLI entrypoint (cobra ou flag std)
├── internal/
│   ├── config/
│   │   └── config.go            # parse portal.yaml, XDG paths
│   ├── enroll/
│   │   └── enroll.go            # POST /portal/enroll
│   ├── ws/
│   │   ├── client.go            # connexion, reconnect, read/write loops
│   │   └── protocol.go          # structs JSON (hello, request, response, ...)
│   └── proxy/
│       └── proxy.go              # exécution HTTP locale + validation allowlist
├── examples/
│   ├── portal.yaml.example
│   └── portal-client.service    # unit systemd
├── go.mod
├── go.sum
└── README.md
```

### 9.2 Commandes CLI

```
portal-client init --server URL --token TOKEN --device-slug SLUG [--config PATH]
   → POST /portal/enroll, écrit portal.yaml, exit 0

portal-client run [--config PATH]
   → charge config, boucle connect + WS forever

portal-client version
   → affiche version + commit hash (build-time ldflags)
```

### 9.3 Config `portal.yaml`

```yaml
# URL du WebSocket HiveFlows
server_url: wss://hiveflows.example.com/portal/ws

# Identité (remplis par `init`)
workspace: acme
device_slug: nas-maison
token: <permanent_token>

# Services exposés — SEULS ces noms sont acceptés par le client
services:
  - name: homeassistant
    route: http://192.168.1.20:8123
  - name: vlc
    route: http://127.0.0.1:8004
```

### 9.4 Validation allowlist côté client

Pour chaque REQUEST reçue :
1. Lookup `service` dans `config.services` — si absent → envoie ERROR `service_not_allowed`, **ne fait aucune requête locale**.
2. Construit l'URL : `{route}{path}` (concat simple).
3. Exécute via `net/http.Client` avec timeout local 25s (< 30s serveur, pour que l'erreur ait le temps de remonter).
4. Lit body jusqu'à 10 MB max ; sinon ERROR `body_too_large`.
5. Sérialise RESPONSE et renvoie sur la WS.

### 9.5 Build cibles

- `linux/amd64` (NAS, serveurs)
- `linux/arm64` (Raspberry Pi 4+, NAS ARM)
- `darwin/amd64`, `darwin/arm64` (macOS Intel + Apple Silicon)
- `windows/amd64`

Script `clients/portal/build.sh` qui fait les 5 builds + checksums. Phase 1 : build manuel, pas de release automatisée.

### 9.6 Installation en service

Exemple systemd fourni dans [clients/portal/examples/portal-client.service](clients/portal/examples/portal-client.service) :

```ini
[Unit]
Description=HiveFlows Portal Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=portal
ExecStart=/usr/local/bin/portal-client run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Pour macOS : exemple `launchd` plist dans le même dossier. Pour Windows : instructions `nssm` ou `sc.exe` dans le README.

---

## 10. API REST de management

### 10.1 Routes

```
POST   /api/portal/devices
       body: {name: "NAS Maison"}
       → {device_id, slug, enroll_token, init_command, expires_at}
       auth: session dashboard

GET    /api/portal/devices?workspace=<slug>
       → [{id, slug, name, status, last_seen_at, services_count}]

GET    /api/portal/devices/{id}
       → {id, slug, name, status, last_seen_at, services, created_at}

DELETE /api/portal/devices/{id}
       → 204, révoque + ferme WS

GET    /api/portal/devices/{id}/audit?limit=50&before=<ts>
       → paginated audit log
```

### 10.2 Endpoints non-authentifiés session

```
POST   /portal/enroll
       body: {enroll_token, device_slug, device_info}
       → {permanent_token, server_url, workspace_slug}

WS     /portal/ws
       header: Authorization: Bearer <permanent_token>
```

---

## 11. Dashboard UI

### 11.1 Pages

- `/dashboard/workspaces/{slug}/portal/devices` — liste :
  - Tableau : nom, slug, status (badge couleur), last_seen relatif, nombre de services, actions
  - Bouton "Add Device" → modal nom + bouton "Create"
  - Après création : modal affiche la commande `portal-client init ...` à copier (once)

- `/dashboard/workspaces/{slug}/portal/devices/{id}` — détail :
  - Header : nom, slug, status, last_seen
  - Card "Services exposés" : liste depuis `device.services` (noms uniquement, routes masquées pour éviter la fuite d'info réseau)
  - Card "Audit log" : tableau paginé avec ts, agent, method, path, status, duration
  - Bouton "Revoke" (confirmation requise)

### 11.2 Conventions UI

- Icônes MDI monochromes cyan (`var(--cyan)`) conformes à [feedback_mdi_icons.md](feedback_mdi_icons.md).
- HTMX pour les actions (create, revoke, pagination audit).
- Templates Jinja2 intégrés dans le core existant.

---

## 12. Sécurité

### 12.1 Principes fondamentaux

1. **Le client est non-trusted** : le serveur valide tout, n'exécute rien qu'il n'a pas prévu, ne fait jamais confiance aveuglément au contenu reçu du client.
2. **Le client garde le dernier mot** sur ce qui est exposable : seuls les services déclarés dans `portal.yaml` sont atteignables. Le serveur ne peut pas pousser de nouvelle route sans action de l'utilisateur (Phase 1 = option A simple ; l'option C hybride reste ouverte pour plus tard).
3. **TLS obligatoire** : la WebSocket tourne en `wss://`. Pas de fallback `ws://` en production.
4. **Principe de moindre privilège** : un token portal ne donne accès qu'à la fonctionnalité portal, rien d'autre dans HiveFlows.

### 12.2 Mesures concrètes

- Tokens stockés hashés (sha256) en DB.
- One-shot enroll tokens avec TTL court (15 min) et usage unique.
- Double validation de l'allowlist : côté serveur (via la liste reçue au HELLO) ET côté client (via le yaml local). Toute requête refusée côté client génère une ligne d'audit.
- Filtrage strict des headers hop-by-hop et sensibles (section 7.4).
- Limite de taille du body (10 MB) pour éviter l'abus mémoire.
- Timeout global (30s serveur, 25s client) pour éviter les requêtes pendantes.
- Audit log exhaustif : chaque requête (succès ou échec) est tracée avec source, agent, device, path, status, durée, octets.
- Révocation immédiate : le `REVOKE` ferme la WS en cours, pas besoin d'attendre un cycle.

### 12.3 Menaces hors scope Phase 1 (à traiter plus tard)

- Rate limiting par agent / par device / par service.
- Détection d'anomalie (agent qui tape 10k req/min sur un service).
- Chiffrement bout-en-bout des payloads (aujourd'hui TLS termine au core, donc le core voit tout — acceptable car le core est l'exécuteur des agents).
- Rotation automatique des tokens.
- mTLS côté WebSocket (aujourd'hui simple Bearer token, suffisant dans la mesure où TLS protège le canal).
- Signature des messages WS pour détecter tampering (non-nécessaire tant que TLS tient).

---

## 13. Intégration infrastructure

### 13.1 Cloudflare / reverse proxy

Le WebSocket `/portal/ws` doit être accessible depuis Internet (les clients sont chez les utilisateurs). L'infra actuelle (serveur 192.168.1.10 avec Cloudflare en amont selon la config) doit :
- Router `/portal/ws` vers le core (port 8002 externe).
- Supporter les WebSockets (Cloudflare le fait nativement ; vérifier qu'aucune règle ne les coupe).
- Ne pas appliquer de timeout agressif (certaines connexions WS vont durer des heures).

**Action** : vérifier la config Cloudflare avant les tests end-to-end ; pour les tests initiaux, on peut shortcutter en utilisant directement l'IP locale `192.168.1.10:8002` depuis un client Go lancé sur la même machine ou le même réseau.

### 13.2 Docker compose

- Pas de nouveau conteneur (tout dans le core).
- Pas de nouveau port exposé (le `/portal/ws` passe par le même port 8002 que le reste du core).
- Variables d'environnement à ajouter au core :
  - `PORTAL_WS_TIMEOUT_SECONDS=30`
  - `PORTAL_MAX_BODY_BYTES=10485760`
  - `PORTAL_ENROLL_TTL_MINUTES=15`

### 13.3 Headers attendus par le core

Pour que les agents puissent s'identifier, le factory `AgentManager` devra injecter automatiquement `X-HiveFlows-Agent-ID` (et `X-HiveFlows-Task-ID` si en contexte de run) dans les requêtes HTTP sortantes vers `http://core:8000/tunnel/...`. Plusieurs approches possibles :
- Middleware sur le HTTP client utilisé par les tools.
- Tool wrapper qui construit l'URL et les headers.
- À valider lors du Bloc C — choix différé pour ne pas bloquer la conception.

---

## 14. Découpage en blocs d'implémentation

Ordre recommandé (certains blocs parallélisables) :

```
A (schéma DB)
│
├─→ B (WS registry) ──┐
│                     │
└─→ D (enrôlement)    │
                      │
                      └─→ C (endpoint /tunnel) ──┐
                                                 │
                                                 └─→ E (client Go) ──┐
                                                                     │
                                                                     └─→ G (tests e2e)
                                                                     │
                                                                     └─→ F (dashboard UI)
                                                                                  │
                                                                                  └─→ H (doc & packaging)
```

### Bloc A — Schéma DB & modèles
- [x] Modèles SQLAlchemy : `PortalDevice`, `PortalEnrollToken`, `PortalAuditLog`
- [x] Migration Alembic : `020_portal_tables.py`
- [x] Enums `PortalDeviceStatus`, `PortalAuditSource`
- [x] Tests de migration (up + down)

### Bloc B — PortalRegistry & WebSocket endpoint
- [x] `core/services/portal_registry.py` : `PortalRegistry`, `PortalConnection`
- [x] Route `WebSocket /portal/ws` avec auth, HELLO, boucle read
- [x] Tests unitaires : connexion, HELLO invalide, disconnect propre, corrélation req/resp, timeout

### Bloc C — Endpoint `/tunnel/...`
- [x] Route `ANY /tunnel/{ws_slug}/{device_slug}/{service_slug}/{path:path}`
- [x] Auth multi-source (agent, app, session)
- [x] Validation, forward, audit log async
- [x] Filtrage headers, limite body
- [x] Tests unitaires : succès, device absent, service non exposé, timeout, révocation

### Bloc D — API de management & enrôlement
- [x] Routes `/api/portal/devices` (CRUD + audit)
- [x] Route `POST /portal/enroll`
- [x] Service `portal_enrollment.py`
- [x] Tests : cycle complet create → enroll → use → revoke

### Bloc E — Client Go `portal-client`
- [x] `clients/portal/` avec structure section 9.1
- [x] Parsing yaml, commande `init`, commande `run`
- [x] Boucle WS avec reconnect backoff
- [x] Validation allowlist, forward HTTP local
- [x] Build script multi-arch
- [x] README avec exemples systemd / launchd / Windows

### Bloc F — Dashboard UI
- [x] Templates `portal/devices/list.html`, `portal/devices/detail.html`
- [x] Intégration HTMX (création, révocation, pagination audit)
- [x] Icônes MDI cyan
- [x] Lien depuis le menu workspace

### Bloc G — Tests end-to-end
- [ ] Test harness : lance `portal-client run` en arrière-plan, avec un server HTTP echo local en Python
- [ ] Scénario 1 : enrôlement complet
- [ ] Scénario 2 : forward GET/POST/PUT/DELETE avec body
- [ ] Scénario 3 : service non exposé → erreur
- [ ] Scénario 4 : timeout → 504
- [ ] Scénario 5 : révocation → connexion fermée, requête suivante → 503
- [ ] Scénario 6 : reconnect après drop de WS
- [ ] Scénario 7 : audit log rempli correctement

### Bloc H — Documentation & packaging
- [ ] `docs/portal.md` : architecture, protocole, exemples d'usage
- [ ] `clients/portal/README.md` : installation, config, troubleshooting
- [ ] Script de release (`build.sh`) + génération checksums
- [ ] Mise à jour du README principal du projet

---

## 15. Paramètres fixés pour la Phase 1

| Paramètre | Valeur | Raison |
|---|---|---|
| Scope protocole | HTTP-only | Simplicité, couvre 90% des besoins |
| Emplacement serveur | Core (pas app Beekeeper) | Colocalisation avec agents, auth partagée |
| Emplacement client | `clients/portal/` dans BoBot | Versionning aligné, CI unique |
| Stack client | Go | Binaire statique portable, multi-arch |
| GUI client | Aucune (CLI uniquement) | Phase 1 minimale ; Desktop = phase ultérieure |
| Modèle de contrôle | Option A (allowlist 100% client) | Simple ; C hybride envisagée pour plus tard |
| Type de token | Dédié `portal` | Révocation propre, audit isolé |
| Rotation token | Manuelle (permanent + révocable) | Simple ; rotation auto = plus tard |
| Device ↔ Workspace | 1 device = 1 workspace | Si multi-workspace : plusieurs instances |
| Permissions agents → devices | Tout agent du workspace = accès | Phase permissions générales = plus tard |
| Timeout forward | 30s serveur / 25s client | Configurable plus tard si besoin |
| Taille body max | 10 MB | Évite abus mémoire ; streaming = plus tard |
| TTL enroll token | 15 min | Friction basse sans exposition longue |
| Audit log rétention | Illimitée Phase 1 | Purge planifiée = plus tard si volume explose |
| Test infra initial | `192.168.1.10` local | Cloudflare à vérifier avant prod |

---

## 16. Extensions futures (hors Phase 1)

- **Streaming** : SSE et chunked transfer pour les APIs longues ou temps-réel.
- **Tunnel TCP générique** : SSH, Postgres, raw TCP via multiplexage de streams sur la WS.
- **Option C hybride** : le serveur peut activer/désactiver des services dans les limites de l'allowlist yaml du client.
- **App Desktop** : Tauri (ou Electron) avec tray icon, panneau de contrôle local, édition du yaml assistée. Le binaire Go de Phase 1 devient le sidecar.
- **Permissions fines** : `allowed_devices: []` dans `AgentConfig`, ACL par service.
- **Rate limiting** : par agent, par device, par service.
- **Rotation automatique des tokens** avec renouvellement négocié sur la WS.
- **Tâche planifiée de purge** de `portal_audit_log`.
- **Metrics Prometheus** : requêtes/s, latence, erreurs, devices connectés.
- **mDNS / auto-discovery** de services locaux par le client Go.
- **Tools agents dédiés** (`portal_http`, `portal_list_devices`, etc.) pour une UX agent plus explicite que le simple appel URL.

---

## 17. Questions ouvertes encore à trancher

Ces points ne bloquent pas le démarrage du Bloc A mais devront être résolus avant les blocs concernés :

1. **Auth des apps Beekeeper vers `/tunnel/`** — existe-t-il déjà un mécanisme de token d'app réutilisable, ou faut-il en créer un ? À aligner au moment du Bloc C.
2. **Injection automatique de `X-HiveFlows-Agent-ID`** dans les requêtes HTTP des agents — middleware sur le HTTP client, tool wrapper, ou autre ? Choix déféré au Bloc C.
3. **Configuration Cloudflare** pour exposer `/portal/ws` — à valider avant les tests end-to-end du Bloc G.
4. **Format exact des routes client** dans le yaml (avec ou sans scheme `http://`, support `https://` pour services locaux TLS-enabled ?). Choix déféré au Bloc E.

---

**Prêt à démarrer au Bloc A** dès validation de ce document.
