# remote-fs

App HiveFlows — Système de fichiers distribué pour le partage synchronisé de `/workspace` entre instances.

**End-goal :** Deux instances HiveFlows (ou plus) partagent le même `/workspace` en temps réel. C'est la fondation de FlowerFields.

---

## Architecture

```
Instance A                         Instance B
  watchdog                           watchdog
     │                                  │
  /ws/sync ◄──── WebSocket P2P ──────► /ws/sync
  REST API ◄──── delta sync ─────────► REST API
     │                                  │
  /workspace  (synchronisé)         /workspace  (synchronisé)
     │
  WebDAV (optionnel — clients externes: Finder, mobile...)
```

**Discovery des peers :** via le service Portal (les instances sont enregistrées comme devices).  
**Config locale :** `/workspace/.hive/remote-fs/config.yaml`

---

## Pile technologique

- **Python 3.11+** / **FastAPI** — dans notre stack, pas de friction
- **websockets** / FastAPI WebSocket — sync temps réel
- **watchdog** — surveillance des changements locaux
- **httpx** (async) — transfert de fichiers entre peers
- **SQLite** (container_data) — journal de sync, manifests, queue offline
- **PyYAML** — lecture de config
- **WebDAV** (optionnel) : `wsgidav` si on expose aux clients externes

---

## Structure des dossiers

```
apps/remote-fs/
├── remote-fs.md          # ce fichier
├── Dockerfile
├── requirements.txt
├── main.py               # entrypoint FastAPI
├── config.py             # lecture de /workspace/.hive/remote-fs/config.yaml
├── sync/
│   ├── watcher.py        # watchdog → détecte les changements locaux
│   ├── manifest.py       # calcul SHA-256, diff de manifests
│   ├── transfer.py       # upload/download de fichiers par chunks
│   └── journal.py        # SQLite — log des ops, queue offline
├── api/
│   ├── files.py          # REST endpoints fichiers
│   ├── ws.py             # WebSocket /ws/sync
│   └── webdav.py         # (optionnel) exposition WebDAV
└── portal/
    └── client.py         # interroge le Portal pour résoudre les peers
```

---

## Config — `/workspace/.hive/remote-fs/config.yaml`

```yaml
node:
  id: "instance-a"           # identifiant unique de cette instance
  workspace: "/workspace"    # chemin local à synchroniser

sync:
  root: "shared"             # sous-répertoire synchronisé (défaut: "shared/")
                             # mettre "." pour tout synchroniser
  chunk_size: 1048576        # 1MB — taille des chunks pour les gros fichiers
  conflict_strategy: "last_write_wins"  # ou "keep_both"
  ignore:
    - ".hive/"
    - "*.tmp"
    - "__pycache__/"

peers:
  # Peers statiques (optionnel — sinon découverte via Portal)
  - id: "instance-b"
    ws_url: "ws://192.168.1.x:8010/ws/sync"
    api_url: "http://192.168.1.x:8010"

portal:
  enabled: true
  api_url: "http://core:8000"   # URL interne du service core
  device_group: "hiveflows"     # filtre les devices à syncer

webdav:
  enabled: false
  path: "/dav"
  port: 8010
```

---

## Protocole de sync

### 1. Connexion initiale (handshake)

Quand deux instances se connectent via WebSocket :

1. **A → B** : `{"type": "HELLO", "node_id": "instance-a"}`
2. **B → A** : `{"type": "MANIFEST", "files": [{"path": "...", "hash": "sha256...", "mtime": 123}]}`
3. **A → B** : `{"type": "MANIFEST", "files": [...]}`
4. Chaque côté calcule le delta et demande les fichiers manquants/modifiés via REST.

### 2. Sync temps réel (watchdog → WebSocket)

Quand un fichier change localement :

```json
{"type": "FILE_CHANGED", "path": "notes/todo.md", "hash": "abc123", "mtime": 1713600000}
```

Le peer reçoit l'event, compare son hash local, et si différent déclenche un `GET /files/{path}`.

### 3. Transfert de fichiers (REST + chunked)

```
GET  /files/{path}              → télécharge le fichier (stream)
PUT  /files/{path}              → upload (stream, chunk par chunk)
DELETE /files/{path}            → suppression
GET  /files/manifest            → liste complète {path, hash, mtime}
GET  /files/{path}/hash         → hash seul (vérification rapide)
```

### 4. Gestion offline (SQLite journal)

Si un peer est déconnecté, les changements sont mis en queue dans SQLite.  
Au reconnect, le peer envoie `{"type": "CATCH_UP", "since": <timestamp>}`.  
Le nœud local rejoue la queue depuis ce timestamp.

### 5. Conflits

- **last_write_wins** (défaut) : le fichier avec le `mtime` le plus récent gagne.
- **keep_both** : le fichier local devient `file.conflict-instance-a` et le distant écrase `file`.

---

## Base de données SQLite

```sql
-- Journal des opérations
CREATE TABLE sync_log (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    operation TEXT NOT NULL,  -- 'create' | 'update' | 'delete'
    hash TEXT,
    mtime INTEGER,
    node_id TEXT,
    synced INTEGER DEFAULT 0, -- 0 = en attente, 1 = propagé
    created_at INTEGER
);

-- Manifest local (cache)
CREATE TABLE file_manifest (
    path TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    mtime INTEGER NOT NULL,
    size INTEGER,
    updated_at INTEGER
);

-- Peers connus
CREATE TABLE peers (
    id TEXT PRIMARY KEY,
    ws_url TEXT,
    api_url TEXT,
    last_seen INTEGER,
    status TEXT  -- 'connected' | 'offline'
);
```

Init au démarrage : si SQLite absent → scan complet de `/workspace` → peuple `file_manifest`.

---

## Intégration Portal

Le Portal maintient un registre des devices HiveFlows. remote-fs interroge le Portal pour :

1. Lister les devices actifs du groupe `hiveflows`
2. Obtenir leurs endpoints (`ws_url`, `api_url`) via le tunnel Portal
3. Initier les connexions WebSocket

```python
# portal/client.py
async def get_hiveflows_peers(portal_url: str, device_group: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{portal_url}/api/devices", params={"group": device_group})
        return r.json()
```

---

## Blocs d'implémentation

- **Bloc A** — Scaffold : Dockerfile, FastAPI de base, lecture config YAML, init SQLite
- **Bloc B** — Manifest & watcher : scan `/workspace`, calcul SHA-256, watchdog, peuple SQLite
- **Bloc C** — REST API fichiers : GET/PUT/DELETE/manifest, chunked transfer, auth basique
- **Bloc D** — WebSocket sync : connexion, handshake, propagation des events watchdog
- **Bloc E** — Queue offline : SQLite journal, catch-up au reconnect
- **Bloc F** — Intégration Portal : découverte automatique des peers via Portal API
- **Bloc G** — Conflits & robustesse : stratégies, retry, logs
- **Bloc H** — WebDAV (optionnel) : exposition `/workspace` aux clients externes via wsgidav

---

## Non-inclus (scope futur)

- Chiffrement de transit (TLS/WSS) — à activer via reverse proxy (Caddy/nginx)
- Sync sélective par workspace (pas tout `/workspace`)
- Interface dashboard pour visualiser l'état de sync
- Support de plus de 2 instances simultanées (P2P mesh) — l'archi le permet, mais non testé
