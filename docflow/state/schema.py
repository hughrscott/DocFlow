"""Canonical archive-scoped SQLite schema and versioned migrations."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

JOB_STATUSES = (
    "discovered", "stabilizing", "ready", "ocr", "privacy_blocked", "awaiting_model",
    "classified", "filing", "review", "completed", "failed", "undoing", "undone",
)
PAGE_STATUSES = ("pending", "filed", "review", "skipped", "blocked")
REVIEW_STATUSES = ("pending", "approved", "corrected", "skipped")
FILE_ROLES = ("original", "filed")
OPERATION_STATUSES = (
    "planned", "running", "completed", "failed", "undoing", "undone", "partially_undone",
)
STEP_STATUSES = ("planned", "done", "failed", "compensated", "skipped")

# Explicit job transitions. Restart may reset volatile model states back to ocr.
JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "discovered": frozenset({"stabilizing", "failed"}),
    "stabilizing": frozenset({"ready", "failed"}),
    "ready": frozenset({"ocr", "failed"}),
    "ocr": frozenset({"privacy_blocked", "awaiting_model", "classified", "failed"}),
    "privacy_blocked": frozenset({"review", "ocr", "failed"}),
    "awaiting_model": frozenset({"classified", "privacy_blocked", "ocr", "failed"}),
    "classified": frozenset({"filing", "review", "ocr", "failed"}),
    "filing": frozenset({"completed", "review", "failed"}),
    "review": frozenset({"filing", "completed", "failed"}),
    "completed": frozenset({"undoing"}),
    "failed": frozenset({"ready"}),
    "undoing": frozenset({"undone", "failed"}),
    "undone": frozenset(),
}
TERMINAL_JOB_STATUSES = frozenset({"completed", "undone"})
VOLATILE_JOB_STATUSES = frozenset({"awaiting_model", "classified"})
REVIEW_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"approved", "corrected", "skipped"}),
    "approved": frozenset(),
    "corrected": frozenset(),
    "skipped": frozenset(),
}


def _enum(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"CHECK ({column} IN ({quoted}))"


def _relative(column: str) -> str:
    return (
        f"CHECK ({column} <> '' AND substr({column}, 1, 1) NOT IN ('/', '\\', '~') "
        f"AND ('/' || {column} || '/') NOT LIKE '%/../%')"
    )


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str


_V1 = f"""
CREATE TABLE archive_scopes (
    id TEXT PRIMARY KEY,
    canonical_root TEXT NOT NULL UNIQUE,
    root_fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE settings (
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    key TEXT NOT NULL CHECK (key <> ''),
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (archive_scope_id, key)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    source_fingerprint TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_locator TEXT NOT NULL CHECK (substr(source_locator, 1, 1) NOT IN ('/', '~')),
    status TEXT NOT NULL {_enum("status", JOB_STATUSES)},
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error_code TEXT,
    UNIQUE (id, archive_scope_id)
);
CREATE INDEX jobs_scope_status ON jobs(archive_scope_id, status);

CREATE TABLE job_pages (
    job_id TEXT NOT NULL,
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    content_sha256 TEXT,
    perceptual_fingerprint TEXT,
    status TEXT NOT NULL {_enum("status", PAGE_STATUSES)},
    PRIMARY KEY (job_id, page_number),
    FOREIGN KEY (job_id, archive_scope_id) REFERENCES jobs(id, archive_scope_id)
);

CREATE TABLE review_items (
    id TEXT PRIMARY KEY,
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    job_id TEXT NOT NULL,
    candidate_json TEXT NOT NULL CHECK (json_valid(candidate_json)),
    suggested_filename TEXT,
    suggested_relative_directory TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL {_enum("status", REVIEW_STATUSES)},
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (suggested_relative_directory IS NULL OR
           substr(suggested_relative_directory, 1, 1) NOT IN ('/', '\\', '~')),
    FOREIGN KEY (job_id, archive_scope_id) REFERENCES jobs(id, archive_scope_id)
);
CREATE INDEX review_items_scope_status ON review_items(archive_scope_id, status);

CREATE TABLE file_records (
    id TEXT PRIMARY KEY,
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    job_id TEXT,
    relative_path TEXT NOT NULL {_relative("relative_path")},
    content_sha256 TEXT NOT NULL,
    page_numbers_json TEXT NOT NULL CHECK (json_valid(page_numbers_json)),
    role TEXT NOT NULL {_enum("role", FILE_ROLES)},
    created_at TEXT NOT NULL,
    UNIQUE (archive_scope_id, relative_path),
    FOREIGN KEY (job_id, archive_scope_id) REFERENCES jobs(id, archive_scope_id)
);

CREATE TABLE operations (
    id TEXT PRIMARY KEY,
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    job_id TEXT,
    kind TEXT NOT NULL CHECK (kind <> ''),
    status TEXT NOT NULL {_enum("status", OPERATION_STATUSES)},
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    undone_at TEXT,
    UNIQUE (id, archive_scope_id),
    FOREIGN KEY (job_id, archive_scope_id) REFERENCES jobs(id, archive_scope_id)
);

CREATE TABLE operation_steps (
    id TEXT PRIMARY KEY,
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    operation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    kind TEXT NOT NULL CHECK (kind <> ''),
    source_locator TEXT CHECK (source_locator IS NULL OR
                               substr(source_locator, 1, 1) NOT IN ('/', '~')),
    destination_relative_path TEXT CHECK (destination_relative_path IS NULL OR
        (destination_relative_path <> ''
         AND substr(destination_relative_path, 1, 1) NOT IN ('/', '\\', '~')
         AND ('/' || destination_relative_path || '/') NOT LIKE '%/../%')),
    expected_sha256 TEXT,
    status TEXT NOT NULL {_enum("status", STEP_STATUSES)},
    error_code TEXT,
    UNIQUE (operation_id, ordinal),
    FOREIGN KEY (operation_id, archive_scope_id) REFERENCES operations(id, archive_scope_id)
);

CREATE TABLE corrections (
    id TEXT PRIMARY KEY,
    archive_scope_id TEXT NOT NULL REFERENCES archive_scopes(id),
    review_item_id TEXT NOT NULL REFERENCES review_items(id),
    normalized_features_json TEXT NOT NULL CHECK (json_valid(normalized_features_json)),
    chosen_rule_id TEXT,
    chosen_relative_directory TEXT NOT NULL {_relative("chosen_relative_directory")},
    created_at TEXT NOT NULL
);
"""

MIGRATIONS: tuple[Migration, ...] = (Migration(1, _V1),)
SCHEMA_VERSION = MIGRATIONS[-1].version

SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL
)
"""


def migration_checksum(migration: Migration) -> str:
    """SHA-256 over the version and whitespace-normalized SQL."""
    normalized = "\n".join(line.strip() for line in migration.sql.strip().splitlines() if line.strip())
    return hashlib.sha256(f"{migration.version}\n{normalized}".encode()).hexdigest()
