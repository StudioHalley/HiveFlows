# Piper TTS — App HiveFlows

App Text-to-Speech basée sur [Piper](https://github.com/rhasspy/piper) (rhasspy) — binaire C++ léger, rapide sur CPU, voix ONNX de haute qualité.

## Démarrage rapide

### 1. Télécharger les voix

Depuis le répertoire du workspace (ex: `~/hiveflows/workspace_data/<workspace_id>/`) :

```bash
bash /path/to/apps/piper/download_voices.sh .hive/piper/voices
```

Les voix sont téléchargées dans `.hive/piper/voices/fr/` et `.hive/piper/voices/en/`.

### 2. Voix custom

Placer les fichiers dans `.hive/piper/voices/custom/` :
```
.hive/piper/voices/custom/
  ├── ma_voix.onnx
  └── ma_voix.onnx.json
```

## Endpoints

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/health` | Santé du service |
| GET | `/voices` | Voix disponibles |
| POST | `/speak` | TTS depuis texte inline |
| POST | `/speak/file` | TTS depuis fichier texte |
| GET | `/result?task_id=...` | Résultat d'une synthèse |

## Voix incluses

| Nom | Langue | Qualité |
|-----|--------|---------|
| `fr_FR-siwis-medium` | Français (FR) | Medium |
| `fr_FR-mls-medium` | Français (FR) | Medium |
| `en_US-lessac-medium` | Anglais (US) | Medium |
| `en_US-ryan-high` | Anglais (US) | High |
| `en_GB-alba-medium` | Anglais (GB) | Medium |

## Ressources

- RAM : ~60-150MB par voix chargée
- CPU : quasi-instantané sur CPU moderne
- Format WAV 22050Hz mono par défaut, conversion MP3 via ffmpeg disponible
