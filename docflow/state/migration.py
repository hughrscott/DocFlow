"""Non-destructive import of legacy queue, filing-log and config state.

Legacy files are only read: they are inventoried, backed up state is taken,
then pending reviews, filing history and user settings are imported in one
transaction with deterministic IDs. Secrets, raw OCR previews, model notes,
placeholder maps and absolute paths are excluded. Legacy files are never renamed
or deleted here; cleanup is reported as deferred.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from docflow.state.backup import create_backup
from docflow.state.database import (
    StateDatabase,
    integrity_problems,
    utc_now,
    verify_schema,
)
from docflow.state.repositories import (
    SECRET_KEY_RE,
    SettingsRepository,
    StateStore,
    UnsafeValueError,
    archive_relative,
    check_safe_json,
    safe_name,
    stable_id,
    validate_source_locator,
)
from docflow.state.schema import REVIEW_STATUSES, SCHEMA_VERSION

QUEUE_FILENAMES = ("review_queue.json", ".docflow_queue.json")
SHIPPED_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default_config.yaml"
IMPORTED_SETTING_KEYS = (
    "confidence_threshold", "scan_watch_folder", "llm_provider", "llm_model", "llm_base_url",
    "openrouter_model", "ollama_model", "ollama_host",
)
PATH_SETTING_KEYS = frozenset({"scan_watch_folder"})
QUEUE_FIELDS_USED = frozenset({
    "id", "source_pdf", "pages", "institution", "doc_type", "period", "suggested_filename",
    "suggested_directory", "rule_matched", "confidence", "status", "corrected_filename",
    "corrected_directory",
})
PAGE_STATUS_FOR_REVIEW = {
    "pending": "review", "approved": "filed", "corrected": "filed", "skipped": "skipped",
}
DEFERRED_KINDS = frozenset({"review_queue", "filing_log", "preview_cache", "hash_index", "app_log"})


class MigrationError(RuntimeError):
    """Legacy import failed; local database changes were rolled back."""


@dataclass(frozen=True, kw_only=True)
class LegacySources:
    archive_root: Path
    user_home: Path
    legacy_home: Path | None = None
    config_path: Path | None = None


@dataclass(frozen=True)
class LegacyItem:
    kind: str
    location: str  # "archive", "legacy_home" or "config"
    name: str  # relative to its location; never absolute
    size: int
    sha256: str | None


@dataclass
class MigrationReport:
    archive_scope_id: str
    backup_path: Path | None
    inventory: list[LegacyItem]
    imported: Counter = field(default_factory=Counter)
    skipped_existing_settings: list[str] = field(default_factory=list)
    excluded_secret_keys: list[str] = field(default_factory=list)
    excluded_path_settings: list[str] = field(default_factory=list)
    excluded_fields: Counter = field(default_factory=Counter)
    retained_in_yaml: list[str] = field(default_factory=list)
    missing_pdfs: int = 0
    shipped_default_config_ignored: bool = False
    not_imported: list[str] = field(default_factory=list)
    deferred_cleanup: list[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_item(kind: str, location: str, base: Path, path: Path) -> LegacyItem:
    return LegacyItem(kind, location, path.relative_to(base).as_posix(), path.stat().st_size,
                      _sha256(path))


def _config_path(sources: LegacySources) -> Path | None:
    if sources.config_path is not None:
        return sources.config_path
    if sources.legacy_home is not None and (sources.legacy_home / "config.yaml").is_file():
        return sources.legacy_home / "config.yaml"
    return None


def inventory_legacy(sources: LegacySources) -> list[LegacyItem]:
    """List legacy operational files without modifying anything."""
    root = sources.archive_root
    items = [_file_item("review_queue", "archive", root, root / n)
             for n in QUEUE_FILENAMES if (root / n).is_file()]
    items += [_file_item("filing_log", "archive", root, p)
              for p in sorted(root.glob("filing_log_*.json")) if p.is_file()]
    cache = root / "_cache"
    if cache.is_dir():
        files = [p for p in cache.rglob("*") if p.is_file()]
        items.append(LegacyItem("preview_cache", "archive", "_cache",
                                sum(p.stat().st_size for p in files), None))
    home = sources.legacy_home
    if home is not None:
        if (home / "content_hashes.json").is_file():
            items.append(_file_item("hash_index", "legacy_home", home, home / "content_hashes.json"))
        if (home / "logs").is_dir():
            items += [_file_item("app_log", "legacy_home", home, p)
                      for p in sorted((home / "logs").glob("*.log")) if p.is_file()]
    config = _config_path(sources)
    if config is not None and config.is_file():
        if home is not None and home in config.parents:
            items.append(_file_item("config", "legacy_home", home, config))
        else:
            items.append(LegacyItem("config", "config", config.name, config.stat().st_size,
                                    _sha256(config)))
    return items


class _Importer:
    def __init__(self, conn: sqlite3.Connection, sources: LegacySources, scope_id: str,
                 report: MigrationReport) -> None:
        self.conn = conn
        self.sources = sources
        self.root = sources.archive_root.resolve()
        self.scope_id = scope_id
        self.report = report
        self.now = utc_now()

    def insert(self, table: str, row: dict[str, Any]) -> None:
        columns = ", ".join(row)
        marks = ", ".join("?" * len(row))
        cursor = self.conn.execute(
            f"INSERT INTO {table}({columns}) VALUES ({marks}) ON CONFLICT DO NOTHING",
            tuple(row.values()),
        )
        self.report.imported[table] += cursor.rowcount

    def relative(self, value: Any) -> str | None:
        """Map a legacy absolute/~ path to an archive-relative path, or None if outside."""
        if not isinstance(value, str) or not value:
            return None
        text = value
        if text.startswith("~"):
            text = str(self.sources.user_home / text.lstrip("~").lstrip("/"))
        path = Path(os.path.normpath(text))
        if not path.is_absolute():
            candidate = path.as_posix()
        else:
            for base in (self.root, Path(os.path.normpath(self.sources.archive_root))):
                try:
                    candidate = path.relative_to(base).as_posix()
                    break
                except ValueError:
                    continue
            else:
                self.report.excluded_fields["absolute_path"] += 1
                return None
        try:
            return archive_relative(candidate)
        except UnsafeValueError:
            self.report.excluded_fields["absolute_path"] += 1
            return None

    def archive_file(self, relative_path: str | None) -> Path | None:
        """Return an existing regular file confined beneath the archive root (read-only)."""
        if relative_path is None:
            return None
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents or not path.is_file():
            return None
        return path


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"legacy file {path.name} is unreadable") from exc


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return 0.0


def _optional_name(value: Any) -> str | None:
    try:
        return safe_name(value)
    except UnsafeValueError:
        return None


def _import_reviews(imp: _Importer) -> None:
    for queue_name in QUEUE_FILENAMES:
        path = imp.sources.archive_root / queue_name
        if not path.is_file():
            continue
        data = _load_json(path)
        items = data.get("items", []) if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise MigrationError(f"legacy file {queue_name} has no item list")
        groups: dict[str, list[dict]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise MigrationError(f"legacy file {queue_name} has a malformed item")
            groups.setdefault(str(item.get("source_pdf", "")), []).append(item)
        for source, group in groups.items():
            _import_review_group(imp, source, group)


def _import_review_group(imp: _Importer, source: str, group: list[dict]) -> None:
    relative = imp.relative(source)
    pdf = imp.archive_file(relative)
    if relative is not None and pdf is not None:
        locator = f"archive:{relative}"
        source_key = relative
        fingerprint = f"sha256:{_sha256(pdf)}"
    else:
        imp.report.missing_pdfs += 1
        source_key = "external:" + hashlib.sha256(source.encode()).hexdigest()
        locator = f"unavailable:{_optional_name(Path(source).name) or 'legacy-source'}"
        fingerprint = source_key
    job_id = stable_id("legacy_job", imp.scope_id, source_key)
    statuses = [str(i.get("status")) if i.get("status") in REVIEW_STATUSES else "pending"
                for i in group]
    imp.insert("jobs", {
        "id": job_id, "archive_scope_id": imp.scope_id, "source_fingerprint": fingerprint,
        "source_name": _optional_name(Path(source).name) or "legacy-source",
        "source_locator": validate_source_locator(locator),
        "status": "review" if "pending" in statuses else "completed",
        "attempt": 0, "created_at": imp.now, "updated_at": imp.now,
    })
    for item, status in zip(group, statuses):
        for key in set(item) - QUEUE_FIELDS_USED:
            imp.report.excluded_fields[key] += 1
        pages = sorted({p for p in item.get("pages", []) if isinstance(p, int) and p >= 1})
        for page in pages:
            imp.insert("job_pages", {
                "job_id": job_id, "archive_scope_id": imp.scope_id, "page_number": page,
                "status": PAGE_STATUS_FOR_REVIEW[status],
            })
        review_id = stable_id("legacy_review", imp.scope_id, source_key,
                              str(item.get("id")), json.dumps(pages))
        features = {k: item.get(k) for k in ("institution", "doc_type", "period")}
        candidate = {"pages": pages, **features, "rule_matched": item.get("rule_matched"),
                     "legacy_id": item.get("id")}
        imp.insert("review_items", {
            "id": review_id, "archive_scope_id": imp.scope_id, "job_id": job_id,
            "candidate_json": check_safe_json(candidate),
            "suggested_filename": _optional_name(item.get("suggested_filename")),
            "suggested_relative_directory": imp.relative(item.get("suggested_directory")),
            "confidence": _confidence(item.get("confidence")), "status": status,
            "created_at": imp.now, "updated_at": imp.now,
        })
        chosen = imp.relative(item.get("corrected_directory")) if status == "corrected" else None
        if chosen is not None:
            imp.insert("corrections", {
                "id": stable_id("legacy_correction", review_id),
                "archive_scope_id": imp.scope_id, "review_item_id": review_id,
                "normalized_features_json": check_safe_json(features),
                "chosen_rule_id": item.get("rule_matched")
                if isinstance(item.get("rule_matched"), str) else None,
                "chosen_relative_directory": chosen, "created_at": imp.now,
            })


def _import_history(imp: _Importer) -> None:
    for log_path in sorted(imp.sources.archive_root.glob("filing_log_*.json")):
        data = _load_json(log_path)
        entries = data.get("entries", []) if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise MigrationError(f"legacy file {log_path.name} has no entry list")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise MigrationError(f"legacy file {log_path.name} has a malformed entry")
            directory = imp.relative(entry.get("target_directory"))
            filename = _optional_name(entry.get("filename"))
            relative_path = f"{directory}/{filename}" if directory and filename else None
            pages = sorted({p for p in entry.get("pages_extracted", [])
                            if isinstance(p, int) and p >= 1})
            pdf = imp.archive_file(relative_path)
            record_id = None
            if relative_path is not None and pdf is not None:
                record_id = stable_id("file_record", imp.scope_id, relative_path)
                imp.insert("file_records", {
                    "id": record_id, "archive_scope_id": imp.scope_id,
                    "relative_path": relative_path, "content_sha256": _sha256(pdf),
                    "page_numbers_json": json.dumps(pages), "role": "filed",
                    "created_at": imp.now,
                })
            else:
                imp.report.missing_pdfs += 1
            filed_at = entry.get("filed_at") if isinstance(entry.get("filed_at"), str) else imp.now
            request = {
                "legacy_log": log_path.name, "filename": filename,
                "relative_directory": directory, "pages": pages,
                "confidence": _confidence(entry.get("confidence")),
                **{k: entry.get(k) for k in ("rule_matched", "institution", "doc_type", "period")},
            }
            imp.insert("operations", {
                "id": stable_id("legacy_filing", imp.scope_id, log_path.name, str(index)),
                "archive_scope_id": imp.scope_id, "kind": "legacy_filing",
                "status": "completed", "request_json": check_safe_json(request),
                "result_json": json.dumps({"file_record_id": record_id,
                                           "availability": "present" if record_id else "missing"}),
                "created_at": filed_at, "completed_at": filed_at,
            })


def _normalize_path_setting(value: Any, home: Path) -> str | None:
    if not isinstance(value, str):
        return None
    if value == "~" or value.startswith("~/"):
        return value
    path = Path(os.path.normpath(value))
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return None


def _import_settings(imp: _Importer, settings: SettingsRepository) -> None:
    config_path = _config_path(imp.sources)
    if config_path is None or not config_path.is_file():
        return
    if config_path.resolve() == SHIPPED_DEFAULT_CONFIG:
        imp.report.shipped_default_config_ignored = True
        return
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise MigrationError("legacy config is unreadable") from exc
    if not isinstance(config, dict):
        raise MigrationError("legacy config is not a mapping")
    for key, value in config.items():
        key = str(key)
        if SECRET_KEY_RE.search(key):
            imp.report.excluded_secret_keys.append(key)
            continue
        if key == "archive_root":
            continue  # represented by the archive scope
        if key not in IMPORTED_SETTING_KEYS:
            imp.report.retained_in_yaml.append(key)
            continue
        if key in PATH_SETTING_KEYS:
            value = _normalize_path_setting(value, imp.sources.user_home)
            if value is None:
                imp.report.excluded_path_settings.append(key)
                continue
        try:
            inserted = settings.set_if_absent(imp.scope_id, key, value)
        except UnsafeValueError:
            imp.report.excluded_secret_keys.append(key)
            continue
        if inserted:
            imp.report.imported["settings"] += 1
        else:
            imp.report.skipped_existing_settings.append(key)


def _validate(imp: _Importer) -> None:
    problems = integrity_problems(imp.conn)
    verify_schema(imp.conn)
    unaccounted = imp.conn.execute(
        "SELECT COUNT(*) FROM jobs j WHERE archive_scope_id = ? AND NOT EXISTS "
        "(SELECT 1 FROM job_pages p WHERE p.job_id = j.id)", (imp.scope_id,),
    ).fetchone()[0]
    if problems or unaccounted:
        raise MigrationError("imported state failed integrity or page-accounting checks")


def migrate_legacy(db: StateDatabase, sources: LegacySources) -> MigrationReport:
    """Import legacy state idempotently; roll back every database change on failure."""
    if not db.is_open:
        raise MigrationError("the state writer lock must be held before migration")
    inventory = inventory_legacy(sources)
    backup = create_backup(db, label="pre-migration")
    store = StateStore(db)
    try:
        with db.transaction() as conn:
            scope = store.scopes.register(sources.archive_root, home=sources.user_home,
                                          touch=False)
            report = MigrationReport(scope.id, backup, inventory)
            importer = _Importer(conn, sources, scope.id, report)
            _import_reviews(importer)
            _import_history(importer)
            _import_settings(importer, store.settings)
            _validate(importer)
            fingerprint = hashlib.sha256(json.dumps(
                [[i.kind, i.location, i.name, i.sha256] for i in inventory]).encode()).hexdigest()
            importer.insert("operations", {
                "id": stable_id("legacy_migration", scope.id, fingerprint),
                "archive_scope_id": scope.id, "kind": "legacy_migration",
                "status": "completed",
                "request_json": json.dumps({"schema_version": SCHEMA_VERSION,
                                            "inventory_sha256": fingerprint}),
                "created_at": importer.now, "completed_at": importer.now,
            })
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError("legacy migration failed and was rolled back") from exc
    report.imported = +report.imported
    report.not_imported = sorted({i.kind for i in inventory} & {"hash_index", "app_log",
                                                               "preview_cache"})
    report.deferred_cleanup = [f"{i.location}/{i.name}" for i in inventory
                               if i.kind in DEFERRED_KINDS]
    return report
