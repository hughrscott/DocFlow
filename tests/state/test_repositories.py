"""Archive-scoped repositories: explicit scope, compare-and-set, restart recovery."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docflow.state.database import StateDatabase
from docflow.state.paths import StatePaths, UnsafePathError
from docflow.state.repositories import (
    InvalidTransitionError,
    ScopeRequiredError,
    StateStore,
    UnsafeValueError,
)


@pytest.fixture()
def db(state_root: Path):
    with StateDatabase(StatePaths(state_root)) as database:
        yield database


@pytest.fixture()
def store(db: StateDatabase) -> StateStore:
    return StateStore(db)


@pytest.fixture()
def scope_id(store: StateStore, archive_root: Path, isolated_home: Path) -> str:
    return store.scopes.register(archive_root, home=isolated_home).id


def _job(store: StateStore, scope_id: str, name: str = "scan.pdf", pages: int = 3):
    return store.jobs.create(
        scope_id,
        source_fingerprint=f"sha256:{name}",
        source_name=name,
        source_locator=f"upload:{name}",
        page_count=pages,
    )


def test_scope_registration_is_validated_and_stable(
    store: StateStore, archive_root: Path, isolated_home: Path, tmp_path: Path
) -> None:
    first = store.scopes.register(archive_root, home=isolated_home)
    again = store.scopes.register(archive_root, home=isolated_home)
    assert first.id == again.id
    assert first.canonical_root == str(archive_root.resolve())
    with pytest.raises(UnsafePathError):
        store.scopes.register(tmp_path / "missing", home=isolated_home)
    with pytest.raises(UnsafePathError):
        store.scopes.register(store.db.paths.root, home=isolated_home)


@pytest.mark.parametrize("bad_scope", [None, "", "unknown-scope"])
def test_archive_scope_id_is_required(store: StateStore, scope_id: str, bad_scope) -> None:
    with pytest.raises(ScopeRequiredError):
        _job(store, bad_scope)
    with pytest.raises(ScopeRequiredError):
        store.reviews.list(bad_scope)
    with pytest.raises(ScopeRequiredError):
        store.settings.set(bad_scope, "confidence_threshold", 0.8)


def test_records_are_invisible_across_scopes(
    store: StateStore, scope_id: str, tmp_path: Path, isolated_home: Path
) -> None:
    other_root = tmp_path / "other-archive"
    other_root.mkdir()
    other = store.scopes.register(other_root, home=isolated_home).id
    job = _job(store, scope_id)
    assert store.jobs.get(scope_id, job.id) is not None
    assert store.jobs.get(other, job.id) is None
    assert store.jobs.transition(other, job.id, expected="discovered", new="stabilizing") is False
    assert store.jobs.get(scope_id, job.id).status == "discovered"


def test_job_creation_records_pages(store: StateStore, scope_id: str) -> None:
    job = _job(store, scope_id, pages=4)
    assert job.status == "discovered" and job.attempt == 0
    assert store.jobs.page_totals(scope_id, job.id) == {"pending": 4}


@pytest.mark.parametrize("locator", ["/abs/scan.pdf", "~/scan.pdf", "watch:../x.pdf",
                                     "file:scan.pdf", "scan.pdf", "archive:"])
def test_arbitrary_client_paths_rejected(store: StateStore, scope_id: str, locator: str) -> None:
    with pytest.raises(UnsafeValueError):
        store.jobs.create(scope_id, source_fingerprint="sha256:x", source_name="x.pdf",
                          source_locator=locator, page_count=1)


def test_compare_and_set_transitions(store: StateStore, scope_id: str) -> None:
    job = _job(store, scope_id)
    assert store.jobs.transition(scope_id, job.id, expected="discovered", new="stabilizing")
    # A stale writer that still believes the job is discovered loses the race.
    assert not store.jobs.transition(scope_id, job.id, expected="discovered", new="stabilizing")
    assert store.jobs.transition(scope_id, job.id, expected="stabilizing", new="ready")
    assert store.jobs.transition(scope_id, job.id, expected="ready", new="ocr")
    current = store.jobs.get(scope_id, job.id)
    assert current.status == "ocr"
    assert current.attempt == 1


def test_invalid_transitions_rejected(store: StateStore, scope_id: str) -> None:
    job = _job(store, scope_id)
    with pytest.raises(InvalidTransitionError):
        store.jobs.transition(scope_id, job.id, expected="discovered", new="completed")
    with pytest.raises(InvalidTransitionError):
        store.jobs.transition(scope_id, job.id, expected="discovered", new="not-a-status")
    with pytest.raises(InvalidTransitionError):
        store.jobs.transition(scope_id, job.id, expected="undone", new="ready")
    assert store.jobs.get(scope_id, job.id).status == "discovered"


def _advance(store: StateStore, scope_id: str, job_id: str, *statuses: str) -> None:
    current = store.jobs.get(scope_id, job_id).status
    for status in statuses:
        assert store.jobs.transition(scope_id, job_id, expected=current, new=status)
        current = status


def test_privacy_block_creates_pending_review(store: StateStore, scope_id: str) -> None:
    job = _job(store, scope_id)
    _advance(store, scope_id, job.id, "stabilizing", "ready", "ocr")
    item = store.jobs.block_for_privacy(scope_id, job.id, expected="ocr",
                                        error_code="unresolved_sensitive_token")
    assert item is not None and item.status == "pending"
    assert store.jobs.get(scope_id, job.id).status == "privacy_blocked"
    assert store.jobs.get(scope_id, job.id).last_error_code == "unresolved_sensitive_token"
    assert [r.id for r in store.reviews.list(scope_id)] == [item.id]
    assert store.jobs.block_for_privacy(scope_id, job.id, expected="ocr", error_code="x") is None


def test_review_resolution_is_compare_and_set(store: StateStore, scope_id: str) -> None:
    job = _job(store, scope_id)
    item = store.reviews.create(
        scope_id, job.id, candidate={"pages": [1, 2], "institution": "pnc"},
        suggested_filename="PNCBankStatement.pdf", suggested_relative_directory="Household/PNC",
        confidence=0.4,
    )
    assert store.reviews.resolve(scope_id, item.id, expected="pending", new="approved")
    assert not store.reviews.resolve(scope_id, item.id, expected="pending", new="skipped")
    assert store.reviews.list(scope_id) == []
    assert [r.id for r in store.reviews.list(scope_id, status="approved")] == [item.id]
    with pytest.raises(InvalidTransitionError):
        store.reviews.resolve(scope_id, item.id, expected="approved", new="pending")


def test_review_correction_records_normalized_features(store: StateStore, scope_id: str) -> None:
    job = _job(store, scope_id)
    item = store.reviews.create(scope_id, job.id, candidate={"pages": [1]},
                                suggested_filename="a.pdf", suggested_relative_directory="A",
                                confidence=0.2)
    with pytest.raises(UnsafeValueError):
        store.reviews.correct(scope_id, item.id, chosen_relative_directory="B",
                              chosen_rule_id="rule_b",
                              normalized_features={"raw_text": "ocr text"})
    with pytest.raises(UnsafeValueError):
        store.reviews.correct(scope_id, item.id, chosen_relative_directory="/abs/B",
                              chosen_rule_id="rule_b", normalized_features={})
    assert store.reviews.get(scope_id, item.id).status == "pending"
    correction_id = store.reviews.correct(
        scope_id, item.id, chosen_relative_directory="Tax/2026 Taxes", chosen_rule_id="tax",
        normalized_features={"institution": "cpa", "doc_type": "invoice"},
    )
    assert correction_id is not None
    assert store.reviews.get(scope_id, item.id).status == "corrected"
    row = store.db.connection.execute(
        "SELECT * FROM corrections WHERE id = ?", (correction_id,)).fetchone()
    assert row["archive_scope_id"] == scope_id
    assert row["chosen_relative_directory"] == "Tax/2026 Taxes"
    assert store.reviews.correct(scope_id, item.id, chosen_relative_directory="C",
                                 chosen_rule_id=None, normalized_features={}) is None


@pytest.mark.parametrize("candidate", [
    {"pages": [1], "raw_text_preview": "ocr text"},
    {"pages": [1], "signals": {"raw_texts": ["ocr text"]}},
    {"pages": [1], "placeholder_map": {"PERSON_1": "someone"}},
])
def test_review_candidate_rejects_raw_ocr_and_lookup_maps(
    store: StateStore, scope_id: str, candidate: dict
) -> None:
    job = _job(store, scope_id)
    with pytest.raises(UnsafeValueError):
        store.reviews.create(scope_id, job.id, candidate=candidate, suggested_filename="a.pdf",
                             suggested_relative_directory="A", confidence=0.5)


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 1.5])
def test_review_confidence_must_be_finite_unit_interval(
    store: StateStore, scope_id: str, confidence: float
) -> None:
    job = _job(store, scope_id)
    with pytest.raises(UnsafeValueError):
        store.reviews.create(scope_id, job.id, candidate={}, suggested_filename="a.pdf",
                             suggested_relative_directory="A", confidence=confidence)


@pytest.mark.parametrize("directory", ["/abs/dir", "../escape", "A/../../B", "~/Docs"])
def test_review_destination_must_be_archive_relative(
    store: StateStore, scope_id: str, directory: str
) -> None:
    job = _job(store, scope_id)
    with pytest.raises(UnsafeValueError):
        store.reviews.create(scope_id, job.id, candidate={}, suggested_filename="a.pdf",
                             suggested_relative_directory=directory, confidence=0.5)


def test_settings_exclude_secrets_and_absolute_paths(store: StateStore, scope_id: str) -> None:
    store.settings.set(scope_id, "confidence_threshold", 0.8)
    assert store.settings.get(scope_id, "confidence_threshold") == 0.8
    secret_value = "sk-" + "x" * 30
    for key, value in [("llm_api_key", "anything"), ("openrouter_api_key", "anything"),
                       ("access_token", "anything"), ("llm_model", secret_value),
                       ("scan_watch_folder", "/abs/inbox")]:
        with pytest.raises(UnsafeValueError):
            store.settings.set(scope_id, key, value)
    assert set(store.settings.all(scope_id)) == {"confidence_threshold"}


def test_set_if_absent_never_overwrites(store: StateStore, scope_id: str) -> None:
    store.settings.set(scope_id, "confidence_threshold", 0.9)
    assert store.settings.set_if_absent(scope_id, "confidence_threshold", 0.75) is False
    assert store.settings.get(scope_id, "confidence_threshold") == 0.9
    assert store.settings.set_if_absent(scope_id, "llm_provider", "ollama") is True


def test_restart_reloads_pending_and_resets_volatile_model_states(
    state_root: Path, archive_root: Path, isolated_home: Path
) -> None:
    paths = StatePaths(state_root)
    with StateDatabase(paths) as db:
        store = StateStore(db)
        scope = store.scopes.register(archive_root, home=isolated_home).id
        awaiting = _job(store, scope, "a.pdf")
        _advance(store, scope, awaiting.id, "stabilizing", "ready", "ocr", "awaiting_model")
        classified = _job(store, scope, "b.pdf")
        _advance(store, scope, classified.id, "stabilizing", "ready", "ocr", "classified")
        reviewing = _job(store, scope, "c.pdf")
        _advance(store, scope, reviewing.id, "stabilizing", "ready", "ocr", "classified", "review")
        review = store.reviews.create(scope, reviewing.id, candidate={"pages": [1]},
                                      suggested_filename="c.pdf",
                                      suggested_relative_directory="C", confidence=0.3)
        done = _job(store, scope, "d.pdf")
        _advance(store, scope, done.id, "stabilizing", "ready", "ocr", "classified", "filing",
                 "completed")
        # Simulated crash: the memory-only lookup map dies with the process.

    with StateDatabase(paths) as db:
        store = StateStore(db)
        report = store.recover_after_restart(scope)
        assert set(report.reset_job_ids) == {awaiting.id, classified.id}
        for job_id in report.reset_job_ids:
            job = store.jobs.get(scope, job_id)
            assert job.status == "ocr"
            assert job.last_error_code == "volatile_state_lost"
        assert {j.id for j in report.pending_jobs} == {awaiting.id, classified.id, reviewing.id}
        assert [r.id for r in report.pending_reviews] == [review.id]
        # Second recovery is a no-op.
        assert store.recover_after_restart(scope).reset_job_ids == []
        columns = {
            row[1].lower()
            for table in ("jobs", "job_pages", "review_items", "operations", "corrections")
            for row in db.connection.execute(f"PRAGMA table_info({table})")
        }
        assert not any(word in col for col in columns
                       for word in ("lookup", "placeholder", "raw", "ocr_text", "preview"))
        dump = json.dumps([list(r) for r in db.connection.execute("SELECT * FROM jobs")])
        assert "lookup" not in dump
