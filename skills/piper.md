# Piper TTS — Installation et configuration Docker

Serveur TTS self-hosted basé sur [piper-tts-http-server](https://github.com/Kamil-Krawiec/piper-tts-http-server), compatible API OpenAI `/v1/audio/speech`.

---

## Démarrage rapide (Docker)

```bash
docker run --rm \
  -p 5000:5000 \
  -e PORT=5000 \
  -v "$(pwd)/piper-data:/data" \
  kamilkrawiec/piper-openai-tts
```

Le serveur est accessible sur `http://localhost:5000/v1/audio/speech`.

Les voix sont téléchargées automatiquement depuis Hugging Face au premier appel et cachées dans le volume `/data`.

---

## Docker Compose

```yaml
services:
  piper-tts:
    image: kamilkrawiec/piper-openai-tts
    ports:
      - "5000:5000"
    environment:
      - PORT=5000
    volumes:
      - ./piper-data:/data
    restart: unless-stopped
```

```bash
docker compose up -d
```

---

## Variables d'environnement du serveur

| Variable   | Défaut  | Description                                      |
|------------|---------|--------------------------------------------------|
| `DATA_DIR` | `/data` | Dossier de cache des voix (monter un volume ici) |
| `PORT`     | `5000`  | Port d'écoute du serveur                         |

---

## Variable d'environnement pour le tool

Le tool `PiperTTSTool` lit l'URL du serveur depuis :

```bash
export PIPER_TTS_URL=http://localhost:5000
```

---

## Voix disponibles

Les voix suivent le format `<lang>_<REGION>-<nom>-<qualité>`.

### Voix françaises
| Voix                      | Qualité |
|---------------------------|---------|
| `fr_FR-siwis-medium`     | medium  |
| `fr_FR-upmc-medium`      | medium  |
| `fr_FR-gilles-low`       | low     |

### Voix anglaises
| Voix                      | Qualité |
|---------------------------|---------|
| `en_US-lessac-high`      | high    |
| `en_US-lessac-medium`    | medium  |
| `en_US-amy-low`          | low     |
| `en_GB-alan-medium`      | medium  |

Liste complète : https://huggingface.co/rhasspy/piper-voices/tree/main

---

## Pré-charger des voix

Pour éviter le téléchargement au premier appel, placer les fichiers `.onnx` et `.onnx.json` dans le dossier `piper-data/` :

```bash
cd piper-data
# Exemple : télécharger la voix française siwis-medium
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json
```

---

## Test rapide

### curl

```bash
curl -o output.wav \
  -H "Content-Type: application/json" \
  -d '{"model":"piper","voice":"fr_FR-siwis-medium","input":"Bonjour, ceci est un test."}' \
  http://localhost:5000/v1/audio/speech
```

### Python (avec le tool)

```python
from tools.web.piper_tts import PiperTTSTool

tool = PiperTTSTool()
result = tool.run_from_dict({
    "text": "Bonjour, ceci est un test de synthèse vocale.",
    "output_dir": "/tmp/tts",
    "voice": "fr_FR-siwis-medium",
})
print(result.data["file_path"])
```

### CLI

```bash
export PIPER_TTS_URL=http://localhost:5000
python -m tools.web.piper_tts '{"text": "Bonjour le monde"}'
```

---

## Format MP3

Le conteneur inclut `ffmpeg`. Pour obtenir du MP3 :

```bash
curl -o output.mp3 \
  -H "Content-Type: application/json" \
  -d '{"model":"piper","voice":"fr_FR-siwis-medium","input":"Bonjour","response_format":"mp3"}' \
  http://localhost:5000/v1/audio/speech
```

Ou via le tool :

```python
result = tool.run_from_dict({
    "text": "Bonjour",
    "response_format": "mp3",
})
```

---

## Build custom (optionnel)

Si besoin de personnaliser l'image :

```bash
git clone https://github.com/Kamil-Krawiec/piper-tts-http-server.git
cd piper-tts-http-server
docker build -t my-piper-tts .
docker run --rm -p 5000:5000 -v "$(pwd)/piper-data:/data" my-piper-tts
```
