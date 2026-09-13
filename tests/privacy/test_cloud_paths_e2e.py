"""P03/P08: intercepted end-to-end egress for every real cloud entry path."""
from __future__ import annotations

import json

from docflow.clustering.clusterer import cluster_pages
from docflow.llm.gateway import Feature
from tests.privacy import fakes
from tests.privacy import sentinels as s


def _cluster_reply(request) -> str:
    entity = fakes.tokens(request, "ENTITY")[0]
    return json.dumps({"documents": [{"pages": [1, 2], "doc_type": "statement",
                                      "institution": entity, "confidence": 0.93,
                                      "reasoning": f"letterhead {entity}"}]})


def test_cluster_pages_egress_is_intercepted_and_rehydrated(intercept, network_attempts) -> None:
    intercept.responses = [_cluster_reply]
    [candidate] = cluster_pages(fakes.pages(), s.synthetic_config())
    [request] = intercept.requests
    assert request.feature is Feature.CLUSTERING
    s.assert_no_sentinels(request.body)
    assert candidate.pages == [1, 2]
    assert candidate.raw_signals["clustering_source"] == "llm"
    assert candidate.raw_signals["llm_reasoning"] == f"letterhead {s.ENTITY_LEGAL}"
    assert candidate.clustering_confidence == 0.93
    assert network_attempts == []


def test_cluster_pages_falls_back_locally_on_invalid_output(intercept) -> None:
    intercept.responses = ['{"documents": [{"pages": [1, 1, 2], "confidence": 0.9}]}']
    candidates = cluster_pages(fakes.pages(), s.synthetic_config())
    assert len(intercept.requests) == 1
    assert sorted(p for c in candidates for p in c.pages) == [1, 2]
    assert all(c.raw_signals["clustering_source"] == "rules" for c in candidates)


def test_cluster_pages_local_only_makes_zero_calls(intercept, network_attempts) -> None:
    config = {**s.synthetic_config(), "privacy_mode": "local_only"}
    candidates = cluster_pages(fakes.pages(), config)
    assert intercept.requests == [] and network_attempts == []
    assert sorted(p for c in candidates for p in c.pages) == [1, 2]


def _candidate(**overrides):
    from docflow.clustering.clusterer import DocumentCandidate

    fields = {"pages": [1], "institution": "unknown_bank", "account": s.ACCOUNT,
              "period": "March2026", "doc_type": "statement", "clustering_confidence": 0.8,
              "raw_signals": {"raw_texts": [s.page_text()], "institutions": ["unknown_bank"]}}
    fields.update(overrides)
    return DocumentCandidate(**fields)


def _rules_md(tmp_path) -> str:
    path = tmp_path / "rules.md"
    path.write_text(
        f"# Rules\n\n## {s.RULES_MD_NAME}\n- **Institution**: pnc\n"
        f"- **Account hints**: {s.ACCOUNT_HINT}\n- **File to**: {s.RULE_FILE_TO}\n"
        f"- **Filename**: {s.RULE_TEMPLATE}\n"
    )
    return str(path)


def test_classify_candidates_rules_md_path_is_intercepted(intercept, tmp_path) -> None:
    from docflow.classification.classifier import classify_candidates

    archive = tmp_path / "archive"
    config = {**s.synthetic_config(archive_root=str(archive)), "filing_rules": [],
              "rules_file": _rules_md(tmp_path)}
    intercept.responses = [lambda r: json.dumps({
        "rule_id": fakes.tokens(r, "RULE")[0], "confidence": 0.88, "reasoning": "rule fits"})]
    [decision] = classify_candidates([_candidate()], config)
    [request] = intercept.requests
    assert request.feature is Feature.CLASSIFICATION
    s.assert_no_sentinels(request.body)
    assert str(tmp_path) not in request.body.decode()
    assert decision.rule_matched == f"rules_md:{s.RULES_MD_NAME}"
    assert decision.filename == "ZephyrineKettleworksCheckingMarch2026.pdf"
    assert decision.target_directory == str(archive / s.RULE_FILE_TO)
    assert decision.confidence == 0.88


def test_classify_candidates_legacy_yaml_path_is_intercepted(intercept, tmp_path) -> None:
    from docflow.classification.classifier import classify_candidates

    archive = tmp_path / "archive"
    config = {**s.synthetic_config(archive_root=str(archive)),
              "rules_file": str(tmp_path / "missing-rules.md")}
    intercept.responses = [lambda r: json.dumps({
        "rule_id": None, "confidence": 0.61, "reasoning": "new kind",
        "suggested_directory": "Statements/Synthetic",
        "suggested_filename": "SyntheticStatementMarch2026.pdf"})]
    [decision] = classify_candidates([_candidate()], config)
    [request] = intercept.requests
    s.assert_no_sentinels(request.body)
    assert decision.rule_matched == "llm_suggested"
    assert decision.target_directory == str(archive / "Statements/Synthetic")
    assert decision.filename == "SyntheticStatementMarch2026.pdf"
    assert decision.auto_file is False


def test_classify_candidates_rejected_output_routes_to_review(intercept, tmp_path) -> None:
    from docflow.classification.classifier import classify_candidates

    config = {**s.synthetic_config(archive_root=str(tmp_path / "archive")),
              "rules_file": _rules_md(tmp_path)}
    intercept.responses = [json.dumps({"rule_id": None, "confidence": 0.99,
                                       "suggested_directory": "../../outside",
                                       "suggested_filename": "x.pdf"})]
    [decision] = classify_candidates([_candidate()], config)
    assert len(intercept.requests) == 2  # rules.md path, then legacy YAML path
    for request in intercept.requests:
        s.assert_no_sentinels(request.body)
    assert decision.rule_matched == "none"
    assert decision.confidence == 0.0 and decision.auto_file is False


def test_classify_candidates_local_only_uses_local_rules_only(intercept, tmp_path) -> None:
    from docflow.classification.classifier import classify_candidates

    config = {**s.synthetic_config(archive_root=str(tmp_path / "archive")),
              "privacy_mode": "local_only", "rules_file": _rules_md(tmp_path)}
    matched = _candidate(institution="pnc", account=s.ACCOUNT_HINT)
    decisions = classify_candidates([matched, _candidate()], config)
    assert intercept.requests == []
    assert decisions[0].rule_matched == s.RULE_ID
    assert decisions[1].rule_matched == "none"


def _learning_setup(tmp_path, **overrides):
    archive = tmp_path / "archive"
    archive.mkdir()
    config = {**s.synthetic_config(archive_root=str(archive)), "rules_file": _rules_md(tmp_path),
              **overrides}
    original = {"institution": "pnc", "doc_type": "invoice",
                "suggested_filename": f"Unmatched_{s.ENTITY_NAME}_pages1.pdf",
                "suggested_directory": str(archive / "_Unmatched"),
                "raw_text_preview": s.page_text()}
    return archive, config, original


def test_record_correction_rule_learning_is_intercepted(intercept, tmp_path) -> None:
    from docflow.config.learner import record_correction

    archive, config, original = _learning_setup(tmp_path)
    intercept.responses = [lambda r: json.dumps({
        "add_rule": True, "rule_name": "Synthetic Invoices", "institution": "pnc",
        "doc_types": ["invoice"],
        "file_to": fakes.data(r)["correction"]["corrected_directory"],
        "filename": fakes.data(r)["correction"]["corrected_filename"],
        "reasoning": "new category"})]
    record_correction(original, s.CORRECTION_FILENAME, str(archive / s.CORRECTION_DIR), config,
                      log_path=tmp_path / "state" / "corrections.json")
    [request] = intercept.requests
    assert request.feature is Feature.RULE_LEARNING
    s.assert_no_sentinels(request.body)
    assert str(tmp_path) not in request.body.decode()
    rules_md = (tmp_path / "rules.md").read_text()
    assert "## Synthetic Invoices\n- **Institution**: pnc\n- **Document types**: invoice\n" \
        f"- **File to**: {s.CORRECTION_DIR}\n- **Filename**: {s.CORRECTION_FILENAME}" in rules_md


def test_record_correction_rejected_or_local_only_learns_nothing(intercept, tmp_path) -> None:
    from docflow.config.learner import record_correction

    archive, config, original = _learning_setup(tmp_path)
    before = (tmp_path / "rules.md").read_text()
    intercept.responses = [json.dumps({"add_rule": True, "rule_name": "X\n## Injected",
                                       "file_to": "A", "filename": "a.pdf"})]
    record_correction(original, s.CORRECTION_FILENAME, str(archive / s.CORRECTION_DIR), config,
                      log_path=tmp_path / "state" / "corrections.json")
    assert len(intercept.requests) == 1
    record_correction(original, s.CORRECTION_FILENAME, str(archive / s.CORRECTION_DIR),
                      {**config, "privacy_mode": "local_only"},
                      log_path=tmp_path / "state" / "corrections.json")
    assert len(intercept.requests) == 1
    assert (tmp_path / "rules.md").read_text() == before
