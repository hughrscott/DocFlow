"""Archive-scoped repositories and the state service contract behind ``/api/v1``.

Every operation takes an explicit ``archive_scope_id``; scope is never inferred
from a client-supplied path. Stored values are validated so that secrets, raw
OCR text, placeholder lookup maps and absolute paths never reach SQLite.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from docflow.state.database import StateDatabase, utc_now
from docflow.state.paths import validate_archive_root
from docflow.state.schema import (
    JOB_STATUSES,
    JOB_TRANSITIONS,
    REVIEW_TRANSITIONS,
    TERMINAL_JOB_STATUSES,
    VOLATILE_JOB_STATUSES,
)

ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "docflow.state")
LOCATOR_SCHEMES = ("upload", "watch", "archive", "unavailable")
SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|credential|auth)")
SECRET_VALUE_RE = re.compile(
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|://[^/\s:@]+:[^/\s@]+@"
)
ABSOLUTE_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")
CONTENT_KEYS = frozenset({
    "raw_text", "raw_texts", "raw_text_preview", "text_preview", "preview", "ocr_text",
    "full_text", "notes", "placeholder_map", "placeholders", "lookup", "lookup_map",
})


class ScopeRequiredError(ValueError):
    """An operation was attempted without a known explicit archive scope."""


class InvalidTransitionError(ValueError):
    """A status change is not permitted by the canonical transition table."""


class UnsafeValueError(ValueError):
    """A value would store secrets, raw content, lookup maps, or unsafe paths."""


def stable_id(kind: str, *parts: str) -> str:
    """Deterministic identifier used for idempotent imports."""
    return str(uuid.uuid5(ID_NAMESPACE, "\x1f".join((kind, *parts))))


def archive_relative(value: str) -> str:
    """Validate and normalize an archive-relative POSIX path."""
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise UnsafeValueError("path must be a non-empty archive-relative string")
    if value.startswith("~") or ABSOLUTE_PATH_RE.match(value) or PureWindowsPath(value).drive:
        raise UnsafeValueError("absolute paths are not allowed")
    parts = [p for p in PurePosixPath(value).parts if p != "."]
    if not parts or ".." in parts:
        raise UnsafeValueError("path must stay beneath the archive root")
    return PurePosixPath(*parts).as_posix()


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."} or any(
        c in value for c in "/\\\0"
    ):
        raise UnsafeValueError("name must be a single path component")
    return value


def validate_source_locator(value: str) -> str:
    """Validate an opaque ``scheme:reference`` locator; arbitrary paths are rejected."""
    scheme, sep, rest = value.partition(":") if isinstance(value, str) else ("", "", "")
    if not sep or scheme not in LOCATOR_SCHEMES:
        raise UnsafeValueError("source locator must use a known scheme")
    ref = archive_relative(rest) if scheme in {"watch", "archive"} else safe_name(rest)
    return f"{scheme}:{ref}"


def check_safe_json(value: Any, *, allow_home_relative: bool = False) -> str:
    """Serialize ``value`` after rejecting secrets, raw content and absolute paths."""
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if str(key).lower() in CONTENT_KEYS or SECRET_KEY_RE.search(str(key)):
                    raise UnsafeValueError("content, lookup or secret field is not storable")
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            if SECRET_VALUE_RE.search(node):
                raise UnsafeValueError("secret-like value is not storable")
            if ABSOLUTE_PATH_RE.match(node) or (node.startswith("~") and not allow_home_relative):
                raise UnsafeValueError("absolute path value is not storable")

    walk(value)
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise UnsafeValueError("value is not finite JSON") from exc


def _confidence(value: float) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise UnsafeValueError("confidence must be a finite number in [0, 1]")
    return float(value)


@dataclass(frozen=True)
class ArchiveScope:
    id: str
    canonical_root: str
    root_fingerprint: str


@dataclass(frozen=True)
class Job:
    id: str
    archive_scope_id: str
    source_fingerprint: str
    source_name: str
    source_locator: str
    status: str
    attempt: int
    created_at: str
    updated_at: str
    last_error_code: str | None


@dataclass(frozen=True)
class ReviewItem:
    id: str
    archive_scope_id: str
    job_id: str
    candidate: dict
    suggested_filename: str | None
    suggested_relative_directory: str | None
    confidence: float
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RecoveryReport:
    reset_job_ids: list[str]
    pending_jobs: list[Job]
    pending_reviews: list[ReviewItem]


class _Repository:
    def __init__(self, db: StateDatabase) -> None:
        self.db = db

    @property
    def conn(self) -> sqlite3.Connection:
        return self.db.connection

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Join the caller's transaction, or open a new one."""
        if self.conn.in_transaction:
            yield self.conn
        else:
            with self.db.transaction() as conn:
                yield conn

    def require_scope(self, scope_id: str | None) -> str:
        if not isinstance(scope_id, str) or not scope_id:
            raise ScopeRequiredError("archive_scope_id is required")
        row = self.conn.execute("SELECT 1 FROM archive_scopes WHERE id = ?", (scope_id,)).fetchone()
        if row is None:
            raise ScopeRequiredError("archive_scope_id is not registered")
        return scope_id


class ScopeRepository(_Repository):
    def get(self, scope_id: str) -> ArchiveScope:
        """Return a registered scope; unknown or missing IDs raise ScopeRequiredError."""
        self.require_scope(scope_id)
        row = self.conn.execute(
            "SELECT id, canonical_root, root_fingerprint FROM archive_scopes WHERE id = ?",
            (scope_id,),
        ).fetchone()
        return ArchiveScope(*row)

    def register(self, root: Path, *, home: Path, touch: bool = True) -> ArchiveScope:
        """Validate and register an archive root (``POST /api/v1/archive-scopes``)."""
        canonical = str(validate_archive_root(root, home=home, state_root=self.db.paths.root))
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        scope = ArchiveScope(stable_id("archive_scope", fingerprint), canonical, fingerprint)
        now = utc_now()
        with self.write() as conn:
            conn.execute(
                "INSERT INTO archive_scopes(id, canonical_root, root_fingerprint, created_at, "
                "last_seen_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (scope.id, canonical, fingerprint, now, now),
            )
            if touch:
                conn.execute("UPDATE archive_scopes SET last_seen_at = ? WHERE id = ?",
                             (now, scope.id))
        return scope


class JobRepository(_Repository):
    def create(
        self,
        scope_id: str,
        *,
        source_fingerprint: str,
        source_name: str,
        source_locator: str,
        page_count: int,
        job_id: str | None = None,
    ) -> Job:
        """Create a discovered job from an upload handle or configured watch candidate."""
        self.require_scope(scope_id)
        locator = validate_source_locator(source_locator)
        name = safe_name(source_name)
        if not isinstance(page_count, int) or page_count < 1:
            raise UnsafeValueError("page_count must be a positive integer")
        job_id = job_id or uuid.uuid4().hex
        now = utc_now()
        with self.write() as conn:
            conn.execute(
                "INSERT INTO jobs(id, archive_scope_id, source_fingerprint, source_name, "
                "source_locator, status, attempt, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'discovered', 0, ?, ?)",
                (job_id, scope_id, source_fingerprint, name, locator, now, now),
            )
            conn.executemany(
                "INSERT INTO job_pages(job_id, archive_scope_id, page_number, status) "
                "VALUES (?, ?, ?, 'pending')",
                [(job_id, scope_id, n) for n in range(1, page_count + 1)],
            )
        job = self.get(scope_id, job_id)
        assert job is not None
        return job

    def get(self, scope_id: str, job_id: str) -> Job | None:
        self.require_scope(scope_id)
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id = ? AND archive_scope_id = ?", (job_id, scope_id)
        ).fetchone()
        return Job(**dict(row)) if row else None

    def list_pending(self, scope_id: str) -> list[Job]:
        self.require_scope(scope_id)
        excluded = sorted(TERMINAL_JOB_STATUSES | {"failed"})
        rows = self.conn.execute(
            f"SELECT * FROM jobs WHERE archive_scope_id = ? AND status NOT IN "
            f"({', '.join('?' * len(excluded))}) ORDER BY created_at, id",
            (scope_id, *excluded),
        )
        return [Job(**dict(r)) for r in rows]

    def page_totals(self, scope_id: str, job_id: str) -> dict[str, int]:
        self.require_scope(scope_id)
        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM job_pages WHERE archive_scope_id = ? AND job_id = ? "
            "GROUP BY status", (scope_id, job_id),
        )
        return {status: count for status, count in rows}

    def transition(
        self, scope_id: str, job_id: str, *, expected: str, new: str,
        error_code: str | None = None,
    ) -> bool:
        """Compare-and-set ``expected -> new``; False when the job is not in ``expected``."""
        self.require_scope(scope_id)
        if new not in JOB_STATUSES or new not in JOB_TRANSITIONS.get(expected, ()):
            raise InvalidTransitionError(f"job transition {expected!r} -> {new!r} not allowed")
        with self.write() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, attempt = attempt + ?, updated_at = ?, "
                "last_error_code = ? WHERE id = ? AND archive_scope_id = ? AND status = ?",
                (new, 1 if new == "ocr" else 0, utc_now(), error_code, job_id, scope_id, expected),
            )
        return cursor.rowcount == 1

    def block_for_privacy(
        self, scope_id: str, job_id: str, *, expected: str, error_code: str
    ) -> ReviewItem | None:
        """Move a job to privacy_blocked and open a pending local review item."""
        with self.write():
            if not self.transition(scope_id, job_id, expected=expected, new="privacy_blocked",
                                   error_code=error_code):
                return None
            return ReviewRepository(self.db).create(
                scope_id, job_id, candidate={"blocked_reason": error_code},
                suggested_filename=None, suggested_relative_directory=None, confidence=0.0,
            )


class ReviewRepository(_Repository):
    def create(
        self,
        scope_id: str,
        job_id: str,
        *,
        candidate: dict,
        suggested_filename: str | None,
        suggested_relative_directory: str | None,
        confidence: float,
        status: str = "pending",
        item_id: str | None = None,
    ) -> ReviewItem:
        self.require_scope(scope_id)
        candidate_json = check_safe_json(candidate)
        filename = None if suggested_filename is None else safe_name(suggested_filename)
        directory = (None if suggested_relative_directory is None
                     else archive_relative(suggested_relative_directory))
        if status not in REVIEW_TRANSITIONS:
            raise InvalidTransitionError(f"unknown review status {status!r}")
        item_id = item_id or uuid.uuid4().hex
        now = utc_now()
        with self.write() as conn:
            conn.execute(
                "INSERT INTO review_items(id, archive_scope_id, job_id, candidate_json, "
                "suggested_filename, suggested_relative_directory, confidence, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, scope_id, job_id, candidate_json, filename, directory,
                 _confidence(confidence), status, now, now),
            )
        item = self.get(scope_id, item_id)
        assert item is not None
        return item

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ReviewItem:
        data = dict(row)
        data["candidate"] = json.loads(data.pop("candidate_json"))
        return ReviewItem(**data)

    def get(self, scope_id: str, item_id: str) -> ReviewItem | None:
        self.require_scope(scope_id)
        row = self.conn.execute(
            "SELECT * FROM review_items WHERE id = ? AND archive_scope_id = ?", (item_id, scope_id)
        ).fetchone()
        return self._from_row(row) if row else None

    def list(self, scope_id: str, status: str = "pending") -> list[ReviewItem]:
        """``GET /api/v1/review-items?archive_scope_id=...&status=...``."""
        self.require_scope(scope_id)
        rows = self.conn.execute(
            "SELECT * FROM review_items WHERE archive_scope_id = ? AND status = ? "
            "ORDER BY created_at, id", (scope_id, status),
        )
        return [self._from_row(r) for r in rows]

    def resolve(self, scope_id: str, item_id: str, *, expected: str, new: str) -> bool:
        """Compare-and-set review status (approve/correct/skip primitives)."""
        self.require_scope(scope_id)
        if new not in REVIEW_TRANSITIONS.get(expected, ()):
            raise InvalidTransitionError(f"review transition {expected!r} -> {new!r} not allowed")
        with self.write() as conn:
            cursor = conn.execute(
                "UPDATE review_items SET status = ?, updated_at = ? "
                "WHERE id = ? AND archive_scope_id = ? AND status = ?",
                (new, utc_now(), item_id, scope_id, expected),
            )
        return cursor.rowcount == 1

    def correct(
        self,
        scope_id: str,
        item_id: str,
        *,
        chosen_relative_directory: str,
        chosen_rule_id: str | None,
        normalized_features: dict,
    ) -> str | None:
        """Mark a pending item corrected and record the correction; None if not pending."""
        self.require_scope(scope_id)
        features_json = check_safe_json(normalized_features)
        directory = archive_relative(chosen_relative_directory)
        with self.write() as conn:
            if not self.resolve(scope_id, item_id, expected="pending", new="corrected"):
                return None
            correction_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO corrections(id, archive_scope_id, review_item_id, "
                "normalized_features_json, chosen_rule_id, chosen_relative_directory, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (correction_id, scope_id, item_id, features_json, chosen_rule_id, directory,
                 utc_now()),
            )
        return correction_id


class SettingsRepository(_Repository):
    @staticmethod
    def _encode(key: str, value: Any) -> str:
        if not isinstance(key, str) or not key or SECRET_KEY_RE.search(key):
            raise UnsafeValueError("secret settings belong in the keychain or environment")
        return check_safe_json(value, allow_home_relative=True)

    def set(self, scope_id: str, key: str, value: Any) -> None:
        self.require_scope(scope_id)
        encoded = self._encode(key, value)
        with self.write() as conn:
            conn.execute(
                "INSERT INTO settings(archive_scope_id, key, value_json, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(archive_scope_id, key) DO UPDATE SET "
                "value_json = excluded.value_json, updated_at = excluded.updated_at "
                "WHERE settings.value_json <> excluded.value_json",
                (scope_id, key, encoded, utc_now()),
            )

    def set_if_absent(self, scope_id: str, key: str, value: Any) -> bool:
        """Insert only when no user value exists; never overwrites."""
        self.require_scope(scope_id)
        encoded = self._encode(key, value)
        with self.write() as conn:
            cursor = conn.execute(
                "INSERT INTO settings(archive_scope_id, key, value_json, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (scope_id, key, encoded, utc_now()),
            )
        return cursor.rowcount == 1

    def get(self, scope_id: str, key: str) -> Any:
        self.require_scope(scope_id)
        row = self.conn.execute(
            "SELECT value_json FROM settings WHERE archive_scope_id = ? AND key = ?",
            (scope_id, key),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, scope_id: str) -> dict[str, Any]:
        self.require_scope(scope_id)
        rows = self.conn.execute(
            "SELECT key, value_json FROM settings WHERE archive_scope_id = ? ORDER BY key",
            (scope_id,),
        )
        return {key: json.loads(value) for key, value in rows}


class StateStore:
    """Service contract backing the local ``/api/v1`` job and review endpoints."""

    def __init__(self, db: StateDatabase) -> None:
        self.db = db
        self.scopes = ScopeRepository(db)
        self.jobs = JobRepository(db)
        self.reviews = ReviewRepository(db)
        self.settings = SettingsRepository(db)

    def recover_after_restart(self, scope_id: str) -> RecoveryReport:
        """Reload pending work; reset jobs whose memory-only lookup state was lost.

        ``awaiting_model``/``classified`` jobs return to ``ocr`` so local OCR and
        pseudonymization re-run with fresh placeholders. No lookup map is stored.
        """
        self.jobs.require_scope(scope_id)
        reset: list[str] = []
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, status FROM jobs WHERE archive_scope_id = ? AND status IN (?, ?) "
                "ORDER BY created_at, id", (scope_id, *sorted(VOLATILE_JOB_STATUSES)),
            ).fetchall()
            for job_id, status in rows:
                if self.jobs.transition(scope_id, job_id, expected=status, new="ocr",
                                        error_code="volatile_state_lost"):
                    reset.append(job_id)
        return RecoveryReport(reset, self.jobs.list_pending(scope_id), self.reviews.list(scope_id))
