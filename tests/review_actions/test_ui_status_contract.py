"""UI contract for mode-aware status surfaces.

The shipped pages run in Node against a recording DOM and a scripted ``fetch``
(``ui_harness.js``); nothing is served. These tests exercise the real shared footer
(``shared.js`` → ``#llm-status``) and the real settings System Health panel
(``settings.html`` → ``#health-api``), not the dashboard capability note.

Local-only mode is a configured state, not a fault: no status surface may render it
with alarming missing-key copy or danger styling.
"""
from __future__ import annotations

from tests.review_actions.test_ui_contract import SCOPE, run_ui

ALARMING = ("No API Key", "no api key", "text-danger", "bg-danger", "danger")
LOCAL_ONLY_DETAIL = "OCR runs locally. AI classification is off."


def _health(**fields) -> dict:
    body = {
        "watch_folder": "/synthetic/inbox", "archive_root": "/synthetic/archive",
        "privacy_mode": "local_only", "text_extraction": True, "ai_classification": False,
        "llm_ready": False, "llm_status": "local_only", "llm_status_label": "Local Only",
        "llm_status_level": "neutral", "llm_status_detail": LOCAL_ONLY_DETAIL,
        "model": "synthetic-model", "threshold": 0.75,
    }
    return {"method": "GET", "url": "^/api/health$", "body": {**body, **fields}}


def _missing_key() -> dict:
    return _health(privacy_mode="cloud", ai_classification=True, llm_status="missing_key",
                   llm_status_label="No API Key", llm_status_level="warning",
                   llm_status_detail="Add an API key to enable AI classification.")


def _review_page(health: dict) -> list[dict]:
    return [
        {"method": "GET", "url": "^/api/v1/archive-scopes/active$",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": "^/api/v1/review-items\\?",
         "body": {"archive_scope_id": SCOPE, "status": "pending", "job_id": None, "items": []}},
        {"method": "GET", "url": "^/api/unmatched$", "body": {"files": [], "count": 0}},
        health,
    ]


def _settings_page(health: dict, connection: dict | None = None) -> list[dict]:
    responses = [
        {"method": "GET", "url": "^/api/settings$", "body": {
            "archive_root": "/synthetic/archive", "scan_watch_folder": "/synthetic/inbox",
            "confidence_threshold": 0.75, "llm_provider": "openrouter",
            "llm_model": "synthetic-model", "llm_base_url": "", "privacy_mode": "local_only",
            "privacy_warning": "Pseudonymization is not anonymity."}},
        {"method": "GET", "url": "^/api/settings/rules$", "body": {"rules": []}},
        {"method": "GET", "url": "^/api/settings/entities$",
         "body": {"family": [], "entities": []}},
        {"method": "GET", "url": "^/api/v1/archive-scopes/active$",
         "body": {"archive_scope": {"id": SCOPE}}},
        {"method": "GET", "url": "^/api/v1/review-items\\?",
         "body": {"archive_scope_id": SCOPE, "status": "pending", "items": []}},
        health,
    ]
    if connection is not None:
        responses.append({"method": "POST", "url": "^/api/settings/test-connection$",
                          "body": connection})
        responses.append({"method": "POST", "url": "^/api/settings$", "body": {"status": "updated"}})
    return responses


def _rendered(result: dict) -> str:
    """Everything a browser would have shown or interpreted as markup."""
    return "\n".join([*result["texts"],
                      *(text for snap in result["snapshots"] for text in snap["texts"]),
                      *(write["html"] for write in result["html_writes"])])


def _shown(value) -> str:
    return "\n".join(str(part) for part in value if part)


# -- shared footer ----------------------------------------------------------

FOOTER_STEP = ("(() => { const el = document.getElementById('llm-status');"
               " return [el.textContent, el.className, el.getAttribute('title')]; })()")


def test_shared_footer_shows_local_only_neutrally(tmp_path) -> None:
    result = run_ui(tmp_path, _review_page(_health()), [FOOTER_STEP], page="review.html")

    text, class_name, title = result["snapshots"][0]["value"]
    assert text == "Local Only"
    assert "danger" not in class_name
    assert title == LOCAL_ONLY_DETAIL
    assert "No API Key" not in _rendered(result)


def test_shared_footer_keeps_the_missing_key_warning_in_cloud_mode(tmp_path) -> None:
    result = run_ui(tmp_path, _review_page(_missing_key()), [FOOTER_STEP], page="review.html")

    text, class_name, _title = result["snapshots"][0]["value"]
    assert text == "No API Key"
    assert "text-danger" in class_name


def test_shared_footer_never_invents_a_missing_key_from_an_unknown_status(tmp_path) -> None:
    """An older or partial health body must not be rendered as a key failure."""
    result = run_ui(tmp_path, _review_page({"method": "GET", "url": "^/api/health$", "body": {}}),
                    [FOOTER_STEP], page="review.html")

    text, class_name, _title = result["snapshots"][0]["value"]
    assert "No API Key" not in text
    assert "danger" not in class_name


# -- settings System Health -------------------------------------------------

HEALTH_STEP = ("(() => { const el = document.getElementById('health-api');"
               " return [el.textContent, el.innerHTML, el.getAttribute('title'),"
               " ...el.children.map(c => c.className)]; })()")


def test_settings_health_shows_local_only_neutrally(tmp_path) -> None:
    result = run_ui(tmp_path, _settings_page(_health()), [HEALTH_STEP], page="settings.html",
                    location={"pathname": "/settings"})

    shown = _shown(result["snapshots"][0]["value"])
    assert "Local Only" in shown
    assert not any(marker in shown for marker in ALARMING)
    assert "No API Key" not in _rendered(result)


def test_settings_health_keeps_the_missing_key_warning_in_cloud_mode(tmp_path) -> None:
    result = run_ui(tmp_path, _settings_page(_missing_key()), [HEALTH_STEP], page="settings.html",
                    location={"pathname": "/settings"})

    shown = _shown(result["snapshots"][0]["value"])
    assert "No API Key" in shown
    assert "danger" in shown


# -- test connection --------------------------------------------------------

TOASTS_STEP = ("(() => { const c = document.getElementById('toast-container');"
               " return c ? c.children.map(t => [t.className, t.textContent,"
               " t.getAttribute('role')]) : []; })()")

LOCAL_ONLY_CONNECTION = {"status": "local_only", "error_code": "local_only",
                         "error": "Local-only mode is on: no model connection was attempted."}


def test_test_connection_in_local_only_is_not_reported_as_a_failure(tmp_path) -> None:
    result = run_ui(tmp_path, _settings_page(_health(), LOCAL_ONLY_CONNECTION),
                    ["testConnection()", TOASTS_STEP], page="settings.html",
                    location={"pathname": "/settings"})

    toasts = result["snapshots"][1]["value"]
    connection = [t for t in toasts if "Local-only mode" in t[1]]
    assert connection, f"no local-only toast among {toasts}"
    [(class_name, message, role)] = connection
    assert "danger" not in class_name
    assert role != "alert"
    assert "failed" not in message.lower()
