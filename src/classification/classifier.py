"""Classification: match DocumentCandidates to filing rules using LLM + rules.md."""
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
# Filename helpers
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
    if re.match(r"[A-Z][a-z]+\d{4}$", period):
        return period
    if re.match(r"20\d{2}$", period):
        return period
    m = re.search(r"(\d{1,2})[/\-](\d{4})", period)
    if m:
        try:
            month_int = int(m.group(1))
            if 1 <= month_int <= 12:
                month_name = calendar.month_name[month_int]
                return f"{month_name}{m.group(2)}"
        except (ValueError, IndexError):
            pass
    m = re.search(r"(20\d{2})", period)
    if m:
        return m.group(1)
    return re.sub(r"[^a-zA-Z0-9]", "", period)


def _sanitise_filename(filename: str) -> str:
    """Remove filesystem-unsafe characters from a filename."""
    # Remove unresolved template vars
    filename = re.sub(r"\{[^}]+\}", "", filename)
    # Remove unsafe chars
    filename = re.sub(r'[/\\:*?"<>|]', '', filename)
    # Ensure .pdf extension
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return filename


def _resolve_target_directory(file_to: str, config: dict, year: str = "") -> str:
    """Build the absolute target directory path."""
    archive_root = os.path.expanduser(config.get("archive_root", "~/ElectronicFiles"))
    resolved = file_to.format(year=year) if "{year}" in file_to else file_to
    return os.path.join(archive_root, resolved)


# ---------------------------------------------------------------------------
# Legacy rule-based matching (fast-path for high-confidence matches)
# ---------------------------------------------------------------------------

def _match_field(rule_value, candidate_value: str | None) -> bool:
    if rule_value is None:
        return True
    if candidate_value is None:
        return False
    candidate_lower = candidate_value.lower()
    if isinstance(rule_value, list):
        return any(str(v).lower() in candidate_lower or candidate_lower in str(v).lower()
                    for v in rule_value)
    return str(rule_value).lower() in candidate_lower or candidate_lower in str(rule_value).lower()


def _match_hints(rule_hints: list[str] | None, text_pool: str) -> bool:
    if not rule_hints:
        return True
    text_lower = text_pool.lower()
    return any(hint.lower() in text_lower for hint in rule_hints)


def _match_rule(rule: dict, candidate: DocumentCandidate) -> bool:
    """Return True if a YAML filing rule matches a DocumentCandidate."""
    match = rule.get("match", {})

    if "institution" in match:
        if not _match_field(match["institution"], candidate.institution):
            return False
    if "doc_type" in match:
        if not _match_field(match["doc_type"], candidate.doc_type):
            return False

    # Build text pool for hint matching
    signal_texts = []
    for key in ("institutions", "accounts", "periods", "doc_types"):
        vals = candidate.raw_signals.get(key, [])
        signal_texts.extend(str(v) for v in vals if v is not None)
    raw_texts = candidate.raw_signals.get("raw_texts", [])
    for rt in raw_texts:
        if rt:
            signal_texts.append(rt[:1000])
    text_pool = " ".join(signal_texts)

    if "account_hints" in match:
        if not _match_hints(match["account_hints"], text_pool):
            if candidate.account:
                if not any(h.lower() in candidate.account.lower()
                           for h in match["account_hints"]):
                    return False
            else:
                return False
    if "entity_hints" in match:
        if not _match_hints(match["entity_hints"], text_pool):
            return False
    return True


def _generate_filename(template: str, candidate: DocumentCandidate) -> str:
    """Fill a filename template with candidate fields."""
    raw_period = candidate.period
    if raw_period and raw_period.lower() in ("null", "none", "unknown"):
        raw_period = None
    period = _normalise_period(raw_period)
    year = _extract_year(raw_period)
    doc_type = (candidate.doc_type or "").replace("_", " ").title().replace(" ", "")
    person = ""
    filename = template.format(period=period, year=year, doc_type=doc_type, person=person)
    return re.sub(r'[/\\:*?"<>|]', '', filename)


# ---------------------------------------------------------------------------
# Main classification — LLM-primary with rule-based fast-path
# ---------------------------------------------------------------------------

def classify_candidates(
    candidates: list[DocumentCandidate], config: dict
) -> list[FilingDecision]:
    """Return a FilingDecision for each candidate.

    Strategy:
    1. Try rule-based fast-path (YAML rules) — if a rule matches with high
       signal clarity, use it directly without an LLM call.
    2. Otherwise, use LLM classification with rules.md as context.
    3. Fall back to unmatched if both fail.
    """
    from src.config.rules_manager import load_rules_md

    filing_rules = config.get("filing_rules", [])
    threshold = config.get("confidence_threshold", 0.75)
    rules_md_content = load_rules_md(config)
    decisions: list[FilingDecision] = []

    for candidate in candidates:
        decision = None

        # Fast-path: try rule-based matching first
        if filing_rules:
            decision = _try_rule_match(candidate, filing_rules, config, threshold)

        # LLM classification (primary path when rules.md exists)
        if decision is None and rules_md_content:
            decision = _llm_classify_with_rules_md(
                candidate, rules_md_content, config, threshold
            )

        # Legacy LLM fallback (when no rules.md but YAML rules exist)
        if decision is None and filing_rules:
            decision = _llm_classify_legacy(candidate, config, threshold)

        # Unmatched fallback
        if decision is None:
            decision = FilingDecision(
                candidate=candidate,
                filename=f"Unmatched_{candidate.institution or 'Unknown'}_pages{'_'.join(str(p) for p in candidate.pages)}.pdf",
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


def _try_rule_match(
    candidate: DocumentCandidate,
    filing_rules: list[dict],
    config: dict,
    threshold: float,
) -> FilingDecision | None:
    """Try rule-based matching. Returns a decision only for high-confidence matches."""
    for rule in filing_rules:
        if _match_rule(rule, candidate):
            filename = _generate_filename(rule["filename_template"], candidate)
            file_to = rule["file_to"].format(year=_extract_year(candidate.period))
            target_dir = _resolve_target_directory(file_to, config)
            confidence = min(candidate.clustering_confidence + 0.15, 1.0)

            return FilingDecision(
                candidate=candidate,
                filename=filename,
                target_directory=target_dir,
                rule_matched=rule["id"],
                confidence=round(confidence, 3),
                auto_file=confidence >= threshold,
                notes=None,
            )
    return None


# ---------------------------------------------------------------------------
# LLM classification using rules.md
# ---------------------------------------------------------------------------

def _llm_classify_with_rules_md(
    candidate: DocumentCandidate,
    rules_md: str,
    config: dict,
    threshold: float,
) -> FilingDecision | None:
    """Use LLM with rules.md context to classify a document."""
    from src.llm.client import chat_json
    from src.llm.prompts import build_rules_md_classification_prompt

    raw_text_preview = ""
    raw_texts = candidate.raw_signals.get("raw_texts", [])
    if raw_texts:
        raw_text_preview = raw_texts[0][:500]

    document_summary = {
        "pages": candidate.pages,
        "institution": candidate.institution,
        "doc_type": candidate.doc_type,
        "period": candidate.period,
        "account": candidate.account,
        "raw_text_preview": raw_text_preview,
    }

    prompt = build_rules_md_classification_prompt(
        document_summary,
        rules_md,
        config.get("entities", []),
        config.get("family", []),
        config.get("user", {}),
    )

    try:
        result = chat_json(prompt, config=config)
    except Exception:
        logger.exception("LLM classification with rules.md failed")
        return None

    suggested_filename = result.get("suggested_filename", "")
    suggested_dir = result.get("suggested_directory", "")
    confidence = result.get("confidence", 0.5)
    rule_name = result.get("rule_matched", "")
    reasoning = result.get("reasoning", "")

    if not suggested_filename or not suggested_dir:
        return None

    # Use the period from LLM if candidate doesn't have one
    period = candidate.period or result.get("period")
    year = _extract_year(period)

    filename = _sanitise_filename(suggested_filename)
    target_dir = _resolve_target_directory(suggested_dir, config, year=year)

    matched_label = f"rules_md:{rule_name}" if rule_name else "llm_suggested"

    return FilingDecision(
        candidate=candidate,
        filename=filename,
        target_directory=target_dir,
        rule_matched=matched_label,
        confidence=round(confidence, 3),
        auto_file=confidence >= threshold,
        notes=f"AI classification: {reasoning}",
    )


# ---------------------------------------------------------------------------
# Legacy LLM fallback (YAML-based prompt, for backwards compatibility)
# ---------------------------------------------------------------------------

def _llm_classify_legacy(
    candidate: DocumentCandidate,
    config: dict,
    threshold: float,
) -> FilingDecision | None:
    """Use LLM with YAML rules context (legacy path)."""
    from src.llm.client import chat_json
    from src.llm.prompts import build_classification_prompt

    raw_text_preview = ""
    raw_texts = candidate.raw_signals.get("raw_texts", [])
    if raw_texts:
        raw_text_preview = raw_texts[0][:500]

    document_summary = {
        "pages": candidate.pages,
        "institution": candidate.institution,
        "doc_type": candidate.doc_type,
        "period": candidate.period,
        "account": candidate.account,
        "raw_text_preview": raw_text_preview,
    }

    prompt = build_classification_prompt(
        document_summary,
        config.get("filing_rules", []),
        config.get("entities", []),
        config.get("family", []),
        config.get("user", {}),
    )

    try:
        result = chat_json(prompt, config=config)
    except Exception:
        logger.exception("Legacy LLM classification failed")
        return None

    # If LLM matched an existing rule, validate
    rule_id = result.get("rule_matched")
    if rule_id:
        filing_rules = config.get("filing_rules", [])
        matched = next((r for r in filing_rules if r["id"] == rule_id), None)
        if matched and _match_rule(matched, candidate):
            filename = _generate_filename(matched["filename_template"], candidate)
            file_to = matched["file_to"].format(
                year=_extract_year(candidate.period or result.get("period"))
            )
            target_dir = _resolve_target_directory(file_to, config)
            confidence = result.get("confidence", 0.6)
            return FilingDecision(
                candidate=candidate,
                filename=filename,
                target_directory=target_dir,
                rule_matched=rule_id,
                confidence=round(confidence, 3),
                auto_file=confidence >= threshold,
                notes=f"LLM matched rule: {result.get('reasoning', '')}",
            )

    # LLM suggested a new filing location
    suggested_filename = result.get("suggested_filename", "")
    suggested_dir = result.get("suggested_directory", "_LLMSuggested")
    confidence = result.get("confidence", 0.5)

    if not suggested_filename:
        return None

    filename = _sanitise_filename(suggested_filename)
    target_dir = _resolve_target_directory(suggested_dir, config)

    return FilingDecision(
        candidate=candidate,
        filename=filename,
        target_directory=target_dir,
        rule_matched="llm_suggested",
        confidence=round(confidence, 3),
        auto_file=confidence >= threshold,
        notes=f"LLM classification: {result.get('reasoning', '')}",
    )
