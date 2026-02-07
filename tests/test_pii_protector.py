"""Tests for PII protection service."""

from services.pii_protector import redact_text, should_redact


def test_should_redact_auto_mode():
    """Auto mode redacts cloud providers, skips local."""
    from config.settings import settings
    original = settings.pii_protection_mode
    try:
        settings.pii_protection_mode = "auto"
        assert should_redact("claude") is True
        assert should_redact("openai") is True
        assert should_redact("ollama") is False
    finally:
        settings.pii_protection_mode = original


def test_should_redact_always_mode():
    from config.settings import settings
    original = settings.pii_protection_mode
    try:
        settings.pii_protection_mode = "always"
        assert should_redact("ollama") is True
        assert should_redact("claude") is True
    finally:
        settings.pii_protection_mode = original


def test_should_redact_never_mode():
    from config.settings import settings
    original = settings.pii_protection_mode
    try:
        settings.pii_protection_mode = "never"
        assert should_redact("claude") is False
        assert should_redact("ollama") is False
    finally:
        settings.pii_protection_mode = original


def test_redact_ssn():
    text = "My SSN is 123-45-6789 and another is 234 56 7890"
    result, records = redact_text(text)
    assert "[REDACTED-SSN]" in result
    assert "123-45-6789" not in result
    assert any(r["redaction_type"] == "ssn" for r in records)


def test_redact_account_number():
    text = "Account number: 1234567890123456"
    result, records = redact_text(text)
    assert "[REDACTED-ACCT]" in result
    assert "1234567890123456" not in result
    assert any(r["redaction_type"] == "account_number" for r in records)


def test_redact_dob():
    text = "Date of birth: 01/15/1990 and also 1985-03-22"
    result, records = redact_text(text)
    assert "[REDACTED-DOB]" in result
    assert "01/15/1990" not in result
    assert any(r["redaction_type"] == "dob" for r in records)


def test_redact_empty_text():
    result, records = redact_text("")
    assert result == ""
    assert records == []


def test_redact_no_pii():
    text = "Hello this is a normal document with no sensitive info."
    result, records = redact_text(text)
    assert result == text
    assert records == []


def test_short_numbers_not_redacted():
    """Short digit sequences (like dates, zip codes) should not be treated as account numbers."""
    text = "Invoice #12345 dated 2025-01"
    result, records = redact_text(text)
    # 12345 is only 5 digits — should not be masked as account
    assert "12345" in result
