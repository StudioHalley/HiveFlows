je sais pas trop comment on peut faire ca... mais j'aimerais intégré une nouvelle app "backend", basé sur le projet Fabric.

Ce projet a deja un docker :

https://github.com/danielmiessler/Fabric?tab=readme-ov-file#docker

et expose un api rest :
https://github.com/danielmiessler/Fabric?tab=readme-ov-file#rest-api-server

par contre, on a pas publish redis ... 


aussi, ce projet utilise les LLM... il y a un fichier de config (qui devra se trouver dans le workspace ./hive/fabric) .. idealement faudrait pas avoir a le configuré, et utilisé la config (llm provider/ model) de l'agent qui fait la demande a l'app.

Aussi, ce projet utilise plein de "pattern" ce sont des prompts qui vise un certain résultat. il faudrait que le manifest expose les tools qu'on peut faire avec fabric. Par contre ca sera pas évident, car je ne connais pas tous les patterns du projet fabric... aussi de version en version, j'imagine que ca peut evoluer... faudrait penser a un mecanisme de mettre a jour le manifest... 

---

## Réflexions — évaluation initiale (2026-04-09)

### 1. Redis — pas un vrai problème

Redis n'est pas exposé en port sur l'hôte, mais il est accessible sur le réseau Docker interne (`redis:6379`). Fabric dans son container n'a qu'à se connecter via le hostname du service. Il suffit de le mettre sur le même réseau Docker que nos autres services (le réseau `hive` ou `apps`). C'est exactement comme ça que les autres apps backend fonctionnent déjà. Pas de port à publier.

### 2. Config LLM — le vrai défi architectural

C'est le point le plus délicat. Fabric a sa propre gestion de config LLM (fichier `~/.config/fabric/.env` dans le container). Plusieurs approches possibles :

**a) Config statique montée en volume** — On génère le fichier de config Fabric à partir de notre `.env` global (OPENROUTER_API_KEY, modèle par défaut) et on le monte dans le container. Simple, mais c'est une double-config avec un risque de désynchronisation.

**b) Variables d'environnement au lancement** — Fabric accepte des env vars (`DEFAULT_MODEL`, `OPENAI_API_KEY`, etc). On peut les injecter depuis notre docker-compose avec les mêmes valeurs que Core. Mieux que (a) car centralisé dans le compose.

**c) Per-request via l'API REST** — Si l'API REST de Fabric permet de spécifier le modèle et la clé par requête, c'est l'idéal : l'agent qui appelle choisit son provider/modèle. À vérifier dans la doc de l'API. Ce serait la solution "propre" car ça respecte le principe que c'est l'agent qui décide de sa config LLM, pas l'app.

**d) Proxy LLM** — On pourrait configurer Fabric pour pointer vers un endpoint interne à nous qui route vers OpenRouter avec les bons credentials. Over-engineered pour l'instant, mais intéressant à long terme si on a plusieurs apps qui consomment des LLMs.

**Recommandation** : commencer par (b), vérifier si (c) est possible. Si oui, (c) est la cible.

### 3. Patterns → Tools — le problème du manifest dynamique

C'est le problème le plus intéressant. Fabric a ~150+ patterns (extract_wisdom, summarize, create_tags, improve_writing, etc.) et ça évolue à chaque release. Trois stratégies :

**a) Un tool par pattern (manifest statique)** — On liste manuellement les patterns utiles dans le manifest. Pro: l'agent sait exactement ce qu'il peut faire. Con: maintenance lourde, on rate les nouveaux patterns.

**b) Meta-tool unique `fabric_run(pattern, input)`** — Un seul tool dans le manifest qui accepte un nom de pattern + le texte. L'agent doit "connaître" les patterns existants (via le system prompt ou une liste). Pro: manifest stable, jamais à mettre à jour. Con: l'agent a besoin d'une liste de patterns quelque part.

**c) Manifest auto-généré (discovery)** — Au démarrage du container Fabric (ou via un cron), on interroge `GET /patterns` de l'API REST, et on génère/met à jour le manifest automatiquement. Pro: toujours à jour. Con: complexité, le ManifestLoader doit supporter le rechargement dynamique.

**Recommandation** : approche hybride **(b) + liste dynamique**. Un meta-tool `fabric_run` dans le manifest, et un endpoint ou mécanisme qui expose la liste des patterns disponibles. L'agent peut d'abord lister les patterns (`fabric_list_patterns`), puis en exécuter un (`fabric_run`). Deux tools stables, zéro maintenance quand Fabric évolue. C'est un peu le même pattern que "list files" + "read file" — on donne à l'agent la capacité de découvrir plutôt que de tout hardcoder.

### 4. Intégration Beekeeper — ça colle bien

Le modèle est déjà là : un manifest.yaml qui déclare le container, le healthcheck, les tools exposés. Fabric rentre bien dans ce cadre :

```
# esquisse, pas un vrai manifest
name: fabric
image: danielmiessler/fabric:latest
ports: ["8080"]  # interne seulement
networks: [hive]
env_from: [core]  # hérite OPENROUTER_API_KEY etc.
healthcheck: /health
tools:
  - fabric_list_patterns  # GET /patterns
  - fabric_run            # POST /run {pattern, input, model?}
```

### 5. Questions ouvertes à creuser avant de coder

- **L'API REST de Fabric permet-elle de passer le modèle/provider per-request ?** C'est le facteur décisif pour la stratégie LLM.
- **Comment Fabric gère-t-il les gros inputs ?** (articles longs, transcripts YouTube) — limites de l'API REST vs CLI ?
- **Persistence** — Fabric garde-t-il un état (historique, sessions) ou est-il stateless ? Si stateless, c'est plus simple.
- **Patterns custom** — On voudra sûrement ajouter nos propres patterns. Comment les monter dans le container ? (probablement un volume sur le dossier patterns)
- **Versions** — Quelle stratégie d'update du container Fabric ? Pin une version ou follow latest ?

---

## Décision architecture — Wrapper CLI (2026-04-09)

**On n'utilise PAS le serveur REST natif de Fabric.** On fait notre propre service FastAPI (même pattern que yt-dlp) qui appelle le CLI Fabric en subprocess.

### Pourquoi

- **Gros inputs** : stdin pipe vers le CLI, pas de limite HTTP body
- **Config LLM per-request** : on passe `--model vendor/model` en flag CLI, l'agent appelant choisit son modèle
- **Un seul serveur HTTP** : le nôtre. Fabric = juste un binaire dans le container
- **CLI = contrat stable** de Fabric, moins fragile que leur API REST entre versions

### Architecture

```
Dockerfile:  FROM kayvan/fabric:latest
             + pip install fastapi uvicorn redis
             + COPY app.py

app.py:      FastAPI (pattern identique à yt-dlp)
             POST /run      → subprocess fabric --pattern X --model Y < input
             GET  /patterns → ls du dossier patterns
             GET  /result   → poll résultat par task_id
```

### Décisions techniques

1. **Flags CLI** — À confirmer en lisant le README du projet Fabric (--pattern, --model, etc.)
2. **Output** — Le résultat de chaque exécution sera écrit dans un fichier sur le workspace (pas retourné inline dans la réponse HTTP). L'agent récupère le chemin du fichier.
3. **Async via Redis** — On n'attend PAS la fin de l'exécution. Le POST /run lance le subprocess en background, retourne un task_id immédiatement. La complétion est signalée via Redis pub/sub (`hiveflows:tasks:{task_id}:progress`), exactement comme yt-dlp.
4. **Config LLM** — Le `.env` Fabric est pré-généré au build avec les credentials OpenRouter par défaut. Mais chaque requête peut override le modèle via le flag CLI `--model`.

### Tools exposés dans le manifest (2 tools stables)

- `fabric_list_patterns` — GET /patterns → retourne la liste des ~251 patterns disponibles
- `fabric_run` — POST /run `{pattern, input, model?}` → lance en background, retourne task_id + chemin du fichier output
- `fabric_get_result` — GET /result?task_id=... → status + chemin fichier résultat


lit le readme pour t'assurer qu'on est dans la bonne track
https://github.com/danielmiessler/Fabric/blob/main/README.md

appelle a partir d'un url :
fabric -u https://github.com/danielmiessler/fabric/ -p analyze_claims

appelle a partir d'un fichier

cat fichier | fabric --pattern summarize

utiliser --output= pour specifier le fichier de sortie (en principe c'est un markdown)

utiliser --model et --vendor pour specifier le model et le provider.  (est-ce qu'on devra faire un match avec notre config actuel ? )

pour l'instant on va hardcodé les pattern dans les tools qu'on espose... ca ne sera pas dynamique, trop compliqué.
extraire la liste de https://github.com/danielmiessler/Fabric/tree/main/data/patterns 

Si dans les prochaines version, il y a des patterne de plus ou de moins... on ajustera rendu la... 