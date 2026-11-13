# TTS & STT — Plan d'intégration Beekeeper

Deux nouvelles apps dans le catalogue Beekeeper, suivant le même pattern que `yt-dlp` et `fabric` :
façade **FastAPI** + **manifest.yaml** avec tools exposés aux agents et aux humains.

---

## Vue d'ensemble

| App | Fonction | Technologie | GPU requis |
|-----|----------|-------------|-----------|
| `piper` | Text-to-Speech | Piper (binaire C++) + FastAPI | Non — CPU-first |
| `whisper` | Speech-to-Text | faster-whisper (CTranslate2) + FastAPI | Non — CPU-optimisé |

---

## App 1 — Piper (TTS)

### Technologie choisie

**Piper** de rhasspy — binaire C++ pré-compilé, très rapide sur CPU, supporte des voix ONNX de haute qualité. Les voix sont des fichiers `.onnx` + `.onnx.json` (config de la voix).

Pas d'image tierce : on part d'une `python:3.11-slim`, on télécharge le binaire Piper depuis les releases GitHub, et on wrape avec FastAPI.

### Structure de fichiers

```
apps/piper/
  Dockerfile
  app.py
  manifest.yaml
  requirements.txt
  piper.md
  download_voices.sh   ← script de pré-téléchargement des voix open source
```

### Voix à inclure

Les voix sont des fichiers statiques, pas dans l'image Docker — elles vivent dans `.hive/piper/voices/` monté en volume.

```
.hive/piper/voices/
  ├── en/
  │   ├── en_US-lessac-medium.onnx
  │   ├── en_US-lessac-medium.onnx.json
  │   ├── en_US-ryan-high.onnx
  │   ├── en_US-ryan-high.onnx.json
  │   ├── en_GB-alba-medium.onnx
  │   └── en_GB-alba-medium.onnx.json
  ├── fr/
  │   ├── fr_FR-siwis-medium.onnx
  │   ├── fr_FR-siwis-medium.onnx.json
  │   ├── fr_FR-mls-medium.onnx
  │   └── fr_FR-mls-medium.onnx.json
  └── custom/
      ├── <nom_voix_custom>.onnx         ← ta voix custom
      └── <nom_voix_custom>.onnx.json
```

Le script `download_voices.sh` (à run une fois sur le serveur) télécharge les voix open source depuis le repo officiel `rhasspy/piper-voices` sur Hugging Face.

### Endpoints FastAPI

```
GET  /health              → {"status": "ok"}
GET  /voices              → liste des voix disponibles (scan du dossier /voices)
POST /speak               → TTS depuis texte inline, retourne task_id
POST /speak/file          → TTS depuis un fichier texte, retourne task_id
GET  /result?task_id=...  → état + chemin du fichier audio généré
```

#### POST /speak — texte inline

```json
{
  "text": "Bonjour, je suis HiveFlows.",
  "voice": "fr_FR-siwis-medium",
  "format": "wav",
  "speed": 1.0
}
```

Audio généré dans `/workspace/tts/<task_id>.wav`.

#### POST /speak/file — depuis un fichier texte

Pour lire un `.txt` ou `.md` existant dans le workspace. L'audio est écrit **dans le même répertoire** que le fichier source.

```json
{
  "text_path": "/workspace/notes/article.txt",
  "voice": "fr_FR-siwis-medium",
  "format": "mp3",
  "speed": 1.0
}
```

Audio généré à côté du fichier source : `/workspace/notes/article.mp3`

#### GET /result — réponse commune

```json
{
  "status": "success",
  "audio_path": "/workspace/notes/article.mp3",
  "voice": "fr_FR-siwis-medium",
  "duration_ms": 4820,
  "text_length": 312
}
```

### Manifest tools

- `speak` — Convertit du texte inline en audio (TTS). Retourne task_id.
- `speak_file` — Lit un fichier texte et génère l'audio à côté. Retourne task_id.
- `get_speak_result` — Poll le résultat, retourne le chemin audio quand prêt.
- `list_voices` — Liste les voix disponibles avec leur langue et style.

### Dockerfile (approche)

```dockerfile
FROM python:3.11-slim

# Télécharge le binaire piper pré-compilé (Linux x86_64)
ARG PIPER_VERSION=2023.11.14-2
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg libgomp1 \
    && curl -L "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_x86_64.tar.gz" \
       | tar xz -C /usr/local/bin --strip-components=1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Mounts dans manifest.yaml :**
- `.hive/piper/voices` → `/voices` (voix ONNX — read-only)
- `.` (workspace) → `/workspace` (fichiers audio générés)

### Ressources recommandées

```yaml
resources:
  memory: 512m
  cpus: "1.0"
```

Piper est extrêmement léger — une voix medium prend ~60MB RAM, la synthèse est quasi-instantanée sur CPU.

---

## App 2 — Whisper (STT)

### Technologie choisie

**faster-whisper** (Systran/faster-whisper sur PyPI) — réimplémentation de Whisper via CTranslate2. 4× plus rapide que le Whisper original sur CPU, même précision. Support multilingue natif.

On part d'une `python:3.11-slim` avec faster-whisper installé + FastAPI. Les modèles sont stockés dans un volume persistant pour ne pas les re-télécharger.

### Structure de fichiers

```
apps/whisper/
  Dockerfile
  app.py
  manifest.yaml
  requirements.txt
  whisper.md
```

### Modèles disponibles

| Modèle | Taille | Vitesse CPU | Précision | Recommandation |
|--------|--------|-------------|-----------|----------------|
| tiny | 39M | Très rapide | Basique | Debug/test |
| base | 74M | Rapide | Correcte | Usage léger |
| small | 244M | Correct | Bonne | **Défaut recommandé** |
| medium | 769M | Lent | Très bonne | Transcriptions importantes |
| large-v3 | 1.5G | Très lent | Excellente | GPU fortement conseillé |

Sans GPU, `small` est le sweet spot qualité/vitesse. `medium` est utilisable pour des fichiers courts (< 2 min) si la précision prime.

Les modèles sont téléchargés automatiquement par faster-whisper au premier usage et mis en cache dans `/models`.

### Endpoints FastAPI

```
GET  /health                → {"status": "ok"}
GET  /models                → liste des modèles téléchargés localement
POST /transcribe            → STT en arrière-plan, retourne task_id
GET  /result?task_id=...    → état + transcript quand prêt
```

**POST /transcribe payload :**
```json
{
  "audio_path": "/workspace/downloads/mon_fichier.mp3",
  "model": "small",          // tiny | base | small | medium
  "language": "auto",        // "fr", "en", "auto" (détection automatique)
  "format": "json"           // json (défaut) | txt | vtt | srt
}
```

**GET /result réponse (succès) :**
```json
{
  "status": "success",
  "language": "fr",
  "language_confidence": 0.98,
  "duration_s": 47.3,
  "transcript": "Bonjour, bienvenue dans HiveFlows...",
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "Bonjour, bienvenue dans HiveFlows..."},
    ...
  ],
  "transcript_path": "/workspace/transcripts/<task_id>.json"
}
```

### Manifest tools

- `transcribe` — Transcrit un fichier audio en texte (STT). Retourne task_id.
- `get_transcription_result` — Poll le résultat, retourne le transcript complet avec segments horodatés.
- `list_models` — Liste les modèles Whisper disponibles localement.

### Dockerfile (approche)

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# faster-whisper, fastapi, uvicorn

COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
```

**requirements.txt :**
```
faster-whisper
fastapi
uvicorn[standard]
```

**Mounts dans manifest.yaml :**
- `.hive/whisper/models` → `/models` (cache des modèles — persistant entre redémarrages)
- `.` (workspace) → `/workspace` (fichiers audio input + transcripts output)

### Ressources recommandées

```yaml
resources:
  memory: 2g      # small=~600MB RAM, medium=~1.5GB RAM
  cpus: "2.0"     # faster-whisper exploite le multi-thread via OpenMP
```

Pour `medium`, envisager `memory: 3g`.

---

## Intégration Beekeeper

Les deux apps s'enregistrent dans le catalogue exactement comme yt-dlp :
- `manifest.yaml` → Beekeeper les découvre et enregistre les tools
- Les tools sont disponibles pour les agents ET les humains via l'interface Beekeeper
- Les fichiers audio générés (TTS) et les transcripts (STT) tombent dans le workspace courant

### Cas d'usage agents

```
Agent → speak("Résumé prêt : ...") → Piper → fichier .wav dans workspace
Agent → transcribe("/workspace/réunion.mp3") → Whisper → texte + segments horodatés
```

### Synergie yt-dlp + Whisper

Workflow naturel : `yt-dlp` télécharge la vidéo + extrait l'audio → `Whisper` transcrit l'audio → agent résume. Utilisable sans les auto-captions YouTube (qui sont parfois absentes ou mauvaises).

---

## Plan d'implémentation (ordre suggéré)

1. **Piper en premier** — plus simple, pas de gestion de modèles lourds
   - [x] Dockerfile + test binaire piper
   - [x] `download_voices.sh` — télécharge voix FR/EN depuis Hugging Face
   - [x] Voix custom intégrée dans `.hive/piper/voices/custom/`
   - [x] `app.py` — endpoints /health, /voices, /speak, /speak/file, /result
   - [x] `manifest.yaml` — 4 tools
   - [x] Test end-to-end (synthèse WAV validée)

2. **Whisper ensuite** — gestion du cache modèles + jobs plus longs
   - [x] Dockerfile + test faster-whisper (numpy<2.0 requis — CPU KVM sans SSE4)
   - [x] `app.py` — endpoints /health, /models, /transcribe, /result
   - [x] Modèle téléchargé au premier appel, mis en cache dans /models
   - [x] `manifest.yaml` — 3 tools
   - [x] Test end-to-end (transcription FR validée)
   - [ ] Test workflow complet yt-dlp → Whisper

3. **GPU (futur)** — quand disponible, seul `whisper` en bénéficie vraiment
   - Ajouter `device: cuda` dans faster-whisper si GPU détecté
   - Piper reste sur CPU (déjà optimal)
   - Débloquer les modèles `medium` et `large-v3` sans compromis vitesse

---

## Notes

- Les fichiers audio générés par Piper sont en **WAV 22050Hz mono** par défaut. Conversion MP3 via ffmpeg si demandé.
- faster-whisper supporte **~100 langues** — la détection auto est fiable pour FR/EN.
- La voix custom Piper doit être au format ONNX (export depuis le repo piper-training ou un service de fine-tuning).
- Les modèles Whisper sont téléchargés depuis Hugging Face au premier appel — prévoir ~5 min pour `small` selon la connexion du serveur.
