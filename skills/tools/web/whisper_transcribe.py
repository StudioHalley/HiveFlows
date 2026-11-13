import os
import hashlib
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from tools.base import BaseTool, ToolOutput

# --- Input schema ---

class WhisperTranscribeInput(BaseModel):
    audio_path: str = Field(description="Chemin vers le fichier audio à transcrire")
    output_dir: str = Field(default="/tmp/transcriptions", description="Dossier de destination")
    filename: str | None = Field(default=None, description="Nom du fichier .txt (auto si absent)")
    language: str | None = Field(default=None, description="Code langue (ex: 'fr', 'en'). Auto-détecté si absent")
    timeout_seconds: int = Field(default=300, ge=1, le=600)

# --- Tool ---

class WhisperTranscribeTool(BaseTool[WhisperTranscribeInput]):
    name = "whisper_transcribe"
    description = (
        "Transcrit un fichier audio en texte via un serveur Whisper distant "
        "(compatible API OpenAI /v1/audio/transcriptions). "
        "L'URL du serveur est lue depuis la variable d'environnement WHISPER_SERVER_URL."
    )
    input_schema = WhisperTranscribeInput

    def _get_server_url(self) -> str:
        url = os.environ.get("WHISPER_SERVER_URL")
        if not url:
            raise ValueError(
                "Variable d'environnement WHISPER_SERVER_URL non définie. "
                "Ex: export WHISPER_SERVER_URL=http://mon-serveur:8000"
            )
        return url.rstrip("/")

    def run(self, params: WhisperTranscribeInput) -> ToolOutput:
        try:
            server_url = self._get_server_url()

            audio_path = Path(params.audio_path)
            if not audio_path.is_file():
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error=f"Fichier introuvable : {params.audio_path}",
                )

            output_dir = Path(params.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Appel au serveur Whisper (API compatible OpenAI)
            endpoint = f"{server_url}/v1/audio/transcriptions"

            data = {"response_format": "json"}
            if params.language:
                data["language"] = params.language

            with open(audio_path, "rb") as audio_file:
                files = {"file": (audio_path.name, audio_file)}
                with httpx.Client(timeout=params.timeout_seconds) as client:
                    response = client.post(
                        endpoint,
                        data=data,
                        files=files,
                    )
                    response.raise_for_status()

            result = response.json()
            transcript = result.get("text", "")

            if not transcript:
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error="Le serveur Whisper n'a retourné aucun texte.",
                )

            # Nom de fichier basé sur le fichier audio source
            if params.filename:
                filename = params.filename
            else:
                filename = audio_path.stem + ".txt"

            file_path = output_dir / filename
            file_path.write_text(transcript, encoding="utf-8")

            return ToolOutput(
                tool_name=self.name,
                data={
                    "file_path": str(file_path),
                    "audio_path": str(audio_path),
                    "language": result.get("language", params.language),
                    "text_length": len(transcript),
                    "size_bytes": len(transcript.encode("utf-8")),
                },
            )

        except httpx.HTTPStatusError as e:
            return ToolOutput(
                status="error",
                tool_name=self.name,
                error=f"HTTP {e.response.status_code} du serveur Whisper",
            )
        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))


# python -m tools.web.whisper_transcribe '{"audio_path": "/tmp/audio.mp3"}'
if __name__ == "__main__":
    import sys
    tool = WhisperTranscribeTool()
    result = tool.run_from_json(sys.argv[1])
    print(result.model_dump_json(indent=2))
