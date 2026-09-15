"""Durable review actions (approve, correct, skip, batch) and journaled undo.

Review items point at pages of a job whose original is retained in the archive.
Every action is an ``operations`` row with ordered ``operation_steps``; files are
staged in application state, placed without replacement and committed together
with the file record and page/review/job state. Paths are archive-relative.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from docflow.filing.operations import (
    IDEMPOTENCY_KEY_RE,
    FilingError,
    collision_candidates,
    confined,
    ensure_parent,
    fsync_directory,
    partial_path,
    place_without_replacing,
    verified_fingerprint,
    write_pdf_pages,
)
from docflow.ingestion.loader import document_sha256, raw_sha256
from docflow.state.repositories import (
    FileRecord,
    InvalidTransitionError,
    Job,
    ReviewItem,
    StateStore,
    UnsafeValueError,
    check_safe_json,
    safe_name,
    stable_id,
)

ACTION_KINDS = {"approve": "review_approve", "correct": "review_correct", "skip": "review_skip"}
RESOLVED_STATUS = {"approve": "approved", "correct": "corrected", "skip": "skipped"}
BATCH_KIND = "review_batch"
UNDO_KIND = "operation_undo"
UNDOABLE_KINDS = frozenset({*ACTION_KINDS.values(), BATCH_KIND})
# One writer thread at a time: the state connection is shared by the local server.
_LOCK = threading.RLock()


class ReviewActionRejected(RuntimeError):
    """An action or undo cannot run; ``code`` is stable and documented."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Target:
    item: ReviewItem
    job: Job
    pages: list[int]


class ReviewActions:
    def __init__(
        self,
        store: StateStore,
        *,
        write_pages: Callable[[Path, list[int], Path], None] = write_pdf_pages,
        link: Callable[[Path, Path], None] = os.link,
        fault: Callable[[str], None] = lambda boundary: None,
    ) -> None:
        self.store = store
        self.write_pages = write_pages
        self.link = link
        self.fault = fault  # test seam: called after each durable boundary
        self.staging_root = store.db.paths.cache / "review-staging"

    def _root(self, scope_id: str) -> Path:
        return Path(self.store.scopes.get(scope_id).canonical_root)

    # -- validation (no mutation) ------------------------------------------------

    def _target(self, scope_id: str, item: ReviewItem) -> _Target:
        """A pending item of a job in review whose pages are all still in review."""
        if item.status != "pending":
            raise ReviewActionRejected("review_item_not_pending")
        job = self.store.jobs.get(scope_id, item.job_id)
        pages = item.candidate.get("pages")
        if job is None or job.status != "review" or not isinstance(pages, list) or not pages \
                or any(type(p) is not int for p in pages) or len(set(pages)) != len(pages):
            raise ReviewActionRejected("review_item_not_actionable")
        statuses = {row["page_number"]: row["status"]
                    for row in self.store.jobs.pages(scope_id, job.id)}
        if any(statuses.get(page) != "review" for page in pages):
            raise ReviewActionRejected("review_item_not_actionable")
        return _Target(item, job, sorted(pages))

    def _original(self, scope_id: str, target: _Target) -> FileRecord:
        """The retained original holding the item's pages, with page fingerprints."""
        originals = [r for r in self.store.files.list_for_job(scope_id, target.job.id)
                     if r.role == "original"]
        hashes = {row["page_number"]: row["content_sha256"]
                  for row in self.store.jobs.pages(scope_id, target.job.id)}
        if len(originals) != 1 or any(not hashes.get(page) for page in target.pages):
            raise ReviewActionRejected("source_unavailable")
        try:
            path = confined(self._root(scope_id), originals[0].relative_path)
        except UnsafeValueError:
            raise ReviewActionRejected("source_unavailable") from None
        if path.is_symlink() or not path.is_file():
            raise ReviewActionRejected("source_unavailable")
        return originals[0]

    def _actions(self, scope_id: str, item: ReviewItem) -> list[str]:
        try:
            target = self._target(scope_id, item)
        except ReviewActionRejected:
            return []
        try:
            self._original(scope_id, target)
        except ReviewActionRejected:
            return ["skip"]
        try:
            validate_review_destination(self._root(scope_id),
                                        item.suggested_relative_directory,
                                        item.suggested_filename)
        except UnsafeValueError:
            return ["correct", "skip"]
        return ["approve", "correct", "skip"]

    def _plan(self, scope_id: str, item: ReviewItem, action: str,
              destination: tuple[str, str] | None = None) -> tuple[dict, dict]:
        """Validated request entry and journal step for one item (no mutation)."""
        target = self._target(scope_id, item)
        if action == "skip":
            return ({"review_item_id": item.id, "job_id": target.job.id, "action": action,
                     "pages": target.pages}, {"kind": "skip_item"})
        original = self._original(scope_id, target)
        if destination is None:
            destination = (item.suggested_relative_directory, item.suggested_filename)
            try:
                validate_review_destination(self._root(scope_id), *destination)
            except UnsafeValueError:
                raise ReviewActionRejected("destination_required") from None
        hashes = {row["page_number"]: row["content_sha256"]
                  for row in self.store.jobs.pages(scope_id, target.job.id)}
        expected = document_sha256([hashes[page] for page in target.pages])
        entry = {"review_item_id": item.id, "job_id": target.job.id, "action": action,
                 "pages": target.pages, "relative_directory": destination[0],
                 "filename": destination[1], "source_relative_path": original.relative_path}
        step = {"kind": "write_filed", "source_locator": f"archive:{original.relative_path}",
                "expected_sha256": expected}
        return entry, step

    # -- actions -----------------------------------------------------------------

    def approve(self, scope_id: str, item_id: str, *, idempotency_key: str) -> dict:
        """``POST /api/v1/review-items/{id}/approve``: file pages at the stored suggestion."""
        return self._single(scope_id, "approve", item_id, idempotency_key)

    def skip(self, scope_id: str, item_id: str, *, idempotency_key: str) -> dict:
        """``POST /api/v1/review-items/{id}/skip``: pages become ``skipped``; no file changes."""
        return self._single(scope_id, "skip", item_id, idempotency_key)

    def correct(self, scope_id: str, item_id: str, *, idempotency_key: str,
                relative_directory: str, filename: str) -> dict:
        """``POST /api/v1/review-items/{id}/correct``: file at a validated chosen destination."""
        return self._single(scope_id, "correct", item_id, idempotency_key,
                            (relative_directory, filename))

    def batch(self, scope_id: str, *, action: str, review_item_ids: list[str],
              idempotency_key: str) -> dict:
        """``POST /api/v1/review-items/batch``: approve or skip many items in one operation."""
        _check_key(idempotency_key)
        operations = self.store.operations
        with _LOCK:
            self.reconcile(scope_id)
            operation_id = stable_id("review_action", scope_id, idempotency_key)
            fingerprint = _fingerprint(f"batch:{action}", list(review_item_ids), None)
            existing = operations.get(scope_id, operation_id)
            if existing is not None:
                if existing.request.get("fingerprint") != fingerprint:
                    raise ReviewActionRejected("idempotency_key_reused")
                return self._replay(scope_id, existing)
            entries, steps, order = [], [], []
            claimed: set[tuple[str, int]] = set()  # each page gets one outcome per batch
            for item_id in review_item_ids:
                item = self.store.reviews.get(scope_id, item_id)
                try:
                    if item is None:
                        raise ReviewActionRejected("review_item_not_found")
                    entry, step = self._plan(scope_id, item, action)
                    pages = {(entry["job_id"], page) for page in entry["pages"]}
                    if pages & claimed:
                        raise ReviewActionRejected("duplicate_page_assignment")
                    claimed |= pages
                except ReviewActionRejected as exc:
                    order.append({"review_item_id": item_id, "action": action,
                                  "error_code": exc.code})
                    continue
                order.append({"ordinal": len(entries)})
                entries.append(entry)
                steps.append(step)
            operations.create(scope_id, job_id=None, kind=BATCH_KIND, steps=steps,
                              request={"fingerprint": fingerprint, "items": entries,
                                       "order": order},
                              operation_id=operation_id)
            return self._replay(scope_id, operations.get(scope_id, operation_id))

    def _single(self, scope_id: str, action: str, item_id: str, key: str,
                destination: tuple[str, str] | None = None) -> dict:
        _check_key(key)
        with _LOCK:
            self.reconcile(scope_id)
            operation_id = stable_id("review_action", scope_id, key)
            fingerprint = _fingerprint(action, [item_id], destination)
            existing = self.store.operations.get(scope_id, operation_id)
            if existing is not None:
                if existing.request.get("fingerprint") != fingerprint:
                    raise ReviewActionRejected("idempotency_key_reused")
                return self._replay(scope_id, existing)
            if destination is not None:
                try:
                    validate_review_destination(self._root(scope_id), *destination)
                except UnsafeValueError:
                    raise ReviewActionRejected("invalid_destination") from None
            item = self.store.reviews.get(scope_id, item_id)
            if item is None:
                raise ReviewActionRejected("review_item_not_found")
            operations = self.store.operations
            try:
                entry, step = self._plan(scope_id, item, action, destination)
            except ReviewActionRejected as exc:
                with self.store.db.transaction():
                    operations.create(scope_id, job_id=item.job_id, kind=ACTION_KINDS[action],
                                      request={"fingerprint": fingerprint}, steps=[],
                                      status="failed", operation_id=operation_id)
                    operations.finish_with_result(scope_id, operation_id, expected="failed",
                                                  status="failed", result={"error": exc.code})
                raise
            operations.create(scope_id, job_id=item.job_id, kind=ACTION_KINDS[action],
                              request={"fingerprint": fingerprint, "items": [entry]},
                              steps=[step],
                              operation_id=operation_id)
            return self._replay(scope_id, operations.get(scope_id, operation_id))

    def reconcile(self, scope_id: str) -> None:
        """Resume interrupted review actions and undos (oldest first) from their journals."""
        with _LOCK:
            for kind in (*UNDOABLE_KINDS, UNDO_KIND):
                for operation in self.store.operations.list_running(scope_id, kind):
                    if kind == UNDO_KIND:
                        self._execute_undo(scope_id, operation.id)
                    else:
                        self._execute(scope_id, operation.id)

    def _replay(self, scope_id: str, operation) -> dict:
        """The stored response for an idempotency key; an interrupted run resumes.

        A single-item action that was rejected or failed raises its stored code again. An
        action that has since been undone refuses: its stored outcome no longer holds.
        """
        if operation.status in {"undone", "partially_undone"}:
            raise ReviewActionRejected("operation_undone")
        if operation.status == "running":
            response = self._execute(scope_id, operation.id)
        elif "error" in operation.result:
            raise ReviewActionRejected(operation.result["error"])
        else:
            response = operation.result["response"]
        if operation.kind != BATCH_KIND and response["outcome"] == "failed":
            raise ReviewActionRejected(response["items"][0]["error_code"])
        return response

    def _execute(self, scope_id: str, operation_id: str) -> dict:
        operation = self.store.operations.get(scope_id, operation_id)
        root = self._root(scope_id)
        shutil.rmtree(self.staging_root / operation_id, ignore_errors=True)  # never trusted
        for step in self.store.operations.steps(scope_id, operation_id):
            if step.status != "planned":
                continue
            entry = operation.request["items"][step.ordinal]
            try:
                if step.kind == "skip_item":
                    self._commit_skip(scope_id, step, entry)
                else:
                    self._write(scope_id, operation_id, step, entry, root)
                continue
            except FilingError as exc:
                code = exc.code
            except UnsafeValueError:
                code = "unsafe_destination"
            except InvalidTransitionError:
                code = "state_changed"
            except OSError:
                code = "io_error"
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="failed", error_code=code)
        shutil.rmtree(self.staging_root / operation_id, ignore_errors=True)
        return self._finalize(scope_id, operation_id)

    def _write(self, scope_id: str, operation_id: str, step, entry: dict, root: Path) -> None:
        pages, expected = entry["pages"], step.expected_sha256
        journaled = step.destination_relative_path
        if journaled:  # interrupted after the destination was journaled
            staged_raw = (self.store.operations.get(scope_id, operation_id).result or {}).get(
                "steps", {}).get(str(step.ordinal), {}).get("staged_raw_sha256")
            placed = confined(root, journaled)
            if placed.parent.is_dir():
                partial_path(placed.parent, operation_id, step.ordinal).unlink(missing_ok=True)
            if staged_raw and not placed.is_symlink() and placed.is_file() \
                    and raw_sha256(placed) == staged_raw:
                self._commit_write(scope_id, operation_id, step, entry, journaled, staged_raw)
                return
        source = confined(root, entry["source_relative_path"])
        if source.is_symlink() or not source.is_file():
            raise FilingError("source_unavailable")
        staged = self.staging_root / operation_id / f"{step.ordinal}.pdf"
        self.write_pages(source, pages, staged)
        verified_fingerprint(staged, len(pages), expected)
        raw = raw_sha256(staged)
        self.fault("staged")
        operations = self.store.operations
        for relative in collision_candidates(entry["relative_directory"], entry["filename"]):
            path = confined(root, relative)
            if os.path.lexists(path) or self.store.files.get_by_path(scope_id, relative):
                continue
            ensure_parent(root, relative)
            with self.store.db.transaction():
                operations.update_step(scope_id, step.id, expected="planned", status="planned",
                                       destination_relative_path=relative)
                operations.record_step_evidence(scope_id, operation_id, step.ordinal, {
                    "outcome": "planned", "relative_path": relative, "staged_raw_sha256": raw})
            self.fault("destination_recorded")
            try:
                place_without_replacing(staged, path,
                                        partial_path(path.parent, operation_id, step.ordinal),
                                        link=self.link)
            except FileExistsError:
                continue  # taken since it was checked; journal the next candidate
            self.fault("placed")
            fsync_directory(path.parent)
            if raw_sha256(path) != raw:
                raise FilingError("output_verification_failed")
            self._commit_write(scope_id, operation_id, step, entry, relative, raw)
            return
        raise FilingError("collision_limit_exceeded")

    def _commit_write(self, scope_id: str, operation_id: str, step, entry: dict,
                      relative: str, raw: str) -> None:
        """File record, page outcomes, review status, evidence and step commit together."""
        jobs, pages, job_id = self.store.jobs, entry["pages"], entry["job_id"]
        with self.store.db.transaction():
            record = self.store.files.add(scope_id, job_id=job_id, relative_path=relative,
                                          content_sha256=step.expected_sha256,
                                          page_numbers=pages, role="filed")
            if (record.job_id, record.content_sha256) != (job_id, step.expected_sha256):
                raise InvalidTransitionError("destination is recorded for another file")
            jobs.move_page_status(scope_id, job_id, pages, expected="review", new="filed")
            evidence = {"outcome": "written", "relative_path": relative, "raw_sha256": raw}
            if entry["action"] == "correct":
                item = self.store.reviews.get(scope_id, entry["review_item_id"])
                evidence["correction_id"] = self.store.reviews.correct(
                    scope_id, item.id, chosen_relative_directory=entry["relative_directory"],
                    chosen_rule_id=None, normalized_features={
                        "reason": _text(item.candidate.get("reason")),
                        "page_count": len(pages),
                        "suggested_relative_directory": item.suggested_relative_directory,
                        "suggested_filename": item.suggested_filename,
                        "chosen_filename": entry["filename"]})
                if evidence["correction_id"] is None:
                    raise InvalidTransitionError("review item is no longer pending")
            elif not self.store.reviews.resolve(scope_id, entry["review_item_id"],
                                                expected="pending",
                                                new=RESOLVED_STATUS[entry["action"]]):
                raise InvalidTransitionError("review item is no longer pending")
            self.store.operations.record_step_evidence(scope_id, operation_id, step.ordinal,
                                                       evidence)
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="done")
            self._complete_job_if_reviewed(scope_id, job_id)
        self.fault("step_committed")

    def _commit_skip(self, scope_id: str, step, entry: dict) -> None:
        with self.store.db.transaction():
            self.store.jobs.move_page_status(scope_id, entry["job_id"], entry["pages"],
                                             expected="review", new="skipped")
            if not self.store.reviews.resolve(scope_id, entry["review_item_id"],
                                              expected="pending", new="skipped"):
                raise InvalidTransitionError("review item is no longer pending")
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="done")
            self._complete_job_if_reviewed(scope_id, entry["job_id"])
        self.fault("step_committed")

    def _complete_job_if_reviewed(self, scope_id: str, job_id: str) -> None:
        pages = self.store.jobs.pages(scope_id, job_id)
        if all(row["status"] != "review" for row in pages):
            self.store.jobs.transition(scope_id, job_id, expected="review", new="completed")

    def _finalize(self, scope_id: str, operation_id: str) -> dict:
        operations = self.store.operations
        operation = operations.get(scope_id, operation_id)
        evidence = (operation.result or {}).get("steps", {})
        steps = operations.steps(scope_id, operation_id)
        order = operation.request.get("order") or [{"ordinal": s.ordinal} for s in steps]
        items = []
        for position in order:
            if "ordinal" not in position:  # rejected before journaling; nothing ran
                items.append({**position, "job_id": None, "result": "rejected",
                              "relative_path": None, "page_numbers": []})
                continue
            step = steps[position["ordinal"]]
            entry = operation.request["items"][step.ordinal]
            done = step.status == "done"
            items.append({
                "review_item_id": entry["review_item_id"], "job_id": entry["job_id"],
                "action": entry["action"],
                "result": ("filed" if step.kind == "write_filed" else "skipped") if done
                else "failed",
                "error_code": step.error_code,
                "relative_path": evidence.get(str(step.ordinal), {}).get("relative_path")
                if done else None,
                "page_numbers": entry["pages"],
            })
        succeeded = any(i["result"] in {"filed", "skipped"} for i in items)
        failed = any(i["result"] in {"failed", "rejected"} for i in items)
        outcome = "failed" if not succeeded else "partial_failure" if failed else "completed"
        with self.store.db.transaction():
            operations.finish_with_result(scope_id, operation_id, expected="running",
                                          status="completed" if succeeded else "failed",
                                          result={"steps": evidence})
            operation = operations.get(scope_id, operation_id)
            response = {
                "operation": _operation_view(operation),
                "outcome": outcome,
                "items": items,
                "jobs": [self._job_view(scope_id, job_id)
                         for job_id in dict.fromkeys(i["job_id"] for i in items if i["job_id"])],
            }
            operations.finish_with_result(scope_id, operation_id, expected=operation.status,
                                          status=operation.status,
                                          result={"steps": evidence, "response": response})
        return response

    # -- undo --------------------------------------------------------------------

    def undo(self, scope_id: str, operation_id: str, *, idempotency_key: str) -> dict:
        """``POST /api/v1/operations/{id}/undo``: journaled, hash-verified compensation.

        Removes only files this operation wrote, after proving their bytes are unchanged;
        missing, modified or unsafe targets fail per step and nothing is deleted for them.
        """
        _check_key(idempotency_key)
        operations = self.store.operations
        with _LOCK:
            self.reconcile(scope_id)
            undo_id = stable_id(UNDO_KIND, scope_id, operation_id, idempotency_key)
            existing = operations.get(scope_id, undo_id)
            if existing is not None:
                if existing.status == "running":
                    return self._execute_undo(scope_id, undo_id)
                return existing.result["response"]
            target = operations.get(scope_id, operation_id)
            if target is None:
                raise ReviewActionRejected("operation_not_found")
            if target.kind not in UNDOABLE_KINDS or target.status not in {
                    "completed", "partially_undone", "undone"}:
                raise ReviewActionRejected("operation_not_undoable")
            if target.status == "undone":  # safe compensation already happened
                return operations.get(scope_id, target.result["undone_by"]).result["response"]
            evidence = target.result.get("steps", {})
            steps = []
            for step in operations.steps(scope_id, target.id):
                if step.status != "done":
                    steps.append({"kind": "none"})
                elif step.kind == "write_filed":
                    steps.append({"kind": "remove_filed",
                                  "destination_relative_path": step.destination_relative_path,
                                  "expected_sha256": evidence[str(step.ordinal)]["raw_sha256"]})
                else:
                    steps.append({"kind": "reopen_skipped"})
            operations.create(scope_id, job_id=target.job_id, kind=UNDO_KIND, steps=steps,
                              request={"target_operation_id": target.id,
                                       "idempotency_key": idempotency_key},
                              operation_id=undo_id)
            return self._execute_undo(scope_id, undo_id)

    def _execute_undo(self, scope_id: str, undo_id: str) -> dict:
        operations = self.store.operations
        undo = operations.get(scope_id, undo_id)
        target = operations.get(scope_id, undo.request["target_operation_id"])
        root = self._root(scope_id)
        target_steps = operations.steps(scope_id, target.id)
        for step in operations.steps(scope_id, undo_id):
            if step.status != "planned":
                continue
            target_step = target_steps[step.ordinal]
            entry = target.request["items"][step.ordinal]
            if step.kind == "none" or target_step.status != "done":
                operations.update_step(scope_id, step.id, expected="planned", status="skipped")
                continue
            try:
                self._check_restorable(scope_id, target, target_step, entry)
                if step.kind == "remove_filed":
                    self._undo_remove(scope_id, undo_id, step, target, target_step, entry, root)
                else:
                    self._restore(scope_id, step, target, target_step, entry)
                continue
            except FilingError as exc:
                code = exc.code
            except UnsafeValueError:
                code = "target_unsafe"
            except InvalidTransitionError:
                code = "state_changed"
            except OSError:
                code = "io_error"
            operations.update_step(scope_id, step.id, expected="planned", status="failed",
                                   error_code=code)
        return self._finalize_undo(scope_id, undo_id)

    def _check_restorable(self, scope_id: str, target, target_step, entry: dict) -> None:
        """State still shows this action's outcome, so compensation cannot clobber newer work."""
        item = self.store.reviews.get(scope_id, entry["review_item_id"])
        outcome = "filed" if target_step.kind == "write_filed" else "skipped"
        statuses = {row["page_number"]: row["status"]
                    for row in self.store.jobs.pages(scope_id, entry["job_id"])}
        if item is None or item.status != RESOLVED_STATUS[entry["action"]] or any(
                statuses.get(page) != outcome for page in entry["pages"]):
            raise FilingError("state_changed")
        if target_step.kind == "write_filed":
            record = self.store.files.get_by_path(scope_id,
                                                  target_step.destination_relative_path)
            if record is None or (record.job_id, record.content_sha256) != (
                    entry["job_id"], target_step.expected_sha256):
                raise FilingError("state_changed")

    def _undo_remove(self, scope_id: str, undo_id: str, step, target, target_step,
                     entry: dict, root: Path) -> None:
        """Verify, detach atomically, re-verify, journal, then delete the created file."""
        expected = step.expected_sha256
        evidence = (self.store.operations.get(scope_id, undo_id).result or {}).get(
            "steps", {}).get(str(step.ordinal), {})
        path = confined(root, step.destination_relative_path)
        trash = path.parent / f".docflow-undo-{undo_id}-{step.ordinal}.trash"
        try:
            if not evidence.get("detached"):
                if os.path.lexists(trash):  # detached before a crash; prove it again
                    _verify_detached(trash, path, expected)
                else:
                    if not os.path.lexists(path):
                        raise FilingError("target_missing")
                    if path.is_symlink() or not path.is_file():
                        raise FilingError("target_unsafe")
                    if raw_sha256(path) != expected:
                        raise FilingError("target_modified")
                    os.rename(path, trash)
                    self.fault("undo_detached")
                    _verify_detached(trash, path, expected)
                self.store.operations.record_step_evidence(scope_id, undo_id, step.ordinal, {
                    "detached": True, "relative_path": step.destination_relative_path})
                self.fault("undo_detach_recorded")
            trash.unlink(missing_ok=True)
        except OSError:
            _reattach(trash, path)  # a failed step never leaves the file hidden
            raise
        fsync_directory(path.parent)
        self.fault("undo_removed")
        self._restore(scope_id, step, target, target_step, entry)

    def _restore(self, scope_id: str, step, target, target_step, entry: dict) -> None:
        """File record, page outcomes, review item, correction and job return together."""
        job_id, pages = entry["job_id"], entry["pages"]
        with self.store.db.transaction():
            if target_step.kind == "write_filed":
                if not self.store.files.remove(
                        scope_id, target_step.destination_relative_path, job_id=job_id,
                        content_sha256=target_step.expected_sha256):
                    raise InvalidTransitionError("file record changed")
                self.store.jobs.move_page_status(scope_id, job_id, pages, expected="filed",
                                                 new="review")
            else:
                self.store.jobs.move_page_status(scope_id, job_id, pages, expected="skipped",
                                                 new="review")
            if not self.store.reviews.reopen(scope_id, entry["review_item_id"],
                                             expected=RESOLVED_STATUS[entry["action"]]):
                raise InvalidTransitionError("review item changed")
            correction_id = target.result["steps"].get(str(target_step.ordinal), {}).get(
                "correction_id")
            if correction_id:
                self.store.reviews.delete_correction(scope_id, entry["review_item_id"],
                                                     correction_id)
            self.store.jobs.reopen_review(scope_id, job_id)
            self.store.operations.update_step(scope_id, target_step.id, expected="done",
                                              status="compensated")
            self.store.operations.update_step(scope_id, step.id, expected="planned",
                                              status="done")
        self.fault("undo_step_committed")

    def _finalize_undo(self, scope_id: str, undo_id: str) -> dict:
        operations = self.store.operations
        undo = operations.get(scope_id, undo_id)
        target = operations.get(scope_id, undo.request["target_operation_id"])
        undo_steps = operations.steps(scope_id, undo_id)
        target_steps = operations.steps(scope_id, target.id)
        compensable = [s for s in target_steps if s.status in {"done", "compensated"}]
        compensated = [s for s in compensable if s.status == "compensated"]
        failed = any(s.status == "failed" for s in undo_steps)
        status = ("undone" if len(compensated) == len(compensable)
                  else "partially_undone" if compensated else target.status)
        evidence = (undo.result or {}).get("steps", {})
        with self.store.db.transaction():
            operations.mark_undo(scope_id, target.id, expected=target.status, status=status,
                                 undone_by=undo_id if status == "undone" else None)
            operations.finish_with_result(scope_id, undo_id, expected="running",
                                          status="failed" if failed else "completed",
                                          result={"steps": evidence})
            entries = target.request["items"]
            response = {
                "undo_operation": _operation_view(operations.get(scope_id, undo_id)),
                "operation": _operation_view(operations.get(scope_id, target.id)),
                "outcome": "partial_failure" if failed else "undone",
                "steps": [{
                    "ordinal": s.ordinal,
                    "review_item_id": entries[s.ordinal]["review_item_id"],
                    "job_id": entries[s.ordinal]["job_id"],
                    "action": entries[s.ordinal]["action"],
                    "kind": s.kind,
                    "status": {"done": "compensated"}.get(s.status, s.status),
                    "error_code": s.error_code,
                    "relative_path": s.destination_relative_path,
                } for s in undo_steps],
                "jobs": [self._job_view(scope_id, job_id)
                         for job_id in dict.fromkeys(e["job_id"] for e in entries)],
            }
            operations.finish_with_result(scope_id, undo_id,
                                          expected="failed" if failed else "completed",
                                          status="failed" if failed else "completed",
                                          result={"steps": evidence, "response": response})
        return response

    def _job_view(self, scope_id: str, job_id: str) -> dict:
        job = self.store.jobs.get(scope_id, job_id)
        statuses = [row["status"] for row in self.store.jobs.pages(scope_id, job_id)]
        totals = {s: statuses.count(s) for s in ("pending", "filed", "review", "skipped",
                                                 "blocked")}
        return {"id": job_id, "status": job.status,
                "page_accounting": {"page_count": len(statuses), **totals,
                                    "complete": bool(statuses) and totals["pending"] == 0}}

    # -- reads -------------------------------------------------------------------

    def page_text(self, scope_id: str, item_id: str, page_number: int) -> dict:
        """One physical source page's stored OCR text for a review item.

        ``page_number`` is the 1-based page of the source PDF and must belong to the
        item. Raw text leaves the machine's local state only through this response.
        """
        item = self.store.reviews.get(scope_id, item_id)
        if item is None:
            raise ReviewActionRejected("review_item_not_found")
        if page_number not in _item_pages(item):
            raise ReviewActionRejected("page_not_found")
        outcome = self.store.jobs.page_ocr(scope_id, item.job_id, page_number)
        if outcome is None:
            raise ReviewActionRejected("page_not_found")
        return {"review_item_id": item.id, "job_id": item.job_id,
                "page_number": page_number, "status": outcome.status,
                "text": outcome.text, "error_code": outcome.error_code}

    def list_items(self, scope_id: str, status: str = "pending",
                   job_id: str | None = None) -> dict:
        """``GET /api/v1/review-items``: text fields are document data, never markup.

        ``source_page_range`` is a compact label for the item's physical source pages
        and ``text_extraction`` aggregates their OCR states. No raw text is returned.
        """
        items = []
        for item in self.store.reviews.list(scope_id, status, job_id):
            job = self.store.jobs.get(scope_id, item.job_id)
            candidate = item.candidate
            pages = candidate.get("pages")
            numbers = _item_pages(item)
            items.append({
                "id": item.id,
                "job_id": item.job_id,
                "status": item.status,
                "source_name": job.source_name if job else None,
                "page_numbers": pages if isinstance(pages, list) else [],
                "source_page_range": page_range(numbers),
                "text_extraction": self._text_extraction(scope_id, item.job_id, numbers),
                "reason": _text(candidate.get("reason") or candidate.get("blocked_reason")),
                "near_duplicate_of": _text(candidate.get("near_duplicate_of")),
                "doc_type": _text(candidate.get("doc_type")),
                "period": _text(candidate.get("period")),
                "suggested_filename": item.suggested_filename,
                "suggested_relative_directory": item.suggested_relative_directory,
                "confidence": item.confidence,
                "actions": self._actions(scope_id, item),
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            })
        return {"archive_scope_id": scope_id, "status": status, "job_id": job_id,
                "items": items}

    def _text_extraction(self, scope_id: str, job_id: str, pages: list[int]) -> str:
        """``missing`` if any page is, else the shared status, else ``mixed``."""
        if not pages:
            return "missing"
        stored = {row["page_number"]: row["ocr_status"]
                  for row in self.store.jobs.pages(scope_id, job_id)}
        states = {stored.get(page, "missing") for page in pages}
        if "missing" in states:
            return "missing"
        return states.pop() if len(states) == 1 else "mixed"


def _verify_detached(trash: Path, path: Path, expected: str) -> None:
    """A detached file must still hold the created bytes; otherwise put it back, untouched."""
    if not trash.is_symlink() and trash.is_file() and raw_sha256(trash) == expected:
        return
    try:
        os.link(trash, path)  # never replaces a file that appeared at the original name
        trash.unlink()
    except FileExistsError:
        raise FilingError("target_conflict") from None
    raise FilingError("target_modified")


def _reattach(trash: Path, path: Path) -> None:
    """Best effort: return a still-detached file to its name without replacing anything."""
    if not os.path.lexists(trash):
        return
    try:
        os.link(trash, path)
        trash.unlink()
    except OSError:
        pass  # the step's own failure is reported; nothing is deleted


def _fingerprint(action: str, item_ids: list[str], destination: tuple[str, str] | None) -> str:
    """Identity of a client request, so one idempotency key cannot name two requests."""
    canonical = json.dumps([action, item_ids, destination], separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _check_key(key: object) -> None:
    if not isinstance(key, str) or not IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ReviewActionRejected("invalid_idempotency_key")


def _operation_view(operation) -> dict:
    return {"id": operation.id, "kind": operation.kind, "status": operation.status,
            "created_at": operation.created_at, "completed_at": operation.completed_at,
            "undone_at": operation.undone_at}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _item_pages(item: ReviewItem) -> list[int]:
    """The item's physical source page numbers, ascending; [] when it has none."""
    pages = item.candidate.get("pages")
    if not isinstance(pages, list):
        return []
    return sorted({p for p in pages if type(p) is int and p >= 1})


def page_range(pages: list[int]) -> str | None:
    """A compact label for physical source pages: ``"2"``, ``"3-4"``, ``"1, 3-5"``."""
    if not pages:
        return None
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][-1] + 1:
            runs[-1].append(page)
        else:
            runs.append([page])
    return ", ".join(str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs)


def validate_review_destination(root: Path, directory: object, filename: object) -> str:
    """A strict archive-relative ``directory/filename.pdf`` beneath ``root``.

    Rejects non-strings, empty/``.``/``..``/hidden components, absolute, home or drive
    paths, backslashes, control characters, the archive root itself, non-``.pdf``
    names and any existing symlinked or non-directory component.
    """
    if not isinstance(directory, str) or not isinstance(filename, str) \
            or not 1 <= len(directory) <= 1024 or not 1 <= len(filename) <= 255:
        raise UnsafeValueError("destination must be a directory and a filename")
    if any(ord(c) < 32 or ord(c) == 127 for c in directory + filename) or "\\" in directory \
            or ":" in directory.split("/")[0]:
        raise UnsafeValueError("destination contains invalid characters")
    parts = directory.split("/")
    for part in parts:
        if not part or part.startswith((".", "~")):
            raise UnsafeValueError("destination directory has an invalid component")
    name = safe_name(filename)
    if name.startswith((".", "~")) or not name.lower().endswith(".pdf"):
        raise UnsafeValueError("filed documents must be visible .pdf files")
    relative = f"{directory}/{name}"
    check_safe_json([directory, name])  # the journal's own storage guard (no secret-like names)
    path = confined(root, relative)
    current = Path(root)
    for part in parts:
        current = current / part
        if current.exists() and not current.is_dir():
            raise UnsafeValueError("destination directory component is not a directory")
    if path.exists() and not path.is_file():
        raise UnsafeValueError("destination name is not a file")
    return relative
