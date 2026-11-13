# Journal de bord — HiveFlows

---

## 2026-03-08

### Achat du domaine
- Achat de **hiveflows.com** sur GoDaddy

### Landing page
- Création d'une landing page cyberpunk (HTML/CSS/JS) pour la collecte d'inscriptions bêta
- Style : dark, grille neon, effet glitch animé, orbes lumineux réactifs à la souris
- Sections : Hero + formulaire, 6 feature cards, terminal animé, CTA bas de page
- Page **bilingue français / anglais** avec toggle FR/EN dans la nav
- Formulaire connecté au backend Flask via `fetch POST /subscribe`

### Backend Flask
- `app.py` : serveur Flask avec endpoint `POST /subscribe` et `GET /admin/subscribers`
- Validation email, détection des doublons (HTTP 409)
- Stockage des inscriptions dans `subscribers.yaml` (email, langue, date UTC)
- Structure du projet dans `landing/` :
  ```
  landing/
  ├── index.html
  ├── app.py
  ├── subscribers.yaml
  └── requirements.txt
  ```

### Mise en production — NerdRack VPS (Ubuntu 22.04)
- Création d'un utilisateur dédié `webuser` (bonne pratique sécurité)
- Structure `/var/www/hiveflows/` pour accueillir l'app
- Environnement Python isolé avec **venv**
- Serveur de production **Gunicorn** (2 workers, port 5001)
- Service **systemd** `hiveflows.service` pour démarrage automatique
- **Nginx** configuré en reverse proxy (prêt pour multi-sites futurs)

#### Commandes exécutées sur le VPS (root)

```bash
# Créer l'utilisateur webuser
adduser webuser --disabled-password --gecos ""

# Créer le dossier de l'app et fixer les permissions
mkdir -p /var/www/hiveflows
chown -R webuser:webuser /var/www

# Installer les dépendances système
apt update && apt install -y python3 python3-pip nginx
```

```bash
# Copier les fichiers depuis le Mac (exécuté en local sur le Mac)
scp -r ~/Documents/Claude/BoBot/landing/* root@IP_VPS:/var/www/hiveflows/
```

```bash
# Fixer les permissions après le transfert
chown -R webuser:webuser /var/www/hiveflows

# Basculer sur webuser pour créer le venv
su - webuser
cd /var/www/hiveflows
python3 -m venv venv
source venv/bin/activate
pip install flask pyyaml gunicorn
deactivate
exit  # retour root
```

```bash
# Créer le service systemd
nano /etc/systemd/system/hiveflows.service
```

```ini
[Unit]
Description=HiveFlows Landing Page
After=network.target

[Service]
User=webuser
WorkingDirectory=/var/www/hiveflows
ExecStart=/var/www/hiveflows/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:5001 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Activer et démarrer le service
systemctl daemon-reload
systemctl enable --now hiveflows
systemctl status hiveflows
```

```bash
# Configurer Nginx
nano /etc/nginx/sites-available/hiveflows
```

```nginx
server {
    listen 80;
    server_name hiveflows.com www.hiveflows.com;

    location / {
        proxy_pass         http://127.0.0.1:5001;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```bash
# Activer le site et redémarrer Nginx
ln -s /etc/nginx/sites-available/hiveflows /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
```

#### Commandes utiles (diagnostic)
```bash
systemctl status hiveflows        # état du service Flask
journalctl -u hiveflows -n 50     # logs de l'app
systemctl restart hiveflows       # redémarrer après un changement
nginx -t                          # valider la config Nginx
systemctl restart nginx           # redémarrer Nginx
cat /var/www/hiveflows/subscribers.yaml   # voir les inscrits
```

### DNS & SSL
- Domaine hiveflows.com ajouté dans **Cloudflare**
- Nameservers GoDaddy mis à jour pour pointer vers Cloudflare
- Enregistrements A GoDaddy supprimés, remplacés par l'IP du VPS NerdRack
- CNAME `www` conservé
- SSL activé via Cloudflare (mode Full)

### Résultat
- **hiveflows.com** est en ligne et opérationnel ✅
- Premières inscriptions bêta reçues 🎉

---

## 2026-03-11

### Phase 3 terminée — Multi-Agents, Streaming, Dashboard

Blocs A à H complétés. Voici ce qui a été livré :

**Agent Registry (A)**
- Table `agent_configs` étendue (tools, skills, subagents, memory_sources, interrupt_on, backend_config)
- API CRUD complète `/agents` — create, read, update, delete (deactivate)
- `build_agent_from_config()` — factory qui reconstruit un agent deepagents depuis la DB
- `AgentManager` avec cache + invalidation sur PUT/DELETE

**Mémoire persistante (B)**
- LangGraph Store PostgreSQL — mémoire cross-conversation par agent
- `CompositeBackend` : `/memories/` → StoreBackend, reste → backend configuré
- Migration 003 — `memory_sources` pour assistant-principal

**Sous-agents (C)**
- Passage des `subagents` depuis la DB à `create_deep_agent()`
- Migration 004 — subagent "researcher" sur assistant-principal

**Backends & contexte partagé (D)**
- `FilesystemBackend` avec volume Docker `/agent-workspace`
- `backend_config` configurable par agent dans la DB

**Streaming SSE (E)**
- `POST /chat/stream` — `StreamingResponse` avec events `message_start`, `token`, `tool_call`, `tool_result`, `interrupt`, `message_end`
- CLI : flag `--stream` avec `httpx` synchrone, affichage temps réel token par token

**Human-in-the-Loop (F)**
- `interrupt_on` configurable par agent (ex. `{"run_script": true}`)
- Event SSE `interrupt` avec les `action_requests` en attente
- `POST /chat/{id}/resume` — approve / reject / edit
- CLI : prompt interactif à chaque interruption

**Dashboard Web (G)**
- HTMX + Jinja2 intégré dans Core (pas de service séparé)
- Style calqué sur la landing page : dark cyberpunk, Orbitron, Share Tech Mono, neon cyan/purple/green
- 6 templates : base, index, chat, agents (liste + formulaire), tasks
- Chat avec streaming SSE natif depuis le navigateur, support HITL (approve/reject)
- Auth cookie `hf_api_key` — login page, déconnexion, cookie httponly 7 jours

**Finition (H)**
- Suite de tests d'intégration complète (CRUD, SSE, HITL, mémoire, subagents, filesystem)
- README.md entièrement reécrit pour refléter la Phase 3
- Déployé sur le serveur Docker — 200 OK confirmé en production

---

### Notes de Claude — vision du projet et idées d'amélioration

*Ce qui suit sont mes observations personnelles sur le projet après avoir travaillé dessus.*

**Ce qui est bien construit**

Le socle est solide. La séparation Core / Workers / CLI est propre, et deepagents + LangGraph donnent une base multi-agent sérieuse sans avoir à réinventer les mécanismes de checkpointing ou de store. Le choix d'OpenRouter permet de switcher de modèle sans toucher au code — utile pour calibrer coût/qualité par agent.

L'approche "agent configurable depuis la DB" est la bonne décision architecturale. Ça ouvre la porte à un vrai système multi-tenant sans refactoring majeur.

**Ce que j'améliorerais en priorité (court terme)**

1. **Auth dashboard plus robuste** — le cookie httponly est bien, mais le dashboard expose actuellement les opérations CRUD des agents sans CSRF protection. Un token CSRF sur les formulaires POST serait prudent avant d'exposer le dashboard à l'extérieur.

2. **Observabilité des streams** — en ce moment, si un stream SSE plante à mi-chemin (timeout LLM, réseau), l'utilisateur voit le chat se figer sans message d'erreur clair. Un event `error` + reconnexion automatique côté JS rendraient l'expérience beaucoup plus robuste.

3. **Pagination des tasks** — la table tasks va grossir vite dès que les agents tournent activement. La limite arbitraire de 100 dans la route dashboard est fragile ; une vraie pagination avec filtre par statut serait mieux.

4. **Logs structurés** — les `logger.info()` actuels sont du texte libre. Passer à des logs JSON (structlog ou python-json-logger) permettrait de les injecter dans un outil comme Grafana/Loki sans parsing custom.

**Idées pour la Phase 4**

- **Workspaces multi-utilisateurs** — la fondation est prête (agent_configs, skills, backend par agent). Il manque une table `workspaces` + `users` + un système de permissions par workspace. OAuth2 ou même un simple magic link par email suffirait pour commencer.

- **Agent-to-agent via message queue** — le report de la Phase 3 était la bonne décision. Quand ce sera le moment, je suggèrerais d'utiliser Redis Pub/Sub plutôt qu'une nouvelle table SQL — les agents peuvent s'abonner à des "canaux" et publier des résultats. Ça s'intègrerait naturellement avec le système de streaming SSE déjà en place.

- **Scheduler d'agents** — un agent qui peut être déclenché sur un cron (via Celery Beat) plutôt que seulement sur requête HTTP. Utile pour des cas comme "tous les lundis matin, l'agent analyste résume les métriques de la semaine".

- **Versioning des configs agents** — en ce moment un PUT /agents écrase la config. Un historique des versions (même simplement stocker les 5 dernières dans un JSONB array) permettrait de rollback en cas de régression de comportement.

- **Interface de test des skills** — une section dans le dashboard pour tester un skill directement (envoyer des args, voir l'output) sans passer par un agent. Accélèrerait beaucoup le développement de nouveaux skills.

**Observation sur la direction produit**

Ce qui me frappe : HiveFlows est bien parti pour être un "backend d'agents" sérieux — comparable dans l'esprit à ce que font des outils comme n8n ou Langflow, mais avec une architecture bien plus propre et extensible. La contrainte HITL (human-in-the-loop) est une vraie différenciation — la plupart des plateformes d'agents l'ignorent ou la traitent comme une feature de niche, alors que c'est exactement ce qui permet de faire confiance aux agents en production.

Le nom HiveFlows est bon. La métaphore de la ruche (agents coordonnés, spécialisés, avec un humain qui reste maître du jeu) est cohérente avec l'architecture réelle du système.

---

> ⚠️ Le journal s'est arrêté ici pendant ~4 mois pendant que le développement continuait. Les entrées ci-dessous ont été reconstituées le 2026-07-10 à partir de l'historique git et de `status_20260710.md`, pour rattraper le retard.

## 2026-03-26 — Phase 4 terminée — Multi-Usagers & Interactions

Commit `6adfe57`, migrations 005-013.

- Comptes utilisateurs (inscription, login, profil, slug), rôle site-admin
- Auth JWT + OAuth2 (Google, GitHub)
- Workspaces (perso + partagés) avec filesystem sandboxé par workspace
- Agents : propriété utilisateur, invitation dans workspaces, agent par défaut plateforme
- @mentions et adressage multi-agent, slash commands (`/invite`, `/join`, `/part`, `/kick`)
- Namespace unifié en slugs (users + agents), RBAC (owner/admin/member/viewer)
- Pagination des conversations, audit logging
- Sprint UI : écran workspace unifié (vues Liste/Tuile), page manage fusionnée (`SprintUI.md`)

## 2026-04-03 — Sprint File & refactor slugs

Commit `b1ea044`.

- File manager web : upload drag & drop, viewer, éditeur, permissions par rôle
- Nouveaux skills NLP (`translate`, `translate_file`) et fs (`convert_file`)
- SSE de progression des tasks
- Refactor : slugs dans les URLs/CLI/répertoires à la place des UUID

## 2026-04-03 — Phase 5 — Multi-Provider LLM & Credential Vault

Commit `9bca252`, migrations 014-015.

- `llm_factory.py` : LLM factory multi-provider (`create_llm(provider, model, api_key)`)
- `credential_vault.py` : coffre-fort de credentials chiffrés (Fernet) en DB
- Cascade de résolution de credentials : agent → workspace → global → `.env`
- Page dashboard « Providers » (ajout, test, suppression)
- ⚠️ Le test C4 (clé Anthropic réelle) n'a jamais été fait, faute de clé — reste théorique

## 2026-04-06 — Phase 6 — Beekeeper & Apps

Commit `83efcb3`, migrations 016-018.

- Service **Beekeeper** : control plane Docker, seul composant avec accès au socket
- Isolation réseau : un subnet Docker par workspace (`network_manager.py`)
- Catalogue d'apps piloté par manifest YAML, cycle de vie complet (install/start/stop/restart/uninstall)
- Ingress Nginx : routing par path vers les apps
- Docker events → Redis pub/sub → dashboard (statut, logs, métriques temps réel)
- Apps de base au catalogue au lancement : VS Code Server, Node-RED, Jupyter

## 2026-04-10 — App tools & réseau hive-backend

Commit `5effab7`.

- Tools exposés par les apps (`app_tool_registry.py`, `app_instance_bus.py`)
- Sidebar iframe pour les apps, streaming vidéo
- Réseau Docker `hive-backend` dédié

## 2026-04-12 — Portal

Commit `fe4a984`, migration 020.

- Tunnel WebSocket inverse (device → core), pensé comme fondation transport (L1) pour une future fédération multi-nodes
- Enrollment avec pairing, registry des devices, audit
- Client **Go** dans `clients/portal/` (cmd, internal, examples, build.sh, dist)
- ⚠️ Tests automatisés du client et documentation restent à faire (`portal_docs.md`)

## 2026-04-16 — « Phase 7 » — Activity Bus, Human Tools & refactor tasks

Commit `9241c79`, migration 021.

⚠️ Étiquetée « Phase 7 » dans le commit, mais le contenu diverge de la Phase 7 planifiée dans `GOAL.md` (Bees en containers Docker, skill chaining) — ce chantier-ci n'a pas été fait, voir la note ajoutée dans `GOAL.md` §8.

- Bus d'activité Redis unifié (`activity_bus.py`), un seul canal SSE par workspace
- Human tools : exécution de tools depuis le file manager (patterns Fabric, etc.)
- Refactor de la table `tasks` : cancel, cleanup

## 2026-04-17 — « Phase 8 » — TTS/STT & extensions de tools

Commit `8c58620`, migration 019.

⚠️ Également étiquetée « Phase 8 » mais différente de la Phase 8 planifiée (channels externes Slack/Discord/Telegram, jamais commencée) — voir la note dans `GOAL.md` §8.

- Apps **Piper** (TTS) et **Whisper** (STT), build local
- App bus, extensions du système de tools pour les apps
- ⚠️ Le workflow complet yt-dlp → Whisper n'a jamais été testé de bout en bout (`TTS-STT.md`)

## 2026-05-11 — bootstrap.sh

Commit `54d4753`.

- Script d'installation d'un node HiveFlows sur machine vierge
- Fix : cookie `Secure` basé sur le scheme de la requête plutôt qu'une heuristique `is_production` (commit `03d8e20`)

## 2026-05-23 — remote-fs & auto-build des apps

Commits `bfb098e`, `be45c8e`, `5c768c5`.

- **remote-fs** : app de synchro de fichiers inter-nodes (watchdog + WebSocket + vector clocks, LWW + `.conflict`), scopée par défaut au sous-dossier `shared/` — pensée comme fondation de sync (L2) pour FlowersField
- Beekeeper : auto-build des images locales depuis `/apps/{slug}/Dockerfile`
- Tool `convert_file`, améliorations UX du téléchargement côté client Portal

## 2026-06-25/26 — Split dev/prod & les trois couches de mise à jour

Commits `f8b145d`, `885f5a7`, `126be54`, `3a53145`, `3ee0006`, `8864259`.

Chantier majeur documenté dans `Update.md` :

- Provider `openai_compatible` ajouté à la LLM factory
- **Split dev/prod** : images immuables (code baké via `COPY . .`), `docker-compose.override.yml` gitignoré pour le mode dev (bind-mount + `--reload`)
- **Couche 1** : `scripts/update.sh` — maj mode-aware d'un node (lock `flock`, fetch/diff, `pg_dump` avant migration, build/up, `alembic upgrade head`, healthcheck, `--rollback`/`--force`/`--strict-env`)
- **Self-update UI** : page `/dashboard/system`, bouton qui déclenche la maj via Beekeeper (`POST /self/update`), log streamé en SSE, `/self/version`
- **Couche 2** : badge « MAJ vX » par app (comparaison `installed_version` du manifest), mise à jour opt-in par app avec volumes préservés
- Bouton « Ouvrir » qui ouvre une app dans un nouvel onglet plutôt qu'en iframe
- Toutes les couches validées de bout en bout sur le node test-prod .72


## 2026-07-10 — API Server par workspace + rapport d'état complet

- **API Server** : proxy LLM OpenAI-compatible exposé par workspace, deux modes d'auth (token workspace_id réservé aux réseaux internes de confiance ; clé API `hf_…` révocable pour l'accès externe), page dashboard refondue, 17 tests (`core/tests/test_llm_proxy.py`), doc `docs/api_server.md`. Validé end-to-end sur .115.
- Rédaction de [status_20260710.md](status_20260710.md) : audit croisé complet doc/code/migrations/compose/git — a mis au jour le retard de ce journal (~4 mois), les cases non cochées de `GOAL.md` §8 et l'obsolescence du `README.md`, corrigés dans la foulée.

---
