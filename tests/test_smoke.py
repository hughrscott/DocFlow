"""Smoke tests — verify all modules import without error."""
import pytest


def test_import_ingestion_loader():
    try:
        from src.ingestion import loader  # noqa: F401
    except Exception as exc:
        pytest.skip(f"pdf2image/poppler not available: {exc}")


def test_import_ingestion_archiver():
    from src.ingestion import archiver  # noqa: F401


def test_import_ocr_analyzer():
    from src.ocr import analyzer  # noqa: F401


def test_import_clustering_clusterer():
    from src.clustering import clusterer  # noqa: F401


def test_import_classification_classifier():
    from src.classification import classifier  # noqa: F401


def test_import_extraction_extractor():
    from src.extraction import extractor  # noqa: F401


def test_import_filing_confidence_gate():
    from src.filing import confidence_gate  # noqa: F401


def test_import_filing_filer():
    from src.filing import filer  # noqa: F401


def test_import_summary_generator():
    from src.summary import generator  # noqa: F401
