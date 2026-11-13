import os
import hashlib
import re
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from tools.base import BaseTool, ToolOutput

# --- Input schema ---

class PiperTTSInput(BaseModel):
    text: str = Field(description="Texte à synthétiser en audio")
    output_dir: str = Field(default="/tmp/tts", description="Dossier de destination")
    filename: str | None = Field(default=None, description="Nom du fichier audio (auto si absent)")
    voice: str = Field(default="fr_FR-siwis-medium", description="Nom de la voix Piper (ex: 'fr_FR-siwis-medium', 'en_US-lessac-high')")
    response_format: str = Field(default="wav", description="Format audio : 'wav' ou 'mp3'")
    speed: float = Field(default=1.0, ge=0.1, le=5.0, description="Vitesse de parole (1.0 = normal)")
    timeout_seconds: int = Field(default=60, ge=1, le=300)

# --- Tool ---

class PiperTTSTool(BaseTool[PiperTTSInput]):
    name = "piper_tts"
    description = (
        "Convertit du texte en fichier audio via un serveur Piper TTS distant "
        "(compatible API OpenAI /v1/audio/speech). "
        "L'URL du serveur est lue depuis la variable d'environnement PIPER_TTS_URL."
    )
    input_schema = PiperTTSInput

    def _get_server_url(self) -> str:
        url = os.environ.get("PIPER_TTS_URL")
        if not url:
            raise ValueError(
                "Variable d'environnement PIPER_TTS_URL non définie. "
                "Ex: export PIPER_TTS_URL=http://mon-serveur:5000"
            )
        return url.rstrip("/")

    def run(self, params: PiperTTSInput) -> ToolOutput:
        try:
            server_url = self._get_server_url()

            output_dir = Path(params.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Appel au serveur Piper (API compatible OpenAI)
            endpoint = f"{server_url}/v1/audio/speech"

            payload = {
                "model": "piper",
                "voice": params.voice,
                "input": params.text,
                "response_format": params.response_format,
                "speed": params.speed,
            }

            with httpx.Client(timeout=params.timeout_seconds) as client:
                response = client.post(endpoint, json=payload)
                response.raise_for_status()

            audio_bytes = response.content

            if not audio_bytes:
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error="Le serveur Piper n'a retourné aucun audio.",
                )

            # Nom de fichier auto basé sur le début du texte
            ext = params.response_format
            if params.filename:
                filename = params.filename
            else:
                slug = re.sub(r'[^\w\s-]', '', params.text[:50].lower())
                slug = re.sub(r'[\s_]+', '-', slug).strip('-')
                if slug:
                    filename = f"{slug}.{ext}"
                else:
                    filename = hashlib.md5(params.text.encode()).hexdigest()[:8] + f".{ext}"

            file_path = output_dir / filename
            file_path.write_bytes(audio_bytes)

            return ToolOutput(
                tool_name=self.name,
                data={
                    "file_path": str(file_path),
                    "voice": params.voice,
                    "format": params.response_format,
                    "size_bytes": len(audio_bytes),
                    "text_length": len(params.text),
                },
            )

        except httpx.HTTPStatusError as e:
            return ToolOutput(
                status="error",
                tool_name=self.name,
                error=f"HTTP {e.response.status_code} du serveur Piper",
            )
        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))


# python -m tools.web.piper_tts '{"text": "Bonjour le monde"}'
if __name__ == "__main__":
    import sys
    tool = PiperTTSTool()
    result = tool.run_from_json(sys.argv[1])
    print(result.model_dump_json(indent=2))
