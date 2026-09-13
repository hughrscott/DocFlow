"""Partial writes, archive failures and changed sources preserve the source and stay safe."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from docflow.filing.operations import DocumentAssignment as A
from docflow.filing.operations import write_pdf_pages
from tests.reliability.harness import classified_job, make_env
from tests.reliability.synthetic import sha256_file, tree_digest, write_image_pdf

ORIGINALS = "BeenOrganized033026"
PLAN = [A((1, 2), "filed", "Household/PNC", "Statement.pdf"), A((3,), "filed", "Tax", "Bill.pdf")]


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


def test_partial_write_preserves_source_and_resume_completes(env) -> None:
    job_id = classified_job(env, [1, 2, 3])
    source = env.watch / "scan.pdf"
    source_raw = sha256_file(source)
    calls = {"n": 0}

    def truncating_writer(src: Path, pages: list[int], target: Path) -> None:
        calls["n"] += 1
        write_pdf_pages(src, pages, target)
        if calls["n"] == 2:  # second document: disk-full style truncation
            target.write_bytes(target.read_bytes()[: target.stat().st_size // 3])

    result = env.filer(write_pages=truncating_writer).file_job(
        env.scope_id, job_id, PLAN, original_relative_directory=ORIGINALS)

    assert (result.job_status, result.operation_status, result.last_error_code) == (
        "filing", "running", "output_verification_failed")
    env.reopen()
    assert sha256_file(source) == source_raw
    assert sorted(tree_digest(env.archive)) == ["Household/PNC/Statement.pdf"]
    assert env.rows("SELECT ordinal, status FROM operation_steps ORDER BY ordinal") == [
        (0, "done"), (1, "planned"), (2, "planned"), (3, "planned")]
    assert not [p for p in env.state.cache.rglob("*") if p.is_file()]

    resumed = env.filer().resume(env.scope_id, job_id)

    assert (resumed.job_status, resumed.operation_status, resumed.last_error_code) == (
        "completed", "completed", None)
    assert sorted(tree_digest(env.archive)) == [f"{ORIGINALS}/scan.pdf",
                                                "Household/PNC/Statement.pdf", "Tax/Bill.pdf"]
    assert sha256_file(env.archive / ORIGINALS / "scan.pdf") == source_raw
    assert env.rows("SELECT COUNT(*) FROM file_records") == [(3,)]


def test_archive_failure_preserves_source_and_resume_completes(env) -> None:
    job_id = classified_job(env, [1, 2, 3])
    source = env.watch / "scan.pdf"
    source_raw = sha256_file(source)

    def archive_unavailable(src, dst):
        if Path(dst).parent.name == ORIGINALS:
            raise OSError(5, "Input/output error")
        return os.link(src, dst)

    result = env.filer(link=archive_unavailable).file_job(
        env.scope_id, job_id, PLAN, original_relative_directory=ORIGINALS)

    assert (result.job_status, result.last_error_code) == ("filing", "io_error")
    env.reopen()
    assert sha256_file(source) == source_raw
    assert sorted(tree_digest(env.archive)) == ["Household/PNC/Statement.pdf", "Tax/Bill.pdf"]
    assert env.rows("SELECT kind, status FROM operation_steps ORDER BY ordinal")[2:] == [
        ("retain_original", "planned"), ("cleanup_source", "planned")]

    resumed = env.filer().resume(env.scope_id, job_id)

    assert resumed.job_status == "completed"
    assert sha256_file(env.archive / ORIGINALS / "scan.pdf") == source_raw
    assert not source.exists()
    assert sorted(tree_digest(env.archive)) == [f"{ORIGINALS}/scan.pdf",
                                                "Household/PNC/Statement.pdf", "Tax/Bill.pdf"]


def test_source_changed_after_admission_fails_closed_without_mutation(env) -> None:
    job_id = classified_job(env, [1, 2, 3])
    write_image_pdf(env.watch / "scan.pdf", [7, 8, 9], title="Replaced by user")
    replaced = sha256_file(env.watch / "scan.pdf")

    result = env.filer().file_job(env.scope_id, job_id, PLAN,
                                  original_relative_directory=ORIGINALS)

    env.reopen()
    assert (result.job_status, result.operation_status, result.last_error_code) == (
        "failed", "failed", "source_changed")
    assert tree_digest(env.archive) == {}
    assert sha256_file(env.watch / "scan.pdf") == replaced
    assert env.rows("SELECT DISTINCT status, error_code FROM operation_steps") == [
        ("failed", "source_changed")]
    assert env.rows("SELECT DISTINCT status FROM job_pages") == [("pending",)]
    assert env.rows("SELECT COUNT(*) FROM file_records") == [(0,)]


def test_source_unavailable_during_resume_is_retryable(env) -> None:
    job_id = classified_job(env, [1, 2, 3])
    source = env.watch / "scan.pdf"
    evicted = source.read_bytes()
    source.unlink()
    (env.watch / ".scan.pdf.icloud").write_bytes(b"placeholder")

    result = env.filer().file_job(env.scope_id, job_id, PLAN,
                                  original_relative_directory=ORIGINALS)

    assert (result.job_status, result.operation_status, result.last_error_code) == (
        "filing", "running", "source_unavailable")
    assert tree_digest(env.archive) == {}
    (env.watch / ".scan.pdf.icloud").unlink()
    source.write_bytes(evicted)
    env.reopen()
    assert env.filer().resume(env.scope_id, job_id).job_status == "completed"
    assert sorted(tree_digest(env.archive)) == [f"{ORIGINALS}/scan.pdf",
                                                "Household/PNC/Statement.pdf", "Tax/Bill.pdf"]


UNSAFE = {
    "traversal": ([A((1, 2, 3), "filed", "../outside", "Doc.pdf")], ORIGINALS),
    "absolute": ([A((1, 2, 3), "filed", "/tmp/outside", "Doc.pdf")], ORIGINALS),
    "filename_path": ([A((1, 2, 3), "filed", "Docs", "../Doc.pdf")], ORIGINALS),
    "not_pdf": ([A((1, 2, 3), "filed", "Docs", "Doc.exe")], ORIGINALS),
    "original_traversal": ([A((1, 2, 3), "skipped", reason="x")], "../originals"),
    "symlinked_directory": ([A((1, 2, 3), "filed", "Linked/Sub", "Doc.pdf")], ORIGINALS),
    "review_traversal": ([A((1, 2), "filed", "Docs", "Doc.pdf"),
                          A((3,), "review", "../outside", "Doc.pdf", reason="x")], ORIGINALS),
    "review_bad_confidence": ([A((1, 2), "filed", "Docs", "Doc.pdf"),
                               A((3,), "review", reason="x", confidence=float("nan"))],
                              ORIGINALS),
}


@pytest.mark.parametrize("case", sorted(UNSAFE))
def test_unsafe_destinations_fail_closed_before_mutation(env, tmp_path: Path, case: str) -> None:
    from docflow.state.repositories import UnsafeValueError

    outside = tmp_path / "outside"
    outside.mkdir()
    (env.archive / "Linked").symlink_to(outside, target_is_directory=True)
    job_id = classified_job(env, [1, 2, 3])
    archive_before, watch_before = tree_digest(env.archive), tree_digest(env.watch)
    assignments, originals = UNSAFE[case]

    with pytest.raises(UnsafeValueError):
        env.filer().file_job(env.scope_id, job_id, assignments,
                             original_relative_directory=originals)

    env.reopen()
    job = env.store.jobs.get(env.scope_id, job_id)
    assert (job.status, job.last_error_code) == ("failed", "unsafe_destination")
    assert env.rows("SELECT COUNT(*) FROM operations") == [(0,)]
    assert tree_digest(env.archive) == archive_before
    assert tree_digest(env.watch) == watch_before
    assert list(outside.rglob("*")) == []


def test_symlink_swapped_in_after_planning_fails_closed(env, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    job_id = classified_job(env, [1, 2, 3])

    def attacker(boundary: str) -> None:
        if boundary == "plan_committed":
            (env.archive / "Household").symlink_to(outside, target_is_directory=True)

    result = env.filer(fault=attacker).file_job(env.scope_id, job_id, PLAN,
                                                original_relative_directory=ORIGINALS)

    assert (result.job_status, result.last_error_code) == ("failed", "unsafe_destination")
    assert list(outside.rglob("*")) == []
    assert (env.watch / "scan.pdf").exists()


def test_cross_device_placement_uses_verified_destination_local_copy(env) -> None:
    import errno

    job_id = classified_job(env, [1, 2, 3])
    source_raw = sha256_file(env.watch / "scan.pdf")
    operation_id = {}

    def cross_device(src, dst):
        if Path(src).parent != Path(dst).parent:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return os.link(src, dst)

    def stale_partial(boundary: str) -> None:
        if boundary == "plan_committed":  # a previous attempt died mid-copy
            [(op,)] = env.rows("SELECT id FROM operations")
            operation_id["id"] = op
            stale = env.archive / "Tax" / f".docflow-{op}-1.partial"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"%PDF-1.4 truncated")

    result = env.filer(link=cross_device, fault=stale_partial).file_job(
        env.scope_id, job_id, PLAN, original_relative_directory=ORIGINALS)

    env.reopen()
    assert result.job_status == "completed"
    tree = tree_digest(env.archive)
    assert sorted(tree) == [f"{ORIGINALS}/scan.pdf", "Household/PNC/Statement.pdf",
                            "Tax/Bill.pdf"]
    assert tree[f"{ORIGINALS}/scan.pdf"] == source_raw
    assert not (env.watch / "scan.pdf").exists()
    assert env.rows("SELECT COUNT(*) FROM file_records") == [(3,)]
