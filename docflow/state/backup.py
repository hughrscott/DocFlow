"""State backup/restore through the SQLite backup API, and redacted support exports."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from docflow.state.database import (
    SchemaChecksumError,
    StateDatabase,
    connect,
    integrity_problems,
    utc_now,
    verify_schema,
)
from docflow.state.repositories import SECRET_VALUE_RE
from docflow.state.schema import SCHEMA_VERSION

UNSAFE_EXPORT_RE = re.compile(r'"(?:/|~|[A-Za-z]:[\\/]|\\\\)')


class BackupError(RuntimeError):
    """A backup or restore could not run safely."""


class BackupIntegrityError(BackupError):
    """A database file failed integrity, foreign-key or schema verification."""


def _quiesced(db: StateDatabase) -> sqlite3.Connection:
    if not db.is_open:
        raise BackupError("the state writer must be open and hold the lock")
    if db.connection.in_transaction:
        raise BackupError("the state writer is not quiesced")
    return db.connection


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")


def _unique(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def verify_database_file(path: Path) -> None:
    """Raise BackupIntegrityError unless ``path`` is a healthy DocFlow state database."""
    try:
        conn = connect(path, readonly=True)
    except sqlite3.Error as exc:
        raise BackupIntegrityError("database cannot be opened") from exc
    try:
        problems = integrity_problems(conn)
        verify_schema(conn)
    except (sqlite3.Error, SchemaChecksumError) as exc:
        raise BackupIntegrityError("database failed verification") from exc
    finally:
        conn.close()
    if problems:
        raise BackupIntegrityError(f"database failed {len(problems)} integrity check(s)")


def create_backup(db: StateDatabase, *, label: str = "state", now: datetime | None = None) -> Path:
    """Copy the live database via the SQLite backup API into the backups directory."""
    source = _quiesced(db)
    db.paths.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = _unique(db.paths.backups, f"{label}-{_stamp(now)}", ".sqlite3")
    partial = final.with_name(final.name + ".partial")
    destination = sqlite3.connect(partial)
    try:
        source.backup(destination)
    finally:
        destination.close()
    os.chmod(partial, 0o600)
    try:
        verify_database_file(partial)
    except BackupIntegrityError:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, final)
    return final


def restore_backup(db: StateDatabase, backup: Path) -> None:
    """Verify ``backup`` then copy it over the live database while holding the writer lock."""
    live = _quiesced(db)
    verify_database_file(backup)
    create_backup(db, label="pre-restore")
    source = connect(backup, readonly=True)
    try:
        source.backup(live)
    finally:
        source.close()
    problems = integrity_problems(live)
    verify_schema(live)
    if problems:
        raise BackupIntegrityError("restored database failed integrity checks")


def _counts(conn: sqlite3.Connection, table: str, scope_id: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT status, COUNT(*) FROM {table} WHERE archive_scope_id = ? GROUP BY status "
        "ORDER BY status", (scope_id,),
    )
    return {status: count for status, count in rows}


def build_support_export(db: StateDatabase) -> dict:
    """Counts and schema health only: no roots, paths, filenames, values, OCR, or secrets."""
    conn = _quiesced(db)
    scopes = []
    for (scope_id, fingerprint) in conn.execute(
        "SELECT id, root_fingerprint FROM archive_scopes ORDER BY id"
    ).fetchall():
        def total(table: str, scope: str = scope_id) -> int:
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE archive_scope_id = ?", (scope,)
            ).fetchone()[0]

        scopes.append({
            "archive_scope_id": scope_id,
            "root_fingerprint": fingerprint,
            "jobs_by_status": _counts(conn, "jobs", scope_id),
            "pages_by_status": _counts(conn, "job_pages", scope_id),
            "review_items_by_status": _counts(conn, "review_items", scope_id),
            "operations_by_status": _counts(conn, "operations", scope_id),
            "file_records": total("file_records"),
            "corrections": total("corrections"),
            "settings_keys": [row[0] for row in conn.execute(
                "SELECT key FROM settings WHERE archive_scope_id = ? ORDER BY key", (scope_id,)
            )],
        })
    problems = integrity_problems(conn)
    return {
        "format": "docflow-support-export",
        "created_at": utc_now(),
        "schema_version": SCHEMA_VERSION,
        "migrations": [
            {"version": v, "checksum": c}
            for v, c in conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")
        ],
        "integrity": {"ok": not problems, "problem_count": len(problems)},
        "scopes": scopes,
    }


def write_support_export(db: StateDatabase, *, now: datetime | None = None) -> Path:
    """Write a redacted diagnostic bundle; fail closed if anything path- or secret-like leaks."""
    text = json.dumps(build_support_export(db), indent=2, sort_keys=True)
    if UNSAFE_EXPORT_RE.search(text) or SECRET_VALUE_RE.search(text):
        raise BackupError("support export failed redaction verification")
    db.paths.exports.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = _unique(db.paths.exports, f"support-export-{_stamp(now)}", ".json")
    partial = target.with_name(target.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.chmod(partial, 0o600)
    os.replace(partial, target)
    return target
