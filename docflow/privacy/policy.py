"""Type-aware policy deciding which detected values are replaced."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from docflow.privacy.types import Detection, SensitiveType


class Action(str, Enum):
    KEEP = "keep"
    REPLACE = "replace"
    BLOCK = "block"


@dataclass(frozen=True)
class Decision:
    action: Action
    kind: SensitiveType


class PrivacyPolicy:
    """Replace identifiers; keep dates/amounts/years only when unambiguous.

    Values in account context are masked as accounts; birth dates are masked.
    Government identifiers and secrets are never sent, even pseudonymized.
    """

    def decide(self, detection: Detection) -> Decision:
        kind, flags = detection.kind, detection.flags
        if kind in (SensitiveType.GOVERNMENT_ID, SensitiveType.SECRET):
            return Decision(Action.BLOCK, kind)
        if kind in (SensitiveType.YEAR, SensitiveType.AMOUNT):
            if "account_context" in flags:
                return Decision(Action.REPLACE, SensitiveType.ACCOUNT)
            return Decision(Action.KEEP, kind)
        if kind is SensitiveType.DATE:
            if "dob" in flags:
                return Decision(Action.REPLACE, SensitiveType.DATE)
            if "account_context" in flags and "numeric" in flags:
                return Decision(Action.REPLACE, SensitiveType.ACCOUNT)
            return Decision(Action.KEEP, kind)
        return Decision(Action.REPLACE, kind)
