"""R07: crash or sleep at every durable journal boundary, then restart and reconcile.

One scenario crosses every boundary: a filename collision (R02), a recorded exact
duplicate (R03), a pre-existing original at the retention destination (R04) and a
review page. For each boundary and mode the test reopens SQLite (new writer, new
filer) and asserts filesystem plus database state, then proves reconciliation is
idempotent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from docflow.filing.operations import JOURNAL_BOUNDARIES
from docflow.filing.operations import DocumentAssignment as A
from docflow.ingestion.loader import fingerprint_pdf
from tests.reliability.harness import classified_job, make_env
from tests.reliability.synthetic import sha256_file, tree_digest, write_image_pdf

ORIGINALS = "BeenOrganized033026"
PLAN = [
    A((1, 2), "filed", "Household/PNC", "Statement.pdf"),
    A((3,), "filed", "Other", "Invoice.pdf"),
    A((4,), "review", "Medical", "Bill.pdf", reason="low_confidence", confidence=0.3),
]
MODES = ("crash", "crash_during_recovery", "sleep")


class SimulatedCrash(BaseException):
    """Process death: not an Exception, so no application handler can swallow it."""


@dataclass
class Scenario:
    job_id: str
    source_raw: str
    preexisting: dict[str, str]
    statement_sha: str


def build(env) -> Scenario:
    write_image_pdf(env.archive / "Household/PNC/Statement.pdf", [31, 32])
    duplicate = write_image_pdf(env.archive / "Tax/Invoice.pdf", [3], title="Filed earlier")
    write_image_pdf(env.archive / ORIGINALS / "scan.pdf", [33, 34, 35, 36])
    env.store.files.add(env.scope_id, job_id=None, relative_path="Tax/Invoice.pdf",
                        content_sha256=fingerprint_pdf(duplicate).document_sha256,
                        page_numbers=[1], role="filed")
    preexisting = tree_digest(env.archive)
    job_id = classified_job(env, [1, 2, 3, 4])
    hashes = dict(env.rows("SELECT page_number, content_sha256 FROM job_pages WHERE job_id = ?",
                           job_id))
    from docflow.ingestion.loader import document_sha256
    return Scenario(job_id, sha256_file(env.watch / "scan.pdf"), preexisting,
                    document_sha256([hashes[1], hashes[2]]))


class Hook:
    def __init__(self, crash_at: str | None = None, *, crash_first: bool = False) -> None:
        self.crash_at, self.crash_first = crash_at, crash_first
        self.seen: list[str] = []

    def __call__(self, boundary: str) -> None:
        self.seen.append(boundary)
        if self.crash_first or boundary == self.crash_at:
            self.crash_at, self.crash_first = None, False
            raise SimulatedCrash(boundary)


def assert_safe_at_crash(env, s: Scenario) -> None:
    """Nothing overwritten, source bytes never lost, no unexpected archive files."""
    tree = tree_digest(env.archive)
    for path, digest in s.preexisting.items():
        assert tree[path] == digest, path
    copies = [p for p in (env.watch / "scan.pdf", env.archive / ORIGINALS / "scan_2.pdf")
              if p.exists() and sha256_file(p) == s.source_raw]
    assert copies, "source bytes lost"
    assert set(tree) - set(s.preexisting) <= {"Household/PNC/Statement_2.pdf",
                                              f"{ORIGINALS}/scan_2.pdf"}
    statement = env.archive / "Household/PNC/Statement_2.pdf"
    if statement.exists():
        assert fingerprint_pdf(statement).document_sha256 == s.statement_sha
    for (page,) in env.rows("SELECT page_number FROM job_pages WHERE job_id = ? AND "
                            "status = 'filed' AND page_number IN (1, 2)", s.job_id):
        assert statement.exists(), page


def snapshot(env) -> tuple:
    tables = ("jobs", "job_pages", "file_records", "operations", "operation_steps",
              "review_items")
    return (tree_digest(env.archive), tree_digest(env.watch),
            {t: sorted(env.rows(f"SELECT * FROM {t}")) for t in tables})


def assert_final(env, s: Scenario) -> None:
    tree = tree_digest(env.archive)
    assert set(tree) == set(s.preexisting) | {"Household/PNC/Statement_2.pdf",
                                              f"{ORIGINALS}/scan_2.pdf"}
    for path, digest in s.preexisting.items():
        assert tree[path] == digest, path
    assert tree[f"{ORIGINALS}/scan_2.pdf"] == s.source_raw
    assert fingerprint_pdf(env.archive / "Household/PNC/Statement_2.pdf").document_sha256 == \
        s.statement_sha
    assert tree_digest(env.watch) == {}
    assert not [p for p in env.state.cache.rglob("*") if p.is_file()]
    assert env.store.jobs.get(env.scope_id, s.job_id).status == "review"
    assert env.rows("SELECT page_number, status FROM job_pages WHERE job_id = ? "
                    "ORDER BY page_number", s.job_id) == [
        (1, "filed"), (2, "filed"), (3, "filed"), (4, "review")]
    assert env.rows("SELECT kind, status FROM operations") == [("file_job", "completed")]
    assert env.rows("SELECT ordinal, kind, status, destination_relative_path "
                    "FROM operation_steps ORDER BY ordinal") == [
        (0, "write_filed", "done", "Household/PNC/Statement_2.pdf"),
        (1, "write_filed", "skipped", "Tax/Invoice.pdf"),
        (2, "retain_original", "done", f"{ORIGINALS}/scan_2.pdf"),
        (3, "cleanup_source", "done", None),
    ]
    assert sorted(env.rows("SELECT relative_path, role, job_id IS NOT NULL FROM file_records")) \
        == [(f"{ORIGINALS}/scan_2.pdf", "original", 1),
            ("Household/PNC/Statement_2.pdf", "filed", 1),
            ("Tax/Invoice.pdf", "filed", 0)]
    assert env.rows("SELECT COUNT(*) FROM review_items") == [(1,)]


def restart_and_reconcile(env, hook=None):
    env.reopen()
    env.store.recover_after_restart(env.scope_id)
    kwargs = {"fault": hook} if hook is not None else {}
    return env.filer(**kwargs).reconcile(env.scope_id)


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


def test_scenario_crosses_every_journal_boundary_in_order(env) -> None:
    s = build(env)
    hook = Hook()
    env.filer(fault=hook).file_job(env.scope_id, s.job_id, PLAN,
                                   original_relative_directory=ORIGINALS)
    assert tuple(hook.seen) == JOURNAL_BOUNDARIES
    env.reopen()
    assert_final(env, s)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("boundary", JOURNAL_BOUNDARIES)
def test_r07_boundary_matrix(env, boundary: str, mode: str) -> None:
    s = build(env)
    if mode == "sleep":
        hook = Hook()
        env.filer(fault=hook).file_job(env.scope_id, s.job_id, PLAN,
                                       original_relative_directory=ORIGINALS)
        assert boundary in hook.seen  # the process paused here and carried on
    else:
        with pytest.raises(SimulatedCrash):
            env.filer(fault=Hook(boundary)).file_job(env.scope_id, s.job_id, PLAN,
                                                     original_relative_directory=ORIGINALS)
        env.reopen()
        assert_safe_at_crash(env, s)
        if mode == "crash_during_recovery":
            try:
                restart_and_reconcile(env, Hook(crash_first=True))
            except SimulatedCrash:
                env.reopen()
                assert_safe_at_crash(env, s)
        restart_and_reconcile(env)

    env.reopen()
    assert_final(env, s)
    before = snapshot(env)
    assert restart_and_reconcile(env) == []
    env.filer().resume(env.scope_id, s.job_id)
    env.reopen()
    assert snapshot(env) == before
