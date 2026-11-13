#!/usr/bin/env bash
# download_voices.sh — Télécharge les voix Piper depuis Hugging Face
# Usage : bash download_voices.sh [DEST_DIR]
# Par défaut DEST_DIR = .hive/piper/voices (relatif au CWD = workspace du projet)
#
# Utilise le manifest voices.json officiel pour les URLs exactes.
# Voix incluses :
#   FR : fr_FR-siwis-medium, fr_FR-mls-medium
#   EN : en_US-lessac-medium, en_US-ryan-high, en_GB-alba-medium

set -euo pipefail

DEST="${1:-.hive/piper/voices}"
BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES_JSON_URL="${BASE_URL}/voices.json"

# Voix à télécharger
VOICES=(
    "fr_FR-siwis-medium"
    "fr_FR-mls-medium"
    "en_US-lessac-medium"
    "en_US-ryan-high"
    "en_GB-alba-medium"
)

echo "Téléchargement du manifest voices.json..."
VOICES_JSON=$(curl -fsSL "$VOICES_JSON_URL")

download_voice() {
    local key="$1"

    # Extraire les chemins de fichiers depuis le manifest JSON
    local files
    files=$(echo "$VOICES_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
voice = data.get('$key')
if not voice:
    print('ERROR: voice not found in manifest', file=sys.stderr)
    sys.exit(1)
for path in voice['files']:
    if path.endswith('.onnx') or path.endswith('.onnx.json'):
        print(path)
")

    local lang_dir
    lang_dir=$(echo "$key" | cut -d'-' -f1 | cut -d'_' -f1)  # ex: fr, en

    while IFS= read -r file_path; do
        local filename
        filename=$(basename "$file_path")
        local dest_dir="${DEST}/${lang_dir}"
        mkdir -p "$dest_dir"

        echo "  → ${filename}"
        curl -fsSL --output "${dest_dir}/${filename}" "${BASE_URL}/${file_path}"
    done <<< "$files"
}

echo "Téléchargement des voix Piper vers : ${DEST}"
echo ""

for voice_key in "${VOICES[@]}"; do
    echo "[${voice_key}]"
    download_voice "$voice_key"
done

echo ""
echo "Voix téléchargées dans : ${DEST}"
echo "Pour ajouter une voix custom, placer les fichiers .onnx + .onnx.json dans : ${DEST}/custom/"
