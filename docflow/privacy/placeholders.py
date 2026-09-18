"""Cryptographically random typed placeholders with a memory-only job lookup."""
from __future__ import annotations

import re
import secrets

from docflow.privacy.detector import SensitiveDetector, normalize_text
from docflow.privacy.policy import Action, PrivacyPolicy
from docflow.privacy.types import Detection, SensitiveType, UnresolvedSensitiveError

# Consonants only: tokens contain no digits (never look like dates/accounts) and
# cannot spell words.
TOKEN_ALPHABET = "bcdfghjkmnpqrstvwxz"
TOKEN_LENGTH = 12
PLACEHOLDER_TYPES = tuple(t for t in SensitiveType if t not in (
    SensitiveType.AMOUNT, SensitiveType.YEAR, SensitiveType.GOVERNMENT_ID, SensitiveType.SECRET,
))
_TYPES = "|".join(t.value for t in PLACEHOLDER_TYPES)
PLACEHOLDER_RE = re.compile(rf"(?:{_TYPES})_[{TOKEN_ALPHABET}]{{{TOKEN_LENGTH}}}")


def _normalize_key(value: str) -> str:
    return " ".join(value.split()).casefold()


class PlaceholderMap:
    """Per-job lookup between sensitive values and opaque tokens.

    The lookup lives only in process memory: it refuses pickling/copying and its
    representation never includes values or tokens.
    """

    __slots__ = ("_forward", "_reverse")

    def __init__(self) -> None:
        self._forward: dict[tuple[SensitiveType, str], str] = {}
        self._reverse: dict[str, tuple[SensitiveType, str]] = {}

    def token_for(self, kind: SensitiveType, value: str) -> str:
        key = (kind, _normalize_key(value))
        token = self._forward.get(key)
        if token is None:
            while token is None or token in self._reverse:
                suffix = "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))
                token = f"{kind.value}_{suffix}"
            self._forward[key] = token
            self._reverse[token] = (kind, value)
        return token

    def type_of(self, token: str) -> SensitiveType | None:
        entry = self._reverse.get(token)
        return entry[0] if entry else None

    def rehydrate(self, text: str) -> str:
        return PLACEHOLDER_RE.sub(
            lambda m: self._reverse[m.group(0)][1] if m.group(0) in self._reverse else m.group(0),
            text,
        )

    def __repr__(self) -> str:
        return f"<PlaceholderMap entries={len(self._reverse)}>"

    __str__ = __repr__

    def __reduce_ex__(self, protocol: object) -> object:
        raise TypeError("placeholder lookups are memory-only and cannot be serialized")

    def __copy__(self) -> PlaceholderMap:
        raise TypeError("placeholder lookups cannot be copied")

    def __deepcopy__(self, memo: dict) -> PlaceholderMap:
        raise TypeError("placeholder lookups cannot be copied")


# Most sensitive first: overlapping spans merge into the highest-priority type.
_PRIORITY = (
    SensitiveType.PATH, SensitiveType.EMAIL, SensitiveType.ADDRESS, SensitiveType.PHONE,
    SensitiveType.ACCOUNT, SensitiveType.RULE, SensitiveType.ENTITY, SensitiveType.PERSON,
    SensitiveType.DATE,
)


class Pseudonymizer:
    """Detect, apply policy, and replace sensitive values for one job."""

    def __init__(
        self,
        detector: SensitiveDetector,
        policy: PrivacyPolicy | None = None,
        lookup: PlaceholderMap | None = None,
    ) -> None:
        self.detector = detector
        self.policy = policy or PrivacyPolicy()
        self.lookup = lookup or PlaceholderMap()

    def text(self, value: str) -> str:
        text = normalize_text(value)
        return self._replace(text, self._plan(text))

    def has_unresolved(self, value: str) -> bool:
        """True when already-sanitized text still contains a value policy would replace."""
        return bool(self._plan(normalize_text(value)))

    def _plan(self, text: str) -> list[tuple[int, int, SensitiveType]]:
        protected = [m.span() for m in PLACEHOLDER_RE.finditer(text)
                     if self.lookup.type_of(m.group(0)) is not None]
        kept: list[tuple[int, int]] = []
        digit_runs: list[Detection] = []
        spans: list[tuple[int, int, SensitiveType]] = []
        for detection in self.detector.detect(text):
            if any(detection.start < end and start < detection.end for start, end in protected):
                continue
            if detection.source == "digit_run":
                digit_runs.append(detection)
                continue
            decision = self.policy.decide(detection)
            if decision.action is Action.BLOCK:
                raise UnresolvedSensitiveError(
                    f"unresolved sensitive content of type {decision.kind.value}"
                )
            if decision.action is Action.REPLACE:
                spans.append((detection.start, detection.end, decision.kind))
            else:
                kept.append((detection.start, detection.end))
        for run in digit_runs:
            if not any(start <= run.start and run.end <= end for start, end in kept):
                spans.append((run.start, run.end, SensitiveType.ACCOUNT))
        return _merge(spans)

    def _replace(self, text: str, spans: list[tuple[int, int, SensitiveType]]) -> str:
        for start, end, kind in reversed(spans):
            text = text[:start] + self.lookup.token_for(kind, text[start:end]) + text[end:]
        return text


def _merge(spans: list[tuple[int, int, SensitiveType]]) -> list[tuple[int, int, SensitiveType]]:
    merged: list[tuple[int, int, SensitiveType]] = []
    for start, end, kind in sorted(spans):
        if merged and start < merged[-1][1]:
            prev_start, prev_end, prev_kind = merged[-1]
            best = min(prev_kind, kind, key=_PRIORITY.index)
            merged[-1] = (prev_start, max(prev_end, end), best)
        else:
            merged.append((start, end, kind))
    return merged
