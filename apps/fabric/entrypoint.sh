#!/bin/sh
set -e

mkdir -p /root/.config/fabric

if [ -n "$LLM_BASE_URL" ]; then
    # Mode proxy HiveFlows.
    # On configure Fabric avec le vendor "OpenAI" (built-in, pas de validation de liste).
    # OPENAI_API_BASE_URL pointe vers notre proxy Core.
    # Le champ model dans chaque appel = agent_id → Core résout le vrai provider + clé + modèle.
    echo "[entrypoint] Mode proxy HiveFlows (LLM_BASE_URL=${LLM_BASE_URL})"

    export FABRIC_VENDOR="OpenAI"
    # "gpt-4o" est un placeholder valide pour passer la validation locale de Fabric.
    # Notre proxy Core reçoit model="gpt-4o", ne trouve aucun agent avec cet id,
    # et tombe automatiquement sur l'agent is_default=true du workspace.
    export FABRIC_MODEL="gpt-4o"

    cat > /root/.config/fabric/.env << EOF
DEFAULT_VENDOR=OpenAI
DEFAULT_MODEL=${FABRIC_MODEL}
OPENAI_API_KEY=${LLM_API_KEY}
OPENAI_API_BASE_URL=${LLM_BASE_URL}
EOF
else
    # Mode direct OpenRouter (fallback dev local sans Beekeeper).
    echo "[entrypoint] Mode direct OpenRouter (DEFAULT_MODEL=${DEFAULT_MODEL:-openai/gpt-4o-mini})"

    export FABRIC_VENDOR="OpenRouter"
    export FABRIC_MODEL="${DEFAULT_MODEL:-openai/gpt-4o-mini}"

    cat > /root/.config/fabric/.env << EOF
DEFAULT_VENDOR=OpenRouter
DEFAULT_MODEL=${FABRIC_MODEL}
OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
OPENROUTER_API_BASE_URL=${OPENROUTER_API_BASE_URL:-https://openrouter.ai/api/v1}
EOF
fi

echo "[entrypoint] Fabric config: vendor=${FABRIC_VENDOR} model=${FABRIC_MODEL}"
echo "[entrypoint] Starting FastAPI server on :8080..."
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8080
