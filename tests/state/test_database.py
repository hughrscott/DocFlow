"""Schema, foreign keys, migration checksums and single-writer lock (S04)."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from docflow.state.database import (
    SchemaChecksumError,
    StateDatabase,
    WriterLockError,
    connect,
)
from docflow.state.paths import StatePaths
from docflow.state.schema import (
    JOB_STATUSES,
    MIGRATIONS,
    PAGE_STATUSES,
    SCHEMA_VERSION,
    migration_checksum,
)

CANONICAL_TABLES = {
    "schema_migrations", "archive_scopes", "settings", "jobs", "job_pages",
    "review_items", "file_records", "operations", "operation_steps", "corrections",
}
SCOPED_TABLES = CANONICAL_TABLES - {"schema_migrations", "archive_scopes"}
REPO_ROOT = Path(__file__).resolve().parents[2]


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def test_canonical_tables_and_required_scope_column(state_root: Path) -> None:
    with StateDatabase(StatePaths(state_root)) as db:
        tables = {r[0] for r in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert CANONICAL_TABLES <= tables
        for table in SCOPED_TABLES:
            cols = _columns(db.connection, table)
            assert "archive_scope_id" in cols, table
            assert cols["archive_scope_id"][3] == 1, f"{table}.archive_scope_id NOT NULL"
            fks = db.connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            assert any(fk[2] == "archive_scopes" or fk[2] == "jobs" for fk in fks), table


def test_canonical_status_enums() -> None:
    assert JOB_STATUSES == (
        "discovered", "stabilizing", "ready", "ocr", "privacy_blocked", "awaiting_model",
        "classified", "filing", "review", "completed", "failed", "undoing", "undone",
    )
    assert PAGE_STATUSES == ("pending", "filed", "review", "skipped", "blocked")


def test_foreign_keys_enabled_on_every_connection(state_root: Path) -> None:
    with StateDatabase(StatePaths(state_root)) as db:
        assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        other = connect(db.paths.database)
        try:
            assert other.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError):
                other.execute(
                    "INSERT INTO settings(archive_scope_id, key, value_json, updated_at) "
                    "VALUES ('no-such-scope', 'k', '1', 'now')"
                )
        finally:
            other.close()


def test_constraints_reject_invalid_rows(state_root: Path) -> None:
    with StateDatabase(StatePaths(state_root)) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO archive_scopes(id, canonical_root, root_fingerprint, created_at, last_seen_at)"
            " VALUES ('s1', '/archive-root', 'fp', 't', 't')"
        )
        job = ("INSERT INTO jobs(id, archive_scope_id, source_fingerprint, source_name, "
               "source_locator, status, attempt, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(job, ("j1", "s1", "fp", "a.pdf", "upload:a", "bogus", 0, "t", "t"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(job, ("j1", None, "fp", "a.pdf", "upload:a", "ready", 0, "t", "t"))
        conn.execute(job, ("j1", "s1", "fp", "a.pdf", "upload:a", "ready", 0, "t", "t"))
        page = ("INSERT INTO job_pages(job_id, archive_scope_id, page_number, status) "
                "VALUES (?,?,?,?)")
        conn.execute(page, ("j1", "s1", 1, "pending"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(page, ("j1", "s1", 1, "pending"))  # (job_id, page_number) unique
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(page, ("j1", "s1", 2, "lost"))
        record = ("INSERT INTO file_records(id, archive_scope_id, relative_path, content_sha256, "
                  "page_numbers_json, role, created_at) VALUES (?,?,?,?,?,?,?)")
        conn.execute(record, ("f1", "s1", "A/b.pdf", "h", "[1]", "filed", "t"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(record, ("f2", "s1", "A/b.pdf", "h", "[1]", "filed", "t"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(record, ("f3", "s1", "/abs/b.pdf", "h", "[1]", "filed", "t"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(record, ("f4", "s1", "C/d.pdf", "h", "[1]", "copy", "t"))


def test_migration_checksum_is_deterministic_and_recorded(state_root: Path) -> None:
    first = [migration_checksum(m) for m in MIGRATIONS]
    assert first == [migration_checksum(m) for m in MIGRATIONS]
    assert all(len(c) == 64 for c in first)
    with StateDatabase(StatePaths(state_root)) as db:
        rows = db.connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    assert [tuple(r) for r in rows] == [(m.version, c) for m, c in zip(MIGRATIONS, first)]
    assert rows[-1][0] == SCHEMA_VERSION


def test_tampered_checksum_is_rejected_on_open(state_root: Path) -> None:
    paths = StatePaths(state_root)
    with StateDatabase(paths):
        pass
    raw = sqlite3.connect(paths.database)
    raw.execute("UPDATE schema_migrations SET checksum = 'tampered'")
    raw.commit()
    raw.close()
    with pytest.raises(SchemaChecksumError):
        StateDatabase(paths).open()
    # The failed open released its lock so a later writer is not wedged.
    raw = sqlite3.connect(paths.database)
    raw.execute("UPDATE schema_migrations SET checksum = ?", (migration_checksum(MIGRATIONS[0]),))
    raw.commit()
    raw.close()
    with StateDatabase(paths):
        pass


def test_reopen_is_idempotent(state_root: Path) -> None:
    paths = StatePaths(state_root)
    with StateDatabase(paths):
        pass
    with StateDatabase(paths) as db:
        count = db.connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == len(MIGRATIONS)


def test_second_writer_in_process_rejected(state_root: Path) -> None:
    paths = StatePaths(state_root)
    with StateDatabase(paths), pytest.raises(WriterLockError):
        StateDatabase(paths).open()
    with StateDatabase(paths) as db:  # released on context exit
        assert db.is_open


def test_second_writer_process_rejected(state_root: Path) -> None:
    paths = StatePaths(state_root)
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from docflow.state.database import StateDatabase, WriterLockError\n"
        "from docflow.state.paths import StatePaths\n"
        "try:\n"
        "    StateDatabase(StatePaths(Path(sys.argv[1]))).open()\n"
        "except WriterLockError:\n"
        "    sys.exit(3)\n"
        "sys.exit(0)\n"
    )
    with StateDatabase(paths):
        held = subprocess.run([sys.executable, "-c", script, str(state_root)],
                              capture_output=True, timeout=30, cwd=REPO_ROOT, check=False)
    assert held.returncode == 3, held.stderr
    free = subprocess.run([sys.executable, "-c", script, str(state_root)],
                          capture_output=True, timeout=30, cwd=REPO_ROOT, check=False)
    assert free.returncode == 0, free.stderr


def test_close_releases_connection_and_lock(state_root: Path) -> None:
    db = StateDatabase(StatePaths(state_root))
    db.open()
    db.close()
    assert not db.is_open
    with pytest.raises(RuntimeError):
        _ = db.connection
    db.close()  # idempotent


def test_transaction_rolls_back_on_error(state_root: Path) -> None:
    with StateDatabase(StatePaths(state_root)) as db:
        with pytest.raises(LookupError), db.transaction() as conn:
            conn.execute(
                "INSERT INTO archive_scopes(id, canonical_root, root_fingerprint, created_at,"
                " last_seen_at) VALUES ('s1', '/archive-root', 'fp', 't', 't')"
            )
            raise LookupError("induced failure inside transaction")
        assert db.connection.execute("SELECT COUNT(*) FROM archive_scopes").fetchone()[0] == 0


def test_wal_and_lock_stay_in_state_root(state_root: Path, archive_root: Path) -> None:
    from tests.state.conftest import tree_digest

    before = tree_digest(archive_root)
    with StateDatabase(StatePaths(state_root)) as db:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO archive_scopes(id, canonical_root, root_fingerprint, created_at,"
                " last_seen_at) VALUES ('s1', ?, 'fp', 't', 't')", (str(archive_root),)
            )
        assert db.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.paths.wal.exists() and db.paths.lock.exists()
    assert tree_digest(archive_root) == before
