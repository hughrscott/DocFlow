"""S02/S03: legacy inventory and idempotent, rollback-safe import."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from docflow.state import migration
from docflow.state.backup import create_backup, write_support_export
from docflow.state.database import StateDatabase
from docflow.state.migration import (
    LegacySources,
    MigrationError,
    inventory_legacy,
    migrate_legacy,
)
from docflow.state.paths import StatePaths
from docflow.state.repositories import StateStore
from tests.state.conftest import _write_pdf, tree_digest

REPO_ROOT = Path(__file__).resolve().parents[2]
# Sentinels are assembled at runtime so no credential-shaped literal is tracked.
SECRET = "sk-" + "synthetic" + "Q" * 24
SECOND_SECRET = "synthetic-" + "openrouter-" + "credential-value"
RAW_OCR = "RAW-OCR-" + "SENTINEL-7f3a"
NOTES = "MODEL-NOTES-" + "SENTINEL-2c9d"
ACCOUNT = "ACCOUNT-" + "SENTINEL-4e1b"
PLACEHOLDER = "PLACEHOLDER-" + "SENTINEL-5b1c"
SENTINELS = (SECRET, SECOND_SECRET, RAW_OCR, NOTES, ACCOUNT, PLACEHOLDER)
# Harmless value in an allowed field: proves byte scans can see stored content.
ALLOWED = "ALLOWED-" + "INSTITUTION-SENTINEL-8a6f"
STATE_TABLES = ("archive_scopes", "settings", "jobs", "job_pages", "review_items",
                "file_records", "operations", "operation_steps", "corrections")


def _queue_item(legacy_id: str, source: Path, pages: list[int], status: str, **extra) -> dict:
    item = {
        "id": legacy_id,
        "source_pdf": str(source),
        "pages": pages,
        "institution": "pnc",
        "doc_type": "statement",
        "period": "March2026",
        "account": ACCOUNT,
        "suggested_filename": "PNCBankStatementMarch2026.pdf",
        "suggested_directory": str(source.parents[1] / "Household" / "PNC"),
        "rule_matched": "pnc_household_checking",
        "confidence": 0.42,
        "notes": NOTES,
        "raw_text_preview": RAW_OCR,
        "status": status,
        "corrected_filename": None,
        "corrected_directory": None,
    }
    item.update(extra)
    return item


@pytest.fixture()
def legacy(archive_root: Path, isolated_home: Path) -> LegacySources:
    scan = archive_root / "BeenOrganized033026" / "Scan-synthetic-1.pdf"
    queue = {
        "updated": "2026-03-30T10:15:00",
        "items": [
            _queue_item("1_101500", scan, [1, 2], "pending",
                        placeholder_map={"PERSON_1": PLACEHOLDER}),
            _queue_item("3_101500", scan, [3], "pending", confidence=None),
            _queue_item("4_101500", scan, [4], "corrected",
                        corrected_filename="TaxInvoice.pdf",
                        corrected_directory=str(archive_root / "Tax" / "2026 Taxes")),
            _queue_item("1_111111", isolated_home / "Elsewhere" / "Scan-outside.pdf", [1],
                        "skipped"),
        ],
    }
    (archive_root / "review_queue.json").write_text(json.dumps(queue))
    log = {
        "timestamp": "20260330_101500",
        "entries": [
            {"filename": "PNCBankChecking.pdf",
             "target_directory": str(archive_root / "Household" / "PNC"),
             "rule_matched": "pnc_household_checking", "confidence": 0.93,
             "filed_at": "2026-03-30T10:15:00", "pages_extracted": [5, 6],
             "institution": "pnc", "doc_type": "statement", "period": "March2026",
             "file_size_bytes": 10},
            {"filename": "MissingStatement.pdf",
             "target_directory": str(archive_root / "Medical"),
             "rule_matched": "medical_bill", "confidence": 0.88,
             "filed_at": "2026-03-30T10:15:00", "pages_extracted": [7],
             "institution": "clinic", "doc_type": "bill", "period": "March2026",
             "file_size_bytes": 10},
        ],
    }
    (archive_root / "filing_log_20260330_101500.json").write_text(json.dumps(log))
    previews = archive_root / "_cache" / "previews"
    previews.mkdir(parents=True)
    (previews / "preview-1.txt").write_text(RAW_OCR)

    legacy_home = isolated_home / ".docflow"
    (legacy_home / "logs").mkdir(parents=True)
    (legacy_home / "logs" / "docflow.log").write_text(f"processed {RAW_OCR}\n")
    (legacy_home / "content_hashes.json").write_text(
        json.dumps({"0" * 64: str(archive_root / "Household" / "PNC" / "PNCBankChecking.pdf")})
    )
    config = {
        "archive_root": str(archive_root),
        "scan_watch_folder": str(isolated_home / "DocFlowExample" / "inbox"),
        "confidence_threshold": 0.82,
        "llm_provider": "openrouter",
        "llm_model": "synthetic/model",
        "llm_api_key": SECRET,
        "openrouter_api_key": SECOND_SECRET,
        "user": {"name": "Morgan Redwood"},
        "filing_rules": [{"id": "pnc_household_checking"}],
    }
    (legacy_home / "config.yaml").write_text(yaml.safe_dump(config))
    return LegacySources(archive_root=archive_root, user_home=isolated_home,
                         legacy_home=legacy_home)


@pytest.fixture()
def db(state_root: Path):
    with StateDatabase(StatePaths(state_root)) as database:
        yield database


def _snapshot(db: StateDatabase) -> dict[str, list[tuple]]:
    return {
        table: sorted(tuple(row) for row in db.connection.execute(f"SELECT * FROM {table}"))
        for table in STATE_TABLES
    }


def test_inventory_is_read_only_and_path_free(legacy: LegacySources, isolated_home: Path) -> None:
    archive_before = tree_digest(legacy.archive_root)
    home_before = tree_digest(isolated_home)
    inventory = inventory_legacy(legacy)
    kinds = {item.kind for item in inventory}
    assert kinds == {"review_queue", "filing_log", "preview_cache", "hash_index", "app_log",
                     "config"}
    for item in inventory:
        assert not item.name.startswith(("/", "~"))
    queue = next(i for i in inventory if i.kind == "review_queue")
    assert queue.sha256 == hashlib.sha256(
        (legacy.archive_root / "review_queue.json").read_bytes()).hexdigest()
    assert tree_digest(legacy.archive_root) == archive_before
    assert tree_digest(isolated_home) == home_before


def test_import_preserves_pending_reviews_history_and_settings(
    db: StateDatabase, legacy: LegacySources, isolated_home: Path
) -> None:
    archive_before = tree_digest(legacy.archive_root)
    home_before = tree_digest(isolated_home)
    report = migrate_legacy(db, legacy)
    store = StateStore(db)
    scope = report.archive_scope_id

    pending = store.reviews.list(scope)
    by_pages = {tuple(r.candidate["pages"]): r for r in pending}
    assert set(by_pages) == {(1, 2), (3,)}
    assert all(r.suggested_relative_directory == "Household/PNC" for r in pending)
    assert by_pages[(1, 2)].confidence == 0.42
    assert by_pages[(3,)].confidence == 0.0  # legacy null confidence is not trusted
    assert {r.status for r in store.reviews.list(scope, "corrected")} == {"corrected"}
    assert len(store.reviews.list(scope, "skipped")) == 1

    [scan_job] = [j for j in store.jobs.list_pending(scope)]
    assert scan_job.status == "review"
    assert scan_job.source_locator == "archive:BeenOrganized033026/Scan-synthetic-1.pdf"
    assert store.jobs.page_totals(scope, scan_job.id) == {"review": 3, "filed": 1}

    conn = db.connection
    [correction] = conn.execute("SELECT * FROM corrections").fetchall()
    assert correction["chosen_relative_directory"] == "Tax/2026 Taxes"
    history = conn.execute(
        "SELECT * FROM operations WHERE kind = 'legacy_filing' ORDER BY id").fetchall()
    assert len(history) == 2
    [record] = conn.execute("SELECT * FROM file_records").fetchall()
    assert record["relative_path"] == "Household/PNC/PNCBankChecking.pdf"
    assert record["role"] == "filed"
    assert record["content_sha256"] == hashlib.sha256(
        (legacy.archive_root / record["relative_path"]).read_bytes()).hexdigest()
    # One filed PDF is missing; one queued source lies outside the archive root.
    assert report.missing_pdfs == 2

    settings = store.settings.all(scope)
    assert settings["confidence_threshold"] == 0.82
    assert settings["llm_provider"] == "openrouter"
    assert settings["scan_watch_folder"] == "~/DocFlowExample/inbox"
    assert not any("api_key" in key for key in settings)
    assert sorted(report.excluded_secret_keys) == ["llm_api_key", "openrouter_api_key"]
    assert "filing_rules" in report.retained_in_yaml
    assert report.excluded_fields["raw_text_preview"] == 4
    assert report.excluded_fields["placeholder_map"] == 1

    assert report.backup_path is not None and report.backup_path.exists()
    assert db.paths.backups in report.backup_path.parents
    # Legacy files are untouched; cleanup is only reported for later.
    assert "archive/review_queue.json" in report.deferred_cleanup
    assert "legacy_home/content_hashes.json" in report.deferred_cleanup
    assert tree_digest(legacy.archive_root) == archive_before
    assert tree_digest(isolated_home) == home_before


def test_existing_user_settings_are_not_overwritten(
    db: StateDatabase, legacy: LegacySources
) -> None:
    store = StateStore(db)
    scope = store.scopes.register(legacy.archive_root, home=legacy.user_home).id
    store.settings.set(scope, "confidence_threshold", 0.9)
    report = migrate_legacy(db, legacy)
    assert store.settings.get(scope, "confidence_threshold") == 0.9
    assert "confidence_threshold" in report.skipped_existing_settings
    assert store.settings.get(scope, "llm_model") == "synthetic/model"


def test_shipped_default_config_is_not_imported_as_user_settings(
    db: StateDatabase, legacy: LegacySources
) -> None:
    shipped = LegacySources(archive_root=legacy.archive_root, user_home=legacy.user_home,
                            config_path=REPO_ROOT / "config" / "default_config.yaml")
    report = migrate_legacy(db, shipped)
    assert report.shipped_default_config_ignored
    assert StateStore(db).settings.all(report.archive_scope_id) == {}


def test_repeated_migration_yields_identical_rows_and_ids(
    db: StateDatabase, legacy: LegacySources
) -> None:
    first = migrate_legacy(db, legacy)
    rows_after_first = _snapshot(db)
    second = migrate_legacy(db, legacy)
    assert _snapshot(db) == rows_after_first
    assert second.archive_scope_id == first.archive_scope_id
    assert sum(second.imported.values()) == 0
    assert sum(first.imported.values()) > 0


def test_in_archive_source_appearing_later_keeps_identity(
    db: StateDatabase, legacy: LegacySources
) -> None:
    late = legacy.archive_root / "BeenOrganized033026" / "Scan-late.pdf"
    queue_path = legacy.archive_root / "review_queue.json"
    queue = json.loads(queue_path.read_text())
    queue["items"].append(_queue_item("1_120000", late, [1], "pending"))
    queue_path.write_text(json.dumps(queue))
    assert not late.exists()

    first = migrate_legacy(db, legacy)
    rows_after_first = _snapshot(db)
    _write_pdf(late, 1, 612)
    second = migrate_legacy(db, legacy)

    assert _snapshot(db) == rows_after_first
    assert sum(second.imported.values()) == 0
    assert first.imported["jobs"] == 3 and first.imported["review_items"] == 5
    conn = db.connection
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM review_items").fetchone()[0] == 5


def test_migration_ids_are_stable_across_fresh_databases(
    tmp_path: Path, legacy: LegacySources
) -> None:
    snapshots = []
    for name in ("state-a", "state-b"):
        with StateDatabase(StatePaths(tmp_path / name)) as database:
            migrate_legacy(database, legacy)
            snapshots.append({
                table: sorted(r[0] for r in database.connection.execute(
                    f"SELECT id FROM {table}"))
                for table in ("archive_scopes", "jobs", "review_items", "file_records",
                              "operations", "corrections")
            })
    assert snapshots[0] == snapshots[1]


def test_induced_failure_rolls_back_and_archive_bytes_unchanged(
    db: StateDatabase, legacy: LegacySources, isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(db)
    scope = store.scopes.register(legacy.archive_root, home=legacy.user_home).id
    store.settings.set(scope, "confidence_threshold", 0.9)
    before = _snapshot(db)
    archive_before = tree_digest(legacy.archive_root)
    home_before = tree_digest(isolated_home)

    def explode(*args, **kwargs):
        raise RuntimeError("induced failure after reviews were inserted")

    monkeypatch.setattr(migration, "_import_history", explode)
    with pytest.raises(MigrationError):
        migrate_legacy(db, legacy)

    assert _snapshot(db) == before
    assert not db.connection.in_transaction
    assert tree_digest(legacy.archive_root) == archive_before
    assert tree_digest(isolated_home) == home_before
    assert any(p.name.startswith("pre-migration-") for p in db.paths.backups.iterdir())


def test_malformed_legacy_queue_fails_closed(db: StateDatabase, legacy: LegacySources) -> None:
    (legacy.archive_root / "review_queue.json").write_text("{not json")
    before = _snapshot(db)
    with pytest.raises(MigrationError):
        migrate_legacy(db, legacy)
    assert _snapshot(db) == before


def test_no_secrets_raw_ocr_maps_or_absolute_paths_in_db_backup_or_export(
    db: StateDatabase, legacy: LegacySources, tmp_path: Path
) -> None:
    queue_path = legacy.archive_root / "review_queue.json"
    queue = json.loads(queue_path.read_text())
    queue["items"][0]["institution"] = ALLOWED
    queue_path.write_text(json.dumps(queue))
    report = migrate_legacy(db, legacy)
    conn = db.connection
    [canonical_root] = [r[0] for r in conn.execute("SELECT canonical_root FROM archive_scopes")]
    dumped = []
    for table in STATE_TABLES:
        for row in conn.execute(f"SELECT * FROM {table}"):
            values = dict(row)
            if table == "archive_scopes":
                values.pop("canonical_root")  # the one registered scope root
            dumped.append(json.dumps(values))
    text = "\n".join(dumped)
    assert ALLOWED in text
    for sentinel in SENTINELS:
        assert sentinel not in text
    assert str(tmp_path) not in text
    assert '"/' not in text

    # The pre-migration backup predates the import, so scan a fresh post-migration
    # backup, plus the live database with its WAL/SHM where committed rows still live.
    post_backup = create_backup(db, label="post-migration")
    assert post_backup != report.backup_path
    live_paths = (db.paths.database, db.paths.wal, db.paths.shm)
    assert db.paths.wal.stat().st_size > 0
    evidence = {
        "post_migration_backup": post_backup.read_bytes(),
        "live_db_wal_shm": b"".join(p.read_bytes() for p in live_paths if p.exists()),
    }
    export_text = write_support_export(db).read_text()
    for name, blob in evidence.items():
        assert ALLOWED.encode() in blob, f"positive control not found in {name}"
        assert canonical_root.encode() in blob, name
        for sentinel in SENTINELS:
            assert sentinel.encode() not in blob, name
        # canonical_root is the only permitted absolute path in stored bytes.
        assert str(tmp_path).encode() not in blob.replace(canonical_root.encode(), b""), name
    for sentinel in SENTINELS:
        assert sentinel not in export_text
    assert str(tmp_path) not in export_text


def test_migration_requires_open_writer(state_root: Path, legacy: LegacySources) -> None:
    with pytest.raises(MigrationError):
        migrate_legacy(StateDatabase(StatePaths(state_root)), legacy)
