"""Tests for src/ocr/analyzer — signal extraction and OCR helpers."""
from __future__ import annotations

import pytest

from docflow.ocr.analyzer import (
    PageRecord,
    extract_account_hint,
    extract_doc_type,
    extract_institution,
    extract_page_of_n,
    extract_period,
    _estimate_confidence,
)


# ---------------------------------------------------------------------------
# Institution extraction
# ---------------------------------------------------------------------------

class TestExtractInstitution:
    def test_pnc(self):
        assert extract_institution("Your PNC Bank statement is ready") == "pnc"

    def test_guardian(self):
        assert extract_institution("Guardian Life Insurance Group\nPO Box 981572") == "guardian"

    def test_frost(self):
        assert extract_institution("Cullen/Frost Bankers, Inc.") == "frost"

    def test_lloyds(self):
        assert extract_institution("Lloyds Bank International Limited") == "lloyds"

    def test_bettencourt(self):
        assert extract_institution("Bettencourt Tax Advisors\nInvoice") == "bettencourt"

    def test_pesikoff(self):
        assert extract_institution("Core Primary Care - Dr. Pesikoff") == "pesikoff"

    def test_houston_alarm(self):
        assert extract_institution(
            "City of Houston\nHouston Emergency Center\nBurglar Alarm Administration"
        ) == "houston_alarm"

    def test_bluecross(self):
        assert extract_institution("Blue Cross Blue Shield of Texas") == "bluecross"

    def test_transnational(self):
        assert extract_institution("Transnational Payments\n9550 West Higgins") == "transnational"

    def test_corporate_filings(self):
        assert extract_institution("Corporate Filings LLC\nMaryland") == "corporate_filings"

    def test_no_match(self):
        assert extract_institution("Just some random text with no signals") is None

    def test_empty(self):
        assert extract_institution("") is None


# ---------------------------------------------------------------------------
# Document type extraction
# ---------------------------------------------------------------------------

class TestExtractDocType:
    def test_statement(self):
        assert extract_doc_type("Monthly Statement for Account") == "statement"

    def test_invoice(self):
        assert extract_doc_type("INVOICE\nAmount Due: $150.00") == "invoice"

    def test_eob(self):
        assert extract_doc_type("Explanation of Benefits\nThis is not a bill") == "eob"

    def test_checking(self):
        assert extract_doc_type("Checking Account Summary") == "checking"

    def test_line_of_credit(self):
        assert extract_doc_type("Line of Credit Statement") == "line_of_credit"

    def test_bill(self):
        assert extract_doc_type("Balance Due: $45.00") == "bill"

    def test_visit_summary(self):
        assert extract_doc_type("Office Visit Summary\nDate of Service") == "visit_summary"

    def test_no_match(self):
        assert extract_doc_type("Dear valued customer, thank you") is None


# ---------------------------------------------------------------------------
# Period extraction
# ---------------------------------------------------------------------------

class TestExtractPeriod:
    def test_full_month_year(self):
        assert extract_period("Statement for February 2026") == "February2026"

    def test_abbreviated_month(self):
        assert extract_period("Statement period: Mar 2026") == "March2026"

    def test_statement_date_full_year(self):
        text = "Statement Date: 02/28/2026"
        assert extract_period(text) == "February2026"

    def test_statement_date_short_year(self):
        text = "Statement Date 01/15/26"
        assert extract_period(text) == "January2026"

    def test_bare_date_mm_dd_yyyy(self):
        text = "Processed on 03/15/2026 by teller"
        assert extract_period(text) == "March2026"

    def test_bare_date_mm_dd_yy(self):
        text = "Date: 12/01/25"
        assert extract_period(text) == "December2025"

    def test_no_date(self):
        assert extract_period("No dates in this text at all") is None

    def test_period_ending(self):
        text = "For the period ending 01/31/2026"
        assert extract_period(text) == "2026"


# ---------------------------------------------------------------------------
# Page of N extraction
# ---------------------------------------------------------------------------

class TestExtractPageOfN:
    def test_standard(self):
        assert extract_page_of_n("Page 2 of 4") == "Page 2 of 4"

    def test_lowercase(self):
        assert extract_page_of_n("page 1 of 3") == "page 1 of 3"

    def test_embedded(self):
        result = extract_page_of_n("Some text\nPage 3 of 5\nMore text")
        assert result == "Page 3 of 5"

    def test_no_match(self):
        assert extract_page_of_n("This page has no page indicator") is None


# ---------------------------------------------------------------------------
# Account hint extraction
# ---------------------------------------------------------------------------

class TestExtractAccountHint:
    def test_account_number(self):
        assert extract_account_hint("Account #****7364") == "7364"

    def test_acct_colon(self):
        assert extract_account_hint("Acct: ***5678") == "5678"

    def test_account_with_spaces(self):
        assert extract_account_hint("Account Number: XXXX XXXX 9012") == "9012"

    def test_no_account(self):
        assert extract_account_hint("No account info here") is None


# ---------------------------------------------------------------------------
# Confidence estimation
# ---------------------------------------------------------------------------

class TestEstimateConfidence:
    def test_empty_string(self):
        assert _estimate_confidence("") == 0.0

    def test_clean_text(self):
        text = "PNC Bank National Association Monthly Statement February 2026 " * 10
        conf = _estimate_confidence(text)
        assert conf > 0.7

    def test_garbage_text(self):
        text = "!@#$%^&*(){}|<>?~`" * 5
        conf = _estimate_confidence(text)
        assert conf < 0.4

    def test_short_text(self):
        conf = _estimate_confidence("Hi")
        assert conf < 0.5  # short text penalised


# ---------------------------------------------------------------------------
# PageRecord dataclass
# ---------------------------------------------------------------------------

class TestPageRecord:
    def test_creation(self):
        rec = PageRecord(
            page_number=1,
            raw_text="test",
            institution="pnc",
            account_hint="7364",
            period_hint="February2026",
            page_of_n="Page 1 of 3",
            doc_type_hint="statement",
            confidence=0.85,
            rotation_applied=0,
        )
        assert rec.page_number == 1
        assert rec.institution == "pnc"
        assert rec.confidence == 0.85
