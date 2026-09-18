"""Classification: match DocumentCandidates to filing rules using LLM + rules.md."""
from __future__ import annotations

import calendar
import dataclasses
import logging
import os
import re
from dataclasses import dataclass

from docflow.clustering.clusterer import DocumentCandidate

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
    archive_root = os.path.expanduser(config.get("archive_root", "~/DocFlowExample/archive"))
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
    candidates: list[DocumentCandidate], config: dict, gateway=None
) -> list[FilingDecision]:
    """Return a FilingDecision for each candidate.

    Strategy:
    1. Try rule-based fast-path (YAML rules) — if a rule matches with high
       signal clarity, use it directly without an LLM call.
    2. Otherwise, use LLM classification with rules.md as context.
    3. Fall back to unmatched if both fail.

    Model calls go only through the job's CloudPromptGateway.
    """
    from docflow.config.rules_manager import load_rules_md
    from docflow.llm.gateway import CloudPromptGateway

    filing_rules = config.get("filing_rules", [])
    threshold = config.get("confidence_threshold", 0.75)
    rules_md_content = load_rules_md(config)
    if gateway is None:
        gateway = CloudPromptGateway(config, rules_md=rules_md_content)
    decisions: list[FilingDecision] = []

    for candidate in candidates:
        decision = None

        # Fast-path: try rule-based matching first
        if filing_rules:
            decision = _try_rule_match(candidate, filing_rules, config, threshold)

        # LLM classification (primary path when rules.md exists)
        if decision is None and rules_md_content:
            decision = _llm_classify_with_rules_md(
                candidate, rules_md_content, config, threshold, gateway
            )

        # Legacy LLM fallback (when no rules.md but YAML rules exist)
        if decision is None and filing_rules:
            decision = _llm_classify_legacy(candidate, config, threshold, gateway)

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
# Model classification through CloudPromptGateway
# ---------------------------------------------------------------------------

def _model_classify(candidate: DocumentCandidate, rules: list, gateway):
    """Return a validated Classification, or None when no model result is accepted."""
    from docflow.privacy.types import NoModelResult

    raw_texts = candidate.raw_signals.get("raw_texts", [])
    document = {
        "pages": candidate.pages,
        "institution": candidate.institution,
        "doc_type": candidate.doc_type,
        "period": candidate.period,
        "account": candidate.account,
        "text_preview": raw_texts[0][:500] if raw_texts else "",
    }
    try:
        return gateway.classify(document, rules)
    except NoModelResult as exc:
        logger.info("Model classification unavailable (%s)", exc.code)
        return None


def _rule_decision(
    candidate: DocumentCandidate, result, config: dict, threshold: float, label: str, notes: str,
) -> FilingDecision | None:
    """Build filename and destination locally from a matched rule."""
    rule = result.rule
    if not rule.file_to or not rule.filename_template:
        return None
    period = candidate.period or result.period
    year = _extract_year(period)
    try:
        filename = _generate_filename(rule.filename_template,
                                      dataclasses.replace(candidate, period=period))
        target_dir = _resolve_target_directory(rule.file_to, config, year=year)
    except (KeyError, IndexError, ValueError):
        return None
    confidence = round(result.confidence, 3)
    return FilingDecision(
        candidate=candidate,
        filename=filename,
        target_directory=target_dir,
        rule_matched=label,
        confidence=confidence,
        auto_file=confidence >= threshold,
        notes=notes,
    )


def _suggested_decision(
    candidate: DocumentCandidate, result, config: dict, threshold: float, label: str, notes: str,
    default_directory: str | None = None,
) -> FilingDecision | None:
    """Use a validated, archive-confined model suggestion for a new location."""
    directory = result.relative_directory or default_directory
    if not result.filename or not directory:
        return None
    confidence = round(result.confidence, 3)
    return FilingDecision(
        candidate=candidate,
        filename=result.filename,
        target_directory=_resolve_target_directory(directory, config),
        rule_matched=label,
        confidence=confidence,
        auto_file=confidence >= threshold,
        notes=notes,
    )


def _llm_classify_with_rules_md(
    candidate: DocumentCandidate,
    rules_md: str,
    config: dict,
    threshold: float,
    gateway=None,
) -> FilingDecision | None:
    """Classify with the model using rules.md rules (sent only as opaque rule tokens)."""
    from docflow.config.rules_manager import parse_rules_md
    from docflow.llm.gateway import CloudPromptGateway, LocalRule

    gateway = gateway or CloudPromptGateway(config, rules_md=rules_md)
    rules = [LocalRule.from_rules_md(r) for r in parse_rules_md(rules_md)]
    result = _model_classify(candidate, rules, gateway)
    if result is None:
        return None
    notes = f"AI classification: {result.reasoning}"
    if result.rule is not None:
        return _rule_decision(candidate, result, config, threshold,
                              f"rules_md:{result.rule.key}", notes)
    return _suggested_decision(candidate, result, config, threshold, "llm_suggested", notes)


# ---------------------------------------------------------------------------
# Legacy model fallback (YAML rules, for backwards compatibility)
# ---------------------------------------------------------------------------

def _llm_classify_legacy(
    candidate: DocumentCandidate,
    config: dict,
    threshold: float,
    gateway=None,
) -> FilingDecision | None:
    """Classify with the model using YAML filing rules (sent only as opaque rule tokens)."""
    from docflow.llm.gateway import CloudPromptGateway, LocalRule

    gateway = gateway or CloudPromptGateway(config)
    rules = [LocalRule.from_yaml(r) for r in config.get("filing_rules", [])]
    result = _model_classify(candidate, rules, gateway)
    if result is None:
        return None

    # If the model matched an existing rule, validate it locally
    if result.rule is not None and _match_rule(result.rule.source, candidate):
        decision = _rule_decision(candidate, result, config, threshold, result.rule.key,
                                  f"LLM matched rule: {result.reasoning}")
        if decision is not None:
            return decision

    return _suggested_decision(candidate, result, config, threshold, "llm_suggested",
                               f"LLM classification: {result.reasoning}",
                               default_directory="_LLMSuggested")
