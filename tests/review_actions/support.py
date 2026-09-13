"""Synthetic review jobs: a scan filed by ``DurableFiler`` with pages left for review."""
from __future__ import annotations

from docflow.filing.operations import DocumentAssignment as A
from docflow.state.repositories import ReviewItem
from tests.reliability.harness import Env, classified_job

ORIGINALS = "BeenOrganized091326"


def review_job(env: Env, *, name: str = "scan.pdf", marks: tuple[int, ...] = (1, 2, 3, 4)
               ) -> tuple[str, dict[tuple[int, ...], ReviewItem]]:
    """Page 1 filed; page 2 in review with a suggestion; pages 3-4 in review without one."""
    job_id = classified_job(env, list(marks), name)
    result = env.filer().file_job(env.scope_id, job_id, [
        A((1,), "filed", "Docs", "Filed.pdf"),
        A((2,), "review", "Review", "Suggested.pdf", reason="low_confidence", confidence=0.4),
        A((3, 4), "review", None, None, reason="unmatched", confidence=0.1),
    ], original_relative_directory=ORIGINALS)
    assert (result.job_status, result.operation_status) == ("review", "completed")
    items = [i for i in env.store.reviews.list(env.scope_id) if i.job_id == job_id]
    return job_id, {tuple(item.candidate["pages"]): item for item in items}


def page_statuses(env: Env, job_id: str) -> dict[int, str]:
    return {row["page_number"]: row["status"] for row in env.store.jobs.pages(env.scope_id, job_id)}


def body(env: Env, key: str, **extra) -> dict:
    return {"archive_scope_id": env.scope_id, "idempotency_key": key, **extra}
