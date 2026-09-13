"""Rule learning and connection test through the gateway."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docflow.llm.gateway import SYSTEM_PROMPTS, CloudPromptGateway, Feature, LocalRule
from docflow.llm.schemas import InvalidModelOutput
from tests.privacy import fakes
from tests.privacy import sentinels as s


def _correction(archive: Path) -> dict:
    return {
        "institution": "pnc",
        "doc_type": "invoice",
        "original_filename": f"Unmatched_{s.ENTITY_NAME}_pages1.pdf",
        "original_directory": str(archive / "_Unmatched"),
        "corrected_filename": s.CORRECTION_FILENAME,
        "corrected_directory": s.CORRECTION_DIR,
        "raw_text_preview": s.page_text()[:200],
    }


def _correction_token(request, key: str) -> str:
    return fakes.data(request)["correction"][key]


def _learn(archive: Path, **fields):
    def reply(request) -> str:
        body = {"add_rule": True, "rule_name": "Synthetic Invoices", "institution": "pnc",
                "doc_types": ["invoice"], "file_to": None, "filename": None, "reasoning": "new"}
        for key, value in fields.items():
            body[key] = value(request) if callable(value) else value
        return json.dumps(body)

    config = s.synthetic_config(archive_root=str(archive))
    transport = fakes.FakeTransport(reply)
    rules = [LocalRule.from_yaml(r) for r in config["filing_rules"]]
    rules.append(LocalRule.from_rules_md({"name": s.RULES_MD_NAME, "institution": "pnc",
                                          "file_to": s.RULE_FILE_TO}))
    proposal = CloudPromptGateway(config, transport=transport).learn_rule(_correction(archive), rules)
    return proposal, transport


def test_rule_learning_egress_hides_corrections_and_rules(tmp_path: Path) -> None:
    proposal, transport = _learn(
        tmp_path,
        rule_name=lambda r: fakes.tokens(r, "ENTITY")[0] + " Invoices",
        file_to=lambda r: _correction_token(r, "corrected_directory"),
        filename=lambda r: _correction_token(r, "corrected_filename"),
    )
    [request] = transport.requests
    s.assert_no_sentinels(request.body)
    assert str(tmp_path) not in request.body.decode()
    assert fakes.messages(request)[0]["content"] == SYSTEM_PROMPTS[Feature.RULE_LEARNING]
    assert all(r["rule_id"].startswith("RULE_") for r in fakes.data(request)["existing_rules"])
    assert proposal.rule_name == f"{s.ENTITY_LEGAL} Invoices"
    assert proposal.body == (
        "- **Institution**: pnc\n- **Document types**: invoice\n"
        f"- **File to**: {s.CORRECTION_DIR}\n- **Filename**: {s.CORRECTION_FILENAME}"
    )


def test_rule_learning_accepts_template_variables_and_declines(tmp_path: Path) -> None:
    proposal, _ = _learn(tmp_path, file_to="Invoices/Synthetic",
                         filename="SyntheticInvoice{period}.pdf")
    assert "SyntheticInvoice{period}.pdf" in proposal.body
    declined, _ = _learn(tmp_path, add_rule=False, rule_name=None)
    assert declined is None


@pytest.mark.parametrize(("fields", "reason"), [
    ({"file_to": lambda r: _correction_token(r, "original_directory")}, "unsafe_destination"),
    ({"file_to": "../../outside"}, "unsafe_destination"),
    ({"filename": "../escape.pdf"}, "unsafe_filename"),
    ({"rule_name": "Innocent\n## Injected rule"}, "unsafe_rule_text"),
    ({"institution": "pnc\n- **File to**: /etc"}, "unsafe_rule_text"),
    ({"rule_name": "PERSON_bcdfghjkmnpq Rule"}, "unknown_placeholder"),
    ({"doc_types": ["Not A Type"]}, "schema_violation"),
    ({"rule_body": "- **File to**: /etc"}, "schema_violation"),
    ({"rule_name": None}, "incomplete_rule"),
])
def test_unsafe_rule_learning_output_is_rejected(tmp_path: Path, fields: dict, reason: str) -> None:
    with pytest.raises(InvalidModelOutput) as excinfo:
        _learn(tmp_path, **{"file_to": "Invoices/Synthetic", "filename": "Synthetic.pdf", **fields})
    assert excinfo.value.reason == reason


def test_connection_test_sends_only_a_synthetic_probe() -> None:
    transport = fakes.FakeTransport('{"status": "ok"}')
    result = CloudPromptGateway(s.synthetic_config(), transport=transport).test_connection()
    [request] = transport.requests
    s.assert_no_sentinels(request.body)
    assert fakes.data(request) == {"probe": "connectivity"}
    assert fakes.messages(request)[0]["content"] == SYSTEM_PROMPTS[Feature.CONNECTION_TEST]
    assert result.model == "synthetic-model"


@pytest.mark.parametrize(("raw", "reason"), [
    ('{"status": "ok", "echo": "hi"}', "schema_violation"),
    ('{"status": "maybe"}', "schema_violation"),
    ("OK", "malformed_json"),
    ("", "empty_response"),
])
def test_connection_test_rejects_unexpected_reply(raw: str, reason: str) -> None:
    with pytest.raises(InvalidModelOutput) as excinfo:
        CloudPromptGateway(s.synthetic_config(),
                           transport=fakes.FakeTransport(raw)).test_connection()
    assert excinfo.value.reason == reason
