"""Strict model-response schemas. Model output is untrusted data."""
from __future__ import annotations

import json
import re
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from docflow.privacy.types import NoModelResult


class InvalidModelOutput(NoModelResult):
    """The model reply was rejected; no result is accepted."""

    code = "invalid_model_output"

    def __init__(self, reason: str) -> None:
        super().__init__(f"model output rejected: {reason}")
        self.reason = reason


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
ShortText = Annotated[str, StringConstraints(max_length=500)]
DocType = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Period = Annotated[str, StringConstraints(pattern=r"^(?:[A-Z][a-z]{2,8})?(?:19|20)\d{2}$")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class ClusterDocument(_Strict):
    pages: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)
    doc_type: DocType | None = None
    period: Period | None = None
    institution: Label | None = None
    confidence: Confidence
    reasoning: ShortText = ""


class ClusteringResponse(_Strict):
    documents: list[ClusterDocument] = Field(min_length=1, max_length=1000)


class ClassificationResponse(_Strict):
    rule_id: str | None
    doc_type: DocType | None = None
    period: Period | None = None
    person: str | None = None
    suggested_filename: Annotated[str, StringConstraints(max_length=200)] | None = None
    suggested_directory: Annotated[str, StringConstraints(max_length=300)] | None = None
    confidence: Confidence
    reasoning: ShortText = ""


class RuleLearningResponse(_Strict):
    add_rule: bool
    rule_name: Label | None = None
    institution: Label | None = None
    doc_types: list[DocType] = Field(default_factory=list, max_length=10)
    file_to: Annotated[str, StringConstraints(max_length=300)] | None = None
    filename: Annotated[str, StringConstraints(max_length=200)] | None = None
    reasoning: ShortText = ""


class ConnectionTestResponse(_Strict):
    status: Literal["ok"]


_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()&,'+/-]{0,79}")
_TEMPLATE_VARS = re.compile(r"\{(?:period|year|doc_type|person)\}")
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9 ._()&,'+-]{0,119}")


def validate_filename(value: str) -> str:
    """A single safe PDF filename component; never silently repaired."""
    if (not _SAFE_COMPONENT.fullmatch(value) or ".." in value or len(value) > 180
            or not value.lower().endswith(".pdf")):
        raise InvalidModelOutput("unsafe_filename")
    return value


def validate_relative_directory(value: str) -> str:
    """A relative POSIX directory of safe components; no absolute or traversal parts."""
    trimmed = value.rstrip("/")
    parts = trimmed.split("/")
    if (not trimmed or trimmed.startswith("/") or "\\" in trimmed
            or any(part in ("", ".", "..") or not _SAFE_COMPONENT.fullmatch(part) for part in parts)):
        raise InvalidModelOutput("unsafe_destination")
    return trimmed


def validate_rule_text(value: str) -> str:
    """Single-line label safe to write into rules.md (no markdown structure)."""
    if not _SAFE_LABEL.fullmatch(value):
        raise InvalidModelOutput("unsafe_rule_text")
    return value


def validate_filename_template(value: str) -> str:
    validate_filename(_TEMPLATE_VARS.sub("X", value))
    return value


T = TypeVar("T", bound=_Strict)

MAX_RESPONSE_CHARS = 64_000
INSTRUCTION_LIKE_RE = re.compile(
    r"(?i)\bignore\b.{0,40}\b(?:instruction|rule|polic|prompt|previous|above)"
    r"|\bdisregard\b|\bsystem\s*prompt\b|\byou\s+are\s+now\b|\bnew\s+instructions?\b"
    r"|<\s*/?\s*(?:system|script|instructions?|assistant|user)\b"
    r"|\b(?:system|assistant|developer)\s*:|\boverride\b.{0,40}\b(?:instruction|rule|polic)"
    r"|\bjailbreak\b|\bbegin\s+(?:system|instructions)\b"
)


class _DuplicateKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateKey(key)
        obj[key] = value
    return obj


def _reject_constant(name: str) -> object:
    raise ValueError("non-finite JSON constant")


def _strings(node: object):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


def parse_model_json(raw: object, schema: type[T]) -> T:
    """Parse exactly one JSON object and validate it strictly against ``schema``."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidModelOutput("empty_response")
    if len(raw) > MAX_RESPONSE_CHARS:
        raise InvalidModelOutput("response_too_large")
    if "```" in raw:
        raise InvalidModelOutput("code_fence")
    try:
        obj = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except _DuplicateKey:
        raise InvalidModelOutput("duplicate_key") from None
    except ValueError:
        raise InvalidModelOutput("malformed_json") from None
    if not isinstance(obj, dict):
        raise InvalidModelOutput("malformed_json")
    if any(INSTRUCTION_LIKE_RE.search(text) for text in _strings(obj)):
        raise InvalidModelOutput("instruction_like_output")
    try:
        return schema.model_validate(obj, strict=True)
    except ValidationError:
        raise InvalidModelOutput("schema_violation") from None
