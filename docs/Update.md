# Update.md — Plan de match : maintenir HiveFlows à jour

> Objectif : passer de l'installation manuelle (`git clone` + `bootstrap.sh`) à une
> stratégie de mise à jour **élégante, sûre et déclenchable depuis le web UI**, tout en
> traitant correctement les trois cycles de vie distincts qui cohabitent dans le projet.

---

## 0. Les trois couches de mise à jour

Il faut absolument les séparer dans nos têtes — ce sont trois rythmes et trois mécanismes
différents :

| Couche | Quoi | Source | Rythme | Déclencheur |
|--------|------|--------|--------|-------------|
| **1 — Plateforme** | Le cœur HiveFlows (core, worker, beekeeper, nginx, compose, migrations) | `git pull` du repo `HiveFlows` | À chaque release | Bouton web UI / `update.sh` |
| **2 — Apps** | Les apps de `apps/*` (fabric, jupyter, silverbullet, node-red, whisper…) | Images tierces (`pull`) **ou** build local depuis `apps/{slug}/Dockerfile` | Indépendant, par app | Action opt-in par app, par workspace |
| **3 — Packages Python** | Dépendances de `core/requirements.txt`, `workers`, `skills` | PyPI → commit dans le repo | Au besoin (dev) | Skill `python-update` côté dev → arrive en prod via la **Couche 1** |

Point clé : **la Couche 3 alimente la Couche 1** (un bump de dépendances finit dans un commit,
donc se déploie via le `git pull` de la plateforme), et **les apps de la Couche 2 vivent dans le
même repo** que la plateforme mais ne doivent **pas** être redéployées automatiquement avec elle
(instances actives + données utilisateur dans des volumes).

---

## 0.5 — Décision fondatrice : images immuables (split dev / prod)

> ✅ **Décidé et implémenté.** Préalable à tout le reste : sans ça, la stratégie de
> versioning et de rollback (§1.4–1.5) ne tient pas.

**Avant** : les Dockerfiles bakent déjà le code (`COPY . .`), mais le compose montait
`./core`, `./workers`, `./beekeeper` par-dessus (bind-mount) + `uvicorn --reload`. Le code
exécuté = l'arbre de travail git, pas un artefact versionné. C'est un pattern **dev** appliqué
au seul environnement de déploiement.

**Après** (split standard dev / prod) :

- `docker-compose.yml` (base, **prod**) : le code vient de l'**image** (immuable). Seules les
  **données** sont montées : `pg_data`, `redis_data`, `agent_workspace`, `workspace_data`,
  `nginx_conf`, + `./skills` (magasin extensible) et `./apps:ro` (sources que beekeeper builde).
  Pas de `--reload`.
- `docker-compose.override.yml` (**dev**, gitignoré) : réajoute le bind-mount du code + `--reload`.
  Compose le fusionne automatiquement. Modèle committé : `docker-compose.override.yml.example`.
- `bootstrap.sh` : `HIVE_DEV=1 ./scripts/bootstrap.sh` crée l'override → mode dev. Par défaut = prod.

⚠️ L'override **ne doit jamais atteindre un serveur de prod** (il réactiverait le bind-mount) →
d'où le gitignore. Le serveur actuel `192.168.1.10` reste en mode DEV (HIVE_DEV=1) tant qu'il
sert de machine de dev ; le futur 2e serveur tournera en mode PROD (images taggées).

**Conséquence sur cette stratégie** : en prod, l'« anatomie d'un update » du §1.1 (l'arbre de
décision selon le diff) **disparaît** au profit d'une règle unique — *nouvelle image taggée +
`up -d`* — et le rollback devient *redéployer le tag précédent*. Le §1.1 ci-dessous ne décrit donc
plus que le **mode DEV** (bind-mount, itération rapide par rsync + restart).

---

## 1. Couche 1 — Mise à jour de la plateforme

### 1.1 Anatomie d'un update — **mode DEV uniquement**

En mode dev (bind-mount actif), le code est monté en volume. Tout n'a pas besoin du même
traitement après un `git pull`/rsync ; le moteur lit le diff et agit au minimum :

| Ce qui a changé dans le diff | Action requise (dev) |
|------------------------------|----------------|
| `templates/`, `static/` uniquement | **Rien** — volume monté, déjà live |
| Code Python (`core/`, `workers/`, `beekeeper/`, `skills/`) | `docker compose restart core worker beekeeper` |
| `requirements.txt` ou un `Dockerfile` de service | `docker compose up -d --build <service>` |
| Nouvelle migration Alembic (`core/alembic/versions/`) | `alembic upgrade head` (après le restart) |
| `docker-compose.yml` | `docker compose up -d` (recreate) |
| `.env.example` | **Alerter** : nouvelle variable à reporter manuellement dans `.env` (cf. 1.5) |

**En mode PROD**, tout ceci se réduit à : `docker compose up -d --build` (ou pull du tag) +
`alembic upgrade head` + healthcheck. Un seul chemin, déterministe.

> ℹ️ À propos de `.env` : il est injecté via `env_file:`, donc un changement de variable exige un
> `docker compose up -d` (recreate), pas un simple `restart` — c'est le comportement normal de Docker,
> pas un bug. La règle simple : **`update.sh` utilise toujours `up -d`**, jamais `restart` nu.
> Seul résidu réellement manuel : reporter une **nouvelle** variable de `.env.example` vers `.env` (§1.5).

### 1.2 `scripts/update.sh` — le moteur

On crée un pendant de `bootstrap.sh`. Idempotent, sûr, ré-exécutable. Squelette :

```
scripts/update.sh
  1. acquérir un lock (flock) — empêche deux updates concurrents
  2. git fetch + déterminer le diff entre HEAD et origin/main (ou le tag cible)
  3. pg_dump de sauvegarde (cf. 1.5) avant toute migration
  4. git pull
  5. analyser le diff → décider des actions (table 1.1)
  6. docker compose up -d --build / restart selon le diff
  7. alembic upgrade head si nouvelles migrations
  8. healthcheck /health (réutiliser la boucle de bootstrap.sh)
  9. si échec → rollback (cf. 1.5) ; libérer le lock
```

Toute la logique de décision vit **dans le script**, pas dans l'UI ni dans le container core.
L'UI ne fait que le déclencher et afficher la progression.

### 1.3 Déclenchement depuis le web UI — l'approche recommandée

Le défi : le container `core` ne peut pas faire `git pull` + `docker compose` sur l'hôte tout seul
(il ne voit qu'`/app`, pas le repo complet ni le daemon Docker), et un service ne peut pas
proprement se redémarrer lui-même en plein milieu d'un `compose up`.

**Recommandation : faire de `beekeeper` l'orchestrateur de self-update.** Il a déjà tout ce qu'il faut :

- le socket Docker monté (`/var/run/docker.sock`)
- le pattern Redis pub/sub de progression (déjà utilisé pour `app_pull`)
- une API authentifiée (`X-Beekeeper-Key`)
- des background tasks

Ce qu'il faut ajouter :

1. **Monter le repo complet** dans beekeeper en lecture-écriture : `~/hiveflows:/hive` (en plus du
   `./apps:/apps:ro` actuel).
2. **Ajouter `git` + le plugin `docker compose`** à l'image beekeeper. Compose v2 parle au daemon
   via le socket → `docker compose -f /hive/docker-compose.yml up -d --build` tourne contre le
   daemon de l'hôte. ✅
3. **Endpoint `POST /self/update`** sur beekeeper qui lance `scripts/update.sh` en background et
   **stream la progression sur un canal Redis** (`hive:platform:update`).
4. **Côté core** : un bouton dans le dashboard admin → appelle beekeeper → relaie la progression au
   navigateur via **SSE** (mécanisme déjà en place).

Flux complet :

```
[Web UI bouton "Mettre à jour"]
        │  (core, route admin)
        ▼
[core] ──POST /self/update──► [beekeeper]
                                   │ lance scripts/update.sh (background)
                                   │ publie la progression ──► Redis: hive:platform:update
[core] ◄──SSE──────────────────────┘ (relai live des logs vers le navigateur)
```

> 🔪 **Le piège du "self-restart"** : si l'update doit redémarrer **beekeeper lui-même**, la commande
> qui tourne dans beekeeper se fait tuer. Mitigation : update.sh restart `core` + `worker` normalement,
> et traite beekeeper **en dernier** via un mini-helper détaché (`nohup docker compose up -d beekeeper`
> lancé en arrière-plan juste avant de rendre la main), ou n'auto-update beekeeper que quand son propre
> Dockerfile/requirements a changé.

**Alternatives évaluées (et pourquoi non) :**

- *Service systemd `hive-updater` sur l'hôte* qui watch un sentinelle écrit par core. Plus robuste pour
  le self-restart, mais ajoute une dépendance hors-Docker à installer/maintenir → contre l'esprit
  "tout passe par Docker". À garder en réserve si beekeeper-orchestrator pose problème.
- *Core fait tout via le socket Docker SDK* : le SDK sait restart/rebuild des containers individuels
  mais ne reproduit pas la sémantique `compose` (réseaux, dépendances, recreate sur changement d'env).
  Trop fragile.

### 1.4 Versioning & indicateur "à jour / en retard"

Aujourd'hui : aucun fichier `VERSION`. On s'appuie sur **git** :

- **Releases = tags semver** (`v1.4.0`) poussés sur le Gitea. `update.sh` peut cibler `origin/main`
  (rolling) **ou** le dernier tag (releases stables) — choix à trancher (§4).
- **Indicateur dashboard** : `git fetch` + comparaison `HEAD` vs `origin/main` →
  affiche `à jour` ou `N commits / X versions en retard` + le message du dernier commit/tag.
- Exposer ça via `GET /health` enrichi ou un `GET /version` (commit court, tag, date de build).

### 1.5 Sécurité, sauvegarde, rollback

- **Lock** : `flock` sur un fichier → un seul update à la fois.
- **Sauvegarde DB avant migration** : `pg_dump` dans un volume daté, avant `alembic upgrade head`.
  C'est le filet de sécurité principal (les migrations Alembic ne sont pas toujours réversibles).
- **Rollback plateforme** : `git checkout <tag précédent>` + rebuild + (si besoin) `alembic downgrade`
  / restore du dump. À scripter dans `update.sh --rollback`.
- **Réconciliation `.env`** : si `.env.example` a gagné des variables, l'update **s'arrête** et liste
  le diff `.env.example` vs `.env` à reporter à la main (puis `docker compose up -d`).
- **Mode maintenance** (optionnel, plus tard) : page nginx "mise à jour en cours" le temps du restart.

---

## 2. Couche 2 — Mise à jour des apps (`apps/*`)

**Séparé volontairement** de la plateforme : ce sont des instances par workspace, souvent avec des
données utilisateur dans des volumes. On ne les redéploie **jamais** automatiquement avec un update
plateforme.

Deux familles, déjà gérées par beekeeper :

- **Images tierces** (silverbullet, node-red, jupyter, whisper, piper…) : mise à jour = `docker pull`
  d'un tag plus récent + recreate du container. Beekeeper a déjà `POST /apps/pull` avec progression
  Redis. Versionnage via **tags d'image** dans le `manifest.yaml`.
- **Apps buildées localement** (fabric, custom — `apps/{slug}/Dockerfile`) : leur code arrive via le
  `git pull` de la **Couche 1** (puisque `apps/` est dans le repo). Mais l'image n'est reconstruite
  que sur demande → `build_image_local()` existe déjà dans `beekeeper/services/container_manager.py`.

Plan pour la Couche 2 :

1. **Détection de mise à jour dispo** : dans le catalogue d'apps du dashboard, badge "MAJ dispo" quand
   - image tierce : digest du tag distant ≠ digest local, **ou**
   - app locale : le `Dockerfile`/sources sous `apps/{slug}/` ont changé depuis le dernier build.
2. **Action opt-in par app** (et idéalement par instance/workspace) : "Mettre à jour cette app" →
   pull/rebuild + recreate du/des container(s), **en préservant les volumes de données**.
3. **Avertir si des instances tournent** : un update d'app = downtime de cette app pour ce workspace.
   Confirmation explicite.
4. `manifest.yaml` porte une **version** par app → affichage clair "installé vs disponible".

> En résumé : la plateforme et les apps partagent le même `git pull`, mais l'application effective
> d'une nouvelle version d'app reste **manuelle, granulaire et réversible**.

> ✅ **Fait (commit `3ee0006`).** Détection par **version de manifest** : `app_instances.installed_version`
> (migration 023, backfill) comparé à `app_catalog.version` → badge « MAJ vX » sur la carte d'app.
> beekeeper `POST /apps/update` (rebuild local **ou** pull selon présence d'un Dockerfile → remove →
> recreate, **volumes de données préservés**) et `POST /apps/sync-catalog` (re-sync manifests sans
> restart). UI apps : badge + bouton « Mettre à jour » + « Vérifier les MAJ ». Validé end-to-end sur .72
> (silverbullet pull+recreate, données conservées). Workflow : dev bumpe `version:` du manifest → commit →
> Couche 1 livre → « Vérifier les MAJ » re-sync → badge → bouton par app.

---

## 3. Couche 3 — Packages Python : est-ce pertinent ici ?

**Oui, mais comme amont, pas comme mécanisme runtime séparé.** À clarifier dans ce doc justement pour
éviter la confusion.

- Le skill **`python-update`** (déjà installé) est un workflow **côté développeur** : check PyPI →
  backup `requirements.txt` → bump → rebuild `--no-cache` → tests → rollback si cassé → rapport daté.
- Son output est un **commit** (`requirements.txt` modifié). Ce commit, une fois poussé sur le Gitea,
  est livré aux serveurs **par la mise à jour de la Couche 1** (le `git pull` voit `requirements.txt`
  changé → rebuild — exactement la ligne "requirements.txt → up -d --build" de la table 1.1).

Donc : **aucun mécanisme runtime dédié aux packages Python n'est nécessaire.** Le seul lien à acter :

```
[dev local] skill python-update ──commit──► [Gitea] ──git pull (Couche 1)──► [serveur] rebuild
```

À documenter pour que ce soit limpide : "pour mettre à jour les dépendances en prod, on ne touche pas
au serveur — on bump via le skill, on commit, et l'update plateforme s'en charge."

---

## 4. Décisions à trancher

1. **Cible des updates plateforme** : rolling sur `origin/main`, ou releases taguées semver ? (Je
   recommande des **tags** pour la prod — plus de contrôle, rollback trivial.)
2. **Orchestrateur** : beekeeper (recommandé, réutilise l'existant) vs service `hive-updater` systemd
   hôte (plus robuste pour le self-restart de beekeeper) ?
3. **Granularité Couche 2** : update par app (catalogue) seulement, ou aussi par instance/workspace ?
4. **Auto vs manuel** : update plateforme toujours déclenché à la main depuis l'UI, ou option
   "auto-update sur nouvelle release" (avec fenêtre de maintenance) ?
5. **CI/CD futur** : la note infra mentionne un 2e serveur + pipeline "deploy on commit". Le
   `update.sh` conçu ici en est la **brique de base** réutilisable par le futur pipeline.
   → détaillé en **§6** (build seulement nos images, pull pour Docker Hub, archi CPU unique).

---

## 5. Plan de match — phasage proposé

- **Étape 1 — `scripts/update.sh`** : ✅ **fait** (commit `126be54`). Moteur CLI mode-aware (détecte
  dev/prod via présence de l'override), lock `flock`, fetch, diff→build/up, `pg_dump` avant migrations,
  `alembic upgrade head`, healthcheck, `--rollback`, `--force`, `--strict-env`. Validé end-to-end sur
  le node prod .72 (idempotent + chemin complet).
- **Étape 2 — `GET /version` + indicateur dashboard** : afficher version courante et "MAJ dispo".
- **Étape 3 — Self-update via beekeeper** : ✅ **fait** (commit `3a53145`). Repo monté à
  l'identique + git/docker/compose dans l'image beekeeper ; `POST /self/update` lance
  `update.sh --exclude-beekeeper` (core+worker seulement → pas de self-restart), buffer de log +
  `GET /self/update/status` ; core relaie en SSE vers la page `/dashboard/system` (version + bouton +
  log live). `GET /self/version` montre l'état « à jour / N commits en retard ». Validé end-to-end
  sur .72. Vars requises : `HIVE_REPO_HOST_DIR`, `HIVE_SSH_DIR` (posées par bootstrap).
  Note : Étape 2 (indicateur version) absorbée ici via `/self/version`.
- **Étape 4 — Couche 2 (apps)** : ✅ **fait** (commit `3ee0006`) — badge « MAJ vX » (détection par
  version de manifest) + action opt-in pull/rebuild + recreate par app, volumes préservés. Validé sur .72.
- **Étape 5 — Doc & garde-fous** : réconciliation `.env`, mode maintenance, procédure rollback
  documentée ; acter le lien Couche 3 (python-update).

---

## 6. À régler plus tard — pipeline de build d'images (CI/CD)

> 💭 Réflexion à approfondir, non bloquante pour les étapes 1→5. Le découplage code/image
> (§0.5) en est le prérequis : il rend ce pipeline possible.

Une fois les images devenues l'artefact de déploiement, la question naturelle est : **qui les
build, et où ?** Aujourd'hui le build se fait sur la machine cible (`docker compose up -d --build`).
À terme, un pipeline CI/CD devrait automatiser ça pour ne plus jamais builder sur un serveur de prod.

**Distinction essentielle — toutes les images ne se buildent pas :**

| Type d'image | Origine | Action du pipeline |
|---|---|---|
| **Tierces** (Docker Hub : silverbullet, node-red, jupyter, postgres, redis…) | déjà publiées | **`pull` seulement** — jamais de build, juste télécharger le tag |
| **Plateforme** (core, worker, beekeeper) | nos Dockerfiles | **build + tag + push** vers un registre (Gitea registry ?) |
| **Apps custom beekeeper** (`apps/{slug}/Dockerfile`, ex. fabric) | nos Dockerfiles | **build + tag + push** au même titre que la plateforme |

Donc le pipeline ne build que **nos** images (plateforme + apps custom) ; les images Docker Hub
ne font que transiter par un `pull`. C'est ce qui distingue clairement la Couche 1/Couche 2 « build »
de la Couche 2 « pull » (cf. §2).

**Esquisse de pipeline (à concevoir) :**

```
[commit/tag sur Gitea] ──► [CI] build core/worker/beekeeper + apps custom modifiées
                                 │ tag = version (semver ou SHA)
                                 │ push ──► registre (ex. Gitea container registry)
                                 ▼
[serveur prod] ──► update.sh : docker compose pull (au lieu de --build) + up -d
```

`update.sh` (étape 1) est conçu pour absorber ça : en prod on remplace `--build` par un `pull` du
tag depuis le registre — le reste (migrations, healthcheck, rollback) est identique.

**Contraintes / décisions de départ :**

- **Architecture CPU unique pour l'instant** : on build et on ne supporte que l'archi des serveurs
  HiveFlows (x86-64 / `linux/amd64`). **Pas de multi-arch** (pas d'ARM ni autre) → pas de
  `docker buildx --platform` multi-cible, builds plus simples et plus rapides. À réévaluer seulement
  si un futur node tourne sur une archi différente.
- **Registre à choisir** : le Gitea self-hosted expose-t-il un container registry ? Sinon, registre
  local sur le serveur, ou Docker Hub privé.
- **Quelles apps rebuild** : seulement celles dont le `Dockerfile`/sources ont changé dans le commit
  (détection par diff de `apps/{slug}/`), pas tout le catalogue à chaque fois.
- **Lien avec l'existant** : `build_image_local()` (beekeeper) reste le fallback "build sur la cible"
  tant que le pipeline n'est pas en place — il ne disparaît pas, il devient le mode dégradé.
