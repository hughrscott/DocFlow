import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from config.settings import settings


def test_folder_suggestions_refresh(tmp_path):
    client = TestClient(app)

    tmp_docs = tmp_path / "docs"
    tmp_docs.mkdir()
    (tmp_docs / "Banking" / "Personal").mkdir(parents=True)
    (tmp_docs / "Banking" / "Personal" / "PNC").mkdir(parents=True)
    (tmp_docs / "Banking" / "Personal" / "PNC" / "sample.pdf").write_text("sample")

    original_docs_dir = settings.documents_dir
    try:
        settings.documents_dir = str(tmp_docs)

        resp = client.post("/api/v1/documents/folder_suggestions/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["suggestions"], "should return suggestions"
        assert any("Banking/Personal/PNC" in suggestion["path"] for suggestion in data["suggestions"])
        assert data["generated_at"]

        # Second call should use cache
        resp2 = client.get("/api/v1/documents/folder_suggestions")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["suggestions"]
    finally:
        settings.documents_dir = original_docs_dir
        shutil.rmtree(tmp_docs, ignore_errors=True)
