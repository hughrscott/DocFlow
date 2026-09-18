"""Shared privacy types: sensitive categories, detections, and typed failures."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SensitiveType(str, Enum):
    PERSON = "PERSON"
    ENTITY = "ENTITY"
    ADDRESS = "ADDRESS"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    ACCOUNT = "ACCOUNT"
    PATH = "PATH"
    RULE = "RULE"
    DATE = "DATE"
    AMOUNT = "AMOUNT"
    YEAR = "YEAR"
    GOVERNMENT_ID = "GOVERNMENT_ID"
    SECRET = "SECRET"


@dataclass(frozen=True)
class Detection:
    """A sensitive span in normalized text. Never carries the matched value."""

    start: int
    end: int
    kind: SensitiveType
    source: str = "pattern"
    flags: frozenset[str] = frozenset()


class NoModelResult(Exception):
    """No accepted model result. Messages carry codes/categories, never values."""

    code = "no_model_result"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.code)
        if code:
            self.code = code
        self.review_item_id: str | None = None


class PrivacyBlocked(NoModelResult):
    """Pre-egress failure: nothing was sent."""

    code = "privacy_blocked"


class DetectorError(PrivacyBlocked):
    code = "detector_failure"


class UnresolvedSensitiveError(PrivacyBlocked):
    code = "unresolved_sensitive"


class SerializationError(PrivacyBlocked):
    code = "serialization_failure"


class EgressValidationError(PrivacyBlocked):
    code = "egress_validation_failure"


class GatewayBypassError(PrivacyBlocked):
    """A model call was attempted without a request sealed by CloudPromptGateway."""

    code = "gateway_bypass"
