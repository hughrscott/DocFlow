"""One plain sentence describes what this install does, everywhere it is shown.

The server owns the wording (``/api/health`` and the processing status capabilities);
every surface renders it verbatim. Local-only must read as a deliberate configuration,
in words a non-technical user understands.
"""
from __future__ import annotations

import pytest

from docflow.web import app as web_app

LOCAL_ONLY_SENTENCE = "OCR runs locally. AI classification is off."
CLOUD_SENTENCE = "OCR runs locally. AI classification is on."


@pytest.fixture()
def local_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web_app, "_config", {"privacy_mode": "local_only"})


@pytest.fixture()
def cloud(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web_app, "_config", {"privacy_mode": "cloud",
                                             "llm_provider": "openrouter",
                                             "llm_api_key": "synthetic-not-a-real-key"})


def test_health_describes_local_only_in_plain_words(client, local_only) -> None:
    body = client.get("/api/health").json()

    assert body["llm_status_detail"] == LOCAL_ONLY_SENTENCE
    assert body["llm_status_level"] == "neutral"
    assert body["llm_status_label"] == "Local Only"


def test_the_capability_summary_matches_the_health_detail(client, local_only) -> None:
    assert web_app._capabilities()["summary"] == LOCAL_ONLY_SENTENCE
    assert client.get("/api/health").json()["llm_status_detail"] == LOCAL_ONLY_SENTENCE


def test_cloud_mode_says_classification_is_on_in_the_same_shape(client, cloud) -> None:
    assert web_app._capabilities()["summary"] == CLOUD_SENTENCE
    assert client.get("/api/health").json()["llm_status_detail"] == CLOUD_SENTENCE


def test_the_sentence_never_reads_as_a_fault_or_a_missing_key(client, local_only) -> None:
    detail = client.get("/api/health").json()["llm_status_detail"]

    for alarming in ("key", "missing", "error", "unavailable", "fail", "disabled"):
        assert alarming not in detail.lower(), detail
