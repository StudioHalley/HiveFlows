#!/usr/bin/env bash
# ============================================================================
# bootstrap.sh — Initialise un node HiveFlows complet sur une machine vierge
#
# Pré-requis sur la machine cible : docker, docker compose, git, ssh, openssl, curl
# (le script vérifie tout au démarrage et donne les commandes d'install)
#
# Usage typique sur un nouveau node :
#   clone le git dans ~/hiveflows
#   cd ~/hiveflows && ./scripts/bootstrap.sh
#
# Variables d'environnement (override possible) :
#   CORE_PORT       (défaut: 8002)
#   SKIP_HEALTHCHECK=1   ne pas attendre /health après le up
#   HIVE_DEV=1           mode développement : active docker-compose.override.yml
#                        (bind-mount du code + hot-reload). Par défaut = PROD
#                        (le code est servi depuis l'image, immuable).
# ============================================================================
set -euo pipefail

# ── Couleurs ────────────────────────────────────────────────────────────────
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

hr
echo -e "${BOLD}  HiveFlows Bootstrap${RESET}  →  $REPO_DIR"
hr

# ── 1. Détection OS ─────────────────────────────────────────────────────────
case "$(uname -s)" in
  Linux*)   OS="linux" ;;
  Darwin*)  OS="macos" ;;
  MINGW*|MSYS*|CYGWIN*)
    err "Windows natif non supporté — utilise WSL2 (Ubuntu) puis relance le script."
    exit 1
    ;;
  *)
    err "OS inconnu : $(uname -s)"
    exit 1
    ;;
esac
log "OS détecté : $OS"

# ── 2. Vérification des dépendances ─────────────────────────────────────────
log "Vérification des dépendances..."
MISSING=0
need() {
  if command -v "$1" >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${RESET} $1"
  else
    echo -e "  ${RED}✗${RESET} $1 (manquant)"
    MISSING=1
  fi
}

need git
need docker
need ssh
need curl
need openssl

if docker compose version >/dev/null 2>&1; then
  echo -e "  ${GREEN}✓${RESET} docker compose (v2)"
else
  echo -e "  ${RED}✗${RESET} docker compose (v2 plugin manquant)"
  MISSING=1
fi

if [ "$MISSING" -ne 0 ]; then
  echo
  warn "Dépendances manquantes. Suggestions d'installation :"
  case "$OS" in
    linux)
      cat <<EOF
  # Debian/Ubuntu :
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker \$USER    # puis se déconnecter/reconnecter
  sudo apt install -y git openssh-client curl openssl

  # Fedora :
  sudo dnf install -y docker docker-compose-plugin git openssh-clients curl openssl
  sudo systemctl enable --now docker
EOF
      ;;
    macos)
      cat <<EOF
  brew install --cask docker      # puis lance Docker.app au moins une fois
  brew install git openssl curl
EOF
      ;;
  esac
  exit 1
fi
ok "Toutes les dépendances sont présentes."

# ── 3. Daemon Docker ────────────────────────────────────────────────────────
log "Vérification du daemon Docker..."
if ! docker info >/dev/null 2>&1; then
  err "Le daemon Docker ne tourne pas."
  case "$OS" in
    macos)  echo "  → open -a Docker" ;;
    linux)  echo "  → sudo systemctl start docker" ;;
  esac
  exit 1
fi
ok "Daemon Docker OK."

# ── 4. Configuration .env ───────────────────────────────────────────────────
if [ -f .env ]; then
  warn ".env existe déjà — pas de regénération des secrets."
else
  log "Création de .env depuis .env.example..."
  cp .env.example .env

  # Cross-platform sed -i
  if [ "$OS" = "macos" ]; then
    sed_i() { sed -i '' "$@"; }
  else
    sed_i() { sed -i "$@"; }
  fi

  # Génère les secrets aléatoires
  JWT_SECRET=$(openssl rand -hex 32)
  BEEKEEPER_KEY=$(openssl rand -base64 32 | tr -d '=' | tr '+/' '-_')
  # Fernet key = urlsafe_b64encode(32 random bytes), padding inclus
  FERNET_KEY=$(openssl rand 32 | openssl base64 -A | tr '+/' '-_')
  ABS_WORKSPACE="$REPO_DIR/workspace_data"

  sed_i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$JWT_SECRET|" .env
  sed_i "s|^BEEKEEPER_API_KEY=.*|BEEKEEPER_API_KEY=$BEEKEEPER_KEY|" .env
  sed_i "s|^CREDENTIAL_ENCRYPTION_KEY=.*|CREDENTIAL_ENCRYPTION_KEY=$FERNET_KEY|" .env
  sed_i "s|^WORKSPACE_DATA_HOST_DIR=.*|WORKSPACE_DATA_HOST_DIR=$ABS_WORKSPACE|" .env

  # Self-update UI : repo monté à l'identique dans beekeeper + clé SSH pour le pull.
  sed_i "s|^HIVE_REPO_HOST_DIR=.*|HIVE_REPO_HOST_DIR=$REPO_DIR|" .env
  sed_i "s|^HIVE_SSH_DIR=.*|HIVE_SSH_DIR=$HOME/.ssh|" .env

  ok "Secrets générés (JWT, Beekeeper, Fernet) et chemins absolus posés (workspace, repo, ssh)."
  echo
  warn "Édite .env pour renseigner OPENROUTER_API_KEY (et OAuth si voulu) :"
  echo "    \${EDITOR:-nano} $REPO_DIR/.env"
  echo
  read -rp "Appuie sur ENTRÉE quand .env est prêt (Ctrl+C pour annuler)... " _

  # Sanity check : OPENROUTER_API_KEY doit être renseigné
  if grep -q "^OPENROUTER_API_KEY=sk-or-\.\.\.$" .env || grep -q "^OPENROUTER_API_KEY=$" .env; then
    err "OPENROUTER_API_KEY non configuré dans .env. Avorte."
    exit 1
  fi
  ok "OPENROUTER_API_KEY configuré."
fi

# ── 5. Workspace data dir ───────────────────────────────────────────────────
mkdir -p workspace_data
ok "workspace_data/ prêt."

# ── 5b. Mode dev (bind-mount du code + hot-reload) ──────────────────────────
if [ "${HIVE_DEV:-0}" = "1" ]; then
  if [ -f docker-compose.override.yml ]; then
    warn "docker-compose.override.yml existe déjà — laissé tel quel (mode DEV)."
  else
    cp docker-compose.override.yml.example docker-compose.override.yml
    ok "Mode DEV activé : docker-compose.override.yml créé (bind-mount + reload)."
  fi
else
  log "Mode PROD : code servi depuis l'image. Pour le dev : HIVE_DEV=1 ./scripts/bootstrap.sh"
fi

# ── 6. docker compose up ────────────────────────────────────────────────────
log "Build et démarrage des services (premier run = quelques minutes)..."
docker compose up -d --build

# ── 7. Healthcheck ──────────────────────────────────────────────────────────
if [ "${SKIP_HEALTHCHECK:-0}" = "1" ]; then
  warn "Healthcheck skippé (SKIP_HEALTHCHECK=1)."
else
  log "Attente de /health sur le port $CORE_PORT (timeout 120s)..."
  for i in $(seq 1 60); do
    if curl -fsS "http://localhost:$CORE_PORT/health" >/dev/null 2>&1; then
      ok "Core est sain."
      break
    fi
    sleep 2
    if [ "$i" -eq 60 ]; then
      err "Le core n'a pas répondu en 120s. Logs : docker compose logs core"
      exit 1
    fi
  done
fi

# ── 8. Migrations Alembic ───────────────────────────────────────────────────
log "Application des migrations Alembic..."
docker compose exec -T core alembic upgrade head
ok "Migrations à jour."

# ── Résumé ──────────────────────────────────────────────────────────────────
hr
echo -e "${GREEN}${BOLD}  Node HiveFlows initialisé${RESET}"
hr
echo "  Dashboard : http://localhost:$CORE_PORT/dashboard"
echo "  Health    : http://localhost:$CORE_PORT/health"
echo "  Logs      : docker compose logs -f core"
echo "  Stop      : docker compose down"
echo
