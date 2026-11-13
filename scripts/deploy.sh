#!/usr/bin/env bash
# ============================================================================
# deploy.sh — Déploie HiveFlows sur un serveur distant via SSH
#
# Usage :
#   ./scripts/deploy.sh              # sync + restart services modifiés
#   ./scripts/deploy.sh --rebuild    # sync + rebuild toutes les images Docker
#   ./scripts/deploy.sh --full       # rebuild + migrate + restart tout
#   ./scripts/deploy.sh --dry-run    # affiche ce qui serait fait, sans exécuter
#
# Variables d'environnement (override possible) :
#   SSH_USER   (défaut: $USER)
#   SSH_HOST   (défaut: 192.168.1.10)
#   REMOTE     (défaut: ~/hiveflows)
# ============================================================================
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
SSH_USER="${SSH_USER:-$USER}"
SSH_HOST="${SSH_HOST:-192.168.1.10}"
REMOTE="${REMOTE:-~/hiveflows}"
LOCAL="$(cd "$(dirname "$0")/.." && pwd)"   # racine du repo local

# ── Flags ────────────────────────────────────────────────────────────────────
OPT_REBUILD=false
OPT_FULL=false
OPT_DRY_RUN=false
OPT_NO_MIGRATE=false
OPT_SKIP_BUILD_APPS=false

for arg in "$@"; do
  case "$arg" in
    --rebuild)         OPT_REBUILD=true ;;
    --full)            OPT_REBUILD=true; OPT_FULL=true ;;
    --dry-run)         OPT_DRY_RUN=true ;;
    --no-migrate)      OPT_NO_MIGRATE=true ;;
    --skip-app-builds) OPT_SKIP_BUILD_APPS=true ;;
    --help|-h)
      grep "^#" "$0" | head -20 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────
BOLD='\033[1m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'
YELLOW='\033[0;33m'; RED='\033[0;31m'; RESET='\033[0m'

log()  { echo -e "${CYAN}▸${RESET} $*"; }
ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET} $*"; }
err()  { echo -e "${RED}✗${RESET} $*" >&2; }
hr()   { echo -e "${BOLD}────────────────────────────────────────────${RESET}"; }

ssh_run() {
  # Exécute une commande sur le serveur (ou l'affiche en dry-run)
  if $OPT_DRY_RUN; then
    echo -e "  ${YELLOW}[dry-run]${RESET} ssh ${SSH_USER}@${SSH_HOST} \"$*\""
  else
    ssh "${SSH_USER}@${SSH_HOST}" "$@"
  fi
}

rsync_to() {
  # rsync_to <src> <dest_relative>
  local src="$1" dest="${REMOTE}/${2:-}"
  if $OPT_DRY_RUN; then
    echo -e "  ${YELLOW}[dry-run]${RESET} rsync $src → ${SSH_USER}@${SSH_HOST}:$dest"
  else
    rsync -avz --checksum --delete \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='*.pyo' \
      --exclude='.pytest_cache' \
      --exclude='venv/' \
      --exclude='venvmac/' \
      --exclude='.env' \
      --exclude='*.egg-info' \
      --exclude='.DS_Store' \
      "$src" "${SSH_USER}@${SSH_HOST}:$dest"
  fi
}

# ── Détection des changements (git diff) ─────────────────────────────────────
changed_files() {
  # Fichiers modifiés depuis HEAD (staged + unstaged + untracked dans apps/)
  git -C "$LOCAL" diff --name-only HEAD 2>/dev/null || true
  git -C "$LOCAL" ls-files --others --exclude-standard apps/ 2>/dev/null || true
}

has_changed() {
  # has_changed <prefix> → true si un fichier avec ce préfixe a changé
  changed_files | grep -q "^${1}" 2>/dev/null
}

# ── Début du déploiement ──────────────────────────────────────────────────────
hr
echo -e "${BOLD}  HiveFlows Deploy${RESET}  →  ${SSH_USER}@${SSH_HOST}:${REMOTE}"
$OPT_DRY_RUN && warn "MODE DRY-RUN — aucune commande réelle exécutée"
$OPT_REBUILD  && warn "MODE REBUILD — toutes les images Docker seront reconstruites"
hr

# ── Étape 1 : Sync des sources ────────────────────────────────────────────────
log "Sync des sources..."

# Services core (Python - volume monté, pas de rebuild nécessaire)
rsync_to "$LOCAL/core/"       "core/"
rsync_to "$LOCAL/workers/"    "workers/"
rsync_to "$LOCAL/beekeeper/"  "beekeeper/"

# Skills (montés en volume, disponibles immédiatement)
rsync_to "$LOCAL/skills/"     "skills/"

# Apps (manifests + dossiers apps custom)
rsync_to "$LOCAL/apps/"       "apps/"

# Nginx
rsync_to "$LOCAL/nginx/"      "nginx/"

# docker-compose.yml (racine)
if $OPT_DRY_RUN; then
  echo -e "  ${YELLOW}[dry-run]${RESET} rsync docker-compose.yml → ${SSH_USER}@${SSH_HOST}:${REMOTE}/"
else
  rsync -avz --checksum \
    "$LOCAL/docker-compose.yml" \
    "${SSH_USER}@${SSH_HOST}:${REMOTE}/"
fi

ok "Sources synchronisées"

# ── Étape 2 : Build des images custom apps ────────────────────────────────────
# Chaque sous-dossier de apps/ avec un Dockerfile = image custom à builder sur le serveur

if ! $OPT_SKIP_BUILD_APPS; then
  log "Détection des apps avec Dockerfile..."

  for app_dir in "$LOCAL/apps"/*/; do
    app_slug=$(basename "$app_dir")
    dockerfile="$app_dir/Dockerfile"
    [ -f "$dockerfile" ] || continue

    # Builder si : --rebuild OU fichiers de l'app ont changé
    should_build=false
    $OPT_REBUILD && should_build=true
    has_changed "apps/${app_slug}/" && should_build=true

    if $should_build; then
      log "Build image: hiveflows/${app_slug}:latest"
      ssh_run "cd ${REMOTE}/apps/${app_slug} && docker build -t hiveflows/${app_slug}:latest ."
      ok "Image hiveflows/${app_slug}:latest construite"
    else
      echo "  ↷  apps/${app_slug}/ — inchangé, skip build"
    fi
  done
fi

# ── Étape 3 : Rebuild des services docker-compose si nécessaire ───────────────
log "Mise à jour des services Docker..."

# Détecter quels services ont leur Dockerfile modifié
rebuild_services=()

if $OPT_REBUILD; then
  rebuild_services=(core worker beekeeper)
else
  has_changed "core/Dockerfile"       && rebuild_services+=(core)
  has_changed "workers/Dockerfile"    && rebuild_services+=(worker)
  has_changed "beekeeper/Dockerfile"  && rebuild_services+=(beekeeper)
  has_changed "docker-compose.yml"    && rebuild_services=(core worker beekeeper)
fi

if [ ${#rebuild_services[@]} -gt 0 ]; then
  log "Rebuild: ${rebuild_services[*]}"
  ssh_run "cd ${REMOTE} && docker compose build ${rebuild_services[*]}"
  ssh_run "cd ${REMOTE} && docker compose up -d ${rebuild_services[*]}"
else
  log "Aucun Dockerfile modifié → restart simple des services Python"
  # Les fichiers Python sont montés en volume → restart suffit
  RESTART_SERVICES=""
  has_changed "core/"       && RESTART_SERVICES="$RESTART_SERVICES core"
  has_changed "workers/"    && RESTART_SERVICES="$RESTART_SERVICES worker"
  has_changed "beekeeper/"  && RESTART_SERVICES="$RESTART_SERVICES beekeeper"

  if [ -n "$RESTART_SERVICES" ]; then
    log "Restart:${RESTART_SERVICES}"
    ssh_run "cd ${REMOTE} && docker compose restart${RESTART_SERVICES}"
  else
    log "Aucun service Python modifié"
  fi

  # docker-compose.yml modifié → recreate tous les services
  has_changed "docker-compose.yml" && {
    log "docker-compose.yml modifié → recreate complet"
    ssh_run "cd ${REMOTE} && docker compose up -d"
  }
fi

ok "Services Docker à jour"

# ── Étape 4 : Migrations Alembic ──────────────────────────────────────────────
if ! $OPT_NO_MIGRATE; then
  log "Migrations Alembic..."
  ssh_run "cd ${REMOTE} && docker compose exec -T core alembic upgrade head"
  ok "Migrations appliquées"
else
  warn "Migrations skippées (--no-migrate)"
fi

# ── Étape 5 : Re-sync manifests dans beekeeper (si apps/ modifiés) ────────────
if has_changed "apps/" || $OPT_FULL; then
  log "Re-sync catalogue apps (beekeeper restart)..."
  ssh_run "cd ${REMOTE} && docker compose restart beekeeper"
  ok "Catalogue apps rechargé"
fi

# ── Résumé ────────────────────────────────────────────────────────────────────
hr
echo -e "${GREEN}${BOLD}  Déploiement terminé${RESET}"
hr
log "Vérification santé des services..."
ssh_run "cd ${REMOTE} && docker compose ps --format 'table {{.Name}}\t{{.Status}}'"
