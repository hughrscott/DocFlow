"""
PII Protection Service for DocFlow.

Detects and redacts PII (SSNs, full account numbers, DOBs) from text
before sending to cloud LLM providers. Local providers (Ollama) bypass
redaction entirely since data stays on the user's machine.
"""

import re
import logging
from typing import List, Tuple
from config.settings import settings

logger = logging.getLogger(__name__)

# ── Regex patterns for common PII ────────────────────────────────────────

# SSN: 123-45-6789 or 123456789 (9 digits with optional dashes/spaces)
SSN_PATTERN = re.compile(
    r"\b(\d{3})[-\s]?(\d{2})[-\s]?(\d{4})\b"
)

# Full account/card numbers: sequences of 8-19 digits (with optional separators)
ACCOUNT_NUMBER_PATTERN = re.compile(
    r"\b(\d[-\s]?){8,19}\b"
)

# Date of birth patterns: MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD
DOB_PATTERN = re.compile(
    r"\b(?:"
    r"(?:0[1-9]|1[0-2])[/\-](?:0[1-9]|[12]\d|3[01])[/\-](?:19|20)\d{2}"
    r"|"
    r"(?:19|20)\d{2}[/\-](?:0[1-9]|1[0-2])[/\-](?:0[1-9]|[12]\d|3[01])"
    r")\b"
)

# Cloud provider names that require PII protection
CLOUD_PROVIDERS = {"claude", "openai"}


def should_redact(provider_name: str) -> bool:
    """
    Determine whether PII should be redacted for this provider.

    Args:
        provider_name: LLM provider name (e.g. "claude", "ollama")

    Returns:
        True if redaction is needed
    """
    mode = settings.pii_protection_mode.lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    # auto: redact for cloud providers only
    return provider_name.lower() in CLOUD_PROVIDERS


def redact_text(text: str) -> Tuple[str, List[dict]]:
    """
    Detect and replace PII in *text* with ``[REDACTED]`` placeholders.

    Returns the redacted string and a list of redaction records (for audit
    logging).  Each record is a dict with ``redaction_type`` and
    ``count`` keys.

    Args:
        text: Raw text that may contain PII

    Returns:
        (redacted_text, redaction_records)
    """
    if not text:
        return text, []

    records: List[dict] = []
    redacted = text

    # SSN
    ssn_matches = list(SSN_PATTERN.finditer(redacted))
    if ssn_matches:
        # Only treat as SSN if it looks like a real SSN (not a date, phone, etc.)
        real_ssn_count = 0
        for m in ssn_matches:
            full = m.group(0).replace("-", "").replace(" ", "")
            # Basic sanity: area number (first 3) not 000/666/9xx
            area = int(full[:3])
            if area == 0 or area == 666 or area >= 900:
                continue
            real_ssn_count += 1
        if real_ssn_count:
            redacted = SSN_PATTERN.sub("[REDACTED-SSN]", redacted)
            records.append({"redaction_type": "ssn", "count": real_ssn_count})

    # Full account numbers (only long digit sequences)
    acct_matches = list(ACCOUNT_NUMBER_PATTERN.finditer(redacted))
    acct_count = 0
    for m in acct_matches:
        digits_only = re.sub(r"[\s\-]", "", m.group(0))
        if len(digits_only) >= 10:
            acct_count += 1
    if acct_count:
        def _mask_account(m: re.Match) -> str:
            digits_only = re.sub(r"[\s\-]", "", m.group(0))
            if len(digits_only) >= 10:
                return "[REDACTED-ACCT]"
            return m.group(0)
        redacted = ACCOUNT_NUMBER_PATTERN.sub(_mask_account, redacted)
        records.append({"redaction_type": "account_number", "count": acct_count})

    # DOB
    dob_matches = list(DOB_PATTERN.finditer(redacted))
    if dob_matches:
        redacted = DOB_PATTERN.sub("[REDACTED-DOB]", redacted)
        records.append({"redaction_type": "dob", "count": len(dob_matches)})

    if records:
        total = sum(r["count"] for r in records)
        logger.info(f"Redacted {total} PII item(s) from text")

    return redacted, records
