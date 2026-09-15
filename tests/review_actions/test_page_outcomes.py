"""P5-B03: review outcomes and terminal page statuses agree; every page exactly once."""
from __future__ import annotations

import pytest

from docflow.filing.review_actions import ReviewActionRejected, ReviewActions
from tests.review_actions.support import page_statuses, review_job


def _archive_files(env) -> list[str]:
    return sorted(p.relative_to(env.archive).as_posix() for p in env.archive.rglob("*")
                  if p.is_file() or p.is_symlink())


def test_batch_naming_one_item_twice_files_its_pages_once(env) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]

    result = ReviewActions(env.store).batch(env.scope_id, action="approve",
                                            review_item_ids=[item.id, item.id],
                                            idempotency_key="ui-b-dup")

    assert [p for p in _archive_files(env) if p.startswith("Review/")] == ["Review/Suggested.pdf"]
    assert [(i["result"], i.get("error_code")) for i in result["items"]] == [
        ("filed", None), ("rejected", "duplicate_page_assignment")]
    assert result["outcome"] == "partial_failure"
    assert page_statuses(env, job_id)[2] == "filed"
    assert env.rows("SELECT COUNT(*) FROM file_records WHERE role = 'filed' "
                    "AND page_numbers_json = '[2]'") == [(1,)]


def test_replaying_an_undone_action_refuses_instead_of_reporting_stale_outcome(env) -> None:
    job_id, items = review_job(env)
    item = items[(2,)]
    actions = ReviewActions(env.store)
    approved = actions.approve(env.scope_id, item.id, idempotency_key="ui-a")
    actions.undo(env.scope_id, approved["operation"]["id"], idempotency_key="ui-u")

    env.reopen()
    with pytest.raises(ReviewActionRejected) as rejected:
        ReviewActions(env.store).approve(env.scope_id, item.id, idempotency_key="ui-a")

    assert rejected.value.code == "operation_undone"
    assert env.store.reviews.get(env.scope_id, item.id).status == "pending"
    assert page_statuses(env, job_id)[2] == "review"
    assert not [p for p in _archive_files(env) if p.startswith("Review/")]
