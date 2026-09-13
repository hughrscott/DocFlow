"""The ordinary processing workflow feeds the durable review queue (no archive queue state).

Local OCR is replaced by synthetic page records; the model transport is deny-all and the
config is local-only. ``uvicorn.run`` is replaced by an in-process client; HOME, archive,
inbox and application state are synthetic temp paths.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import uvicorn
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient

from docflow.cli import cli
from docflow.ocr.analyzer import PageRecord
from docflow.web import app as web_app
from tests.reliability.synthetic import sha256_file, write_image_pdf

OPERATIONAL = ("review_queue.json", "MailArchivingSummary.xlsx", "MailArchivingSummary.txt")


@pytest.fixture()
def synthetic_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two statement pages of one synthetic institution; no Tesseract, no raw data."""
    from docflow.ingestion import loader
    from docflow.ocr import analyzer

    def pages(images):
        return [PageRecord(page_number=n, raw_text=f"Synthetic Bank statement page {n}",
                           institution="synthbank", account_hint=None, period_hint="08/2026",
                           page_of_n=f"Page {n} of {len(images)}", doc_type_hint="statement",
                           confidence=0.9, rotation_applied=0)
                for n in range(1, len(images) + 1)]

    monkeypatch.setattr(loader, "load_pdf", lambda path: ["page"] * 2)
    monkeypatch.setattr(analyzer, "analyze_pages", pages)


def write_config(tmp_path: Path, archive: Path, inbox: Path, **extra) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "archive_root": str(archive), "scan_watch_folder": str(inbox),
        # A threshold above 1.0 keeps synthetic rule matches in review.
        "privacy_mode": "local_only", "confidence_threshold": 1.01,
        "rules_file": str(tmp_path / "no-rules.md"),
        "filing_rules": [{"id": "synthetic_statement", "match": {"institution": "synthbank"},
                          "file_to": "Household/SynthBank",
                          "filename_template": "SynthBankStatement{period}.pdf"}],
        **extra}))
    return path


def run_ui(config: Path, monkeypatch: pytest.MonkeyPatch, driver) -> None:
    """Run ``docflow ui`` with ``driver(client)`` in place of the server (no socket)."""
    def run(app, **kwargs):
        with TestClient(app) as client:
            driver(client)

    monkeypatch.setattr(uvicorn, "run", run)
    result = CliRunner().invoke(cli, ["--config", str(config), "ui"])
    assert result.exit_code == 0, (result.output, repr(result.exception))


def wait_for(client: TestClient, legacy_job_id: str) -> dict:
    for _ in range(400):
        state = client.get(f"/api/process/status/{legacy_job_id}").json()
        if state["status"] in {"completed", "error"}:
            return state
        time.sleep(0.02)
    raise AssertionError("processing did not finish")


def operational_files(archive: Path) -> list[str]:
    return sorted(p.relative_to(archive).as_posix() for p in archive.rglob("*")
                  if p.name in OPERATIONAL or p.name.startswith("filing_log_")
                  or p.name == "content_hashes.json")


def test_processed_upload_creates_durable_review_item_that_undoes_after_restart(
        tmp_path, isolated_home, monkeypatch, synthetic_ocr) -> None:
    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    archive.mkdir()
    inbox.mkdir()
    scan = write_image_pdf(tmp_path / "scanner/scan.pdf", [1, 2])
    config = write_config(tmp_path, archive, inbox)
    first: dict = {}

    def process_and_approve(client: TestClient) -> None:
        uploaded = client.post("/api/upload", files={
            "file": ("scan.pdf", scan.read_bytes(), "application/pdf")})
        started = client.post("/api/process", json={"path": uploaded.json()["path"]})
        assert started.status_code == 200, started.text
        first["state"] = wait_for(client, started.json()["job_id"])
        scope_id = client.get("/api/v1/archive-scopes/active").json()["archive_scope"]["id"]
        items = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id, "status": "pending"}).json()["items"]
        first.update(scope_id=scope_id, items=items)
        if items:
            first["approved"] = client.post(f"/api/v1/review-items/{items[0]['id']}/approve",
                                            json={"archive_scope_id": scope_id,
                                                  "idempotency_key": "ui-approve-1"})

    run_ui(config, monkeypatch, process_and_approve)

    assert first["state"]["status"] == "completed", first["state"]
    (item,) = first["items"]
    assert (item["page_numbers"], item["source_name"], item["actions"]) == (
        [1, 2], "scan.pdf", ["approve", "correct", "skip"])
    assert (item["suggested_relative_directory"], item["suggested_filename"]) == (
        "Household/SynthBank", "SynthBankStatementAugust2026.pdf")
    assert first["approved"].status_code == 200, first["approved"].text
    operation_id = first["approved"].json()["operation"]["id"]
    assert (archive / "Household/SynthBank/SynthBankStatementAugust2026.pdf").is_file()
    assert operational_files(archive) == []
    second: dict = {}

    def undo_after_restart(client: TestClient) -> None:
        scope_id = client.get("/api/v1/archive-scopes/active").json()["archive_scope"]["id"]
        second["undo"] = client.post(f"/api/v1/operations/{operation_id}/undo", json={
            "archive_scope_id": scope_id, "idempotency_key": "ui-undo-1"})
        second["items"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id, "status": "pending"}).json()["items"]
        second["job"] = client.get(f"/api/v1/jobs/{item['job_id']}",
                                   params={"archive_scope_id": scope_id}).json()

    run_ui(config, monkeypatch, undo_after_restart)

    assert second["undo"].json()["outcome"] == "undone", second["undo"].text
    assert [i["id"] for i in second["items"]] == [item["id"]]
    assert second["job"]["job"]["status"] == "review"
    assert [p["status"] for p in second["job"]["page_accounting"]["pages"]] == ["review", "review"]
    assert not (archive / "Household/SynthBank/SynthBankStatementAugust2026.pdf").exists()
    (original,) = archive.glob("BeenOrganized*/scan.pdf")
    assert sha256_file(original) == sha256_file(scan)
    assert operational_files(archive) == []
    assert sorted(p.name for p in archive.iterdir()) == [original.parent.name, "Household"]


def _pending(config: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict], dict]:
    seen: dict = {}

    def read(client: TestClient) -> None:
        scope_id = client.get("/api/v1/archive-scopes/active").json()["archive_scope"]["id"]
        seen["items"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id, "status": "pending"}).json()["items"]
        seen["jobs"] = {i["job_id"]: client.get(f"/api/v1/jobs/{i['job_id']}", params={
            "archive_scope_id": scope_id}).json() for i in seen["items"]}

    run_ui(config, monkeypatch, read)
    return seen["items"], seen["jobs"]


def test_cli_pipeline_files_durably_for_watch_and_outside_inputs(
        tmp_path, isolated_home, monkeypatch, synthetic_ocr) -> None:
    from docflow.cli import _run_pipeline

    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    archive.mkdir()
    watched = write_image_pdf(inbox / "watched.pdf", [1, 2])
    outside = write_image_pdf(tmp_path / "scanner/outside.pdf", [3, 4])
    outside_bytes = outside.read_bytes()
    config = write_config(tmp_path, archive, inbox)

    _run_pipeline(watched, config)
    _run_pipeline(outside, config)

    items, jobs = _pending(config, monkeypatch)
    assert sorted((i["source_name"], i["page_numbers"]) for i in items) == [
        ("outside.pdf", [1, 2]), ("watched.pdf", [1, 2])]
    for job in jobs.values():
        assert job["job"]["status"] == "review"
        assert [p["status"] for p in job["page_accounting"]["pages"]] == ["review", "review"]
    assert not watched.exists()  # watch-folder source removed only after verified retention
    assert outside.read_bytes() == outside_bytes  # a file outside the roots is never moved
    assert sorted(p.name for p in archive.glob("BeenOrganized*/*.pdf")) == [
        "outside.pdf", "watched.pdf"]
    (retained_outside,) = archive.glob("BeenOrganized*/outside.pdf")
    assert retained_outside.stat().st_ino != outside.stat().st_ino  # an independent copy
    assert not (archive / "_uploads").exists()
    assert operational_files(archive) == []


def test_bridged_items_keep_page_references_and_collision_safe_originals(
        tmp_path, isolated_home, monkeypatch, synthetic_ocr) -> None:
    from docflow.filing.processing import originals_directory
    from docflow.ingestion.loader import fingerprint_pdf

    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    existing = archive / originals_directory() / "scan.pdf"
    write_image_pdf(existing, [9])
    existing_hash = sha256_file(existing)
    scans = [write_image_pdf(tmp_path / f"scanner{n}/scan.pdf", marks)
             for n, marks in ((1, [1, 2]), (2, [5, 6]))]
    config = write_config(tmp_path, archive, inbox)
    seen: dict = {}

    def workflow(client: TestClient) -> None:
        scope_id = client.get("/api/v1/archive-scopes/active").json()["archive_scope"]["id"]
        for scan in scans:
            uploaded = client.post("/api/upload", files={
                "file": ("scan.pdf", scan.read_bytes(), "application/pdf")})
            started = client.post("/api/process", json={"path": uploaded.json()["path"]})
            assert wait_for(client, started.json()["job_id"])["status"] == "completed"
        items = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id}).json()["items"]
        first, second = sorted(items, key=lambda i: i["created_at"] + i["id"])[:2]
        body = {"archive_scope_id": scope_id}
        seen["jobs"] = [client.get(f"/api/v1/jobs/{i['job_id']}", params=body).json()
                        for i in (first, second)]
        seen["correct"] = client.post(f"/api/v1/review-items/{first['id']}/correct", json={
            **body, "idempotency_key": "c-1", "relative_directory": "Household/Corrected",
            "filename": "Statement.pdf"}).json()
        seen["batch"] = client.post("/api/v1/review-items/batch", json={
            **body, "idempotency_key": "b-1", "action": "skip",
            "review_item_ids": [second["id"]]}).json()
        seen["corrected_pages"] = fingerprint_pdf(archive / "Household/Corrected/Statement.pdf")
        seen["undo"] = [client.post(f"/api/v1/operations/{op['operation']['id']}/undo", json={
            **body, "idempotency_key": f"u-{n}"}).json()
            for n, op in enumerate((seen["correct"], seen["batch"]))]
        seen["pending"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id}).json()["items"]

    run_ui(config, monkeypatch, workflow)

    originals = [f["relative_path"] for job in seen["jobs"] for f in job["files"]
                 if f["role"] == "original"]
    assert originals == [f"{originals_directory()}/scan_2.pdf",
                         f"{originals_directory()}/scan_3.pdf"]
    assert sha256_file(existing) == existing_hash
    for original, scan in zip(originals, scans):
        assert sha256_file(archive / original) == sha256_file(scan)
    retained = fingerprint_pdf(archive / originals[0])
    assert seen["corrected_pages"].pages[0].content_sha256 == retained.pages[0].content_sha256
    assert seen["corrected_pages"].page_count == 2
    assert seen["batch"]["items"][0]["result"] == "skipped"
    assert [u["outcome"] for u in seen["undo"]] == ["undone", "undone"]
    assert len(seen["pending"]) == 2
    assert not (archive / "Household/Corrected/Statement.pdf").exists()
    assert operational_files(archive) == []


def test_existing_legacy_queue_and_summary_files_are_inert_and_byte_identical(
        tmp_path, isolated_home, monkeypatch, synthetic_ocr) -> None:
    import json

    from tests.reliability.synthetic import tree_digest

    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    legacy_scan = write_image_pdf(archive / "BeenOrganized033026/Scan-legacy.pdf", [7])
    (archive / "review_queue.json").write_text(json.dumps({"items": [{
        "id": "1_101500", "source_pdf": str(legacy_scan), "pages": [1], "status": "pending",
        "suggested_filename": "Legacy.pdf", "suggested_directory": str(archive / "Legacy"),
        "confidence": 0.3}]}))
    (archive / "MailArchivingSummary.txt").write_text("legacy summary\n")
    (archive / "MailArchivingSummary.xlsx").write_bytes(b"legacy workbook bytes")
    (archive / "filing_log_20260330_101500.json").write_text('{"entries": []}')
    legacy = {k: v for k, v in tree_digest(archive).items()}
    scan = write_image_pdf(tmp_path / "scanner/scan.pdf", [1, 2])
    config = write_config(tmp_path, archive, inbox)
    seen: dict = {}

    def ui_workflow(client: TestClient) -> None:
        scope_id = client.get("/api/v1/archive-scopes/active").json()["archive_scope"]["id"]
        seen["before"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id}).json()["items"]
        seen["legacy_routes"] = [client.get("/api/queue").status_code,
                                 client.post("/api/queue/approve/1_101500").status_code,
                                 client.post("/api/queue/skip/1_101500").status_code]
        uploaded = client.post("/api/upload", files={
            "file": ("scan.pdf", scan.read_bytes(), "application/pdf")})
        started = client.post("/api/process", json={"path": uploaded.json()["path"]})
        wait_for(client, started.json()["job_id"])
        seen["after"] = client.get("/api/v1/review-items", params={
            "archive_scope_id": scope_id}).json()["items"]

    run_ui(config, monkeypatch, ui_workflow)

    def legacy_review_command(client: TestClient) -> None:
        seen["review_command"] = [client.get("/queue").status_code,
                                  client.post("/skip/1_101500").status_code,
                                  client.post("/approve/1_101500").status_code]

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: legacy_review_command(TestClient(app)))
    result = CliRunner().invoke(cli, ["--config", str(config), "review"])

    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert seen["before"] == []
    assert seen["legacy_routes"] == [404, 404, 404]
    assert seen["review_command"] == [404, 404, 404]
    assert [i["source_name"] for i in seen["after"]] == ["scan.pdf"]
    current = tree_digest(archive)
    assert {k: current[k] for k in legacy} == legacy
    assert operational_files(archive) == sorted(
        k for k in legacy if not k.startswith("BeenOrganized"))


def test_no_production_module_writes_legacy_queue_or_summary_state() -> None:
    import importlib.util

    for retired in ("docflow.review.server", "docflow.review.queue", "docflow.summary.generator"):
        try:
            spec = importlib.util.find_spec(retired)
        except ModuleNotFoundError:  # the whole legacy package is gone
            spec = None
        assert spec is None, retired
    package = Path(__file__).resolve().parents[2] / "docflow"
    # Only the read-only legacy migration inventory may still name these files.
    writers = sorted(p.relative_to(package).as_posix() for p in package.rglob("*.py")
                     if p.name != "migration.py" and (
                         "review_queue.json" in p.read_text()
                         or "MailArchivingSummary" in p.read_text()))
    assert writers == []


def test_no_default_archive_and_no_caller_selected_paths(
        tmp_path, isolated_home, monkeypatch, synthetic_ocr) -> None:
    from docflow.cli import _run_pipeline

    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    archive.mkdir()
    scan = write_image_pdf(inbox / "scan.pdf", [1, 2])
    scan_hash = sha256_file(scan)
    no_root = tmp_path / "no-root.yaml"
    no_root.write_text(yaml.safe_dump({"scan_watch_folder": str(inbox),
                                       "privacy_mode": "local_only"}))

    with pytest.raises(Exception, match="archive_root is not set"):
        _run_pipeline(scan, no_root)
    assert sha256_file(scan) == scan_hash
    assert not (isolated_home / "DocFlowExample").exists()
    assert not (isolated_home / ".local/share/docflow").exists()

    seen: dict = {}

    def attempts(client: TestClient) -> None:
        payload = scan.read_bytes()
        seen["uploads"] = [client.post("/api/upload", files={
            "file": (name, payload, "application/pdf")}).status_code
            for name in ("../escape.pdf", "../../escape.pdf", "sub/escape.pdf",
                         str(tmp_path / "abs-escape.pdf"), ".hidden.pdf")]
        seen["process"] = [client.post("/api/process", json={"path": p}).status_code
                           for p in (str(scan.parent.parent / "outside.pdf"), "watch:scan.pdf",
                                     str(archive / "BeenOrganized"), "../inbox/scan.pdf")]

    write_image_pdf(tmp_path / "outside.pdf", [3])
    run_ui(write_config(tmp_path, archive, inbox), monkeypatch, attempts)

    assert seen["uploads"] == [400] * 5
    assert seen["process"] == [400] * 4
    escaped = [p for p in tmp_path.rglob("*escape.pdf")] + list(tmp_path.rglob(".hidden.pdf"))
    assert escaped == []

    monkeypatch.setattr(web_app, "_processing_state", {})
    offline = TestClient(web_app.app).post("/api/process", json={"path": str(scan)})
    assert offline.status_code == 503
    assert web_app._processing_state == {}


def test_unmatched_reclassify_is_confined_and_logs_outside_the_archive(
        tmp_path, isolated_home, monkeypatch) -> None:
    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    unmatched = write_image_pdf(archive / "_Unmatched/letter.pdf", [1])
    letter_hash = sha256_file(unmatched)
    outside = write_image_pdf(tmp_path / "outside/private.pdf", [2])
    outside_hash = sha256_file(outside)
    kept = write_image_pdf(archive / "Docs/Keep.pdf", [3])
    seen: dict = {}

    def reclassify(client: TestClient) -> None:
        post = lambda **body: client.post("/api/unmatched/reclassify", json=body)
        seen["rejected"] = [
            post(path=str(outside), filename="Private.pdf", directory="Docs").status_code,
            post(path=str(kept), filename="Moved.pdf", directory="Docs").status_code,
            post(path=str(unmatched), filename="Letter.pdf",
                 directory="../../outside").status_code,
            post(path=str(unmatched), filename="../Letter.pdf", directory="Docs").status_code,
            post(path=str(unmatched), filename="Letter.pdf", directory="/tmp").status_code,
        ]
        seen["accepted"] = post(path=str(unmatched), filename="Letter.pdf",
                                directory="Household/Letters")

    run_ui(write_config(tmp_path, archive, inbox), monkeypatch, reclassify)

    assert all(code in {400, 403} for code in seen["rejected"]), seen["rejected"]
    assert sha256_file(outside) == outside_hash and kept.is_file()
    assert seen["accepted"].status_code == 200, seen["accepted"].text
    assert sha256_file(archive / "Household/Letters/Letter.pdf") == letter_hash
    assert not unmatched.exists()
    assert list(archive.rglob("corrections_log.json")) == []
    assert (isolated_home / ".local/share/docflow/logs/corrections_log.json").is_file()
    assert str(tmp_path) not in seen["accepted"].text


def test_unmatched_preview_cache_lives_in_application_state(
        tmp_path, isolated_home, monkeypatch) -> None:
    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    unmatched = write_image_pdf(archive / "_Unmatched/letter.pdf", [1])
    seen: dict = {}

    def preview(client: TestClient) -> None:
        seen["response"] = client.get("/api/preview/file",
                                      params={"path": str(unmatched), "page": 1})

    run_ui(write_config(tmp_path, archive, inbox), monkeypatch, preview)

    assert seen["response"].status_code == 200
    assert seen["response"].headers["content-type"] == "image/png"
    assert not (archive / "_cache").exists()
    assert list((isolated_home / ".local/share/docflow/cache").rglob("*.png"))


def test_existing_migration_stays_idempotent_over_bridged_state(
        tmp_path, isolated_home, monkeypatch, synthetic_ocr) -> None:
    import json

    from docflow.state.database import StateDatabase
    from docflow.state.migration import LegacySources, migrate_legacy
    from docflow.web.bootstrap import local_state_paths
    from tests.reliability.synthetic import tree_digest

    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    legacy_scan = write_image_pdf(archive / "BeenOrganized033026/Scan-legacy.pdf", [7])
    (archive / "review_queue.json").write_text(json.dumps({"items": [{
        "id": "1_101500", "source_pdf": str(legacy_scan), "pages": [1], "status": "pending",
        "suggested_filename": "Legacy.pdf", "suggested_directory": str(archive / "Legacy"),
        "confidence": 0.3}]}))
    write_image_pdf(inbox / "scan.pdf", [1, 2])
    config = write_config(tmp_path, archive, inbox)
    from docflow.cli import _run_pipeline

    _run_pipeline(inbox / "scan.pdf", config)
    legacy = tree_digest(archive)
    _, paths = local_state_paths(yaml.safe_load(config.read_text()))
    sources = LegacySources(archive_root=archive, user_home=isolated_home)
    counts = "SELECT (SELECT COUNT(*) FROM jobs), (SELECT COUNT(*) FROM review_items), " \
             "(SELECT COUNT(*) FROM file_records), (SELECT COUNT(*) FROM operations " \
             "WHERE kind <> 'legacy_migration')"

    with StateDatabase(paths) as db:
        before = tuple(db.connection.execute(counts).fetchone())
        migrate_legacy(db, sources)
        once = tuple(db.connection.execute(counts).fetchone())
        migrate_legacy(db, sources)
        twice = tuple(db.connection.execute(counts).fetchone())
        bridged = db.connection.execute(
            "SELECT r.status FROM review_items r JOIN jobs j ON j.id = r.job_id "
            "WHERE j.source_name = 'scan.pdf'").fetchall()

    assert once == twice
    assert once[0] >= before[0] and once[2:] == before[2:]
    assert [tuple(row) for row in bridged] == [("pending",)]
    assert tree_digest(archive) == legacy
