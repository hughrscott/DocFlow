"""P5-B01: explicit schema version (PRAGMA user_version), upgrade, rollback, compatibility."""
from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from docflow.state import database
from docflow.state.backup import BackupIntegrityError, verify_database_file
from docflow.state.database import SchemaVersionError, StateDatabase
from docflow.state.paths import StatePaths
from docflow.state.repositories import StateStore
from docflow.state.schema import MIGRATIONS, SCHEMA_VERSION, Migration
from tests.state.conftest import tree_digest


def _user_version(path: Path) -> int:
    raw = sqlite3.connect(path)
    try:
        return raw.execute("PRAGMA user_version").fetchone()[0]
    finally:
        raw.close()


def test_fresh_database_records_schema_version_in_user_version(state_root: Path) -> None:
    paths = StatePaths(state_root)
    with StateDatabase(paths) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert _user_version(paths.database) == SCHEMA_VERSION == 1


def _set_user_version(path: Path, version: int) -> None:
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {version}")
    raw.commit()
    raw.close()


def test_phase_1_4_database_without_user_version_is_stamped_without_rerunning(
    state_root: Path, archive_root: Path, isolated_home: Path,
) -> None:
    paths = StatePaths(state_root)
    with StateDatabase(paths) as db:
        scope = StateStore(db).scopes.register(archive_root, home=isolated_home)
        applied = db.connection.execute("SELECT * FROM schema_migrations").fetchall()
    _set_user_version(paths.database, 0)  # as written by the pre-user_version runner

    with StateDatabase(paths) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert [tuple(r) for r in db.connection.execute("SELECT * FROM schema_migrations")] \
            == [tuple(r) for r in applied]
        assert StateStore(db).scopes.get(scope.id).id == scope.id


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("newer", ["user_version_only", "recorded_migration"])
def test_database_from_newer_docflow_is_refused_unchanged(state_root: Path, newer: str) -> None:
    paths = StatePaths(state_root)
    with StateDatabase(paths):
        pass
    future = SCHEMA_VERSION + 1
    raw = sqlite3.connect(paths.database)
    if newer == "recorded_migration":
        raw.execute("INSERT INTO schema_migrations VALUES (?, '2027-01-01T00:00:00+00:00', ?)",
                    (future, "f" * 64))
    raw.execute(f"PRAGMA user_version = {future}")
    raw.commit()
    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw.close()
    before = _digest(paths.database)

    with pytest.raises(SchemaVersionError) as excinfo:
        StateDatabase(paths).open()

    assert excinfo.value.found == future and excinfo.value.supported == SCHEMA_VERSION
    assert _digest(paths.database) == before
    assert _user_version(paths.database) == future
    with pytest.raises(BackupIntegrityError):
        verify_database_file(paths.database)
    # The refused open released the writer lock.
    fd = os.open(paths.lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


@pytest.mark.parametrize("outcome", ["upgraded", "failed"])
def test_upgrade_keeps_verified_pre_upgrade_backup_for_rollback(
    state_root: Path, archive_root: Path, isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    paths = StatePaths(state_root)
    archive_before = tree_digest(archive_root)
    with StateDatabase(paths) as db:
        scope = StateStore(db).scopes.register(archive_root, home=isolated_home)
    assert not list(paths.backups.glob("*.sqlite3"))  # a fresh database needs no backup

    probe = "CREATE TABLE p5_probe (id INTEGER PRIMARY KEY);"
    if outcome == "failed":
        probe += "\nCREATE TABLE archive_scopes (x);"
    monkeypatch.setattr(database, "MIGRATIONS", (*MIGRATIONS, Migration(2, probe)))
    if outcome == "upgraded":
        StateDatabase(paths).open().close()
        StateDatabase(paths).open().close()  # idempotent: no second upgrade or backup
        assert _user_version(paths.database) == 2
    else:
        with pytest.raises(sqlite3.Error):
            StateDatabase(paths).open()
        assert _user_version(paths.database) == 1

    backups = list(paths.backups.glob("*.sqlite3"))
    assert [b.name.startswith("pre-upgrade-") for b in backups] == [True]
    assert _user_version(backups[0]) == 1

    # Roll back to the previous code: it refuses the upgraded file, and the backup restores it.
    monkeypatch.setattr(database, "MIGRATIONS", MIGRATIONS)
    verify_database_file(backups[0])
    if outcome == "upgraded":
        with pytest.raises(SchemaVersionError):
            StateDatabase(paths).open()
        shutil.copyfile(backups[0], paths.database)
    with StateDatabase(paths) as db:
        assert StateStore(db).scopes.get(scope.id).id == scope.id
        assert [r[0] for r in db.connection.execute("SELECT version FROM schema_migrations")] == [1]
    assert _user_version(paths.database) == 1
    assert tree_digest(archive_root) == archive_before
