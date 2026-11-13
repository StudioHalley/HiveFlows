from pathlib import Path
from pydantic import BaseModel, Field
from tools.base import BaseTool, ToolOutput
from PIL import Image
from pyzbar.pyzbar import decode, ZBarSymbol

# --- Input schema ---

class QRCodeToURLInput(BaseModel):
    image_path: str = Field(description="Chemin vers le fichier image contenant un QR code")

# --- Tool ---

class QRCodeToURLTool(BaseTool[QRCodeToURLInput]):
    name = "qrcode_to_url"
    description = (
        "Détecte et décode un QR code dans une image (peu importe sa position) "
        "et retourne l'URL ou le texte encodé. Fonctionne entièrement en local."
    )
    input_schema = QRCodeToURLInput

    def run(self, params: QRCodeToURLInput) -> ToolOutput:
        try:
            image_path = Path(params.image_path)
            if not image_path.is_file():
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error=f"Fichier introuvable : {params.image_path}",
                )

            image = Image.open(image_path)

            # Décoder tous les QR codes trouvés dans l'image
            results = decode(image, symbols=[ZBarSymbol.QRCODE])

            if not results:
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error="Aucun QR code détecté dans l'image.",
                )

            # Extraire les données de chaque QR code trouvé
            qrcodes = []
            for r in results:
                data = r.data.decode("utf-8")
                rect = r.rect
                qrcodes.append({
                    "data": data,
                    "type": r.type,
                    "position": {
                        "left": rect.left,
                        "top": rect.top,
                        "width": rect.width,
                        "height": rect.height,
                    },
                })

            return ToolOutput(
                tool_name=self.name,
                data={
                    "image_path": str(image_path),
                    "url": qrcodes[0]["data"],
                    "count": len(qrcodes),
                    "qrcodes": qrcodes,
                },
            )

        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))


# python -m tools.image.qrcode_reader '{"image_path": "/tmp/qr.png"}'
if __name__ == "__main__":
    import sys
    tool = QRCodeToURLTool()
    result = tool.run_from_json(sys.argv[1])
    print(result.model_dump_json(indent=2))
