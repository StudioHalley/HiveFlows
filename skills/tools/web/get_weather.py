import httpx
from pydantic import BaseModel, Field
from tools.base import BaseTool, ToolOutput

# --- Input schema ---

class GetWeatherInput(BaseModel):
    location: str = Field(description="Ville ou code postal (ex: 'Paris', '75001', 'London')")
    timeout_seconds: int = Field(default=10, ge=1, le=30)

# --- Tool ---

class GetWeatherTool(BaseTool[GetWeatherInput]):
    name = "get_weather"
    description = (
        "Récupère la météo actuelle pour une ville ou un code postal "
        "via wttr.in et retourne les données JSON."
    )
    input_schema = GetWeatherInput

    def run(self, params: GetWeatherInput) -> ToolOutput:
        try:
            url = f"https://wttr.in/{params.location}?format=j1"

            with httpx.Client(timeout=params.timeout_seconds) as client:
                response = client.get(url, follow_redirects=True)
                response.raise_for_status()

            data = response.json()

            return ToolOutput(
                tool_name=self.name,
                data={
                    "location": params.location,
                    "weather": data,
                },
            )

        except httpx.HTTPStatusError as e:
            return ToolOutput(
                status="error",
                tool_name=self.name,
                error=f"HTTP {e.response.status_code} pour {params.location}",
            )
        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))


# python -m tools.web.get_weather '{"location": "Paris"}'
if __name__ == "__main__":
    import sys
    tool = GetWeatherTool()
    result = tool.run_from_json(sys.argv[1])
    print(result.model_dump_json(indent=2))
