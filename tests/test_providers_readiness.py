from fastapi.testclient import TestClient
from main import app
from config.settings import settings


def test_providers_readiness_endpoint_smoke(tmp_path, monkeypatch):
    # Point to a minimal temp llm config so LLMManager loads
    cfg_path = tmp_path / "llm.yaml"
    cfg_path.write_text(
        """
llm:
  vision_provider: "ollama"
  text_provider: "ollama"
providers:
  ollama:
    enabled: true
    base_url: "http://127.0.0.1:65535"  # invalid to avoid network dependence
    vision_model: "llava:latest"
    text_model: "mistral:latest"
        """
    )
    settings.llm_config_path = str(cfg_path)

    client = TestClient(app)
    r = client.get("/api/v1/providers/readiness")
    assert r.status_code == 200
    data = r.json()
    assert "vision_provider" in data and "text_provider" in data
    assert isinstance(data.get("providers"), dict)
    # If ollama present, structure for a provider should include flags
    if "ollama" in data.get("providers", {}):
        o = data["providers"]["ollama"]
        assert set(["name", "enabled", "configured", "reachable", "details"]).issubset(o.keys())

