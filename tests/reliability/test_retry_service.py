"""Idempotent job retry and the durable job/page-accounting payload."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from docflow.filing.operations import DocumentAssignment as A
from docflow.filing.operations import PageAccountingError, RetryRejected, job_payload
from tests.reliability.harness import classified_job, make_env
from tests.reliability.synthetic import sha256_file, tree_digest, write_image_pdf

ORIGINALS = "BeenOrganized033026"
PLAN = [A((1, 2), "filed", "Household/PNC", "Statement.pdf"), A((3,), "review", reason="low")]


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


def _interrupted_job(env) -> str:
    job_id = classified_job(env, [1, 2, 3])

    def archive_down(src, dst):
        if Path(dst).parent.name == ORIGINALS:
            raise OSError(5, "Input/output error")
        return os.link(src, dst)

    result = env.filer(link=archive_down).file_job(env.scope_id, job_id, PLAN,
                                                   original_relative_directory=ORIGINALS)
    assert result.job_status == "filing"
    return job_id


def test_retry_resumes_interrupted_filing_once_per_idempotency_key(env) -> None:
    job_id = _interrupted_job(env)
    env.reopen()

    first = env.filer().retry(env.scope_id, job_id, idempotency_key="retry-0001")
    tree = tree_digest(env.archive)
    env.reopen()
    replay = env.filer().retry(env.scope_id, job_id, idempotency_key="retry-0001")

    assert first["action"] == "resumed"
    assert first["job"]["status"] == "review"
    assert replay == first
    assert tree_digest(env.archive) == tree
    assert env.rows("SELECT COUNT(*) FROM operations WHERE kind = 'job_retry'") == [(1,)]
    with pytest.raises(RetryRejected) as rejected:
        env.filer().retry(env.scope_id, job_id, idempotency_key="retry-0002")
    assert rejected.value.code == "job_not_retryable"


def test_retry_of_failed_job_requeues_only_when_source_is_unchanged(env) -> None:
    job_id = classified_job(env, [1, 2, 3])
    with pytest.raises(PageAccountingError):
        env.filer().file_job(env.scope_id, job_id, [A((1, 2), "filed", "D", "x.pdf")],
                             original_relative_directory=ORIGINALS)  # page 3 missing
    requeued = env.filer().retry(env.scope_id, job_id, idempotency_key="k-requeue")
    assert requeued["action"] == "requeued"
    assert (requeued["job"]["status"], requeued["job"]["last_error_code"]) == ("ready", None)

    other = classified_job(env, [4, 5], name="other.pdf")
    with pytest.raises(PageAccountingError):
        env.filer().file_job(env.scope_id, other, [A((1,), "filed", "D", "y.pdf")],
                             original_relative_directory=ORIGINALS)
    write_image_pdf(env.watch / "other.pdf", [9, 9], title="changed")
    changed = sha256_file(env.watch / "other.pdf")
    with pytest.raises(RetryRejected) as rejected:
        env.filer().retry(env.scope_id, other, idempotency_key="k-changed")
    assert rejected.value.code == "source_changed"
    assert env.store.jobs.get(env.scope_id, other).status == "failed"
    assert sha256_file(env.watch / "other.pdf") == changed
    with pytest.raises(RetryRejected) as replayed:
        env.filer().retry(env.scope_id, other, idempotency_key="k-changed")
    assert replayed.value.code == "source_changed"


def test_job_payload_reports_durable_page_accounting_and_journal(env) -> None:
    job_id = _interrupted_job(env)
    env.reopen()
    payload = job_payload(env.store, env.scope_id, job_id)

    assert payload["job"]["status"] == "filing"
    assert payload["job"]["last_error_code"] == "io_error"
    assert payload["page_accounting"] == {
        "page_count": 3, "pending": 1, "filed": 2, "review": 0, "skipped": 0, "blocked": 0,
        "complete": False,
        "pages": [{"page_number": 1, "status": "filed"}, {"page_number": 2, "status": "filed"},
                  {"page_number": 3, "status": "pending"}],
    }
    assert payload["recovery"] == {"state": "resumable", "retryable": True}
    assert [s["status"] for s in payload["operation"]["steps"]] == ["done", "planned", "planned"]
    assert payload["files"] == [{"relative_path": "Household/PNC/Statement.pdf",
                                 "role": "filed", "page_numbers": [1, 2]}]
    serialized = repr(payload)
    assert str(env.archive) not in serialized and str(env.watch) not in serialized

    env.filer().resume(env.scope_id, job_id)
    done = job_payload(env.store, env.scope_id, job_id)
    assert done["page_accounting"]["complete"] is True
    assert (done["page_accounting"]["filed"], done["page_accounting"]["review"]) == (2, 1)
    assert done["recovery"] == {"state": "none", "retryable": False}


@pytest.mark.parametrize(("reclassified", "final"), [
    ([A((1, 2), "filed", "Household/PNC", "Regrouped.pdf"), A((3,), "review", reason="low")],
     "review"),
    ([A((1,), "review", reason="low"), A((2, 3), "filed", "Household/PNC", "Regrouped.pdf")],
     "completed"),
], ids=["regrouped", "demoted"])
def test_retry_after_partial_failed_filing_reconciles_prior_page_outcomes(
    env, tmp_path: Path, reclassified, final
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    job_id = classified_job(env, [1, 2, 3])
    first = [A((1,), "filed", "Household/PNC", "Page1.pdf"),
             A((2, 3), "filed", "Other", "Rest.pdf")]

    def swap_in_symlink(boundary: str) -> None:
        if boundary == "step_committed" and not (env.archive / "Other").is_symlink():
            (env.archive / "Other").symlink_to(outside, target_is_directory=True)

    failed = env.filer(fault=swap_in_symlink).file_job(env.scope_id, job_id, first,
                                                       original_relative_directory=ORIGINALS)
    assert (failed.job_status, failed.last_error_code) == ("failed", "unsafe_destination")
    (env.archive / "Other").unlink()
    assert env.filer().retry(env.scope_id, job_id, idempotency_key="k-partial")["action"] == \
        "requeued"
    jobs = env.store.jobs
    assert jobs.transition(env.scope_id, job_id, expected="ready", new="ocr")
    assert jobs.transition(env.scope_id, job_id, expected="ocr", new="classified")

    result = env.filer().file_job(env.scope_id, job_id, reclassified,
                                  original_relative_directory=ORIGINALS)

    assert (result.job_status, result.operation_status) == (final, "completed")
    filed = [r for r in env.store.files.list_for_job(env.scope_id, job_id) if r.role == "filed"]
    filed_pages = [n for record in filed for n in record.page_numbers]
    review_pages = [n for item in env.store.reviews.list(env.scope_id)
                    for n in item.candidate["pages"]]
    assert sorted(filed_pages + review_pages) == [1, 2, 3]  # every page exactly once
    statuses = {row["page_number"]: row["status"] for row in jobs.pages(env.scope_id, job_id)}
    assert {n for n, s in statuses.items() if s == "filed"} == set(filed_pages)
    assert {n for n, s in statuses.items() if s == "review"} == set(review_pages)
    placed = sorted(p.name for p in (env.archive / "Household" / "PNC").glob("*.pdf"))
    assert placed == sorted(r.relative_path.rpartition("/")[2] for r in filed)
    payload = job_payload(env.store, env.scope_id, job_id)
    assert {"pages": [1], "outcome": "filed", "reason": "prior_attempt_filed",
            "relative_directory": "Household/PNC", "filename": "Page1.pdf",
            "near_duplicate_of": None} in payload["operation"]["assignments"]
