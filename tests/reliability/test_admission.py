"""Durable admission of stabilized inputs into archive-scoped jobs."""
from __future__ import annotations

from pathlib import Path

import pytest

from docflow.ingestion.loader import fingerprint_pdf
from tests.reliability.harness import make_env
from tests.reliability.synthetic import sha256_file, tree_digest, write_image_pdf


@pytest.fixture()
def env(tmp_path: Path, isolated_home: Path):
    environment = make_env(tmp_path, isolated_home)
    yield environment
    environment.close()


def test_stable_source_is_admitted_with_durable_page_fingerprints(env) -> None:
    source = write_image_pdf(env.watch / "scan.pdf", [1, 2, 3])
    expected = fingerprint_pdf(source)

    admission = env.filer().admit(env.scope_id, "watch:scan.pdf")

    assert admission.state == "ready" and not admission.retryable
    env.reopen()
    job = env.store.jobs.get(env.scope_id, admission.job_id)
    assert job.status == "ready"
    assert job.source_fingerprint == f"sha256:{expected.raw_sha256}"
    assert job.source_locator == "watch:scan.pdf"
    assert env.rows("SELECT page_number, content_sha256, perceptual_fingerprint, status "
                    "FROM job_pages WHERE job_id = ? ORDER BY page_number", job.id) == [
        (p.page_number, p.content_sha256, p.dhash64, "pending") for p in expected.pages
    ]
    assert sha256_file(source) == expected.raw_sha256


def test_r05_unstable_inputs_create_no_job_and_stay_unmodified(env) -> None:
    write_image_pdf(env.watch / "good.pdf", [1])
    (env.watch / "empty.pdf").write_bytes(b"")
    (env.watch / "broken.pdf").write_bytes(b"%PDF-1.4 broken")
    write_image_pdf(env.watch / "dl.pdf.crdownload", [1])
    (env.watch / ".cloud.pdf.icloud").write_bytes(b"placeholder")
    before = tree_digest(env.watch)
    filer = env.filer()

    states = {name: filer.admit(env.scope_id, f"watch:{name}") for name in
              ("missing.pdf", "empty.pdf", "broken.pdf", "dl.pdf.crdownload", "cloud.pdf")}

    assert {n: (a.state, a.retryable, a.job_id) for n, a in states.items()} == {
        "missing.pdf": ("missing", True, None),
        "empty.pdf": ("zero_byte", True, None),
        "broken.pdf": ("malformed", True, None),
        "dl.pdf.crdownload": ("download_not_ready", True, None),
        "cloud.pdf": ("unavailable", True, None),
    }
    assert env.rows("SELECT COUNT(*) FROM jobs") == [(0,)]
    assert tree_digest(env.watch) == before


def test_source_changed_after_stabilization_is_not_admitted(env, monkeypatch) -> None:
    from docflow.filing import operations

    source = write_image_pdf(env.watch / "scan.pdf", [1, 2])
    real = operations.fingerprint_pdf

    def copier_rewrites_then_fingerprint(path):
        write_image_pdf(source, [7, 8, 9])
        return real(path)

    monkeypatch.setattr(operations, "fingerprint_pdf", copier_rewrites_then_fingerprint)
    admission = env.filer().admit(env.scope_id, "watch:scan.pdf")

    assert (admission.state, admission.retryable, admission.job_id) == ("changing", True, None)
    assert env.rows("SELECT COUNT(*) FROM jobs") == [(0,)]


def test_readmitting_the_same_source_is_idempotent(env) -> None:
    write_image_pdf(env.watch / "scan.pdf", [1, 2])
    first = env.filer().admit(env.scope_id, "watch:scan.pdf")
    env.reopen()
    second = env.filer().admit(env.scope_id, "watch:scan.pdf")
    assert second == first
    assert env.rows("SELECT COUNT(*) FROM jobs") == [(1,)]
