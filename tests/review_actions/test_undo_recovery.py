"""P5-B04: multi-step batch undo survives crashes and I/O failures between its boundaries."""
from __future__ import annotations

import pytest

from docflow.filing.review_actions import ReviewActions
from docflow.web import bootstrap
from tests.review_actions.support import page_statuses, review_job


def _archive_files(env) -> list[str]:
    return sorted(p.relative_to(env.archive).as_posix() for p in env.archive.rglob("*")
                  if p.is_file() or p.is_symlink())


def _fail_on_call(boundary: str, call: int, error: BaseException):
    seen = {"count": 0}

    def fault(name: str) -> None:
        if name == boundary:
            seen["count"] += 1
            if seen["count"] == call:
                raise error
    return fault


def _batch(env):
    first_job, first = review_job(env)
    second_job, second = review_job(env, name="scan2.pdf", marks=(5, 6, 7, 8))
    ids = [first[(2,)].id, second[(2,)].id]
    batch = ReviewActions(env.store).batch(env.scope_id, action="approve",
                                           review_item_ids=ids, idempotency_key="ui-b")
    assert [i["relative_path"] for i in batch["items"]] == [
        "Review/Suggested.pdf", "Review/Suggested_2.pdf"]
    return batch["operation"]["id"], ids, (first_job, second_job)


def test_io_failure_after_detaching_second_file_puts_it_back_and_retry_converges(env) -> None:
    operation_id, ids, jobs = _batch(env)
    second = env.archive / "Review/Suggested_2.pdf"
    created = second.read_bytes()
    before = [p for p in _archive_files(env) if not p.startswith("Review/")]

    partial = ReviewActions(env.store, fault=_fail_on_call(
        "undo_detach_recorded", 2, PermissionError("synthetic: directory not writable"))).undo(
        env.scope_id, operation_id, idempotency_key="ui-u-1")

    assert (partial["outcome"], partial["operation"]["status"]) == (
        "partial_failure", "partially_undone")
    assert [(s["status"], s["error_code"]) for s in partial["steps"]] == [
        ("compensated", None), ("failed", "io_error")]
    assert second.read_bytes() == created  # the unverified-to-remove file is visible again
    assert [p for p in _archive_files(env) if p.startswith("Review/")] == [
        "Review/Suggested_2.pdf"]
    assert env.store.reviews.get(env.scope_id, ids[1]).status == "approved"
    assert page_statuses(env, jobs[1])[2] == "filed"

    env.reopen()
    retried = ReviewActions(env.store).undo(env.scope_id, operation_id,
                                            idempotency_key="ui-u-2")

    assert (retried["outcome"], retried["operation"]["status"]) == ("undone", "undone")
    assert _archive_files(env) == before
    assert [env.store.reviews.get(env.scope_id, i).status for i in ids] == ["pending"] * 2
    assert [page_statuses(env, j)[2] for j in jobs] == ["review"] * 2


class Crash(BaseException):
    """Simulated process death: not caught by application error handling."""


@pytest.mark.parametrize("boundary", [
    "undo_detached", "undo_detach_recorded", "undo_removed", "undo_step_committed"])
def test_crash_in_second_undo_step_converges_on_restart_without_another_action(
        env, monkeypatch, boundary) -> None:
    operation_id, ids, jobs = _batch(env)
    before = [p for p in _archive_files(env) if not p.startswith("Review/")]
    with pytest.raises(Crash):
        ReviewActions(env.store, fault=_fail_on_call(boundary, 2, Crash(boundary))).undo(
            env.scope_id, operation_id, idempotency_key="ui-u")

    env.close()  # process death; the next process only starts the local UI
    monkeypatch.setattr(bootstrap, "resolve_state_paths", lambda roots, home: env.state)
    state = bootstrap.open_local_state({"archive_root": str(env.archive)}, home=env.home)
    try:
        store, scope_id = state.store, state.scope_id
        assert scope_id == env.scope_id
        assert store.operations.get(scope_id, operation_id).status == "undone"
        assert [store.reviews.get(scope_id, i).status for i in ids] == ["pending"] * 2
        assert [store.jobs.pages(scope_id, j)[1]["status"] for j in jobs] == ["review"] * 2
        assert _archive_files(env) == before  # no created file and no hidden detached copy
    finally:
        state.close()
