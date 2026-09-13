"""Legacy public adapters keep their signatures but follow Phase 3 filing rules."""
from __future__ import annotations

from pathlib import Path

import pytest

from docflow.classification.classifier import FilingDecision
from docflow.clustering.clusterer import DocumentCandidate
from docflow.extraction.extractor import extract_documents
from docflow.ingestion.loader import fingerprint_pdf
from tests.reliability.synthetic import sha256_file, tree_digest, write_image_pdf


def decision(pages: list[int], directory: Path, filename: str) -> FilingDecision:
    candidate = DocumentCandidate(pages=pages, institution="synthetic", account=None,
                                  period="March2026", doc_type="statement",
                                  clustering_confidence=0.9)
    return FilingDecision(candidate=candidate, filename=filename,
                          target_directory=str(directory), rule_matched="synthetic_rule",
                          confidence=0.9, auto_file=True, notes=None)


@pytest.fixture()
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    return root


def test_extract_same_name_and_page_count_different_content_is_not_skipped(tmp_path, archive):
    existing = write_image_pdf(archive / "Tax" / "Invoice.pdf", [11, 12])
    before = sha256_file(existing)
    source = write_image_pdf(tmp_path / "scan.pdf", [1, 2])

    written = extract_documents(source, [decision([1, 2], archive / "Tax", "Invoice.pdf")],
                                {"archive_root": str(archive)})

    assert written == [archive / "Tax" / "Invoice_2.pdf"]
    assert sha256_file(existing) == before
    assert fingerprint_pdf(written[0]).document_sha256 != fingerprint_pdf(existing).document_sha256


def test_extract_exact_duplicate_creates_no_second_pdf(tmp_path, archive):
    existing = write_image_pdf(archive / "Tax" / "Invoice.pdf", [1, 2], title="earlier export")
    before = tree_digest(archive)
    source = write_image_pdf(tmp_path / "scan.pdf", [1, 2])

    written = extract_documents(source, [decision([1, 2], archive / "Tax", "Invoice.pdf")],
                                {"archive_root": str(archive)})

    assert written == [existing]
    assert tree_digest(archive) == before


# Legacy callers pass only auto-filed decisions, so uncovered pages are allowed here;
# duplicate, out-of-range and empty assignments still fail closed.
@pytest.mark.parametrize("pages", [[[1, 2], [2]], [[1], [3]], [[0, 1]], [[]]],
                         ids=["duplicate", "out_of_range", "zero", "empty"])
def test_extract_invalid_page_assignments_fail_closed_before_writing(tmp_path, archive, pages):
    from docflow.filing.operations import PageAccountingError

    source = write_image_pdf(tmp_path / "scan.pdf", [1, 2])
    decisions = [decision(p, archive / "Docs", f"Doc{i}.pdf") for i, p in enumerate(pages)]

    with pytest.raises(PageAccountingError):
        extract_documents(source, decisions, {"archive_root": str(archive)})

    assert tree_digest(archive) == {}


def test_extract_never_writes_legacy_hash_json(tmp_path, archive, isolated_home):
    home = isolated_home
    source = write_image_pdf(tmp_path / "scan.pdf", [1])

    extract_documents(source, [decision([1], archive / "Docs", "Doc.pdf")],
                      {"archive_root": str(archive)})

    assert not (home / ".docflow").exists()
    assert tree_digest(archive).keys() == {"Docs/Doc.pdf"}


def test_legacy_content_hash_distinguishes_equal_dimension_image_only_pdfs(tmp_path):
    from docflow.filing.dedup import hash_pdf_content

    first = write_image_pdf(tmp_path / "a.pdf", [1, 2])
    second = write_image_pdf(tmp_path / "b.pdf", [1, 3])
    assert hash_pdf_content(first) != hash_pdf_content(second)
    assert hash_pdf_content(first) == fingerprint_pdf(first).document_sha256


def test_dedup_adapters_without_state_never_touch_legacy_hash_json(tmp_path, archive,
                                                                   isolated_home):
    from docflow.filing import dedup

    legacy = isolated_home / ".docflow" / "content_hashes.json"
    legacy.parent.mkdir()
    legacy.write_text('{"' + "0" * 64 + '": "Household/PNC/Statement.pdf"}')
    before = tree_digest(isolated_home)
    pdf = write_image_pdf(archive / "Docs" / "Doc.pdf", [1])

    assert dedup.is_empty() is False
    assert dedup.build_initial_index(archive) == 0
    assert dedup.is_duplicate(pdf) == (False, None)
    assert dedup.register_file(pdf) is None
    assert tree_digest(isolated_home) == before
    assert tree_digest(archive).keys() == {"Docs/Doc.pdf"}


def test_dedup_index_lives_in_sqlite_file_records(tmp_path, archive, isolated_home):
    from docflow.filing import dedup
    from docflow.state.database import StateDatabase
    from docflow.state.paths import StatePaths
    from docflow.state.repositories import StateStore

    statement = write_image_pdf(archive / "Household" / "Statement.pdf", [1, 2])
    write_image_pdf(archive / "_Unmatched" / "skip.pdf", [3])
    write_image_pdf(archive / ".hidden" / "skip.pdf", [4])
    with StateDatabase(StatePaths(tmp_path / "state")) as db:
        store = StateStore(db)
        scope = store.scopes.register(archive, home=isolated_home).id
        store.files.add(scope, job_id=None, relative_path="Household/Statement.pdf",
                        content_sha256="f" * 64, page_numbers=[1, 2], role="filed")
        assert dedup.is_empty(store=store, scope_id=scope) is False
        other = write_image_pdf(archive / "Tax" / "Bill.pdf", [5])

        assert dedup.build_initial_index(archive, store=store, scope_id=scope) == 1
        assert dedup.build_initial_index(archive, store=store, scope_id=scope) == 0
        rows = sorted(tuple(r) for r in db.connection.execute(
            "SELECT relative_path, content_sha256 FROM file_records"))
        copy = write_image_pdf(tmp_path / "copy.pdf", [5], title="re-export")
        changed = write_image_pdf(tmp_path / "changed.pdf", [6])
        dup = dedup.is_duplicate(copy, store=store, scope_id=scope)
        not_dup = dedup.is_duplicate(changed, store=store, scope_id=scope)
        other.write_bytes(statement.read_bytes())  # recorded file modified afterwards
        stale = dedup.is_duplicate(copy, store=store, scope_id=scope)

    assert rows == [("Household/Statement.pdf", "f" * 64),
                    ("Tax/Bill.pdf", fingerprint_pdf(copy).document_sha256)]
    assert dup == (True, "Tax/Bill.pdf")
    assert not_dup == (False, None)
    assert stale == (False, None)


def test_archive_original_never_overwrites_and_both_originals_remain(tmp_path):
    from datetime import UTC, datetime

    from docflow.ingestion.archiver import archive_original

    watch = tmp_path / "watch"
    folder = watch / f"BeenOrganized{datetime.now(UTC).astimezone().strftime('%m%d%y')}"
    earlier = write_image_pdf(folder / "scan.pdf", [21, 22])
    earlier_raw = sha256_file(earlier)
    source = write_image_pdf(tmp_path / "upload" / "scan.pdf", [1, 2])
    source_raw = sha256_file(source)

    dest = archive_original(source, {"scan_watch_folder": str(watch)})

    assert dest == folder / "scan_2.pdf"
    assert sha256_file(earlier) == earlier_raw
    assert sha256_file(dest) == source_raw
    assert not source.exists()


def test_archive_original_failure_preserves_source(tmp_path, monkeypatch):
    from docflow.filing import operations
    from docflow.ingestion.archiver import archive_original

    source = write_image_pdf(tmp_path / "upload" / "scan.pdf", [1, 2])
    source_raw = sha256_file(source)

    def unavailable(src, dst):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(operations.os, "link", unavailable)
    with pytest.raises(OSError):
        archive_original(source, {"scan_watch_folder": str(tmp_path / "watch")})

    assert sha256_file(source) == source_raw
    assert not [p for p in (tmp_path / "watch").rglob("*") if p.is_file()]


@pytest.mark.parametrize("same_bytes", [False, True], ids=["different_copy", "identical_copy"])
def test_web_pipeline_removes_watch_copy_only_when_identical(tmp_path, archive, monkeypatch,
                                                            same_bytes):
    import asyncio

    from docflow.ingestion import loader
    from docflow.ocr import analyzer
    from docflow.web import app as web_app
    from tests.privacy import fakes
    from tests.privacy import sentinels as s

    def local_pages(images):
        records = fakes.pages(len(images))
        for record in records:
            record.institution = record.account_hint = None
        return records

    monkeypatch.setattr(loader, "load_pdf", lambda path: ["page-image-1", "page-image-2"])
    monkeypatch.setattr(analyzer, "analyze_pages", local_pages)
    from docflow.web.bootstrap import open_local_state
    from tests.reliability.harness import quick_observe

    inbox = tmp_path / "inbox"
    watch_copy = write_image_pdf(inbox / "scan.pdf", [1, 2] if same_bytes else [8, 9])
    copy_raw = sha256_file(watch_copy)
    config = {**s.synthetic_config(archive_root=str(archive)), "scan_watch_folder": str(inbox),
              "rules_file": str(tmp_path / "missing.md"), "filing_rules": [],
              "privacy_mode": "local_only"}
    state = open_local_state(config)
    state.filer.observe = quick_observe
    write_image_pdf(state.filer.source_roots["upload"] / "scan.pdf", [1, 2])
    monkeypatch.setattr(web_app, "_config", config)
    monkeypatch.setattr(web_app, "_state_store", state.store)
    monkeypatch.setattr(web_app, "_filer", state.filer)
    monkeypatch.setattr(web_app, "_active_scope_id", state.scope_id)
    monkeypatch.setattr(web_app, "_processing_state", {"job": {}})

    try:
        asyncio.run(web_app._run_pipeline_async("job", "upload:scan.pdf"))
    finally:
        state.close()

    assert web_app._processing_state["job"]["status"] == "completed"
    retained = list(archive.glob("BeenOrganized*/scan.pdf"))
    assert len(retained) == 1
    if same_bytes:
        assert not watch_copy.exists()
    else:
        assert sha256_file(watch_copy) == copy_raw
