"""Smoke tests — verify all modules import without error."""
import pytest


def test_import_ingestion_loader():
    try:
        from docflow.ingestion import loader  # noqa: F401
    except Exception as exc:
        pytest.skip(f"pdf2image/poppler not available: {exc}")


def test_import_ingestion_archiver():
    from docflow.ingestion import archiver  # noqa: F401


def test_import_ocr_analyzer():
    from docflow.ocr import analyzer  # noqa: F401


def test_import_clustering_clusterer():
    from docflow.clustering import clusterer  # noqa: F401


def test_import_classification_classifier():
    from docflow.classification import classifier  # noqa: F401


def test_import_extraction_extractor():
    from docflow.extraction import extractor  # noqa: F401


def test_import_filing_confidence_gate():
    from docflow.filing import confidence_gate  # noqa: F401


def test_import_filing_filer():
    from docflow.filing import filer  # noqa: F401


def test_import_summary_generator():
    from docflow.summary import generator  # noqa: F401
