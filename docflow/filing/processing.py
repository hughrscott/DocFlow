"""Durable processing of one admitted scan: local OCR, classification, journaled filing.

This is the only processing path for the web UI and CLI. It admits an ``upload:`` or
``watch:`` handle through ``DurableFiler``, runs local OCR, clustering and classification
(model calls only through ``CloudPromptGateway``), and files the result with
``DurableFiler.file_job``. Low-confidence documents become durable review items with page
references; no queue, summary or log files are written to the archive.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from docflow.filing.operations import (
    DocumentAssignment,
    DurableFiler,
    FilingResult,
    job_payload,
    validated_destination,
)
from docflow.state.repositories import (
    StateStore,
    UnsafeValueError,
    archive_relative,
    safe_name,
)

logger = logging.getLogger(__name__)

# Unfinished job states that (re)run local OCR and classification; the lookup map is memory-only.
_RECLASSIFY_FROM = {"ready": "ocr", "classified": "ocr", "awaiting_model": "ocr"}


class ProcessingError(RuntimeError):
    """A scan cannot be processed now; ``code`` is stable (``source_unavailable``, ...)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class ProcessingResult:
    job_id: str
    job_status: str
    decisions: list = field(default_factory=list)
    assignments: list[DocumentAssignment] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


def originals_directory(now: datetime | None = None) -> str:
    """``BeenOrganized<mmddyy>`` for the local calendar date."""
    moment = now or datetime.now(UTC)
    return f"BeenOrganized{moment.astimezone().strftime('%m%d%y')}"


def _relative_directory(target: str, root: Path) -> str | None:
    try:
        return archive_relative(Path(target).relative_to(root).as_posix())
    except (ValueError, UnsafeValueError):
        return None


def assignment_for(decision, root: Path) -> DocumentAssignment:
    """Map a gated decision to a durable outcome; unsafe destinations go to review."""
    pages = tuple(decision.candidate.pages)
    confidence = float(decision.confidence)
    directory = _relative_directory(decision.target_directory, root)
    try:
        filename = safe_name(decision.filename)
    except UnsafeValueError:
        filename = None
    if decision.auto_file and directory and filename:
        try:
            validated_destination(root, directory, filename)
            return DocumentAssignment(pages, "filed", directory, filename, None, confidence)
        except UnsafeValueError:
            return DocumentAssignment(pages, "review", None, None, "unsafe_destination",
                                      confidence)
    if decision.rule_matched == "none":
        return DocumentAssignment(pages, "review", None, None, "unmatched", confidence)
    return DocumentAssignment(pages, "review", directory, filename if directory else None,
                              "low_confidence", confidence)


def process_scan(
    store: StateStore,
    filer: DurableFiler,
    scope_id: str,
    source_locator: str,
    config: dict,
    *,
    progress: Callable[[str, int], None] = lambda step, percent: None,
) -> ProcessingResult:
    """Admit, classify locally and file one scan durably; idempotent for an unfinished job."""
    from docflow.classification.classifier import classify_candidates
    from docflow.clustering.clusterer import cluster_pages
    from docflow.filing.confidence_gate import gate_decisions
    from docflow.ingestion import loader
    from docflow.llm.gateway import CloudPromptGateway
    from docflow.ocr import analyzer

    jobs = store.jobs
    progress("Checking the scan", 5)
    admission = filer.admit(scope_id, source_locator)
    if admission.job_id is None:
        raise ProcessingError("source_unavailable")
    job = jobs.get(scope_id, admission.job_id)
    if job.status == "filing":
        filer.resume(scope_id, job.id)
    if job.status not in {*_RECLASSIFY_FROM, "ocr"}:
        job = jobs.get(scope_id, job.id)
        return ProcessingResult(job.id, job.status,
                                payload=job_payload(store, scope_id, job.id))
    if job.status in _RECLASSIFY_FROM:
        jobs.transition(scope_id, job.id, expected=job.status, new="ocr")

    root = Path(store.scopes.get(scope_id).canonical_root)
    scoped = {**config, "archive_root": str(root)}
    try:
        source = filer.source_path(scope_id, job.source_locator)
        progress("Running OCR", 20)
        records = analyzer.analyze_pages(loader.load_pdf(source))
        gateway = CloudPromptGateway(scoped)  # one gateway (placeholder map) per job
        progress("Clustering documents", 45)
        candidates = cluster_pages(records, scoped, gateway)
        progress("Classifying documents", 65)
        decisions = classify_candidates(candidates, scoped, gateway)
        gate_decisions(decisions, scoped)
    except Exception:
        jobs.transition(scope_id, job.id, expected="ocr", new="failed",
                        error_code="processing_failed")
        raise
    assignments = [assignment_for(decision, root) for decision in decisions]
    jobs.transition(scope_id, job.id, expected="ocr", new="classified")
    progress("Filing documents", 80)
    result: FilingResult = filer.file_job(scope_id, job.id, assignments,
                                          original_relative_directory=originals_directory())
    return ProcessingResult(job.id, result.job_status, decisions, assignments,
                            job_payload(store, scope_id, job.id))
