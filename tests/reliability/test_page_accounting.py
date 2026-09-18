"""R06: every source page ends exactly once as filed, review, skipped or blocked."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docflow.filing.operations import DocumentAssignment as A
from docflow.filing.operations import PageAccountingError
from tests.reliability.harness import classified_job, make_env
from tests.reliability.synthetic import tree_digest


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


INVALID = {
    "duplicate": [A((1, 2), "filed", "Docs", "One.pdf"), A((2, 3), "review")],
    "missing": [A((1, 2), "filed", "Docs", "One.pdf")],
    "out_of_range": [A((1, 2, 3), "filed", "Docs", "One.pdf"), A((4,), "skipped")],
    "zero": [A((0, 1, 2, 3), "filed", "Docs", "One.pdf")],
    "not_integer": [A((1, 2), "filed", "Docs", "One.pdf"), A((True, 3), "review")],
    "empty_document": [A((1, 2, 3), "filed", "Docs", "One.pdf"), A((), "skipped")],
    "unknown_outcome": [A((1, 2, 3), "archived")],
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_r06_invalid_assignments_fail_closed_before_any_mutation(env, case: str) -> None:
    job_id = classified_job(env, [1, 2, 3])
    archive_before, watch_before = tree_digest(env.archive), tree_digest(env.watch)

    with pytest.raises(PageAccountingError):
        env.filer().file_job(env.scope_id, job_id, INVALID[case],
                             original_relative_directory="BeenOrganized033026")

    env.reopen()
    assert env.rows("SELECT COUNT(*) FROM operations") == [(0,)]
    assert env.rows("SELECT COUNT(*) FROM operation_steps") == [(0,)]
    assert env.rows("SELECT COUNT(*) FROM file_records") == [(0,)]
    assert env.rows("SELECT status, COUNT(*) FROM job_pages GROUP BY status") == [("pending", 3)]
    job = env.store.jobs.get(env.scope_id, job_id)
    assert (job.status, job.last_error_code) == ("failed", "page_accounting_invalid")
    assert tree_digest(env.archive) == archive_before
    assert tree_digest(env.watch) == watch_before
    assert not any(env.state.cache.rglob("*.pdf"))


def test_file_job_is_idempotent_for_a_job_already_filing(env) -> None:
    import os

    job_id = classified_job(env, [1, 2, 3])
    plan = [A((1, 2, 3), "filed", "Docs", "One.pdf")]

    def archive_down(src, dst):
        if Path(dst).parent.name == "Originals":
            raise OSError(5, "Input/output error")
        return os.link(src, dst)

    first = env.filer(link=archive_down).file_job(env.scope_id, job_id, plan,
                                                  original_relative_directory="Originals")
    assert first.job_status == "filing"
    second = env.filer().file_job(env.scope_id, job_id, [A((1, 2, 3), "skipped")],
                                  original_relative_directory="Elsewhere")

    assert second.operation_id == first.operation_id
    assert second.job_status == "completed"
    assert env.rows("SELECT COUNT(*) FROM operations") == [(1,)]
    assert sorted(tree_digest(env.archive)) == ["Docs/One.pdf", "Originals/scan.pdf"]


def test_file_job_rejects_jobs_that_are_not_classified(env) -> None:
    from docflow.state.repositories import InvalidTransitionError

    job_id = classified_job(env, [1, 2])
    env.store.jobs.transition(env.scope_id, job_id, expected="classified", new="ocr")
    watch_before = tree_digest(env.watch)

    with pytest.raises(InvalidTransitionError):
        env.filer().file_job(env.scope_id, job_id, [A((1, 2), "skipped")],
                             original_relative_directory="Originals")
    with pytest.raises(InvalidTransitionError):
        env.filer().file_job(env.scope_id, job_id, [A((1,), "skipped")],
                             original_relative_directory="Originals")

    assert env.store.jobs.get(env.scope_id, job_id).status == "ocr"
    assert env.rows("SELECT COUNT(*) FROM operations") == [(0,)]
    assert tree_digest(env.archive) == {}
    assert tree_digest(env.watch) == watch_before


PARTITIONS = {
    "one_filed_document": [A((1, 2, 3, 4), "filed", "Docs", "All.pdf")],
    "every_outcome": [A((1,), "filed", "Docs", "One.pdf"), A((2,), "review", reason="r"),
                      A((3,), "skipped", reason="blank"), A((4,), "blocked", reason="privacy")],
    "non_contiguous": [A((1, 3), "filed", "Docs", "Odd.pdf"), A((4, 2), "filed", "Docs",
                                                                  "Even.pdf")],
    "all_review": [A((1, 2), "review", reason="r"), A((3, 4), "review", reason="r")],
    "skipped_and_blocked": [A((2, 3, 4), "skipped", reason="s"), A((1,), "blocked", reason="b")],
}


@pytest.mark.parametrize("case", sorted(PARTITIONS))
def test_r06_every_page_ends_exactly_once_in_a_terminal_outcome(env, case: str) -> None:
    from pypdf import PdfReader

    job_id = classified_job(env, [1, 2, 3, 4])
    plan = PARTITIONS[case]
    env.filer().file_job(env.scope_id, job_id, plan, original_relative_directory="Originals")
    env.reopen()

    expected = {page: a.outcome for a in plan for page in a.pages}
    rows = env.rows("SELECT page_number, status FROM job_pages WHERE job_id = ? "
                    "ORDER BY page_number", job_id)
    assert rows == sorted(expected.items())
    assert len({page for page, _ in rows}) == 4
    totals = env.store.jobs.page_totals(env.scope_id, job_id)
    assert sum(totals.values()) == 4 and "pending" not in totals
    filed_records = env.rows("SELECT relative_path, page_numbers_json FROM file_records "
                             "WHERE job_id = ? AND role = 'filed'", job_id)
    filed_pages = [p for _, pages in filed_records for p in json.loads(pages)]
    assert sorted(filed_pages) == sorted(p for p, o in expected.items() if o == "filed")
    for relative, pages in filed_records:
        assert len(PdfReader(str(env.archive / relative)).pages) == len(json.loads(pages))
    review_pages = [p for item in env.store.reviews.list(env.scope_id) for p in
                    item.candidate["pages"]]
    assert sorted(review_pages) == sorted(p for p, o in expected.items() if o == "review")
    [(original,)] = env.rows("SELECT page_numbers_json FROM file_records WHERE role = "
                             "'original' AND job_id = ?", job_id)
    assert json.loads(original) == [1, 2, 3, 4]
