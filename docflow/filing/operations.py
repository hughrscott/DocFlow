"""Durable, journaled filing: admission, page accounting, staged writes and recovery.

Operational state lives in the application-state SQLite database; the archive
receives only verified filed PDFs and retained original scans.
"""
from __future__ import annotations

import errno
import math
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from docflow.ingestion.loader import (
    NEAR_DUPLICATE_MAX_DISTANCE,
    document_sha256,
    fingerprint_pdf,
    hamming_distance,
    raw_sha256,
)
from docflow.ingestion.stability import InputState, StabilityResult, check_input
from docflow.state.repositories import (
    InvalidTransitionError,
    Job,
    Operation,
    OperationStep,
    StateStore,
    UnsafeValueError,
    archive_relative,
    safe_name,
    stable_id,
    validate_source_locator,
)

FILE_JOB = "file_job"
JOB_RETRY = "job_retry"
JOB_CREATE = "job_create"
IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# Durable boundaries in execution order; a crash may occur after any of them.
JOURNAL_BOUNDARIES = (
    "plan_committed",
    "staged",
    "destination_recorded",
    "placed",
    "step_committed",
    "duplicate_committed",
    "original_destination_recorded",
    "original_placed",
    "original_committed",
    "source_removed",
    "cleanup_committed",
    "finalized",
)


OUTCOMES = ("filed", "review", "skipped", "blocked")


class PageAccountingError(ValueError):
    """Assignments do not account for every source page exactly once."""


@dataclass(frozen=True)
class DocumentAssignment:
    """One document's pages and terminal outcome, as decided by classification/review."""

    pages: tuple[int, ...]
    outcome: str
    relative_directory: str | None = None
    filename: str | None = None
    reason: str | None = None
    confidence: float = 0.0


def validate_page_accounting(page_count: int, assignments: list[DocumentAssignment]) -> None:
    """Raise unless pages 1..page_count each appear in exactly one assignment."""
    if any(assignment.outcome not in OUTCOMES for assignment in assignments):
        raise PageAccountingError("unknown page outcome")
    check_page_numbers(page_count, [a.pages for a in assignments], require_all=True)


class FilingError(RuntimeError):
    """A filing step could not complete; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, *, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class RetryRejected(RuntimeError):
    """A retry cannot run; ``code`` is stable and replayed for the same idempotency key."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FilingResult:
    job_id: str
    operation_id: str
    job_status: str
    operation_status: str
    last_error_code: str | None
    page_totals: dict[str, int]


def write_pdf_pages(source: Path, pages: list[int], target: Path) -> None:
    """Write ``pages`` (1-indexed) of ``source`` to ``target`` and fsync it."""
    reader = PdfReader(str(source))
    writer = PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page - 1])
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as fh:
        writer.write(fh)
        fh.flush()
        os.fsync(fh.fileno())


def fsync_directory(path: Path) -> None:
    """Persist a directory entry change where the platform supports it."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Admission:
    state: str
    retryable: bool
    job_id: str | None = None


class DurableFiler:
    def __init__(
        self,
        store: StateStore,
        *,
        source_roots: Mapping[str, Path],
        observe: Callable[[Path], StabilityResult] = check_input,
        write_pages: Callable[[Path, list[int], Path], None] = write_pdf_pages,
        link: Callable[[Path, Path], None] = os.link,
        fault: Callable[[str], None] = lambda boundary: None,
    ) -> None:
        self.store = store
        self.source_roots = {k: Path(v) for k, v in source_roots.items()}
        self.observe = observe
        self.write_pages = write_pages
        self.link = link
        self.fault = fault  # test seam: called after each durable boundary
        self.staging_root = store.db.paths.cache / "staging"

    # -- paths -----------------------------------------------------------------

    def _source_path(self, scope_id: str, locator: str) -> Path:
        scheme, _, reference = validate_source_locator(locator).partition(":")
        if scheme == "archive":
            root = Path(self.store.scopes.get(scope_id).canonical_root)
        elif scheme in self.source_roots:
            root = self.source_roots[scheme]
        else:
            raise UnsafeValueError("source locator scheme has no configured root")
        return confined(root, reference)

    # -- admission -------------------------------------------------------------

    def admit(self, scope_id: str, source_locator: str) -> Admission:
        """Stabilize a source and record a ``ready`` job with page fingerprints."""
        locator = validate_source_locator(source_locator)
        path = self._source_path(scope_id, locator)
        stability = self.observe(path)
        if stability.state is not InputState.READY:
            return Admission(str(stability.state), True)
        fingerprint = fingerprint_pdf(path)
        if (fingerprint.raw_sha256, fingerprint.page_count) != (
            stability.raw_sha256, stability.page_count
        ):
            return Admission(str(InputState.CHANGING), True)
        jobs = self.store.jobs
        source_fingerprint = f"sha256:{fingerprint.raw_sha256}"
        existing = jobs.find_unfinished_by_source(scope_id, source_fingerprint=source_fingerprint,
                                                  source_locator=locator)
        if existing is not None:
            return Admission(str(InputState.READY), False, existing.id)
        with self.store.db.transaction():
            job = jobs.create(scope_id, source_fingerprint=source_fingerprint,
                              source_name=path.name, source_locator=locator,
                              page_count=fingerprint.page_count)
            jobs.transition(scope_id, job.id, expected="discovered", new="stabilizing")
            jobs.transition(scope_id, job.id, expected="stabilizing", new="ready")
            jobs.record_page_fingerprints(scope_id, job.id, [
                (p.page_number, p.content_sha256, p.dhash64) for p in fingerprint.pages
            ])
        return Admission(str(InputState.READY), False, job.id)

    # -- filing ----------------------------------------------------------------

    def file_job(
        self,
        scope_id: str,
        job_id: str,
        assignments: list[DocumentAssignment],
        *,
        original_relative_directory: str,
    ) -> FilingResult:
        """Journal every planned step, then execute; invalid plans fail closed first.

        A job already ``filing`` resumes its journaled plan; the new plan is ignored.
        """
        jobs = self.store.jobs
        current = jobs.get(scope_id, job_id)
        if current is not None and current.status == "filing":
            return self.resume(scope_id, job_id)
        if current is None or current.status != "classified":
            raise InvalidTransitionError("only a classified job can be filed")
        pages = jobs.pages(scope_id, job_id)
        try:
            validate_page_accounting(len(pages), assignments)
        except PageAccountingError:
            jobs.transition(scope_id, job_id, expected="classified", new="failed",
                            error_code="page_accounting_invalid")
            raise
        job = jobs.get(scope_id, job_id)
        root = Path(self.store.scopes.get(scope_id).canonical_root)
        try:
            originals = archive_relative(original_relative_directory)
            confined(root, originals)
            destinations = [
                validated_destination(root, a.relative_directory, a.filename)
                if a.outcome == "filed" else validated_suggestion(a)
                for a in assignments
            ]
        except UnsafeValueError:
            jobs.transition(scope_id, job_id, expected="classified", new="failed",
                            error_code="unsafe_destination")
            raise
        hashes = {row["page_number"]: row["content_sha256"] for row in pages}
        dhashes = {row["page_number"]: row["perceptual_fingerprint"] for row in pages}
        plan = []
        for assignment, destination in zip(assignments, destinations):
            entry = asdict(assignment)
            entry["pages"] = list(assignment.pages)
            entry["relative_directory"], entry["filename"] = destination
            if assignment.outcome == "filed":
                near = self._near_duplicate(
                    scope_id, document_sha256([hashes[n] for n in entry["pages"]]),
                    [dhashes[n] for n in entry["pages"]])
                if near is not None:
                    entry.update(outcome="review", reason="near_duplicate_candidate",
                                 near_duplicate_of=near)
            plan.append(entry)
        steps = [
            {"kind": "write_filed", "source_locator": job.source_locator,
             "expected_sha256": document_sha256([hashes[n] for n in entry["pages"]])}
            for entry in plan if entry["outcome"] == "filed"
        ]
        raw = job.source_fingerprint.removeprefix("sha256:")
        steps.append({"kind": "retain_original", "source_locator": job.source_locator,
                      "expected_sha256": raw})
        steps.append({"kind": "cleanup_source", "source_locator": job.source_locator,
                      "expected_sha256": raw})
        request = {"assignments": plan, "page_count": len(pages),
                   "original_relative_directory": originals}
        with self.store.db.transaction():
            if not jobs.transition(scope_id, job_id, expected="classified", new="filing"):
                raise InvalidTransitionError("job changed state while planning")
            operation = self.store.operations.create(scope_id, job_id=job_id, kind=FILE_JOB,
                                                     request=request, steps=steps)
        self.fault("plan_committed")
        return self._execute(scope_id, operation.id)

    def reconcile(self, scope_id: str) -> list[FilingResult]:
        """After a restart, resume every interrupted filing operation from its journal."""
        running = self.store.operations.list_running(scope_id, FILE_JOB)
        if self.staging_root.is_dir():
            for leftover in self.staging_root.iterdir():
                finished = self.store.operations.get(scope_id, leftover.name)
                if finished is not None and finished.status != "running":
                    shutil.rmtree(leftover, ignore_errors=True)
        return [self._execute(scope_id, operation.id) for operation in running]

    def create_job(self, scope_id: str, source_locator: str, *, idempotency_key: str) -> dict:
        """``POST /api/v1/jobs``: admit an ``upload:`` or ``watch:`` handle once per key.

        Only configured upload/watch roots are resolved; arbitrary paths, traversal,
        symlinks and non-PDF names raise ``UnsafeValueError`` before anything is recorded.
        """
        if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY_RE.fullmatch(
            idempotency_key
        ):
            raise RetryRejected("invalid_idempotency_key")
        locator = validate_source_locator(source_locator)
        scheme, _, reference = locator.partition(":")
        if scheme not in {"upload", "watch"} or not reference.lower().endswith(".pdf"):
            raise UnsafeValueError("job sources are uploaded or watch-folder PDFs")
        operations = self.store.operations
        operation_id = stable_id(JOB_CREATE, scope_id, idempotency_key)
        existing = operations.get(scope_id, operation_id)
        if existing is not None:
            if existing.request["source_locator"] != locator:
                raise RetryRejected("idempotency_key_reused")
            return existing.result
        self._source_path(scope_id, locator)  # confinement before any observation
        admission = self.admit(scope_id, locator)
        if admission.job_id is None:
            raise RetryRejected("source_unavailable")
        response = {"admission": {"state": admission.state, "retryable": admission.retryable},
                    **job_payload(self.store, scope_id, admission.job_id)}
        with self.store.db.transaction():
            operations.create(scope_id, job_id=admission.job_id, kind=JOB_CREATE, steps=[],
                              request={"source_locator": locator}, status="completed",
                              operation_id=operation_id)
            operations.finish_with_result(scope_id, operation_id, expected="completed",
                                          status="completed", result=response)
        return response

    def retry(self, scope_id: str, job_id: str, *, idempotency_key: str) -> dict:
        """``POST /api/v1/jobs/{id}/retry``: resume or requeue once per idempotency key."""
        if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY_RE.fullmatch(
            idempotency_key
        ):
            raise RetryRejected("invalid_idempotency_key")
        operations, jobs = self.store.operations, self.store.jobs
        retry_id = stable_id(JOB_RETRY, scope_id, job_id, idempotency_key)
        existing = operations.get(scope_id, retry_id)
        if existing is not None and existing.status != "running":
            if "error" in existing.result:
                raise RetryRejected(existing.result["error"])
            return existing.result
        job = jobs.get(scope_id, job_id)
        if job is None:
            raise RetryRejected("job_not_found")
        if existing is None:
            operations.create(scope_id, job_id=job_id, kind=JOB_RETRY, steps=[],
                              request={"idempotency_key": idempotency_key},
                              operation_id=retry_id)
        try:
            if job.status == "filing":
                self.resume(scope_id, job_id)
                response = {"action": "resumed", **job_payload(self.store, scope_id, job_id)}
                operations.finish_with_result(scope_id, retry_id, expected="running",
                                              status="completed", result=response)
                return response
            if job.status != "failed":
                raise RetryRejected("job_not_retryable")
            source = self._source_path(scope_id, job.source_locator)
            if self.observe(source).state is not InputState.READY:
                raise RetryRejected("source_unavailable")
            if raw_sha256(source) != job.source_fingerprint.removeprefix("sha256:"):
                raise RetryRejected("source_changed")
            with self.store.db.transaction():
                jobs.transition(scope_id, job_id, expected="failed", new="ready")
                response = {"action": "requeued", **job_payload(self.store, scope_id, job_id)}
                operations.finish_with_result(scope_id, retry_id, expected="running",
                                              status="completed", result=response)
            return response
        except RetryRejected as exc:
            operations.finish_with_result(scope_id, retry_id, expected="running",
                                          status="failed", result={"error": exc.code})
            raise

    def resume(self, scope_id: str, job_id: str) -> FilingResult:
        """Continue the job's running filing operation from its journal."""
        operation = self.store.operations.latest_for_job(scope_id, job_id, FILE_JOB)
        if operation is None:
            raise FilingError("no_filing_operation")
        if operation.status != "running":
            return self.result(scope_id, operation.id)
        return self._execute(scope_id, operation.id)

    def _near_duplicate(self, scope_id: str, expected: str, dhashes: list[str]) -> str | None:
        """A filed document that is perceptually close on every page but not identical."""
        for record in self.store.files.list_filed_from_jobs(scope_id):
            if record.content_sha256 == expected or len(record.page_numbers) != len(dhashes):
                continue
            theirs = {row["page_number"]: row["perceptual_fingerprint"]
                      for row in self.store.jobs.pages(scope_id, record.job_id)}
            if all(theirs.get(n) and hamming_distance(theirs[n], mine) <= NEAR_DUPLICATE_MAX_DISTANCE
                   for n, mine in zip(record.page_numbers, dhashes)):
                return record.relative_path
        return None

    def _execute(self, scope_id: str, operation_id: str) -> FilingResult:
        operation = self.store.operations.get(scope_id, operation_id)
        job = self.store.jobs.get(scope_id, operation.job_id)
        shutil.rmtree(self.staging_root / operation.id, ignore_errors=True)  # never trusted
        try:
            source = self._source_path(scope_id, job.source_locator)
            root = Path(self.store.scopes.get(scope_id).canonical_root)
            filed = [a for a in operation.request["assignments"] if a["outcome"] == "filed"]
            steps = self.store.operations.steps(scope_id, operation_id)
            self._remove_partials(root, operation, steps)
            if any(step.status == "planned" and step.kind != "cleanup_source" for step in steps):
                self._verify_source(source, job)
            for step in steps:
                if step.status != "planned":
                    continue
                if step.kind == "write_filed":
                    self._write_filed(scope_id, operation, job, step, filed[step.ordinal],
                                      source, root)
                elif step.kind == "retain_original":
                    self._retain_original(scope_id, operation, job, step, source, root)
                elif step.kind == "cleanup_source":
                    self._cleanup_source(scope_id, operation, step, source, root)
            self._finalize(scope_id, operation, job)
        except FilingError as exc:
            self._record_failure(scope_id, operation, exc.code, retryable=exc.retryable)
        except UnsafeValueError:  # a destination or source path stopped being confined
            self._record_failure(scope_id, operation, "unsafe_destination", retryable=False)
        except OSError:
            self._record_failure(scope_id, operation, "io_error", retryable=True)
        shutil.rmtree(self.staging_root / operation.id, ignore_errors=True)
        return self.result(scope_id, operation_id)

    def _remove_partials(self, root: Path, operation: Operation,
                         steps: list[OperationStep]) -> None:
        """Delete this operation's hidden cross-volume copies left by an interrupted run."""
        request = operation.request
        directories = {a["relative_directory"] for a in request["assignments"]
                       if a["outcome"] == "filed"} | {request["original_relative_directory"]}
        for directory in sorted(directories):
            folder = confined(root, directory)
            if not folder.is_dir():
                continue
            for step in steps:
                if step.status == "planned":
                    partial_path(folder, operation.id, step.ordinal).unlink(missing_ok=True)

    def _verify_source(self, source: Path, job: Job) -> None:
        """The source must still be the exact bytes that were admitted and fingerprinted."""
        if self.observe(source).state is not InputState.READY:
            raise FilingError("source_unavailable")
        if raw_sha256(source) != job.source_fingerprint.removeprefix("sha256:"):
            raise FilingError("source_changed", retryable=False)

    def _record_failure(self, scope_id: str, operation: Operation, code: str, *,
                        retryable: bool) -> None:
        """Retryable: keep the operation resumable. Otherwise fail closed; delete nothing."""
        if retryable:
            self.store.jobs.set_error(scope_id, operation.job_id, expected="filing",
                                      error_code=code)
            return
        with self.store.db.transaction():
            self.store.operations.fail_planned_steps(scope_id, operation.id, code)
            self.store.operations.finish(scope_id, operation.id, expected="running",
                                         status="failed")
            self.store.jobs.transition(scope_id, operation.job_id, expected="filing",
                                       new="failed", error_code=code)

    def _write_filed(self, scope_id: str, operation: Operation, job: Job, step: OperationStep,
                     assignment: dict, source: Path, root: Path) -> None:
        expected, pages = step.expected_sha256, assignment["pages"]
        journaled = step.destination_relative_path
        if journaled and self._renders_as(root, journaled, expected):
            # Our link landed before the crash; the name was free when it was journaled.
            raw = raw_sha256(confined(root, journaled))
            self._commit_written(scope_id, operation, job, step, journaled, pages, raw)
            return
        duplicate = self._verified_filed_duplicate(scope_id, root, expected)
        if duplicate is None:
            staged = self.staging_root / operation.id / f"{step.ordinal}.pdf"
            self.write_pages(source, pages, staged)
            staged_raw = verified_fingerprint(staged, len(pages), expected).raw_sha256
            self.fault("staged")
            while duplicate is None:
                relative, identical = self._resolve_destination(
                    scope_id, root, assignment["relative_directory"], assignment["filename"],
                    expected)
                if identical:
                    duplicate = relative
                    break
                destination = ensure_parent(root, relative)
                self._journal_destination(scope_id, step, relative)
                self.fault("destination_recorded")
                try:
                    self._place(staged, destination, operation.id, step.ordinal)
                except FileExistsError:
                    continue  # taken since it was checked; journal the next candidate
                self.fault("placed")
                fsync_directory(destination.parent)
                if raw_sha256(destination) != staged_raw:
                    raise FilingError("output_verification_failed")
                self._commit_written(scope_id, operation, job, step, relative, pages, staged_raw)
                return
        with self.store.db.transaction():
            self.store.jobs.set_page_status(scope_id, job.id, pages, "filed")
            self.store.operations.record_step_evidence(
                scope_id, operation.id, step.ordinal,
                {"outcome": "duplicate", "relative_path": duplicate})
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="skipped",
                                              destination_relative_path=duplicate)
        self.fault("duplicate_committed")

    def _place(self, src: Path, destination: Path, operation_id: str, ordinal: int) -> None:
        place_without_replacing(src, destination,
                                partial_path(destination.parent, operation_id, ordinal),
                                link=self.link)

    def _journal_destination(self, scope_id: str, step: OperationStep, relative: str) -> None:
        self.store.operations.update_step(scope_id, step.id, expected="planned", status="planned",
                                          destination_relative_path=relative)

    def _commit_written(self, scope_id: str, operation: Operation, job: Job,
                        step: OperationStep, relative: str, pages: list[int], raw: str) -> None:
        """File record, page outcomes, evidence and step completion commit together."""
        with self.store.db.transaction():
            self.store.files.add(scope_id, job_id=job.id, relative_path=relative,
                                 content_sha256=step.expected_sha256, page_numbers=pages,
                                 role="filed")
            self.store.jobs.set_page_status(scope_id, job.id, pages, "filed")
            self.store.operations.record_step_evidence(
                scope_id, operation.id, step.ordinal,
                {"outcome": "written", "relative_path": relative, "raw_sha256": raw})
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="done")
        self.fault("step_committed")

    def _verified_filed_duplicate(self, scope_id: str, root: Path, expected: str) -> str | None:
        """Path of a recorded filed PDF whose current rendering matches ``expected``."""
        for record in self.store.files.find_by_content(scope_id, expected, "filed"):
            if self._renders_as(root, record.relative_path, expected):
                return record.relative_path
        return None

    def _renders_as(self, root: Path, relative: str, expected: str) -> bool:
        path = confined(root, relative)
        if path.is_symlink() or not path.is_file():
            return False
        try:
            return fingerprint_pdf(path).document_sha256 == expected
        except Exception:  # noqa: BLE001 - any unreadable file is not the same document
            return False

    def _free_destination(self, scope_id: str, root: Path, directory: str, filename: str) -> str:
        """First candidate name with neither a file nor a file record."""
        for relative in collision_candidates(directory, filename):
            if not os.path.lexists(confined(root, relative)) and \
                    self.store.files.get_by_path(scope_id, relative) is None:
                return relative
        raise FilingError("collision_limit_exceeded")

    def _resolve_destination(self, scope_id: str, root: Path, directory: str, filename: str,
                             expected: str) -> tuple[str, bool]:
        """First free candidate, or an occupied candidate that already renders identically."""
        for relative in collision_candidates(directory, filename):
            if os.path.lexists(confined(root, relative)):
                if self._renders_as(root, relative, expected):
                    return relative, True
                continue
            if self.store.files.get_by_path(scope_id, relative) is None:
                return relative, False
        raise FilingError("collision_limit_exceeded")

    def _retain_original(self, scope_id: str, operation: Operation, job: Job,
                         step: OperationStep, source: Path, root: Path) -> None:
        expected = step.expected_sha256
        scheme, _, reference = job.source_locator.partition(":")
        if scheme == "archive":  # already retained in the archive plane: record in place
            if not self._bytes_match(root, reference, expected):
                raise FilingError("source_changed", retryable=False)
            self._commit_original(scope_id, operation, job, step, reference)
            return
        journaled = step.destination_relative_path
        if journaled and self._bytes_match(root, journaled, expected):
            self._commit_original(scope_id, operation, job, step, journaled)
            return
        directory = operation.request["original_relative_directory"]
        while True:
            relative = self._free_destination(scope_id, root, directory, job.source_name)
            destination = ensure_parent(root, relative)
            self._journal_destination(scope_id, step, relative)
            self.fault("original_destination_recorded")
            try:
                self._place(source, destination, operation.id, step.ordinal)
            except FileExistsError:
                continue
            break
        self.fault("original_placed")
        fsync_directory(destination.parent)
        if raw_sha256(destination) != expected:
            raise FilingError("original_verification_failed")
        self._commit_original(scope_id, operation, job, step, relative)

    def _commit_original(self, scope_id: str, operation: Operation, job: Job,
                         step: OperationStep, relative: str) -> None:
        pages = self.store.jobs.pages(scope_id, job.id)
        with self.store.db.transaction():
            self.store.files.add(
                scope_id, job_id=job.id, relative_path=relative,
                content_sha256=document_sha256([row["content_sha256"] for row in pages]),
                page_numbers=[row["page_number"] for row in pages], role="original")
            self.store.operations.record_step_evidence(
                scope_id, operation.id, step.ordinal,
                {"outcome": "retained", "relative_path": relative,
                 "raw_sha256": step.expected_sha256})
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="done", destination_relative_path=relative)
        self.fault("original_committed")

    def _bytes_match(self, root: Path, relative: str | None, expected: str | None) -> bool:
        if not relative or not expected:
            return False
        path = confined(root, relative)
        return not path.is_symlink() and path.is_file() and raw_sha256(path) == expected

    def _cleanup_source(self, scope_id: str, operation: Operation, step: OperationStep,
                        source: Path, root: Path) -> None:
        """Remove the inbox copy only after every output and the retained original verify."""
        evidence = (self.store.operations.get(scope_id, operation.id).result or {}).get(
            "steps", {})
        for prior in self.store.operations.steps(scope_id, operation.id)[:step.ordinal]:
            relative = prior.destination_relative_path
            if prior.kind == "retain_original":
                verified = prior.status == "done" and self._bytes_match(root, relative,
                                                                         step.expected_sha256)
            elif prior.status == "done":
                verified = self._bytes_match(root, relative,
                                             evidence.get(str(prior.ordinal), {}).get("raw_sha256"))
            else:
                verified = prior.status == "skipped" and self._renders_as(
                    root, relative, prior.expected_sha256)
            if not verified:
                raise FilingError("output_unverified")
        if self.store.jobs.get(scope_id, operation.job_id).source_locator.startswith("archive:"):
            # The source is the retained original itself; it is never removed.
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="skipped")
            self.fault("cleanup_committed")
            return
        if source.is_file() and not source.is_symlink() and \
                raw_sha256(source) == step.expected_sha256:
            source.unlink()
            fsync_directory(source.parent)
        self.fault("source_removed")
        self.store.operations.update_step(scope_id, step.id, expected="planned", status="done")
        self.fault("cleanup_committed")

    def _finalize(self, scope_id: str, operation: Operation, job: Job) -> None:
        jobs, reviews = self.store.jobs, self.store.reviews
        assignments = operation.request["assignments"]
        with self.store.db.transaction():
            for index, entry in enumerate(assignments):
                if entry["outcome"] == "filed":
                    continue
                jobs.set_page_status(scope_id, job.id, entry["pages"], entry["outcome"])
                if entry["outcome"] == "review":
                    reviews.create(
                        scope_id, job.id,
                        candidate={"pages": entry["pages"], "reason": entry["reason"],
                                   "operation_id": operation.id,
                                   **({"near_duplicate_of": entry["near_duplicate_of"]}
                                      if entry.get("near_duplicate_of") else {})},
                        suggested_filename=entry["filename"],
                        suggested_relative_directory=entry["relative_directory"],
                        confidence=entry["confidence"],
                        item_id=stable_id("filing_review", operation.id, str(index)),
                    )
            self.store.operations.finish(scope_id, operation.id, expected="running",
                                         status="completed")
            final = "review" if any(a["outcome"] == "review" for a in assignments) else "completed"
            jobs.transition(scope_id, job.id, expected="filing", new=final)
        self.fault("finalized")

    def result(self, scope_id: str, operation_id: str) -> FilingResult:
        operation = self.store.operations.get(scope_id, operation_id)
        job = self.store.jobs.get(scope_id, operation.job_id)
        return FilingResult(job.id, operation.id, job.status, operation.status,
                            job.last_error_code, self.store.jobs.page_totals(scope_id, job.id))


def verified_fingerprint(path: Path, page_count: int, expected: str):
    """Reopen a written PDF and prove its page count and rendered content."""
    try:
        with open(path, "rb") as fh:
            pages = len(PdfReader(fh).pages)
        fingerprint = fingerprint_pdf(path)
    except Exception as exc:  # any parse or render failure fails verification
        raise FilingError("output_verification_failed") from exc
    if pages != page_count or fingerprint.page_count != page_count or \
            fingerprint.document_sha256 != expected:
        raise FilingError("output_verification_failed")
    return fingerprint


def job_payload(store: StateStore, scope_id: str, job_id: str) -> dict:
    """Durable job, page-accounting, journal and file view; paths are archive-relative."""
    job = store.jobs.get(scope_id, job_id)
    if job is None:
        raise RetryRejected("job_not_found")
    pages = [{"page_number": row["page_number"], "status": row["status"]}
             for row in store.jobs.pages(scope_id, job_id)]
    totals = {status: sum(p["status"] == status for p in pages)
              for status in ("pending", "filed", "review", "skipped", "blocked")}
    operation = store.operations.latest_for_job(scope_id, job_id, FILE_JOB)
    if job.status == "filing":
        recovery = {"state": "resumable", "retryable": True}
    elif job.status == "failed":
        recovery = {"state": "failed_closed", "retryable": True}
    else:
        recovery = {"state": "none", "retryable": False}
    return {
        "job": {"id": job.id, "archive_scope_id": job.archive_scope_id, "status": job.status,
                "attempt": job.attempt, "source_name": job.source_name,
                "last_error_code": job.last_error_code, "created_at": job.created_at,
                "updated_at": job.updated_at},
        "page_accounting": {"page_count": len(pages), **totals,
                            "complete": bool(pages) and totals["pending"] == 0,
                            "pages": pages},
        "operation": None if operation is None else {
            "id": operation.id, "kind": operation.kind, "status": operation.status,
            "created_at": operation.created_at, "completed_at": operation.completed_at,
            "assignments": [
                {key: entry.get(key) for key in ("pages", "outcome", "reason",
                                                 "relative_directory", "filename",
                                                 "near_duplicate_of")}
                for entry in operation.request["assignments"]],
            "steps": [{"ordinal": step.ordinal, "kind": step.kind, "status": step.status,
                       "destination_relative_path": step.destination_relative_path,
                       "error_code": step.error_code}
                      for step in store.operations.steps(scope_id, operation.id)],
        },
        "recovery": recovery,
        "files": [{"relative_path": r.relative_path, "role": r.role,
                   "page_numbers": r.page_numbers}
                  for r in store.files.list_for_job(scope_id, job_id)],
    }


def place_without_replacing(src: Path, destination: Path, partial: Path, *,
                            link: Callable[[Path, Path], None] | None = None) -> None:
    """Atomically add ``destination`` without replacing anything.

    A hard link is atomic and raises ``FileExistsError`` if the name exists. Across
    volumes the bytes are first copied into an exclusive, fsynced, verified hidden
    file beside the destination, which is then hard-linked into place and removed.
    """
    link = link or os.link
    try:
        link(src, destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    partial.unlink(missing_ok=True)  # the name belongs to exactly one operation step
    try:
        with open(src, "rb") as reader, open(partial, "xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if raw_sha256(partial) != raw_sha256(src):
            raise FilingError("output_verification_failed")
        link(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def check_page_numbers(page_count: int, documents: list, *, require_all: bool) -> None:
    """Pages are ints in range, never repeated, every document non-empty (and complete)."""
    seen: list[int] = []
    for pages in documents:
        if not pages:
            raise PageAccountingError("a document must contain at least one page")
        for page in pages:
            if type(page) is not int or not 1 <= page <= page_count:
                raise PageAccountingError("page number out of range")
        seen.extend(pages)
    if len(seen) != len(set(seen)):
        raise PageAccountingError("page assigned more than once")
    if require_all and set(seen) != set(range(1, page_count + 1)):
        raise PageAccountingError("page left unassigned")


def partial_path(directory: Path, operation_id: str, ordinal: int) -> Path:
    return directory / f".docflow-{operation_id}-{ordinal}.partial"


def validated_suggestion(assignment: DocumentAssignment) -> tuple[str | None, str | None]:
    """Optional review suggestion: archive-relative directory, single name, sane confidence."""
    confidence = assignment.confidence
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or \
            not 0 <= confidence <= 1:
        raise UnsafeValueError("confidence must be a finite number in [0, 1]")
    directory = (None if assignment.relative_directory is None
                 else archive_relative(assignment.relative_directory))
    name = None if assignment.filename is None else safe_name(assignment.filename)
    return directory, name


def validated_destination(root: Path, directory: str | None, filename: str | None
                          ) -> tuple[str, str]:
    """Normalize a filed destination: archive-relative directory, single ``.pdf`` name."""
    relative_directory = archive_relative(directory)
    name = safe_name(filename)
    if not name.lower().endswith(".pdf") or name.startswith("."):
        raise UnsafeValueError("filed documents must be visible .pdf files")
    confined(root, f"{relative_directory}/{name}")
    return relative_directory, name


def collision_candidates(directory: str, filename: str, limit: int = 10_000):
    """Deterministic collision-safe names: ``Doc.pdf``, ``Doc_2.pdf``, ``Doc_3.pdf`` ..."""
    stem, dot, suffix = filename.rpartition(".")
    if not stem:
        stem, dot, suffix = filename, "", ""
    yield f"{directory}/{filename}"
    for counter in range(2, limit + 1):
        yield f"{directory}/{stem}_{counter}{dot}{suffix}"


def ensure_parent(root: Path, relative: str) -> Path:
    """Create the parent directories of a confined path, refusing symlinked components."""
    destination = confined(root, relative)
    current = Path(root)
    for part in archive_relative(relative).split("/")[:-1]:
        current = current / part
        current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise UnsafeValueError("destination directory is not a real directory")
    return destination


def confined(root: Path, relative: str) -> Path:
    """Resolve an archive-relative path under ``root`` refusing symlinked components."""
    parts = archive_relative(relative).split("/")
    for part in parts:
        safe_name(part)
    path = Path(root)
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise UnsafeValueError("path component is a symbolic link")
    return path
