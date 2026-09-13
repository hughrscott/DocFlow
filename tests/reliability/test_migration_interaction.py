"""Phase 1 legacy migration followed by Phase 3 filing and original retention.

Pending review items, correction history, legacy filing history and the legacy
files themselves must survive; retained originals never replace migrated ones.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docflow.filing import dedup
from docflow.filing.operations import DocumentAssignment as A
from docflow.state.migration import LegacySources, migrate_legacy
from tests.reliability.harness import make_env
from tests.reliability.synthetic import sha256_file, tree_digest, write_image_pdf

ORIGINALS = "BeenOrganized033026"
HISTORY_TABLES = ("review_items", "corrections")


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    archive = tmp_path / "archive"
    scan = write_image_pdf(archive / ORIGINALS / "Scan-synthetic-1.pdf", [1, 2, 3])
    write_image_pdf(archive / "Household/PNC/PNCBankChecking.pdf", [40, 41])
    queue = {"items": [
        {"id": "1", "source_pdf": str(scan), "pages": [1, 2], "status": "pending",
         "institution": "synthetic", "doc_type": "statement", "period": "March2026",
         "suggested_filename": "Statement.pdf",
         "suggested_directory": str(archive / "Household/PNC"), "confidence": 0.4},
        {"id": "3", "source_pdf": str(scan), "pages": [3], "status": "corrected",
         "corrected_directory": str(archive / "Tax"), "rule_matched": "synthetic_rule",
         "confidence": 0.5},
    ]}
    (archive / "review_queue.json").write_text(json.dumps(queue))
    (archive / "filing_log_20260330_101500.json").write_text(json.dumps({"entries": [
        {"filename": "PNCBankChecking.pdf", "target_directory": str(archive / "Household/PNC"),
         "pages_extracted": [4, 5], "confidence": 0.9, "filed_at": "2026-03-30T10:15:00"}]}))
    legacy_home = isolated_home / ".docflow"
    legacy_home.mkdir()
    (legacy_home / "content_hashes.json").write_text(json.dumps({"0" * 64: "legacy"}))
    environment = make_env(tmp_path, isolated_home)
    environment.sources = LegacySources(archive_root=archive, user_home=isolated_home,
                                        legacy_home=legacy_home)
    yield environment
    environment.close()


def history(env) -> dict:
    snapshot = {t: sorted(env.rows(f"SELECT * FROM {t}")) for t in HISTORY_TABLES}
    snapshot["legacy_operations"] = sorted(env.rows(
        "SELECT * FROM operations WHERE kind IN ('legacy_filing', 'legacy_migration')"))
    snapshot["legacy_file_records"] = sorted(env.rows(
        "SELECT * FROM file_records WHERE job_id IS NULL OR job_id IN "
        "(SELECT job_id FROM review_items)"))
    return snapshot


def classify(env, admission) -> str:
    assert admission.job_id is not None and admission.state == "ready"
    jobs = env.store.jobs
    assert jobs.transition(env.scope_id, admission.job_id, expected="ready", new="ocr")
    assert jobs.transition(env.scope_id, admission.job_id, expected="ocr", new="classified")
    return admission.job_id


def test_migration_history_survives_filing_retention_and_restart(env, isolated_home) -> None:
    report = migrate_legacy(env.db, env.sources)
    assert report.archive_scope_id == env.scope_id
    before = history(env)
    pending_before = [i.id for i in env.store.reviews.list(env.scope_id)]
    legacy_files = {name: sha256_file(env.archive / name) for name in (
        "review_queue.json", "filing_log_20260330_101500.json",
        f"{ORIGINALS}/Scan-synthetic-1.pdf", "Household/PNC/PNCBankChecking.pdf")}
    legacy_hashes = tree_digest(isolated_home / ".docflow")
    assert len(pending_before) == 1

    # A new, different scan with the same name as the migrated original.
    new_scan_raw = sha256_file(write_image_pdf(env.watch / "Scan-synthetic-1.pdf", [7, 8]))
    new_job = classify(env, env.filer().admit(env.scope_id, "watch:Scan-synthetic-1.pdf"))
    filed = env.filer().file_job(env.scope_id, new_job, [
        A((1, 2), "filed", "Household/PNC", "PNCBankChecking.pdf")],
        original_relative_directory=ORIGINALS)
    # Reprocess the migrated original in place: it must be retained, never removed.
    again = classify(env, env.filer().admit(env.scope_id,
                                            f"archive:{ORIGINALS}/Scan-synthetic-1.pdf"))
    in_place = env.filer().file_job(env.scope_id, again, [A((1, 2, 3), "review", reason="x")],
                                    original_relative_directory=ORIGINALS)
    assert dedup.build_initial_index(env.archive, store=env.store, scope_id=env.scope_id) == 0

    env.reopen()
    env.store.recover_after_restart(env.scope_id)
    assert env.filer().reconcile(env.scope_id) == []
    migrate_legacy(env.db, env.sources)  # idempotent re-run after Phase 3 activity

    assert (filed.job_status, in_place.job_status) == ("completed", "review")
    assert history(env)["legacy_operations"] == before["legacy_operations"]
    assert history(env)["corrections"] == before["corrections"]
    assert all(row in history(env)["review_items"] for row in before["review_items"])
    still_pending = {i.id for i in env.store.reviews.list(env.scope_id, status="pending")}
    assert set(pending_before) <= still_pending
    assert all(row in history(env)["legacy_file_records"]
               for row in before["legacy_file_records"])
    for name, digest in legacy_files.items():
        assert sha256_file(env.archive / name) == digest, name
    assert tree_digest(isolated_home / ".docflow") == legacy_hashes
    assert sha256_file(env.archive / ORIGINALS / "Scan-synthetic-1_2.pdf") == new_scan_raw
    assert not (env.watch / "Scan-synthetic-1.pdf").exists()
    assert (env.archive / "Household/PNC/PNCBankChecking_2.pdf").exists()
    assert sorted(env.rows("SELECT relative_path, role FROM file_records WHERE job_id IN (?, ?)",
                           new_job, again)) == [
        (f"{ORIGINALS}/Scan-synthetic-1.pdf", "original"),
        (f"{ORIGINALS}/Scan-synthetic-1_2.pdf", "original"),
        ("Household/PNC/PNCBankChecking_2.pdf", "filed"),
    ]
