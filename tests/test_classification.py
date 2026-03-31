"""Tests for src/classification/classifier."""
from __future__ import annotations

import pytest

from src.clustering.clusterer import DocumentCandidate
from src.classification.classifier import (
    classify_candidates,
    _match_rule,
    _generate_filename,
    _normalise_period,
)


SAMPLE_CONFIG = {
    "archive_root": "/tmp/test_archive",
    "confidence_threshold": 0.75,
    "filing_rules": [
        {
            "id": "pnc_sulis_solar_checking",
            "match": {
                "institution": "pnc",
                "account_hints": ["sulis", "solar", "1236"],
                "doc_type": ["statement", "checking"],
            },
            "file_to": "SulisSolar/PNC",
            "filename_template": "PNCBankSulisSolarChecking{period}.pdf",
        },
        {
            "id": "guardian_insurance_eob",
            "match": {
                "institution": "guardian",
                "doc_type": ["eob", "explanation_of_benefits"],
            },
            "file_to": "Insurance/Guardian Health",
            "filename_template": "GuardianLifeInsuranceEOB{period}{person}.pdf",
        },
        {
            "id": "lloyds_uk",
            "match": {"institution": "lloyds"},
            "file_to": "UK Finance/Lloyds",
            "filename_template": "LloydsBankAccountStatement{period}.pdf",
        },
        {
            "id": "bettencourt_tax",
            "match": {
                "institution": "bettencourt",
                "doc_type": ["invoice", "statement"],
            },
            "file_to": "Tax/{year} Taxes",
            "filename_template": "BettencourtTaxAdvisors{doc_type}{period}.pdf",
        },
    ],
}


def _make_candidate(
    institution: str = "pnc",
    account: str | None = "1236",
    period: str | None = "February2026",
    doc_type: str | None = "statement",
    pages: list[int] | None = None,
    clustering_confidence: float = 0.8,
) -> DocumentCandidate:
    return DocumentCandidate(
        pages=pages or [1],
        institution=institution,
        account=account,
        period=period,
        doc_type=doc_type,
        clustering_confidence=clustering_confidence,
        raw_signals={
            "institutions": [institution],
            "accounts": [account],
            "periods": [period],
            "doc_types": [doc_type],
        },
    )


class TestMatchRule:
    def test_pnc_sulis_match(self):
        candidate = _make_candidate(institution="pnc", account="1236", doc_type="statement")
        rule = SAMPLE_CONFIG["filing_rules"][0]
        assert _match_rule(rule, candidate) is True

    def test_pnc_no_account_match(self):
        candidate = _make_candidate(institution="pnc", account="9999", doc_type="statement")
        rule = SAMPLE_CONFIG["filing_rules"][0]
        assert _match_rule(rule, candidate) is False

    def test_guardian_eob_match(self):
        candidate = _make_candidate(institution="guardian", doc_type="eob")
        rule = SAMPLE_CONFIG["filing_rules"][1]
        assert _match_rule(rule, candidate) is True

    def test_lloyds_matches_on_institution_only(self):
        candidate = _make_candidate(institution="lloyds", doc_type="statement")
        rule = SAMPLE_CONFIG["filing_rules"][2]
        assert _match_rule(rule, candidate) is True

    def test_wrong_institution_no_match(self):
        candidate = _make_candidate(institution="frost")
        rule = SAMPLE_CONFIG["filing_rules"][0]
        assert _match_rule(rule, candidate) is False


class TestClassifyCandidates:
    def test_pnc_sulis_classified(self):
        candidate = _make_candidate(institution="pnc", account="1236", doc_type="statement")
        decisions = classify_candidates([candidate], SAMPLE_CONFIG)
        assert len(decisions) == 1
        assert decisions[0].rule_matched == "pnc_sulis_solar_checking"
        assert "PNCBankSulisSolarChecking" in decisions[0].filename

    def test_unmatched_goes_to_review(self):
        candidate = _make_candidate(institution="unknown_bank", doc_type="receipt")
        decisions = classify_candidates([candidate], SAMPLE_CONFIG)
        assert len(decisions) == 1
        assert decisions[0].rule_matched == "none"
        assert decisions[0].confidence == 0.0
        assert decisions[0].auto_file is False

    def test_first_match_wins(self):
        """PNC Sulis Solar should match the first rule, not a later PNC rule."""
        candidate = _make_candidate(institution="pnc", account="1236", doc_type="statement")
        decisions = classify_candidates([candidate], SAMPLE_CONFIG)
        assert decisions[0].rule_matched == "pnc_sulis_solar_checking"

    def test_bettencourt_year_in_path(self):
        candidate = _make_candidate(
            institution="bettencourt", doc_type="invoice", period="March2026"
        )
        decisions = classify_candidates([candidate], SAMPLE_CONFIG)
        assert decisions[0].rule_matched == "bettencourt_tax"
        assert "2026 Taxes" in decisions[0].target_directory


class TestNormalisePeriod:
    def test_already_formatted(self):
        assert _normalise_period("February2026") == "February2026"

    def test_none(self):
        assert _normalise_period(None) == ""

    def test_month_slash_year(self):
        assert _normalise_period("02/2026") == "February2026"


class TestGenerateFilename:
    def test_lloyds(self):
        candidate = _make_candidate(
            institution="lloyds", period="August2025"
        )
        result = _generate_filename(
            "LloydsBankAccountStatement{period}.pdf", candidate
        )
        assert result == "LloydsBankAccountStatementAugust2025.pdf"

    def test_missing_period(self):
        candidate = _make_candidate(institution="lloyds", period=None)
        result = _generate_filename(
            "LloydsBankAccountStatement{period}.pdf", candidate
        )
        assert result == "LloydsBankAccountStatement.pdf"
