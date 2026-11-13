# Whisper STT — App HiveFlows

App Speech-to-Text basée sur [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) — 4× plus rapide que le Whisper original sur CPU, même précision, support ~100 langues.

## Démarrage rapide

Au premier appel à `/transcribe`, le modèle sélectionné est téléchargé automatiquement depuis Hugging Face et mis en cache dans `.hive/whisper/models/`. Les appels suivants utilisent le cache.

Modèle recommandé pour débuter : `small` (~244MB, bon équilibre qualité/vitesse).

## Endpoints

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/health` | Santé du service |
| GET | `/models` | Modèles disponibles et téléchargés |
| POST | `/transcribe` | Démarre une transcription |
| GET | `/result?task_id=...` | Résultat d'une transcription |

## Modèles disponibles

| Modèle | Taille | Vitesse CPU | Précision | Recommandation |
|--------|--------|-------------|-----------|----------------|
| `tiny` | 39M | Très rapide | Basique | Debug/test |
| `base` | 74M | Rapide | Correcte | Usage léger |
| `small` | 244M | Correct | Bonne | **Défaut recommandé** |
| `medium` | 769M | Lent | Très bonne | Transcriptions importantes |
| `large-v3` | 1.5G | Très lent | Excellente | GPU fortement conseillé |

## Workflow yt-dlp + Whisper

```
yt-dlp → download_video(url) → audio_path (mp3)
whisper → transcribe(audio_path) → transcript avec segments horodatés
agent → résumé / analyse
```

## Ressources

- RAM : small=~600MB, medium=~1.5GB, large=~3GB
- CPU : `small` sur ~5min de contenu = ~30s de transcription
- Format JSON inclut les segments avec timestamps start/end pour sous-titres
