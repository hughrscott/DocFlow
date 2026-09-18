"""Durable filing: staged verified outputs, collisions, dedup, original retention."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from docflow.filing.operations import DocumentAssignment as A
from docflow.ingestion.loader import document_sha256, fingerprint_pdf
from tests.reliability.harness import classified_job, make_env
from tests.reliability.synthetic import sha256_file, tree_digest, write_image_pdf

ORIGINALS = "BeenOrganized033026"


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


def page_hashes(env, job_id: str) -> dict[int, str]:
    return dict(env.rows("SELECT page_number, content_sha256 FROM job_pages WHERE job_id = ?",
                         job_id))


def test_files_every_outcome_durably_and_retains_original_last(env) -> None:
    job_id = classified_job(env, [1, 2, 3, 4, 5])
    source = env.watch / "scan.pdf"
    source_raw = sha256_file(source)
    hashes = page_hashes(env, job_id)

    result = env.filer().file_job(env.scope_id, job_id, [
        A((1, 2), "filed", "Household/PNC", "Statement.pdf"),
        A((3,), "review", "Medical", "Bill.pdf", reason="low_confidence", confidence=0.4),
        A((4,), "skipped", reason="blank_page"),
        A((5,), "blocked", reason="privacy_blocked"),
    ], original_relative_directory=ORIGINALS)

    assert (result.job_status, result.operation_status) == ("review", "completed")
    env.reopen()
    assert sorted(tree_digest(env.archive)) == [f"{ORIGINALS}/scan.pdf",
                                                "Household/PNC/Statement.pdf"]
    filed = fingerprint_pdf(env.archive / "Household/PNC/Statement.pdf")
    assert [p.content_sha256 for p in filed.pages] == [hashes[1], hashes[2]]
    assert sha256_file(env.archive / ORIGINALS / "scan.pdf") == source_raw
    assert not source.exists()
    assert env.rows("SELECT page_number, status FROM job_pages WHERE job_id = ? "
                    "ORDER BY page_number", job_id) == [
        (1, "filed"), (2, "filed"), (3, "review"), (4, "skipped"), (5, "blocked")]
    assert env.store.jobs.get(env.scope_id, job_id).status == "review"
    assert env.rows("SELECT relative_path, role, page_numbers_json, content_sha256 "
                    "FROM file_records ORDER BY relative_path") == [
        (f"{ORIGINALS}/scan.pdf", "original", "[1, 2, 3, 4, 5]",
         document_sha256([hashes[n] for n in range(1, 6)])),
        ("Household/PNC/Statement.pdf", "filed", "[1, 2]", filed.document_sha256),
    ]
    assert env.rows("SELECT kind, status FROM operations") == [("file_job", "completed")]
    assert env.rows("SELECT ordinal, kind, status, destination_relative_path "
                    "FROM operation_steps ORDER BY ordinal") == [
        (0, "write_filed", "done", "Household/PNC/Statement.pdf"),
        (1, "retain_original", "done", f"{ORIGINALS}/scan.pdf"),
        (2, "cleanup_source", "done", None),
    ]
    [review] = env.store.reviews.list(env.scope_id)
    assert review.candidate["pages"] == [3]
    assert review.candidate["reason"] == "low_confidence"
    assert (review.suggested_relative_directory, review.suggested_filename) == ("Medical",
                                                                                "Bill.pdf")
    [(result_json,)] = env.rows("SELECT result_json FROM operations")
    evidence = json.loads(result_json)["steps"]
    assert evidence["0"]["raw_sha256"] == sha256_file(env.archive / "Household/PNC/Statement.pdf")
    assert evidence["1"]["raw_sha256"] == source_raw
    assert not [p for p in env.state.cache.rglob("*") if p.is_file()]


def test_r02_same_name_and_page_count_with_different_content_gets_unique_name(env) -> None:
    existing = write_image_pdf(env.archive / "Household/PNC/Statement.pdf", [11, 12])
    existing_raw = sha256_file(existing)
    job_id = classified_job(env, [1, 2])

    result = env.filer().file_job(env.scope_id, job_id, [
        A((1, 2), "filed", "Household/PNC", "Statement.pdf")],
        original_relative_directory=ORIGINALS)

    assert result.job_status == "completed"
    assert sha256_file(existing) == existing_raw
    second = env.archive / "Household/PNC/Statement_2.pdf"
    assert fingerprint_pdf(second).document_sha256 != fingerprint_pdf(existing).document_sha256
    assert len(PdfReader(str(second)).pages) == len(PdfReader(str(existing)).pages) == 2
    assert env.rows("SELECT relative_path FROM file_records WHERE role = 'filed'") == [
        ("Household/PNC/Statement_2.pdf",)]


def test_r03_exact_duplicate_input_creates_no_second_filed_pdf(env) -> None:
    first = classified_job(env, [1, 2], name="scan.pdf")
    env.filer().file_job(env.scope_id, first, [A((1, 2), "filed", "Household/PNC",
                                                 "Statement.pdf")],
                         original_relative_directory=ORIGINALS)
    filed_before = sha256_file(env.archive / "Household/PNC/Statement.pdf")

    # Same pixels, different file bytes and name: a re-scan export of the same letter.
    write_image_pdf(env.watch / "rescan.pdf", [1, 2], title="Synthetic re-export")
    admission = env.filer().admit(env.scope_id, "watch:rescan.pdf")
    env.store.jobs.transition(env.scope_id, admission.job_id, expected="ready", new="ocr")
    env.store.jobs.transition(env.scope_id, admission.job_id, expected="ocr", new="classified")
    second = admission.job_id

    result = env.filer().file_job(env.scope_id, second, [A((1, 2), "filed", "Other",
                                                          "Copy.pdf")],
                                  original_relative_directory=ORIGINALS)

    env.reopen()
    assert result.job_status == "completed"
    filed = [p for p in tree_digest(env.archive) if not p.startswith(ORIGINALS)]
    assert filed == ["Household/PNC/Statement.pdf"]
    assert sha256_file(env.archive / "Household/PNC/Statement.pdf") == filed_before
    assert env.rows("SELECT status, destination_relative_path FROM operation_steps "
                    "WHERE kind = 'write_filed' AND operation_id = ?", result.operation_id) == [
        ("skipped", "Household/PNC/Statement.pdf")]
    assert env.rows("SELECT status FROM job_pages WHERE job_id = ?", second) == [("filed",)] * 2
    assert env.rows("SELECT COUNT(*) FROM file_records WHERE role = 'filed'") == [(1,)]


def test_r03_identical_unindexed_file_at_destination_is_not_duplicated(env) -> None:
    job_id = classified_job(env, [3])
    write_image_pdf(env.archive / "Tax/Invoice.pdf", [3], title="Already here")
    before = tree_digest(env.archive)

    result = env.filer().file_job(env.scope_id, job_id, [A((1,), "filed", "Tax", "Invoice.pdf")],
                                  original_relative_directory=ORIGINALS)

    assert result.job_status == "completed"
    after = tree_digest(env.archive)
    assert {k: v for k, v in after.items() if not k.startswith(ORIGINALS)} == before
    assert env.rows("SELECT status, destination_relative_path FROM operation_steps "
                    "WHERE kind = 'write_filed'") == [("skipped", "Tax/Invoice.pdf")]
    assert env.rows("SELECT COUNT(*) FROM file_records WHERE role = 'filed'") == [(0,)]


def test_r04_existing_original_destination_is_preserved_and_both_addressable(env) -> None:
    earlier = write_image_pdf(env.archive / ORIGINALS / "scan.pdf", [21, 22, 23])
    earlier_raw = sha256_file(earlier)
    job_id = classified_job(env, [1, 2])
    source_raw = sha256_file(env.watch / "scan.pdf")

    result = env.filer().file_job(env.scope_id, job_id, [A((1, 2), "skipped", reason="test")],
                                  original_relative_directory=ORIGINALS)

    env.reopen()
    assert result.job_status == "completed"
    assert sha256_file(env.archive / ORIGINALS / "scan.pdf") == earlier_raw
    assert sha256_file(env.archive / ORIGINALS / "scan_2.pdf") == source_raw
    assert env.rows("SELECT relative_path, role FROM file_records") == [
        (f"{ORIGINALS}/scan_2.pdf", "original")]
    assert not (env.watch / "scan.pdf").exists()


def test_near_duplicate_is_routed_to_review_not_deduplicated(env) -> None:
    first = classified_job(env, [6], name="scan.pdf")
    env.filer().file_job(env.scope_id, first, [A((1,), "filed", "Tax", "Letter.pdf")],
                         original_relative_directory=ORIGINALS)
    write_image_pdf(env.watch / "rescan.pdf", [6], nudge=1)
    admission = env.filer().admit(env.scope_id, "watch:rescan.pdf")
    env.store.jobs.transition(env.scope_id, admission.job_id, expected="ready", new="ocr")
    env.store.jobs.transition(env.scope_id, admission.job_id, expected="ocr", new="classified")

    result = env.filer().file_job(env.scope_id, admission.job_id,
                                  [A((1,), "filed", "Tax", "Letter-rescan.pdf", confidence=0.9)],
                                  original_relative_directory=ORIGINALS)

    env.reopen()
    assert result.job_status == "review"
    assert not (env.archive / "Tax/Letter-rescan.pdf").exists()
    assert not (env.archive / "Tax/Letter_2.pdf").exists()
    assert env.rows("SELECT status FROM job_pages WHERE job_id = ?", admission.job_id) == [
        ("review",)]
    [item] = [i for i in env.store.reviews.list(env.scope_id) if i.job_id == admission.job_id]
    assert item.candidate["reason"] == "near_duplicate_candidate"
    assert item.candidate["near_duplicate_of"] == "Tax/Letter.pdf"
    assert (item.suggested_relative_directory, item.suggested_filename) == (
        "Tax", "Letter-rescan.pdf")


def test_archive_source_is_retained_in_place_and_never_removed(env) -> None:
    original = write_image_pdf(env.archive / ORIGINALS / "scan.pdf", [1, 2])
    original_raw = sha256_file(original)
    admission = env.filer().admit(env.scope_id, f"archive:{ORIGINALS}/scan.pdf")
    env.store.jobs.transition(env.scope_id, admission.job_id, expected="ready", new="ocr")
    env.store.jobs.transition(env.scope_id, admission.job_id, expected="ocr", new="classified")

    result = env.filer().file_job(env.scope_id, admission.job_id,
                                  [A((1, 2), "filed", "Household/PNC", "Statement.pdf")],
                                  original_relative_directory="BeenOrganized091326")

    env.reopen()
    assert result.job_status == "completed"
    assert sorted(tree_digest(env.archive)) == [f"{ORIGINALS}/scan.pdf",
                                                "Household/PNC/Statement.pdf"]
    assert sha256_file(original) == original_raw
    assert env.rows("SELECT relative_path, role FROM file_records ORDER BY relative_path") == [
        (f"{ORIGINALS}/scan.pdf", "original"), ("Household/PNC/Statement.pdf", "filed")]
    assert env.rows("SELECT kind, status, destination_relative_path FROM operation_steps "
                    "WHERE kind != 'write_filed' ORDER BY ordinal") == [
        ("retain_original", "done", f"{ORIGINALS}/scan.pdf"), ("cleanup_source", "skipped", None)]
