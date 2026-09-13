"""SQLite connection handling, single-writer lock and schema migration runner."""
from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from docflow.state.paths import StatePaths
from docflow.state.schema import MIGRATIONS, SCHEMA_MIGRATIONS_SQL, migration_checksum


class WriterLockError(RuntimeError):
    """Another process (or handle) already owns the application-state writer lock."""


class SchemaChecksumError(RuntimeError):
    """A recorded migration does not match the migration shipped with this code."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection in explicit-transaction mode with foreign keys enforced."""
    target = f"{Path(path).resolve().as_uri()}?mode=ro" if readonly else str(path)
    # The local server uses the single writer from its event-loop thread, which may
    # differ from the thread that opened it; the file lock still enforces one writer.
    conn = sqlite3.connect(target, isolation_level=None, timeout=5.0, uri=readonly,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise RuntimeError("SQLite foreign key enforcement is unavailable")
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending migrations atomically and verify recorded checksums."""
    conn.execute(SCHEMA_MIGRATIONS_SQL)
    verify_schema(conn, allow_pending=True)
    recorded = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        if migration.version in recorded:
            continue
        script = (
            f"BEGIN IMMEDIATE;\n{migration.sql}\n"
            f"INSERT INTO schema_migrations(version, applied_at, checksum) VALUES "
            f"({migration.version}, '{utc_now()}', '{migration_checksum(migration)}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def verify_schema(conn: sqlite3.Connection, *, allow_pending: bool = False) -> None:
    """Raise SchemaChecksumError unless recorded migrations match shipped ones."""
    expected = {m.version: migration_checksum(m) for m in MIGRATIONS}
    recorded = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
    for version, checksum in recorded.items():
        if expected.get(version) != checksum:
            raise SchemaChecksumError(f"schema migration {version} checksum mismatch")
    if not allow_pending and set(recorded) != set(expected):
        raise SchemaChecksumError("schema migrations incomplete")


def integrity_problems(conn: sqlite3.Connection) -> list[str]:
    """Return integrity and foreign-key violations (empty when healthy)."""
    problems = [row[0] for row in conn.execute("PRAGMA integrity_check") if row[0] != "ok"]
    problems += [f"foreign_key:{row[0]}" for row in conn.execute("PRAGMA foreign_key_check")]
    return problems


class StateDatabase:
    """The single local writer: holds ``docflow.lock`` and one SQLite connection."""

    def __init__(self, paths: StatePaths) -> None:
        self.paths = paths
        self._conn: sqlite3.Connection | None = None
        self._lock_fd: int | None = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("state database is not open")
        return self._conn

    def open(self) -> Self:
        if self.is_open:
            return self
        self.paths.ensure()
        fd = os.open(self.paths.lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise WriterLockError("another DocFlow writer holds the state lock") from None
        self._lock_fd = fd
        try:
            self._conn = connect(self.paths.database)
            self._conn.execute("PRAGMA journal_mode = WAL")
            apply_migrations(self._conn)
            verify_schema(self._conn)
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one immediate write transaction; roll back on any exception."""
        conn = self.connection
        if conn.in_transaction:
            raise RuntimeError("nested state transactions are not supported")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
