"""Classification: match DocumentCandidates to filing rules."""
from __future__ import annotations

import calendar
import logging
import os
import re
from dataclasses import dataclass

from src.clustering.clusterer import DocumentCandidate

logger = logging.getLogger(__name__)


@dataclass
class FilingDecision:
    candidate: DocumentCandidate
    filename: str
    target_directory: str
    rule_matched: str
    confidence: float          # 0.0–1.0
    auto_file: bool
    notes: str | None


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

def _match_field(rule_value, candidate_value: str | None) -> bool:
    """Check if a candidate's field matches a rule's expected value(s).

    rule_value can be a string or list of strings (any match counts).
    candidate_value is the extracted signal (lowercased for comparison).
    If rule_value is not specified (None), it's a wildcard — always matches.
    """
    if rule_value is None:
        return True
    if candidate_value is None:
        return False

    candidate_lower = candidate_value.lower()

    if isinstance(rule_value, list):
        return any(str(v).lower() in candidate_lower or candidate_lower in str(v).lower()
                    for v in rule_value)
    return str(rule_value).lower() in candidate_lower or candidate_lower in str(rule_value).lower()


def _match_hints(rule_hints: list[str] | None, raw_signals: dict, text_pool: str) -> bool:
    """Check if any of the rule's hint strings appear in the raw signals or text."""
    if not rule_hints:
        return True
    text_lower = text_pool.lower()
    return any(hint.lower() in text_lower for hint in rule_hints)


def _match_rule(rule: dict, candidate: DocumentCandidate) -> bool:
    """Return True if a filing rule matches a DocumentCandidate."""
    match = rule.get("match", {})

    # Institution match
    if "institution" in match:
        if not _match_field(match["institution"], candidate.institution):
            return False

    # Doc type match
    if "doc_type" in match:
        if not _match_field(match["doc_type"], candidate.doc_type):
            return False

    # Build a text pool from raw signals for hint matching
    signal_texts = []
    for key in ("institutions", "accounts", "periods", "doc_types"):
        vals = candidate.raw_signals.get(key, [])
        signal_texts.extend(str(v) for v in vals if v is not None)
    text_pool = " ".join(signal_texts)

    # Account hints
    if "account_hints" in match:
        if not _match_hints(match["account_hints"], candidate.raw_signals, text_pool):
            # Also check the candidate's account field directly
            if candidate.account:
                if not any(h.lower() in candidate.account.lower()
                           for h in match["account_hints"]):
                    return False
            else:
                return False

    # Entity hints
    if "entity_hints" in match:
        if not _match_hints(match["entity_hints"], candidate.raw_signals, text_pool):
            return False

    return True


# ---------------------------------------------------------------------------
# Filename generation
# ---------------------------------------------------------------------------

def _extract_year(period: str | None) -> str:
    """Pull a 4-digit year from a period string."""
    if not period:
        return "Unknown"
    m = re.search(r"(20\d{2})", period)
    return m.group(1) if m else "Unknown"


def _normalise_period(period: str | None) -> str:
    """Ensure period is in 'MonthYYYY' format for filenames."""
    if not period:
        return ""
    # Already in good shape like "February2026"
    if re.match(r"[A-Z][a-z]+\d{4}", period):
        return period
    # Try to parse
    m = re.search(r"(\d{1,2})[/\-](\d{4})", period)
    if m:
        try:
            month_name = calendar.month_name[int(m.group(1))]
            return f"{month_name}{m.group(2)}"
        except (ValueError, IndexError):
            pass
    return period


def _generate_filename(template: str, candidate: DocumentCandidate) -> str:
    """Fill a filename template with candidate fields."""
    period = _normalise_period(candidate.period)
    year = _extract_year(candidate.period)

    # Doc type — capitalise for display
    doc_type = (candidate.doc_type or "").replace("_", " ").title().replace(" ", "")

    # Person — not yet extracted in Phase 1, leave empty
    person = ""

    filename = template.format(
        period=period,
        year=year,
        doc_type=doc_type,
        person=person,
    )
    return filename


def _resolve_target_directory(file_to: str, config: dict) -> str:
    """Build the absolute target directory path."""
    archive_root = os.path.expanduser(config.get("archive_root", "~/ElectronicFiles"))

    # Handle {year} in file_to path
    resolved = file_to
    # We can't know the year here without the candidate, but it's already
    # been substituted if needed in the filename. For directory paths with
    # {year}, we handle it at call time.

    return os.path.join(archive_root, resolved)


# ---------------------------------------------------------------------------
# Main classification
# ---------------------------------------------------------------------------

def classify_candidates(
    candidates: list[DocumentCandidate], config: dict
) -> list[FilingDecision]:
    """Return a FilingDecision for each candidate."""
    filing_rules = config.get("filing_rules", [])
    threshold = config.get("confidence_threshold", 0.75)
    decisions: list[FilingDecision] = []

    for candidate in candidates:
        matched_rule = None
        for rule in filing_rules:
            if _match_rule(rule, candidate):
                matched_rule = rule
                break

        if matched_rule:
            filename = _generate_filename(
                matched_rule["filename_template"], candidate
            )
            # Resolve {year} in file_to if present
            file_to = matched_rule["file_to"].format(
                year=_extract_year(candidate.period)
            )
            target_dir = _resolve_target_directory(file_to, config)
            rule_id = matched_rule["id"]

            # Confidence: start with clustering confidence,
            # boost for rule match
            confidence = min(candidate.clustering_confidence + 0.15, 1.0)

            decision = FilingDecision(
                candidate=candidate,
                filename=filename,
                target_directory=target_dir,
                rule_matched=rule_id,
                confidence=round(confidence, 3),
                auto_file=confidence >= threshold,
                notes=None,
            )
        else:
            # No rule matched
            decision = FilingDecision(
                candidate=candidate,
                filename=f"Unmatched_{candidate.institution}_pages{'_'.join(str(p) for p in candidate.pages)}.pdf",
                target_directory=_resolve_target_directory("_Unmatched", config),
                rule_matched="none",
                confidence=0.0,
                auto_file=False,
                notes=f"No filing rule matched for institution={candidate.institution}, "
                      f"doc_type={candidate.doc_type}",
            )

        logger.info(
            "Classified: %s → %s/%s (rule=%s, confidence=%.2f, auto=%s)",
            candidate.institution, decision.target_directory,
            decision.filename, decision.rule_matched,
            decision.confidence, decision.auto_file,
        )
        decisions.append(decision)

    return decisions
