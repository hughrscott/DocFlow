"""P06/P07: classification egress, rule/placeholder allowlists, destination confinement."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docflow.llm.gateway import SYSTEM_PROMPTS, CloudPromptGateway, Feature, LocalRule
from docflow.llm.schemas import InvalidModelOutput
from tests.privacy import fakes
from tests.privacy import sentinels as s

DOCUMENT = {
    "pages": [1],
    "institution": "pnc",
    "doc_type": "statement",
    "period": s.PERIOD,
    "account": s.ACCOUNT,
    "text_preview": s.page_text()[:500],
}


@pytest.fixture()
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    (root / "linked").symlink_to(tmp_path / "outside", target_is_directory=True)
    return root


def _config(archive: Path) -> dict:
    return s.synthetic_config(archive_root=str(archive))


def _rules(config: dict) -> list[LocalRule]:
    return [LocalRule.from_yaml(rule) for rule in config["filing_rules"]]


def _reply(**fields):
    def reply(request) -> str:
        body = {"rule_id": None, "confidence": 0.7, "reasoning": "synthetic"}
        for key, value in fields.items():
            body[key] = value(request) if callable(value) else value
        return json.dumps(body)
    return reply


def _classify(archive: Path, reply) -> tuple:
    config = _config(archive)
    transport = fakes.FakeTransport(reply)
    gateway = CloudPromptGateway(config, transport=transport)
    return gateway.classify(DOCUMENT, _rules(config)), transport


def test_classification_egress_hides_rules_entities_context_and_paths(archive: Path) -> None:
    result, transport = _classify(archive, _reply(
        rule_id=lambda r: fakes.tokens(r, "RULE")[0],
        person=lambda r: fakes.data(r)["context"]["user"]["name"],
        confidence=0.91,
    ))
    [request] = transport.requests
    s.assert_no_sentinels(request.body)
    assert str(archive) not in request.body.decode()
    assert fakes.messages(request)[0]["content"] == SYSTEM_PROMPTS[Feature.CLASSIFICATION]
    payload = fakes.data(request)
    assert set(payload["rules"][0]) == {"rule_id", "institution", "doc_types"}
    assert payload["context"]["entities"][0]["directory"].startswith("PATH_")
    assert result.rule is not None and result.rule.key == s.RULE_ID
    assert result.person == s.USER_NAME
    assert result.confidence == 0.91


def test_new_location_suggestion_is_rehydrated_and_confined(archive: Path) -> None:
    result, _ = _classify(archive, _reply(
        suggested_directory=lambda r: fakes.data(r)["context"]["entities"][0]["directory"]
        + "/Invoices",
        suggested_filename=lambda r: fakes.data(r)["context"]["entities"][0]["entity"]
        + " Invoice March2026.pdf",
    ))
    assert result.rule is None
    assert result.relative_directory == s.CORRECTION_DIR
    assert result.filename == f"{s.ENTITY_NAME} Invoice March2026.pdf"


@pytest.mark.parametrize(("fields", "reason"), [
    ({"rule_id": "RULE_bcdfghjkmnpq"}, "unknown_rule_id"),
    ({"rule_id": s.RULE_ID}, "unknown_rule_id"),
    ({"person": "PERSON_bcdfghjkmnpq"}, "unknown_placeholder"),
    ({"person": s.USER_NAME}, "unknown_placeholder"),
    ({"suggested_directory": "/etc/cron.d"}, "unsafe_destination"),
    ({"suggested_directory": "../../outside"}, "unsafe_destination"),
    ({"suggested_directory": "Tax/../../outside"}, "unsafe_destination"),
    ({"suggested_directory": "~/Library"}, "unsafe_destination"),
    ({"suggested_directory": "C:\\\\Windows"}, "unsafe_destination"),
    ({"suggested_directory": "Tax/{year} Taxes"}, "unsafe_destination"),
    ({"suggested_directory": "linked/inside"}, "destination_escape"),
    ({"suggested_directory": lambda r: fakes.tokens(r, "ACCOUNT")[0]}, "disallowed_placeholder"),
    ({"suggested_filename": "../evil.pdf"}, "unsafe_filename"),
    ({"suggested_filename": "evil.sh"}, "unsafe_filename"),
    ({"suggested_filename": ".hidden.pdf"}, "unsafe_filename"),
    ({"suggested_filename": "nested/evil.pdf"}, "unsafe_filename"),
    ({"suggested_filename": "Name{period}.pdf"}, "unsafe_filename"),
    ({"suggested_filename": lambda r: fakes.tokens(r, "EMAIL")[0] + ".pdf"},
     "disallowed_placeholder"),
    ({"confidence": 2}, "schema_violation"),
])
def test_unsafe_classification_output_is_rejected(archive: Path, fields: dict, reason: str) -> None:
    with pytest.raises(InvalidModelOutput) as excinfo:
        _classify(archive, _reply(**fields))
    assert excinfo.value.reason == reason


def test_prompt_injection_in_document_cannot_alter_instructions_or_escape(archive: Path) -> None:
    obeyed = _reply(suggested_directory="/etc/cron.d", suggested_filename="x.pdf")
    config = _config(archive)
    transport = fakes.FakeTransport(obeyed)
    document = {**DOCUMENT, "text_preview": f"{s.INJECTION}\n{s.CODE_FENCE}"}
    with pytest.raises(InvalidModelOutput):
        CloudPromptGateway(config, transport=transport).classify(document, _rules(config))
    [request] = transport.requests
    system, user = fakes.messages(request)
    assert system["content"] == SYSTEM_PROMPTS[Feature.CLASSIFICATION]
    assert "IGNORE ALL PREVIOUS" not in system["content"]
    assert "IGNORE ALL PREVIOUS" in fakes.data(request)["document"]["text_preview"]
    assert json.loads(user["content"])["task"] == "classification"
    assert "/etc/cron.d" not in request.body.decode()
