"""Layered local deterministic detection of sensitive values.

Layers: known values from the user's own configuration, typed patterns, then
any injected local recognizers. Detection is best effort and never claimed to be complete.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping

from docflow.privacy.types import Detection, DetectorError, SensitiveType

Recognizer = Callable[[str], Iterable[Detection]]

_CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0458": "j", "\u0455": "s",
    "\u0501": "d", "\u04bb": "h", "\u03bf": "o", "\u03b1": "a", "\u03bd": "v",
    "\u03b9": "i", "\u03ba": "k", "\u03c1": "p", "\u03c5": "u", "\u0410": "A",
    "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
    "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0425": "X",
    "\u0406": "I", "\u0408": "J", "\u0405": "S", "\u0391": "A", "\u0392": "B",
    "\u0395": "E", "\u0396": "Z", "\u0397": "H", "\u0399": "I", "\u039a": "K",
    "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P", "\u03a4": "T",
    "\u03a5": "Y", "\u03a7": "X",
})


def normalize_text(value: str) -> str:
    """NFKC, strip invisible format characters, and fold common confusables."""
    text = unicodedata.normalize("NFKC", value)
    text = "".join(
        " " if unicodedata.category(ch) == "Cc" and ch not in "\n\t" else ch
        for ch in text
        if unicodedata.category(ch) != "Cf"
    )
    return text.translate(_CONFUSABLES)


_NAME = r"(?!(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\b)[A-Z][A-Za-z'-]+(?:[ \t]+[A-Z]\.)?(?:[ \t]+[A-Z][A-Za-z'-]+){0,2}"

PATTERNS: tuple[tuple[SensitiveType, re.Pattern[str], int], ...] = (
    (SensitiveType.SECRET, re.compile(
        r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|gh[pousr]_[A-Za-z0-9_]{20,}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
        r"|\b[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"), 0),
    (SensitiveType.GOVERNMENT_ID, re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"), 0),
    (SensitiveType.EMAIL, re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), 0),
    (SensitiveType.PHONE, re.compile(
        r"(?:\+?1[ .-]?)?\(?(?<!\d)\d{3}\)?[ .-]?\d{3}[ .-]\d{4}(?!\d)"
        r"|\+\d{1,3}(?:[ .-]\d{1,4}){2,4}(?!\d)"), 0),
    (SensitiveType.PATH, re.compile(
        r"(?<![\w/.])(?:~|[A-Za-z]:)?[/\\](?:[\w.@-]+[/\\])+[\w.@-]*"
        r"|(?<![\w/.])~[/\\][\w.@/\\-]*"
        r"|(?<![\w/])(?:\.\.[/\\])+[\w.@/\\-]*"), 0),
    (SensitiveType.ADDRESS, re.compile(
        r"(?i)\b\d{1,6}[ \t]+(?:[a-z0-9.'-]+[ \t]+){0,4}"
        r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|parkway|pkwy"
        r"|way|place|pl|circle|cir|terrace|ter|trail|trl|highway|hwy)\b\.?"
        r"(?:,?[ \t]*(?:apt|suite|ste|unit|#)\.?[ \t]*[\w-]+)?"), 0),
    (SensitiveType.ADDRESS, re.compile(r"(?i)\bp\.?[ \t]?o\.?[ \t]*box[ \t]+\d+\b"), 0),
    (SensitiveType.ADDRESS, re.compile(
        r"\b(?:[A-Z][A-Za-z.'-]+[ \t]+){0,3}[A-Z][A-Za-z.'-]+,[ \t]*[A-Z]{2}[ \t]+\d{5}(?:-\d{4})?\b"
        r"|\b[A-Z]{2}[ \t]+\d{5}(?:-\d{4})?\b"), 0),
    (SensitiveType.ENTITY, re.compile(
        r"\b(?:[A-Z][\w&'.-]*,?[ \t]+){1,5}"
        r"(?:LLC|L\.L\.C\.|Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|LLP|PLLC|L\.P\."
        r"|Holdings|Foundation|Trust)(?![\w])"), 0),
    (SensitiveType.PERSON, re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?[ \t]+({_NAME})"), 1),
    (SensitiveType.PERSON, re.compile(
        r"(?i:\b(?:patient|member|insured|subscriber|policy ?holder|account ?holder|beneficiary"
        r"|employee|guarantor|dependent|recipient|customer|name|attn|attention|dear))"
        rf"[ \t]*[:,.-]?[ \t]+({_NAME})"), 1),
    (SensitiveType.ACCOUNT, re.compile(r"(?<!\d)\d(?:[ -]?\d){3,}(?!\d)"), 0),
)

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_NUM_EDGE_L = r"(?<![\d/.,-])"
_NUM_EDGE_R = r"(?!\d|[/.,-]\d)"
_NUMERIC_DATES = (
    re.compile(rf"{_NUM_EDGE_L}(?P<m>\d{{1,2}})[/.-](?P<d>\d{{1,2}})[/.-](?P<y>\d{{4}}|\d{{2}}){_NUM_EDGE_R}"),
    re.compile(rf"{_NUM_EDGE_L}(?P<y>\d{{4}})-(?P<m>\d{{2}})-(?P<d>\d{{2}}){_NUM_EDGE_R}"),
    re.compile(rf"{_NUM_EDGE_L}(?P<m>\d{{1,2}})[/-](?P<y>\d{{4}}){_NUM_EDGE_R}"),
)
_NAMED_DATES = re.compile(
    rf"(?i)\b{_MONTH}\.?[ \t]+\d{{1,2}}(?:st|nd|rd|th)?,?[ \t]+(?:19|20)\d{{2}}\b"
    rf"|\b\d{{1,2}}[ \t]+{_MONTH}\.?,?[ \t]+(?:19|20)\d{{2}}\b"
    rf"|\b{_MONTH}\.?,?[ \t]+(?:19|20)\d{{2}}\b"
)
_AMOUNT = re.compile(
    r"(?:[$\u20ac\u00a3]|USD[ \t]?)[ \t]?\d{1,3}(?:,\d{3})+(?:\.\d{2})?(?![\d,])"
    r"|(?:[$\u20ac\u00a3]|USD[ \t]?)[ \t]?\d+(?:\.\d{2})?(?![\d,])"
    r"|(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d{2}(?![\d])"
    r"|(?<![\d.,])\d{1,7}\.\d{2}(?![\d.])"
)
_YEAR = re.compile(rf"{_NUM_EDGE_L}(?:19|20)\d{{2}}{_NUM_EDGE_R}")
_ACCOUNT_CONTEXT = re.compile(
    r"(?:\b(?:account|acct|a/c|card|ending(?:\s+in)?|ends\s+in|policy|member|routing|iban|loan"
    r"|customer|client|reference|ref|claim|ssn|ein|tax\s*id|id|no\.?|number)|#)"
    r"(?:\s*(?:number|no\.?|#|id))?[\s:#.-]*$|[x*]{2,}[\s-]*$"
)
_DOB_CONTEXT = re.compile(
    r"\b(?:dob|d\.o\.b|date\s+of\s+birth|birth\s*date|birthday|born(?:\s+on)?)[\s:.-]*$"
)


def _context_flags(text: str, start: int) -> set[str]:
    before = text[max(0, start - 40):start].lower()
    flags = set()
    if _ACCOUNT_CONTEXT.search(before):
        flags.add("account_context")
    if _DOB_CONTEXT.search(before):
        flags.add("dob")
    return flags


def _valid_date(m: re.Match[str]) -> bool:
    month, day = int(m.group("m")), int(m.groupdict().get("d") or 1)
    year = m.group("y")
    return 1 <= month <= 12 and 1 <= day <= 31 and (len(year) == 2 or 1900 <= int(year) <= 2099)


def _keepable_detections(text: str) -> list[Detection]:
    """Dates, amounts and years that policy may keep, with context flags."""
    found: list[Detection] = []

    def add(start: int, end: int, kind: SensitiveType, *extra: str) -> None:
        flags = frozenset(_context_flags(text, start) | set(extra))
        found.append(Detection(start, end, kind, "pattern", flags))

    for pattern in _NUMERIC_DATES:
        for m in pattern.finditer(text):
            if _valid_date(m):
                add(m.start(), m.end(), SensitiveType.DATE, "numeric")
    for m in _NAMED_DATES.finditer(text):
        add(m.start(), m.end(), SensitiveType.DATE)
    for m in _AMOUNT.finditer(text):
        add(m.start(), m.end(), SensitiveType.AMOUNT)
    for m in _YEAR.finditer(text):
        add(m.start(), m.end(), SensitiveType.YEAR)
    return found


def _known_value_pattern(value: str) -> str | None:
    words = re.findall(r"[A-Za-z0-9]+|[^\sA-Za-z0-9]", normalize_text(value))
    if not words:
        return None
    body = r"[\s_-]*".join(re.escape(w) for w in words)
    compact = "".join(words)
    if len(compact) >= 6:
        return body
    return rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"


def _template_prefix(template: str) -> str:
    prefix = template.split("{", 1)[0]
    return prefix[:-4] if prefix.lower().endswith(".pdf") else prefix


def known_values_from_config(config: Mapping, rules_md: str = "") -> dict[SensitiveType, set[str]]:
    """Collect the user's own sensitive configuration values by type."""
    from docflow.config.rules_manager import parse_rules_md

    known: dict[SensitiveType, set[str]] = {t: set() for t in SensitiveType}

    def add(kind: SensitiveType, value: object, *, allow_single_word: bool = True) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        text = value.strip()
        single = not re.search(r"[\s/_\\-]", text)
        if single and not allow_single_word and not re.search(r"\d", text):
            return
        known[kind].add(text)

    def add_person(name: object) -> None:
        add(SensitiveType.PERSON, name)
        if isinstance(name, str):
            for part in name.split():
                if len(part) >= 3:
                    add(SensitiveType.PERSON, part)

    user = config.get("user") or {}
    add_person(user.get("name"))
    add(SensitiveType.ADDRESS, user.get("address"))
    for member in config.get("family") or []:
        add_person(member.get("name"))
    for entity in config.get("entities") or []:
        add(SensitiveType.ENTITY, entity.get("name"))
        add(SensitiveType.ENTITY, entity.get("legal_name"))
        add(SensitiveType.ENTITY, entity.get("id"), allow_single_word=False)
        add(SensitiveType.ADDRESS, entity.get("address"))
        add(SensitiveType.PATH, entity.get("directory"), allow_single_word=False)
        for hint in entity.get("account_hints") or []:
            add(SensitiveType.ACCOUNT, str(hint), allow_single_word=False)
    for key in ("archive_root", "scan_watch_folder", "rules_file"):
        add(SensitiveType.PATH, config.get(key), allow_single_word=False)

    def add_rule(rule_id, file_to, template, account_hints, entity_hints) -> None:
        add(SensitiveType.RULE, rule_id, allow_single_word=False)
        add(SensitiveType.PATH, file_to, allow_single_word=False)
        if isinstance(template, str):
            add(SensitiveType.RULE, _template_prefix(template), allow_single_word=False)
        for hint in account_hints or []:
            add(SensitiveType.ACCOUNT, str(hint), allow_single_word=False)
        for hint in entity_hints or []:
            add(SensitiveType.ENTITY, str(hint), allow_single_word=False)

    for rule in config.get("filing_rules") or []:
        match = rule.get("match") or {}
        add_rule(rule.get("id"), rule.get("file_to"), rule.get("filename_template"),
                 match.get("account_hints"), match.get("entity_hints"))
    for rule in parse_rules_md(rules_md or ""):
        add_rule(rule.get("name"), rule.get("file_to"), rule.get("filename"),
                 rule.get("account_hints"), rule.get("entity_hints"))
    return known


class SensitiveDetector:
    """Deterministic detector over normalized text plus optional local recognizers."""

    def __init__(
        self,
        known_values: Mapping[SensitiveType, Iterable[str]] | None = None,
        recognizers: Iterable[Recognizer] = (),
    ) -> None:
        self._known: list[tuple[SensitiveType, re.Pattern[str]]] = []
        for kind, values in (known_values or {}).items():
            patterns = [p for p in (_known_value_pattern(v) for v in values) if p]
            if patterns:
                patterns.sort(key=len, reverse=True)
                self._known.append((kind, re.compile("|".join(patterns), re.IGNORECASE)))
        self._recognizers = tuple(recognizers)

    @classmethod
    def from_config(
        cls, config: Mapping, rules_md: str = "", recognizers: Iterable[Recognizer] = ()
    ) -> SensitiveDetector:
        return cls(known_values_from_config(config, rules_md), recognizers)

    def matches_known(self, text: str) -> bool:
        """True when ``text`` contains one of the user's own configured sensitive values."""
        normalized = normalize_text(text)
        return any(pattern.search(normalized) for _, pattern in self._known)

    def detect(self, text: str) -> list[Detection]:
        """Return detections; any internal or recognizer failure raises DetectorError."""
        try:
            found = self._detect(text)
        except Exception:  # noqa: BLE001 - any detector fault fails closed, without values
            raise DetectorError("sensitive-value detection failed") from None
        for item in found:
            if not (isinstance(item, Detection) and isinstance(item.kind, SensitiveType)
                    and 0 <= item.start < item.end <= len(text)):
                raise DetectorError("detector returned an invalid detection")
        return found

    def _detect(self, text: str) -> list[Detection]:
        found: list[Detection] = []
        for kind, pattern in self._known:
            found.extend(Detection(m.start(), m.end(), kind, "known") for m in pattern.finditer(text))
        for kind, pattern, group in PATTERNS:
            for m in pattern.finditer(text):
                start, end = m.span(group)
                if end > start:
                    source = "digit_run" if kind is SensitiveType.ACCOUNT else "pattern"
                    found.append(Detection(start, end, kind, source))
        found.extend(_keepable_detections(text))
        for recognizer in self._recognizers:
            found.extend(recognizer(text))
        return found
