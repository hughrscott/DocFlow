import pytest

from services.folder_router import FolderRouter
from services.document_analyzer import DocumentAnalyzer


def test_normalize_extraction_maps_core_fields():
    analyzer = object.__new__(DocumentAnalyzer)  # bypass __init__
    # Simulate LLM JSON matching the refined prompt
    llm_json = {
        "issuer": {"name": "Comcast Xfinity", "confidence": 0.97},
        "recipient": {"name": "Vivian Scott", "confidence": 0.9},
        "document_type": {"value": "internet_bill", "confidence": 0.94},
        "period": {"month": "2025-08", "start_date": None, "end_date": None},
        "identifiers": {"account_last4": "1234"},
        "amounts": {"total_due": 89.99, "currency": "USD"},
    }

    # call private method directly
    normalized = DocumentAnalyzer._normalize_extraction(analyzer, llm_json)

    assert normalized["institution"] == "Comcast Xfinity"
    assert normalized["document_type"] == "internet_bill"
    assert normalized["date"] == "2025-08"
    assert normalized["account_number_masked"] == "****1234"
    assert normalized["amount"] == 89.99


def test_folder_router_uses_account_type_and_new_categories(tmp_path):
    router = FolderRouter(str(tmp_path))

    analysis_result = {
        "document_type": "internet_bill",
        "institution": "Comcast Xfinity",
        # account_type is present only in extracted_metadata per analyzer output
        "extracted_metadata": {"account_type": "personal"},
        "confidence_score": 0.88,
    }

    folder, conf = router.propose_folder(analysis_result, create_if_missing=False)
    # Should route to Utilities top-level and include Personal as second level
    assert folder.split("/")[0] == "Utilities"
    assert "Personal" in folder
    assert conf == pytest.approx(0.88)


def test_folder_router_business_location_hint(tmp_path):
    router = FolderRouter(str(tmp_path))

    analysis_result = {
        "document_type": "invoice",
        "institution": "School of Rock",
        "extracted_metadata": {
            "account_type": "business",
            "business_context": {"brand": "School of Rock", "location": "The Heights"},
        },
        "confidence_score": 0.9,
    }

    folder, _ = router.propose_folder(analysis_result, create_if_missing=False)
    # Still under Finance; account_type should produce Business level
    assert folder.startswith("Finance/") or folder.startswith("Documents/")
    assert "Business" in folder


def test_folder_router_prefers_existing_folder_when_signals_match(tmp_path):
    existing = tmp_path / "Banking" / "Personal" / "PNC-Checking"
    existing.mkdir(parents=True)
    (existing / "PNC-Statement-2025-01.pdf").write_text("dummy")

    router = FolderRouter(str(tmp_path))

    analysis_result = {
        "document_type": "bank_statement",
        "institution": "PNC Bank",
        "extracted_metadata": {"account_type": "personal"},
        "confidence_score": 0.92,
    }

    folder, _ = router.propose_folder(analysis_result, create_if_missing=False)
    assert folder == "Banking/Personal/PNC-Checking"
