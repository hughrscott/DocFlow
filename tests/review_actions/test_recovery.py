"""A crash after any review/undo journal boundary resumes on replay without duplicates."""
from __future__ import annotations

import pytest

from docflow.filing.review_actions import ReviewActions
from tests.review_actions.support import page_statuses, review_job


class Crash(BaseException):
    """Simulated process death: not caught by application error handling."""


def _crash_at(name: str):
    def fault(boundary: str) -> None:
        if boundary == name:
            raise Crash(boundary)
    return fault


def _pdfs(env) -> list[str]:
    return sorted(p.relative_to(env.archive).as_posix() for p in env.archive.rglob("*")
                  if p.is_file() or p.is_symlink())


@pytest.mark.parametrize("boundary", [
    "staged", "destination_recorded", "placed", "step_committed"])
def test_approve_crash_resumes_on_replay_without_duplicate_files(env, boundary) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    with pytest.raises(Crash):
        ReviewActions(env.store, fault=_crash_at(boundary)).approve(
            env.scope_id, item.id, idempotency_key="ui-a")

    env.reopen()
    result = ReviewActions(env.store).approve(env.scope_id, item.id, idempotency_key="ui-a")

    assert (result["outcome"], result["items"][0]["relative_path"]) == (
        "completed", "Review/Suggested.pdf")
    assert [p for p in _pdfs(env) if p.startswith("Review/")] == ["Review/Suggested.pdf"]
    assert env.store.reviews.get(env.scope_id, item.id).status == "approved"
    assert page_statuses(env, job_id)[2] == "filed"
    assert ReviewActions(env.store).approve(env.scope_id, item.id,
                                            idempotency_key="ui-a") == result


@pytest.mark.parametrize("boundary", [
    "undo_detached", "undo_detach_recorded", "undo_removed", "undo_step_committed"])
def test_undo_crash_resumes_on_replay_and_leaves_no_detached_files(env, boundary) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    approved = ReviewActions(env.store).approve(env.scope_id, item.id, idempotency_key="ui-a")
    operation_id = approved["operation"]["id"]
    before = [p for p in _pdfs(env) if not p.startswith("Review/")]
    with pytest.raises(Crash):
        ReviewActions(env.store, fault=_crash_at(boundary)).undo(
            env.scope_id, operation_id, idempotency_key="ui-u")

    env.reopen()
    result = ReviewActions(env.store).undo(env.scope_id, operation_id, idempotency_key="ui-u")

    assert (result["outcome"], result["operation"]["status"]) == ("undone", "undone")
    assert _pdfs(env) == before
    assert env.store.reviews.get(env.scope_id, item.id).status == "pending"
    assert page_statuses(env, job_id)[2] == "review"
    assert env.store.files.get_by_path(env.scope_id, "Review/Suggested.pdf") is None


def test_interrupted_operations_are_reconciled_before_new_actions(env) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    with pytest.raises(Crash):
        ReviewActions(env.store, fault=_crash_at("placed")).approve(
            env.scope_id, item.id, idempotency_key="ui-a-1")

    env.reopen()
    from docflow.filing.review_actions import ReviewActionRejected
    with pytest.raises(ReviewActionRejected) as rejected:
        ReviewActions(env.store).approve(env.scope_id, item.id, idempotency_key="ui-a-2")

    assert rejected.value.code == "review_item_not_pending"
    assert [p for p in _pdfs(env) if p.startswith("Review/")] == ["Review/Suggested.pdf"]
    assert env.rows("SELECT COUNT(*) FROM operations WHERE kind = 'review_approve' "
                    "AND status = 'completed'") == [(1,)]
    assert page_statuses(env, job_id)[2] == "filed"
