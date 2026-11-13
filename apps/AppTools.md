# HiveFlows — Standard : App Tools

Ce document définit le standard pour créer une **App Tool** dans HiveFlows.

Une App Tool est une app Docker (`apps/{slug}/`) qui expose une **API REST** consommée par les agents LangChain — sans modification du core et sans rebuild, grâce au système de **registry automatique**.

---

## Concept

```
manifest.yaml  ──► manifest_loader (beekeeper)  ──► app_catalog DB (tools_schema)
                                                          │
                                                          ▼
Agent invoqué  ──► factory.py  ──► AppToolRegistry  ──► LangChain tools dynamiques
                                         │
                                         ▼
                              GET container_ip depuis app_instances DB
                              POST http://hive-app-{ws}-{slug}:{port}/endpoint
```

**La clé** : le manifest déclare les tools. Le registry les crée dynamiquement au runtime (Pydantic `create_model`). Aucun changement de code dans le core pour ajouter une nouvelle app.

---

## Types d'apps

| Type | Usage | Ingress |
|------|-------|---------|
| `persistent` (UI) | Apps avec interface utilisateur (VSCode, Node-RED) | `iframe` ou `link` |
| `backend` | Apps sans UI, consommées par les agents | `none` |

Les App Tools sont **toujours `type: backend`** avec **`ingress: none`**.

---

## Structure du dossier

```
apps/{slug}/
  manifest.yaml       ← définition complète (app + tools)
  Dockerfile          ← image custom ou référence image publique
  app.py              ← serveur FastAPI (point d'entrée)
  requirements.txt    ← dépendances Python
```

---

## Manifest complet

```yaml
name: Mon App
slug: mon-app
version: "1.0"
description: "Ce que fait l'app en une phrase"
image: hiveflows/mon-app:latest   # ou image publique
type: backend
ingress: none
port: 8080

resources:
  memory: 256m
  cpus: "0.5"

# ── Section tools (optionnelle — absente = app sans tools LangChain) ─────────
tools:
  - name: nom_du_tool              # snake_case, unique dans le workspace
    description: >
      Description LLM-facing. Soyez précis : l'agent s'en sert pour décider
      quand utiliser ce tool. Mentionnez ce que ça retourne.
    endpoint: /nom_du_tool         # endpoint POST sur le container
    method: POST                   # GET ou POST (défaut: POST)
    async: false                   # true = fire-and-forget (retourne task_id)
    schema:
      param1:
        type: string               # string | boolean | integer | number
        required: true
        description: "Description du paramètre pour le LLM"
      param2:
        type: boolean
        default: false
        description: "Paramètre optionnel"
      param3:
        type: string
        default: "best"
        enum: ["best", "720p", "480p"]   # valeurs autorisées (optionnel)
        description: "Qualité vidéo"

icon: mdi-download
```

---

## Contrat API du container

### Endpoint obligatoire

```
GET /health
→ 200 {"status": "ok"}
```

Beekeeper utilise `/health` pour déterminer que le container est prêt.

### Endpoints tools

Chaque tool déclaré dans le manifest doit avoir son endpoint correspondant.

**Request** (JSON body, champs = schema du manifest) :
```json
{
  "url": "https://youtube.com/...",
  "audio_only": false,
  "workspace_dir": "/workspaces/mon-workspace"
}
```

> `workspace_dir` est **injecté automatiquement** par le registry si l'app est dans un workspace.
> Le container ne doit jamais écrire en dehors de ce path.

**Response sync** :
```json
{
  "status": "success",
  "data": {
    "fichier": "/workspaces/mon-workspace/downloads/video/video.mp4",
    "metadata": { ... }
  }
}
```

**Response async** (si `async: true` dans le manifest) :
```json
{
  "task_id": "abc-123",
  "status": "queued"
}
```

**Response erreur** (HTTP 4xx/5xx) :
```json
{
  "status": "error",
  "error": "Description de l'erreur"
}
```

---

## Progress Redis (apps async)

Si l'app publie une progression, utiliser le channel standard :

```
PUBLISH hiveflows:tasks:{task_id}:progress <JSON>
```

Format du payload :
```json
{"step": "downloading", "percent": 45, "speed": "2.3MiB/s", "eta": "00:02"}
{"step": "processing",  "percent": 80}
{"step": "done",        "percent": 100, "result": { ... }}
{"step": "error",       "percent": 0,   "error": "message"}
```

La connexion Redis est disponible via `REDIS_URL` (variable d'environnement injectée automatiquement).

---

## Output et volumes

- **Écrire uniquement dans `workspace_dir`** (injecté dans la requête par le registry)
- Structure de sortie recommandée : `workspace_dir/{slug-de-l-operation}/`
- Ne jamais écrire dans `/tmp` ou dans l'image — les données disparaissent au restart

```
workspace_dir/
  {operation_slug}/
    metadata.yaml    ← toujours présent, résumé de l'opération
    output.*         ← fichier(s) produit(s)
```

---

## Comment le registry fonctionne

1. **Beekeeper** scanne `apps/*/manifest.yaml` au démarrage → upsert `app_catalog` (inclut `tools_schema`)
2. **factory.py** appelle `AppToolRegistry.get_tools(workspace_id)` lors du build d'un agent
3. Le registry requête `app_instances` pour trouver les apps `running` dans ce workspace
4. Pour chaque app avec `tools_schema`, il crée des `StructuredTool` LangChain dynamiquement via `pydantic.create_model`
5. L'URL de chaque tool est `http://{container_name}:{port}{endpoint}` (résolu via Docker network)
6. Les tools sont appendés à la liste de l'agent — **sans modification du code core**

### Activation par workspace

Un tool n'est disponible que si l'app est **installée et running** dans ce workspace.
Installer une app = démarrer le container = tools disponibles immédiatement.

---

## Checklist — créer une nouvelle App Tool

```
□ Dossier apps/{slug}/ créé
□ manifest.yaml : type=backend, ingress=none, tools: section remplie
□ Dockerfile : image slim, pas de données dans l'image
□ app.py : FastAPI avec /health + tous les endpoints déclarés dans tools:
□ Chaque endpoint accepte workspace_dir dans le body
□ Écriture uniquement dans workspace_dir
□ REDIS_URL lu depuis env (si async/progress)
□ Response format respecté (status/data ou task_id/status)
□ Testé : GET /health → 200, POST /endpoint → réponse valide
□ Image buildée et pushée (ou Dockerfile local pour build beekeeper)
```

---

## Exemple de référence : yt-dlp

```
apps/yt-dlp/
  manifest.yaml     ← type: backend, tools: [download_video]
  Dockerfile        ← python:3.12-slim + ffmpeg + yt-dlp
  app.py            ← FastAPI: /health + /download
  requirements.txt  ← fastapi, yt-dlp, redis, pyyaml
```

**Tool déclaré** : `download_video(url, audio_only, transcript, quality)`  
**Output** : `workspace_dir/downloads/{video_slug}/` avec `metadata.yaml`, `.mp4`, `.mp3`, `transcript.md`  
**Progress** : publish Redis `hiveflows:tasks:{task_id}:progress`

---

## Ce qui n'est PAS une App Tool

- App avec interface utilisateur → `type: persistent`, `ingress: iframe`
- Script ponctuel → utiliser un skill (`skills/tools/`)
- Traitement qui utilise déjà le worker Celery → Celery task (translate, run_script)

La règle : si ça tourne en continu, expose une API, et que les agents l'appellent → App Tool.
