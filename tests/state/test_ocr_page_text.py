"""Per-page OCR state: the v1 -> v2 migration, write invariants, and durability.

Raw OCR text is local application state. It lives in one dedicated ``job_pages``
column and must never reach candidate JSON, settings, or any archive file.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from docflow.state import database
from docflow.state.database import StateDatabase
from docflow.state.paths import StatePaths
from docflow.state.repositories import (
    PageOcr,
    ScopeRequiredError,
    StateStore,
    UnsafeValueError,
    check_safe_json,
)
from docflow.state.schema import MIGRATIONS, OCR_STATUSES, SCHEMA_VERSION
from tests.state.conftest import tree_digest

V1 = MIGRATIONS[:1]


def _open(paths: StatePaths) -> StateDatabase:
    return StateDatabase(paths).open()


def _job(store: StateStore, scope_id: str, *, pages: int = 3, name: str = "scan.pdf") -> str:
    return store.jobs.create(scope_id, source_fingerprint="sha256:" + "a" * 64,
                             source_name=name, source_locator=f"watch:{name}",
                             page_count=pages).id


def _ocr_rows(db: StateDatabase, job_id: str) -> list[tuple]:
    return [tuple(r) for r in db.connection.execute(
        "SELECT page_number, ocr_status, ocr_text, ocr_error_code FROM job_pages "
        "WHERE job_id = ? ORDER BY page_number", (job_id,))]


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_v1_database_upgrades_and_every_existing_page_defaults_to_missing(
    state_root: Path, archive_root: Path, isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = StatePaths(state_root)
    archive_before = tree_digest(archive_root)
    monkeypatch.setattr(database, "MIGRATIONS", V1)
    with _open(paths) as db:
        store = StateStore(db)
        scope_id = store.scopes.register(archive_root, home=isolated_home).id
        job_id = _job(store, scope_id, pages=4)
        item = store.reviews.create(scope_id, job_id, candidate={"pages": [2]},
                                    suggested_filename="S.pdf",
                                    suggested_relative_directory="Review", confidence=0.4)
        record = store.files.add(scope_id, job_id=job_id, relative_path="Docs/Filed.pdf",
                                 content_sha256="b" * 64, page_numbers=[1], role="filed")
        jobs_before = [tuple(r) for r in db.connection.execute("SELECT * FROM jobs")]
        assert "ocr_status" not in {c[1] for c in
                                   db.connection.execute("PRAGMA table_info(job_pages)")}

    monkeypatch.setattr(database, "MIGRATIONS", MIGRATIONS)
    with _open(paths) as db:
        store = StateStore(db)
        versions = [r[0] for r in db.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version")]
        assert versions == [m.version for m in MIGRATIONS] == [1, 2]
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
        # No historical backfill: every migrated page is 'missing' with no text or error.
        assert _ocr_rows(db, job_id) == [(n, "missing", None, None) for n in (1, 2, 3, 4)]
        # Nothing else moved or was lost.
        assert [tuple(r) for r in db.connection.execute("SELECT * FROM jobs")] == jobs_before
        assert store.reviews.get(scope_id, item.id) == item
        assert store.files.get_by_path(scope_id, "Docs/Filed.pdf") == record
        assert store.jobs.page_ocr(scope_id, job_id, 2) == PageOcr(2, "missing", None, None)

    assert list(paths.backups.glob("pre-upgrade-v1-*.sqlite3"))  # rollback copy kept
    assert tree_digest(archive_root) == archive_before


def test_migrated_column_rejects_an_unknown_ocr_status_at_the_database_level(
    state_root: Path, archive_root: Path, isolated_home: Path,
) -> None:
    paths = StatePaths(state_root)
    with _open(paths) as db:
        store = StateStore(db)
        scope_id = store.scopes.register(archive_root, home=isolated_home).id
        job_id = _job(store, scope_id, pages=1)
        assert set(OCR_STATUSES) == {"missing", "extracted", "no_text", "failed"}
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "UPDATE job_pages SET ocr_status = 'bogus' WHERE job_id = ?", (job_id,))


# ---------------------------------------------------------------------------
# Write invariants
# ---------------------------------------------------------------------------

BAD = {
    "extracted_without_text": PageOcr(1, "extracted", None, None),
    "extracted_with_blank_text": PageOcr(1, "extracted", "   \n\t ", None),
    "extracted_with_error": PageOcr(1, "extracted", "Statement", "ocr_failed"),
    "no_text_with_text": PageOcr(1, "no_text", "Statement", None),
    "no_text_with_error": PageOcr(1, "no_text", None, "ocr_failed"),
    "failed_without_error": PageOcr(1, "failed", None, None),
    "failed_with_text": PageOcr(1, "failed", "Statement", "ocr_failed"),
    "failed_with_blank_error": PageOcr(1, "failed", None, ""),
    # A message, not a code: it leaks a path and could differ on every run.
    "failed_with_unstable_error": PageOcr(1, "failed", None, "Tesseract died: ../tmp/x.pdf"),
    "failed_with_oversized_error": PageOcr(1, "failed", None, "e" * 200),
    "missing_with_text": PageOcr(1, "missing", "Statement", None),
    "missing_with_error": PageOcr(1, "missing", None, "ocr_failed"),
    "unknown_status": PageOcr(1, "partial", "Statement", None),
    "text_with_nul": PageOcr(1, "extracted", "State\0ment", None),
    "non_string_text": PageOcr(1, "extracted", 42, None),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_repository_refuses_inconsistent_ocr_outcomes_and_writes_nothing(
    state_root: Path, archive_root: Path, isolated_home: Path, case: str,
) -> None:
    with _open(StatePaths(state_root)) as db:
        store = StateStore(db)
        scope_id = store.scopes.register(archive_root, home=isolated_home).id
        job_id = _job(store, scope_id, pages=2)

        with pytest.raises(UnsafeValueError):
            store.jobs.record_ocr(scope_id, job_id,
                                  [BAD[case], PageOcr(2, "no_text", None, None)])

        assert _ocr_rows(db, job_id) == [(1, "missing", None, None), (2, "missing", None, None)]


def test_accepted_outcomes_round_trip_every_extraction_state(
    state_root: Path, archive_root: Path, isolated_home: Path,
) -> None:
    with _open(StatePaths(state_root)) as db:
        store = StateStore(db)
        scope_id = store.scopes.register(archive_root, home=isolated_home).id
        job_id = _job(store, scope_id, pages=4)

        store.jobs.record_ocr(scope_id, job_id, [
            PageOcr(1, "extracted", "SYNTHETIC STATEMENT\nPERIOD FEBRUARY 2026", None),
            PageOcr(2, "no_text", None, None),
            PageOcr(3, "failed", None, "ocr_engine_error"),
            PageOcr(4, "missing", None, None),
        ])

        assert _ocr_rows(db, job_id) == [
            (1, "extracted", "SYNTHETIC STATEMENT\nPERIOD FEBRUARY 2026", None),
            (2, "no_text", None, None),
            (3, "failed", None, "ocr_engine_error"),
            (4, "missing", None, None),
        ]
        assert store.jobs.page_ocr(scope_id, job_id, 3) == PageOcr(3, "failed", None,
                                                                   "ocr_engine_error")
        # The page's filing status is untouched by an OCR write.
        assert {r["page_number"]: r["status"] for r in store.jobs.pages(scope_id, job_id)} == {
            1: "pending", 2: "pending", 3: "pending", 4: "pending"}


# ---------------------------------------------------------------------------
# Atomic, complete batches
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("batch", [
    pytest.param([PageOcr(1, "extracted", "one", None)], id="incomplete"),
    pytest.param([PageOcr(1, "extracted", "one", None), PageOcr(2, "no_text", None, None),
                  PageOcr(3, "no_text", None, None), PageOcr(4, "no_text", None, None)],
                 id="page_beyond_the_job"),
    pytest.param([PageOcr(1, "extracted", "one", None), PageOcr(1, "no_text", None, None),
                  PageOcr(3, "no_text", None, None)], id="duplicate_page"),
    pytest.param([PageOcr(1, "extracted", "one", None), PageOcr(2, "no_text", None, None),
                  PageOcr(3, "failed", None, None)], id="invalid_last_entry"),
    pytest.param([], id="empty"),
])
def test_a_partial_or_incomplete_ocr_batch_commits_nothing(
    state_root: Path, archive_root: Path, isolated_home: Path, batch: list[PageOcr],
) -> None:
    with _open(StatePaths(state_root)) as db:
        store = StateStore(db)
        scope_id = store.scopes.register(archive_root, home=isolated_home).id
        job_id = _job(store, scope_id, pages=3)

        with pytest.raises(UnsafeValueError):
            store.jobs.record_ocr(scope_id, job_id, batch)

        assert _ocr_rows(db, job_id) == [(n, "missing", None, None) for n in (1, 2, 3)]


def test_recorded_ocr_text_survives_a_writer_restart(
    state_root: Path, archive_root: Path, isolated_home: Path,
) -> None:
    paths = StatePaths(state_root)
    with _open(paths) as db:
        store = StateStore(db)
        scope_id = store.scopes.register(archive_root, home=isolated_home).id
        job_id = _job(store, scope_id, pages=2)
        store.jobs.record_ocr(scope_id, job_id, [
            PageOcr(1, "extracted", "PAGE ONE TEXT", None),
            PageOcr(2, "failed", None, "ocr_engine_error"),
        ])

    with _open(paths) as db:  # a new process, a new connection
        store = StateStore(db)
        assert store.jobs.page_ocr(scope_id, job_id, 1) == PageOcr(1, "extracted",
                                                                   "PAGE ONE TEXT", None)
        assert store.jobs.page_ocr(scope_id, job_id, 2) == PageOcr(2, "failed", None,
                                                                   "ocr_engine_error")


def test_page_text_is_isolated_to_its_own_archive_scope(
    tmp_path: Path, state_root: Path, archive_root: Path, isolated_home: Path,
) -> None:
    other_root = tmp_path / "other-archive"
    other_root.mkdir()
    with _open(StatePaths(state_root)) as db:
        store = StateStore(db)
        first = store.scopes.register(archive_root, home=isolated_home).id
        second = store.scopes.register(other_root, home=isolated_home).id
        assert first != second
        job_id = _job(store, first, pages=1)
        store.jobs.record_ocr(first, job_id, [PageOcr(1, "extracted", "SCOPE ONE", None)])

        assert store.jobs.page_ocr(second, job_id, 1) is None
        with pytest.raises(UnsafeValueError):
            store.jobs.record_ocr(second, job_id, [PageOcr(1, "extracted", "CROSS", None)])
        with pytest.raises(ScopeRequiredError):
            store.jobs.page_ocr("not-a-scope", job_id, 1)
        assert store.jobs.page_ocr(first, job_id, 1).text == "SCOPE ONE"


def test_raw_ocr_text_still_cannot_be_stored_in_candidate_json_or_settings(
    state_root: Path, archive_root: Path, isolated_home: Path,
) -> None:
    with _open(StatePaths(state_root)) as db:
        store = StateStore(db)
        scope_id = store.scopes.register(archive_root, home=isolated_home).id
        job_id = _job(store, scope_id, pages=1)

        for blob in ({"ocr_text": "SECRET PAGE"}, {"raw_text": "SECRET PAGE"},
                     {"text_preview": "SECRET PAGE"}):
            with pytest.raises(UnsafeValueError):
                check_safe_json(blob)
            with pytest.raises(UnsafeValueError):
                store.reviews.create(scope_id, job_id, candidate={"pages": [1], **blob},
                                     suggested_filename=None,
                                     suggested_relative_directory=None, confidence=0.1)
            with pytest.raises(UnsafeValueError):
                store.settings.set(scope_id, "last_page", blob)
