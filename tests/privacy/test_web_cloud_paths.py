"""P03/P08: intercepted egress for web cloud routes (legacy and /api/v1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from docflow.llm.gateway import Feature, TransportFailure
from docflow.web import app as web_app
from tests.privacy import fakes
from tests.privacy import sentinels as s

SUGGESTION = json.dumps({"rule_id": None, "confidence": 0.7, "reasoning": "synthetic",
                         "suggested_directory": "Statements/Synthetic",
                         "suggested_filename": "SyntheticStatement.pdf"})


def _pdf(path: Path, pages: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


@pytest.fixture()
def ocr_text(monkeypatch):
    import pdf2image
    import pytesseract
    from PIL import Image

    text = {"value": s.page_text()}
    monkeypatch.setattr(pdf2image, "convert_from_path",
                        lambda *args, **kwargs: [Image.new("L", (8, 8))])
    monkeypatch.setattr(pytesseract, "image_to_string", lambda image: text["value"])
    return text


@pytest.fixture()
def web(monkeypatch, archive_root: Path, tmp_path: Path, ocr_text):
    config = {**s.synthetic_config(archive_root=str(archive_root)),
              "rules_file": str(tmp_path / "missing-rules.md")}
    monkeypatch.setattr(web_app, "_config", config)
    return TestClient(web_app.app), config


def test_legacy_unmatched_suggest_is_intercepted(intercept, web, archive_root: Path) -> None:
    client, _ = web
    pdf = _pdf(archive_root / "_Unmatched" / "scan.pdf")
    intercept.responses = [SUGGESTION]
    response = client.post("/api/unmatched/suggest", json={"path": str(pdf)})
    assert response.status_code == 200
    [request] = intercept.requests
    assert request.feature is Feature.UNMATCHED_SUGGESTION
    s.assert_no_sentinels(request.body)
    assert str(archive_root) not in request.body.decode()
    assert response.json() == {
        "rule_matched": None, "suggested_filename": "SyntheticStatement.pdf",
        "suggested_directory": "Statements/Synthetic", "doc_type": None, "period": None,
        "confidence": 0.7, "reasoning": "synthetic",
    }


def test_legacy_unmatched_suggest_confines_paths(intercept, web, archive_root: Path,
                                                 tmp_path: Path) -> None:
    client, _ = web
    outside = _pdf(tmp_path / "outside" / "scan.pdf")
    (archive_root / "_Unmatched" / "link.pdf").symlink_to(outside)
    for path in (outside, archive_root / "_Unmatched" / "link.pdf",
                 archive_root / "_Unmatched" / ".." / ".." / "outside" / "scan.pdf"):
        assert client.post("/api/unmatched/suggest", json={"path": str(path)}).status_code == 403
    missing = client.post("/api/unmatched/suggest",
                          json={"path": str(archive_root / "_Unmatched" / "missing.pdf")})
    assert missing.status_code == 404
    assert intercept.requests == []


@pytest.mark.parametrize(("setup", "status", "code", "calls"), [
    ("local_only", 409, "local_only", 0),
    ("ssn", 422, "unresolved_sensitive", 0),
    ("invalid", 422, "invalid_model_output", 1),
    ("down", 502, "transport_unavailable", 1),
])
def test_legacy_unmatched_suggest_refusals(intercept, web, archive_root, ocr_text, monkeypatch,
                                           setup, status, code, calls) -> None:
    client, config = web
    pdf = _pdf(archive_root / "_Unmatched" / "scan.pdf")
    if setup == "local_only":
        monkeypatch.setitem(config, "privacy_mode", "local_only")
    elif setup == "ssn":
        ocr_text["value"] = f"W-2 SSN {s.SSN}\n" + ocr_text["value"]
    elif setup == "invalid":
        intercept.responses = ['{"rule_id": null, "confidence": "high"}']
    else:
        intercept.responses = [TransportFailure("down", code="transport_unavailable")]
    response = client.post("/api/unmatched/suggest", json={"path": str(pdf)})
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert len(intercept.requests) == calls


def test_legacy_test_connection_is_intercepted(intercept, web) -> None:
    client, _ = web
    intercept.responses = ['{"status": "ok"}']
    response = client.post("/api/settings/test-connection")
    assert response.json() == {"status": "connected", "reply": "OK", "model": "synthetic-model"}
    [request] = intercept.requests
    assert request.feature is Feature.CONNECTION_TEST
    s.assert_no_sentinels(request.body)


@pytest.mark.parametrize(("setup", "code", "calls"), [
    ("local_only", "local_only", 0),
    ("down", "transport_unavailable", 1),
    ("invalid", "invalid_model_output", 1),
])
def test_legacy_test_connection_failures_are_coded(intercept, web, monkeypatch,
                                                   setup, code, calls) -> None:
    client, config = web
    if setup == "local_only":
        monkeypatch.setitem(config, "privacy_mode", "local_only")
    elif setup == "down":
        intercept.responses = [TransportFailure(f"echo {s.USER_NAME}", code="transport_unavailable")]
    else:
        intercept.responses = ['{"status": "ok", "extra": 1}']
    body = client.post("/api/settings/test-connection").json()
    assert body["status"] == ("local_only" if setup == "local_only" else "error")
    assert body["error_code"] == code
    assert s.USER_NAME not in json.dumps(body)
    assert len(intercept.requests) == calls


@pytest.fixture()
def v1(monkeypatch, web, store):
    monkeypatch.setattr(web_app, "_state_store", store)
    return web


def _privacy(mode: str = "cloud") -> dict:
    from docflow.llm.gateway import PSEUDONYMIZATION_WARNING

    return {"mode": mode, "warning": PSEUDONYMIZATION_WARNING}


def test_v1_ask_ai_is_intercepted(intercept, v1, scope_id, archive_root) -> None:
    client, _ = v1
    _pdf(archive_root / "_Unmatched" / "scan.pdf")
    intercept.responses = [SUGGESTION]
    response = client.post("/api/v1/ask-ai", json={"archive_scope_id": scope_id,
                                                   "relative_path": "_Unmatched/scan.pdf"})
    assert response.status_code == 200
    [request] = intercept.requests
    assert request.feature is Feature.ASK_AI
    s.assert_no_sentinels(request.body)
    assert str(archive_root) not in request.body.decode()
    assert response.json() == {
        "status": "suggested",
        "error_code": None,
        "suggestion": {"rule_matched": None, "suggested_filename": "SyntheticStatement.pdf",
                       "suggested_relative_directory": "Statements/Synthetic", "doc_type": None,
                       "period": None, "confidence": 0.7, "reasoning": "synthetic"},
        "privacy": _privacy(),
    }


def test_v1_ask_ai_matched_rule_is_built_locally(intercept, v1, scope_id, archive_root,
                                                 monkeypatch, tmp_path) -> None:
    client, config = v1
    rules = tmp_path / "rules.md"
    rules.write_text(f"## {s.RULES_MD_NAME}\n- **Institution**: pnc\n"
                     f"- **File to**: {s.RULE_FILE_TO}/{{year}}\n- **Filename**: {s.RULE_TEMPLATE}\n")
    monkeypatch.setitem(config, "rules_file", str(rules))
    _pdf(archive_root / "_Unmatched" / "scan.pdf")
    intercept.responses = [lambda r: json.dumps({"rule_id": fakes.tokens(r, "RULE")[0],
                                                 "period": "March2026", "confidence": 0.8})]
    suggestion = client.post("/api/v1/ask-ai", json={
        "archive_scope_id": scope_id, "relative_path": "_Unmatched/scan.pdf"}).json()["suggestion"]
    s.assert_no_sentinels(intercept.requests[0].body)
    assert suggestion["rule_matched"] == s.RULES_MD_NAME
    assert suggestion["suggested_filename"] == "ZephyrineKettleworksCheckingMarch2026.pdf"
    assert suggestion["suggested_relative_directory"] == f"{s.RULE_FILE_TO}/2026"


SCOPE = "<registered scope>"


@pytest.mark.parametrize(("payload", "status", "code"), [
    ({}, 422, "invalid_request"),
    ({"relative_path": "_Unmatched/scan.pdf"}, 422, "invalid_request"),
    ({"archive_scope_id": SCOPE, "relative_path": "_Unmatched/scan.pdf", "extra": 1}, 422,
     "invalid_request"),
    ({"archive_scope_id": SCOPE, "relative_path": 7}, 422, "invalid_request"),
    ({"archive_scope_id": "unknown-scope", "relative_path": "_Unmatched/scan.pdf"}, 404,
     "scope_not_found"),
    ({"archive_scope_id": SCOPE, "relative_path": "/etc/scan.pdf"}, 400, "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "../outside/scan.pdf"}, 400, "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "_Unmatched/../../outside/scan.pdf"}, 400,
     "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "~/scan.pdf"}, 400, "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "_Unmatched\\\\scan.pdf"}, 400, "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "_Unmatched/link.pdf"}, 400, "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "linkdir/scan.pdf"}, 400, "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "_Unmatched/notes.txt"}, 400, "invalid_path"),
    ({"archive_scope_id": SCOPE, "relative_path": "_Unmatched/missing.pdf"}, 404,
     "file_not_found"),
])
def test_v1_ask_ai_rejects_bad_requests(intercept, v1, scope_id, archive_root, tmp_path,
                                        payload, status, code) -> None:
    client, _ = v1
    _pdf(archive_root / "_Unmatched" / "scan.pdf")
    (archive_root / "_Unmatched" / "notes.txt").write_text("synthetic")
    outside = _pdf(tmp_path / "outside" / "scan.pdf")
    (archive_root / "_Unmatched" / "link.pdf").symlink_to(outside)
    (archive_root / "linkdir").symlink_to(outside.parent, target_is_directory=True)
    body = {k: scope_id if v == SCOPE else v for k, v in payload.items()}
    response = client.post("/api/v1/ask-ai", json=body)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert intercept.requests == []


@pytest.mark.parametrize(("setup", "status", "code", "mode", "calls"), [
    ("config_local_only", "local_only", "local_only", "local_only", 0),
    ("scope_local_only", "local_only", "local_only", "local_only", 0),
    ("ssn", "blocked", "unresolved_sensitive", "cloud", 0),
    ("invalid", "invalid_model_output", "invalid_model_output", "cloud", 1),
    ("down", "unavailable", "transport_unavailable", "cloud", 1),
])
def test_v1_ask_ai_outcomes(intercept, v1, store, scope_id, archive_root, ocr_text, monkeypatch,
                            setup, status, code, mode, calls) -> None:
    client, config = v1
    _pdf(archive_root / "_Unmatched" / "scan.pdf")
    if setup == "config_local_only":
        monkeypatch.setitem(config, "privacy_mode", "local_only")
    elif setup == "scope_local_only":
        store.settings.set(scope_id, "privacy_mode", "local_only")
    elif setup == "ssn":
        ocr_text["value"] = f"W-2 SSN {s.SSN}\n" + ocr_text["value"]
    elif setup == "invalid":
        intercept.responses = ['{"rule_id": null, "confidence": 7}']
    else:
        intercept.responses = [TransportFailure("down", code="transport_unavailable")]
    body = client.post("/api/v1/ask-ai", json={"archive_scope_id": scope_id,
                                               "relative_path": "_Unmatched/scan.pdf"}).json()
    assert body == {"status": status, "error_code": code, "suggestion": None,
                    "privacy": _privacy(mode)}
    assert len(intercept.requests) == calls


def test_v1_connection_test_is_intercepted(intercept, v1, scope_id) -> None:
    client, _ = v1
    intercept.responses = ['{"status": "ok"}']
    response = client.post("/api/v1/connection-test", json={"archive_scope_id": scope_id})
    assert response.json() == {"status": "connected", "model": "synthetic-model",
                               "error_code": None, "privacy": _privacy()}
    [request] = intercept.requests
    assert request.feature is Feature.CONNECTION_TEST
    s.assert_no_sentinels(request.body)


@pytest.mark.parametrize(("setup", "status", "code", "calls"), [
    ("scope_local_only", "local_only", "local_only", 0),
    ("down", "error", "timeout", 2),
])
def test_v1_connection_test_outcomes(intercept, v1, store, scope_id, setup, status, code,
                                     calls) -> None:
    client, _ = v1
    if setup == "scope_local_only":
        store.settings.set(scope_id, "privacy_mode", "local_only")
    else:
        intercept.responses = [TransportFailure("slow", code="timeout", retryable=True)]
    body = client.post("/api/v1/connection-test", json={"archive_scope_id": scope_id}).json()
    assert body["status"] == status and body["error_code"] == code and body["model"] is None
    assert len(intercept.requests) == calls


@pytest.mark.parametrize(("payload", "status", "code"), [
    ({}, 422, "invalid_request"),
    ({"archive_scope_id": "unknown-scope"}, 404, "scope_not_found"),
])
def test_v1_connection_test_rejects_bad_requests(intercept, v1, payload, status, code) -> None:
    client, _ = v1
    response = client.post("/api/v1/connection-test", json=payload)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert intercept.requests == []


@pytest.mark.parametrize("route", ["/api/v1/ask-ai", "/api/v1/connection-test"])
def test_v1_routes_require_local_state(intercept, web, monkeypatch, route) -> None:
    client, _ = web
    monkeypatch.setattr(web_app, "_state_store", None)
    response = client.post(route, json={"archive_scope_id": "any", "relative_path": "a.pdf"})
    assert (response.status_code, response.json()["error"]["code"]) == (503, "state_unavailable")
    assert intercept.requests == []


def test_settings_expose_privacy_mode_and_warning(web) -> None:
    from docflow.llm.gateway import PSEUDONYMIZATION_WARNING

    client, _ = web
    body = client.get("/api/settings").json()
    assert body["privacy_mode"] == "cloud"
    assert body["privacy_warning"] == PSEUDONYMIZATION_WARNING
    assert "not anonymity" in PSEUDONYMIZATION_WARNING


def test_settings_accept_only_known_privacy_modes(web, monkeypatch) -> None:
    client, config = web
    monkeypatch.setattr(web_app, "_persist_config", lambda: None)
    assert client.post("/api/settings", json={"privacy_mode": "sometimes"}).status_code == 400
    assert config["privacy_mode"] == "cloud"
    assert client.post("/api/settings", json={"privacy_mode": "local_only"}).status_code == 200
    assert config["privacy_mode"] == "local_only"
    assert client.get("/api/settings").json()["privacy_mode"] == "local_only"


def test_shipped_default_config_declares_privacy_mode() -> None:
    import yaml

    root = Path(__file__).resolve().parents[2]
    text = (root / "config" / "default_config.yaml").read_text()
    assert yaml.safe_load(text)["privacy_mode"] in ("cloud", "local_only")
    assert "Pseudonymization is not anonymity" in text


def test_v1_ask_ai_confines_destinations_to_the_selected_scope(intercept, v1, scope_id,
                                                               archive_root, monkeypatch,
                                                               tmp_path) -> None:
    client, config = v1
    other_root = tmp_path / "config-archive"
    (other_root / "linked").mkdir(parents=True)
    monkeypatch.setitem(config, "archive_root", str(other_root))
    (tmp_path / "outside").mkdir()
    (archive_root / "linked").symlink_to(tmp_path / "outside", target_is_directory=True)
    _pdf(archive_root / "_Unmatched" / "scan.pdf")
    intercept.responses = [json.dumps({"rule_id": None, "confidence": 0.9,
                                       "suggested_directory": "linked/escape",
                                       "suggested_filename": "x.pdf"})]
    body = client.post("/api/v1/ask-ai", json={"archive_scope_id": scope_id,
                                               "relative_path": "_Unmatched/scan.pdf"}).json()
    assert (body["status"], body["error_code"]) == ("invalid_model_output", "invalid_model_output")
    assert str(other_root) not in intercept.requests[0].body.decode()
    assert str(archive_root) not in intercept.requests[0].body.decode()


@pytest.mark.parametrize("file_to", ["/Users/example/Absolute", "../outside", "linked/escape"])
def test_rule_derived_suggestions_are_validated_like_model_output(intercept, v1, scope_id,
                                                                  archive_root, monkeypatch,
                                                                  tmp_path, file_to) -> None:
    client, config = v1
    (tmp_path / "outside").mkdir()
    (archive_root / "linked").symlink_to(tmp_path / "outside", target_is_directory=True)
    rules = tmp_path / "rules.md"
    rules.write_text(f"## Synthetic Rule\n- **Institution**: pnc\n- **File to**: {file_to}\n"
                     "- **Filename**: SyntheticRule{period}.pdf\n")
    monkeypatch.setitem(config, "rules_file", str(rules))
    _pdf(archive_root / "_Unmatched" / "scan.pdf")
    intercept.responses = [lambda r: json.dumps({"rule_id": fakes.tokens(r, "RULE")[0],
                                                 "confidence": 0.8})]
    body = client.post("/api/v1/ask-ai", json={"archive_scope_id": scope_id,
                                               "relative_path": "_Unmatched/scan.pdf"}).json()
    assert body["status"] == "suggested"
    assert body["suggestion"]["rule_matched"] == "Synthetic Rule"
    assert body["suggestion"]["suggested_relative_directory"] is None
    assert body["suggestion"]["suggested_filename"] is None


def test_legacy_unmatched_suggest_accepts_originals_in_configured_watch_folder(
    intercept, web, monkeypatch, tmp_path
) -> None:
    client, config = web
    inbox = tmp_path / "inbox"
    monkeypatch.setitem(config, "scan_watch_folder", str(inbox))
    original = _pdf(inbox / "BeenOrganized033026" / "scan.pdf")
    intercept.responses = [SUGGESTION]
    response = client.post("/api/unmatched/suggest", json={"path": str(original)})
    assert response.status_code == 200
    s.assert_no_sentinels(intercept.requests[0].body)
    assert str(inbox) not in intercept.requests[0].body.decode()
