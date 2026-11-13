#!/usr/bin/env bash
# ============================================================================
# update.sh — Met à jour un node HiveFlows déjà installé
#
# Encode la procédure validée à la main :
#   git pull → (re)build/up selon le diff → migrations Alembic → healthcheck
#
# Détecte automatiquement le mode du node via la présence de
# docker-compose.override.yml :
#   - PRÉSENT  → mode DEV  (code monté en volume + --reload)
#   - ABSENT   → mode PROD (code servi depuis l'image immuable)
#
# Non-interactif (utilisable par un déclencheur distant, ex. self-update beekeeper).
# Idempotent : sans nouveau commit en amont, ne fait rien (sauf --force).
#
# Usage :
#   ./scripts/update.sh                 # mise à jour normale
#   ./scripts/update.sh --force         # rebuild même si déjà à jour
#   ./scripts/update.sh --strict-env    # échoue si .env.example a des clés absentes de .env
#   ./scripts/update.sh --rollback      # revient au commit précédent + rebuild
#   ./scripts/update.sh --no-build      # saute le build (debug)
#   ./scripts/update.sh --exclude-beekeeper   # ne recrée pas beekeeper (déclenché par l'UI)
#
# Variables d'environnement :
#   CORE_PORT       (défaut: 8002)      port du healthcheck /health
#   HIVE_BRANCH     (défaut: main)      branche git à suivre
#   SKIP_HEALTHCHECK=1                  ne pas attendre /health
# ============================================================================
set -euo pipefail

# ── Couleurs / log ───────────────────────────────────────────────────────────
BOLD='\033[1m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'
YELLOW='\033[0;33m'; RED='\033[0;31m'; RESET='\033[0m'
log()  { echo -e "${CYAN}▸${RESET} $*"; }
ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET} $*"; }
err()  { echo -e "${RED}✗${RESET} $*" >&2; }
hr()   { echo -e "${BOLD}────────────────────────────────────────────${RESET}"; }

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

CORE_PORT="${CORE_PORT:-8002}"
HIVE_BRANCH="${HIVE_BRANCH:-main}"
LOCKFILE="/tmp/hiveflows-update.lock"
PREV_SHA_FILE="$REPO_DIR/.update-prev-sha"
BACKUP_DIR="$REPO_DIR/backups"

# ── Args ─────────────────────────────────────────────────────────────────────
FORCE=0; STRICT_ENV=0; ROLLBACK=0; NO_BUILD=0; EXCLUDE_BEEKEEPER=0
for arg in "$@"; do
  case "$arg" in
    --force)             FORCE=1 ;;
    --strict-env)        STRICT_ENV=1 ;;
    --rollback)          ROLLBACK=1 ;;
    --no-build)          NO_BUILD=1 ;;
    --exclude-beekeeper) EXCLUDE_BEEKEEPER=1 ;;
    -h|--help)           grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                   err "Argument inconnu : $arg"; exit 2 ;;
  esac
done

# Git peut refuser un repo dont le propriétaire diffère de l'utilisateur courant
# (cas du beekeeper root agissant sur un repo possédé par l'utilisateur hôte).
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

# ── Helpers ──────────────────────────────────────────────────────────────────
compose() { docker compose "$@"; }

detect_mode() {
  if [ -f "$REPO_DIR/docker-compose.override.yml" ]; then echo "dev"; else echo "prod"; fi
}

healthcheck() {
  [ "${SKIP_HEALTHCHECK:-0}" = "1" ] && { warn "Healthcheck skippé."; return 0; }
  # HEALTH_URL surchargeable : depuis l'hôte = localhost:CORE_PORT ; depuis le
  # container beekeeper (self-update) = http://core:8000/health (DNS Docker).
  local url="${HEALTH_URL:-http://localhost:$CORE_PORT/health}"
  log "Attente de $url (timeout 120s)..."
  for i in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      ok "Core est sain."
      return 0
    fi
    sleep 2
  done
  err "Le core n'a pas répondu en 120s. Logs : docker compose logs core"
  return 1
}

# Sauvegarde DB (avant migrations). Best-effort : n'interrompt pas l'update si elle échoue.
db_backup() {
  if [ ! -f "$REPO_DIR/.env" ]; then warn "Pas de .env — backup DB sauté."; return 0; fi
  local pguser pgdb ts dest
  pguser="$(grep -E '^POSTGRES_USER=' .env | head -1 | cut -d= -f2-)"
  pgdb="$(grep -E '^POSTGRES_DB=' .env | head -1 | cut -d= -f2-)"
  [ -z "$pguser" ] && pguser="hiveflows"
  [ -z "$pgdb" ] && pgdb="hiveflows"
  mkdir -p "$BACKUP_DIR"
  ts="$(date +%Y%m%d-%H%M%S)"
  dest="$BACKUP_DIR/db-${ts}.sql.gz"
  log "Sauvegarde DB → $dest"
  if compose exec -T postgres pg_dump -U "$pguser" "$pgdb" | gzip > "$dest"; then
    ok "Dump DB créé ($(du -h "$dest" | cut -f1))."
  else
    warn "Dump DB échoué (non-fatal) — vérifie que postgres tourne."
    rm -f "$dest"
  fi
}

# Vérifie que .env contient toutes les clés de .env.example.
env_check() {
  [ -f .env.example ] || return 0
  [ -f .env ] || { warn ".env absent — saute la vérification des clés."; return 0; }
  local missing=() key
  while IFS= read -r key; do
    grep -qE "^${key}=" .env || missing+=("$key")
  done < <(grep -oE '^[A-Z][A-Z0-9_]*=' .env.example | tr -d '=')
  if [ "${#missing[@]}" -gt 0 ]; then
    warn "Clés présentes dans .env.example mais absentes de .env :"
    printf '    %s\n' "${missing[@]}"
    warn "Reporte-les dans .env puis relance (un recreate \`up -d\` les prendra)."
    if [ "$STRICT_ENV" = 1 ]; then err "--strict-env : arrêt."; exit 1; fi
  fi
}

# ── Rollback ─────────────────────────────────────────────────────────────────
do_rollback() {
  hr; echo -e "${BOLD}  HiveFlows Rollback${RESET}"; hr
  [ -f "$PREV_SHA_FILE" ] || { err "Aucun SHA précédent ($PREV_SHA_FILE) — rien à rollback."; exit 1; }
  local prev; prev="$(cat "$PREV_SHA_FILE")"
  log "Retour au commit $prev ..."
  git reset --hard "$prev"
  ok "Code revenu à $prev."
  log "Rebuild + recreate..."
  compose up -d --build
  healthcheck || warn "Healthcheck KO après rollback — inspecte les logs."
  warn "⚠ La base de données N'A PAS été restaurée automatiquement."
  warn "  Si la migration doit être annulée, restaure le dernier dump :"
  local last; last="$(ls -1t "$BACKUP_DIR"/db-*.sql.gz 2>/dev/null | head -1 || true)"
  if [ -n "$last" ]; then
    echo "    gunzip -c '$last' | docker compose exec -T postgres psql -U <user> <db>"
  else
    echo "    (aucun dump trouvé dans $BACKUP_DIR)"
  fi
  hr; ok "Rollback terminé."; exit 0
}

# ── Lock ─────────────────────────────────────────────────────────────────────
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  err "Un update est déjà en cours (lock: $LOCKFILE). Abandon."
  exit 1
fi

MODE="$(detect_mode)"

[ "$ROLLBACK" = 1 ] && do_rollback

# ── Démarrage ────────────────────────────────────────────────────────────────
hr; echo -e "${BOLD}  HiveFlows Update${RESET}  →  $REPO_DIR  (mode: ${MODE})"; hr

# ── 1. État courant + fetch ──────────────────────────────────────────────────
CURRENT_SHA="$(git rev-parse HEAD)"
log "Commit courant : $(git rev-parse --short HEAD)"
log "Fetch origin/$HIVE_BRANCH ..."
git fetch --quiet origin "$HIVE_BRANCH"
REMOTE_SHA="$(git rev-parse "origin/$HIVE_BRANCH")"

if [ "$CURRENT_SHA" = "$REMOTE_SHA" ] && [ "$FORCE" = 0 ]; then
  ok "Déjà à jour ($(git rev-parse --short HEAD)). Rien à faire. (--force pour rebuild)"
  exit 0
fi

# ── 2. Diff des fichiers ─────────────────────────────────────────────────────
if [ "$CURRENT_SHA" = "$REMOTE_SHA" ]; then
  CHANGED=""   # --force sans nouveau commit
else
  CHANGED="$(git diff --name-only "$CURRENT_SHA" "$REMOTE_SHA")"
  log "Fichiers modifiés :"
  echo "$CHANGED" | sed 's/^/    /'
fi

# ── 3. Vérification .env (avant toute action) ────────────────────────────────
env_check

# ── 4. Sauvegarde du SHA + pull ──────────────────────────────────────────────
echo "$CURRENT_SHA" > "$PREV_SHA_FILE"
if [ "$CURRENT_SHA" != "$REMOTE_SHA" ]; then
  log "git pull (fast-forward) ..."
  git pull --ff-only origin "$HIVE_BRANCH"
  ok "Code à jour : $(git rev-parse --short HEAD)"
  # Si on tourne en root (cas du self-update via beekeeper), les fichiers tirés
  # seraient root → casserait un futur `git pull` côté utilisateur hôte. On rétablit
  # la propriété d'origine (celle du dossier repo).
  if [ "$(id -u)" = "0" ]; then
    owner="$(stat -c '%u:%g' "$REPO_DIR" 2>/dev/null || true)"
    [ -n "$owner" ] && chown -R "$owner" "$REPO_DIR" 2>/dev/null || true
  fi
fi

# ── 5. Décision build vs up ──────────────────────────────────────────────────
NEED_BUILD=0
MIGRATIONS_CHANGED=0
if [ -n "$CHANGED" ]; then
  grep -q '^core/alembic/versions/' <<<"$CHANGED" && MIGRATIONS_CHANGED=1
fi

if [ "$FORCE" = 1 ]; then
  NEED_BUILD=1
elif [ "$MODE" = "prod" ]; then
  # Prod : le code est dans l'image → rebuild si du code de service a changé.
  grep -qE '^(core|workers|beekeeper)/' <<<"$CHANGED" && NEED_BUILD=1
else
  # Dev : code monté + --reload → rebuild seulement si inputs de build changent.
  grep -qE '(^|/)(Dockerfile|requirements\.txt)$' <<<"$CHANGED" && NEED_BUILD=1
fi
[ "$NO_BUILD" = 1 ] && NEED_BUILD=0

# ── 6. Backup DB si migrations à venir ───────────────────────────────────────
if [ "$MIGRATIONS_CHANGED" = 1 ]; then
  log "Nouvelles migrations détectées."
  db_backup
fi

# ── 7. Recreate / rebuild ────────────────────────────────────────────────────
# Cibles : par défaut tous les services. En mode --exclude-beekeeper (update
# déclenché par l'UI), on ne touche QUE core + worker pour que beekeeper reste
# vivant et puisse streamer la progression — pas de self-restart.
TARGETS=()
if [ "$EXCLUDE_BEEKEEPER" = 1 ]; then
  TARGETS=(core worker)
  if grep -q '^beekeeper/' <<<"$CHANGED"; then
    warn "beekeeper/ a changé mais n'est PAS recréé (mode --exclude-beekeeper)."
    warn "  Applique-le en CLI/SSH : ./scripts/update.sh"
  fi
fi

BUILD_FLAG=()
[ "$NEED_BUILD" = 1 ] && BUILD_FLAG=(--build)
log "docker compose up -d ${BUILD_FLAG[*]:-} ${TARGETS[*]:-(tous)} ..."
compose up -d ${BUILD_FLAG[@]+"${BUILD_FLAG[@]}"} ${TARGETS[@]+"${TARGETS[@]}"}
ok "Containers à jour."

# ── 8. Migrations Alembic (idempotent) ───────────────────────────────────────
log "alembic upgrade head ..."
compose exec -T core alembic upgrade head
ok "Migrations à jour."

# ── 9. Healthcheck ───────────────────────────────────────────────────────────
healthcheck

# ── Résumé ───────────────────────────────────────────────────────────────────
hr
echo -e "${GREEN}${BOLD}  Node mis à jour${RESET}  (mode: ${MODE})"
hr
echo "  Version   : $(git rev-parse --short HEAD)"
echo "  Dashboard : http://localhost:$CORE_PORT/dashboard"
echo "  Rollback  : ./scripts/update.sh --rollback"
echo
