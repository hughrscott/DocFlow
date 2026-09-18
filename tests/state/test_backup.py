"""S05: SQLite backup/restore integrity and redacted support export."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from docflow.state.backup import (
    BackupError,
    BackupIntegrityError,
    create_backup,
    restore_backup,
    verify_database_file,
    write_support_export,
)
from docflow.state.database import StateDatabase, integrity_problems
from docflow.state.paths import StatePaths
from docflow.state.repositories import StateStore
from tests.state.conftest import tree_digest


@pytest.fixture()
def seeded(state_root: Path, archive_root: Path, isolated_home: Path):
    with StateDatabase(StatePaths(state_root)) as db:
        store = StateStore(db)
        scope = store.scopes.register(archive_root, home=isolated_home).id
        job = store.jobs.create(scope, source_fingerprint="sha256:abc", source_name="scan.pdf",
                                source_locator="upload:scan.pdf", page_count=2)
        store.reviews.create(scope, job.id, candidate={"pages": [1, 2], "institution": "pnc"},
                             suggested_filename="PNCBankStatement.pdf",
                             suggested_relative_directory="Household/PNC", confidence=0.5)
        store.settings.set(scope, "scan_watch_folder", "~/DocFlowExample/inbox")
        store.settings.set(scope, "llm_model", "synthetic-model")
        yield db, store, scope


def _count(db: StateDatabase, table: str) -> int:
    return db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_backup_is_outside_archive_and_passes_integrity(seeded, archive_root: Path) -> None:
    db, _, _ = seeded
    before = tree_digest(archive_root)
    backup = create_backup(db)
    assert db.paths.backups in backup.parents
    assert archive_root.resolve() not in backup.resolve().parents
    verify_database_file(backup)
    check = sqlite3.connect(backup)
    try:
        assert integrity_problems(check) == []
        assert check.execute("SELECT COUNT(*) FROM review_items").fetchone()[0] == 1
    finally:
        check.close()
    assert tree_digest(archive_root) == before


def test_backup_requires_quiesced_writer(seeded) -> None:
    db, _, _ = seeded
    with db.transaction(), pytest.raises(BackupError):
        create_backup(db)


def test_backup_requires_open_writer(state_root: Path) -> None:
    with pytest.raises(BackupError):
        create_backup(StateDatabase(StatePaths(state_root)))


def test_restore_returns_to_backup_state(seeded) -> None:
    db, store, scope = seeded
    backup = create_backup(db)
    job = store.jobs.create(scope, source_fingerprint="sha256:later", source_name="later.pdf",
                            source_locator="upload:later.pdf", page_count=1)
    assert _count(db, "jobs") == 2
    restore_backup(db, backup)
    assert _count(db, "jobs") == 1
    assert store.jobs.get(scope, job.id) is None
    assert integrity_problems(db.connection) == []
    assert any(p.name.startswith("pre-restore-") for p in db.paths.backups.iterdir())


def test_restore_rejects_corrupt_backup(seeded, tmp_path: Path) -> None:
    db, _, _ = seeded
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database" * 100)
    with pytest.raises(BackupIntegrityError):
        restore_backup(db, corrupt)
    assert _count(db, "jobs") == 1


def test_restore_rejects_backup_with_foreign_key_violations(seeded) -> None:
    db, _, _ = seeded
    backup = create_backup(db)
    raw = sqlite3.connect(backup)  # foreign keys off by default on a raw handle
    raw.execute("INSERT INTO settings(archive_scope_id, key, value_json, updated_at) "
                "VALUES ('orphan-scope', 'k', '1', 't')")
    raw.commit()
    raw.close()
    with pytest.raises(BackupIntegrityError):
        verify_database_file(backup)
    with pytest.raises(BackupIntegrityError):
        restore_backup(db, backup)
    assert _count(db, "settings") == 2


def test_state_lifecycle_leaves_only_pdfs_in_archive(seeded, archive_root: Path) -> None:
    db, _, _ = seeded
    backup = create_backup(db)
    write_support_export(db)
    restore_backup(db, backup)
    assert {p.suffix for p in archive_root.rglob("*") if p.is_file()} == {".pdf"}


def test_support_export_is_redacted(seeded, archive_root: Path, isolated_home: Path) -> None:
    db, _, scope = seeded
    export = write_support_export(db)
    assert db.paths.exports in export.parents
    text = export.read_text()
    data = json.loads(text)
    assert data["integrity"]["ok"] is True
    [scope_summary] = data["scopes"]
    assert scope_summary["archive_scope_id"] == scope
    assert scope_summary["jobs_by_status"] == {"discovered": 1}
    assert scope_summary["review_items_by_status"] == {"pending": 1}
    assert sorted(scope_summary["settings_keys"]) == ["llm_model", "scan_watch_folder"]
    for forbidden in (str(archive_root), str(isolated_home), "~/", "DocFlowExample",
                      "PNCBankStatement", "Household/PNC", "synthetic-model"):
        assert forbidden not in text
