"""P01/P05: layered local detection and type-aware policy."""
from __future__ import annotations

import re

import pytest

from docflow.privacy.detector import SensitiveDetector
from docflow.privacy.placeholders import PLACEHOLDER_RE, Pseudonymizer
from docflow.privacy.types import (
    Detection,
    DetectorError,
    PrivacyBlocked,
    SensitiveType,
    UnresolvedSensitiveError,
)
from tests.privacy import sentinels as s


def _pz() -> Pseudonymizer:
    return Pseudonymizer(SensitiveDetector.from_config(s.synthetic_config()))


def test_identifiers_in_free_text_are_replaced_consistently() -> None:
    pz = _pz()
    text = (
        f"Patient: {s.DOC_PERSON}. Dear Mrs. {s.HONORIFIC_PERSON}, {s.USER_NAME} and "
        f"{s.FAMILY_NAME} of {s.ENTITY_LEGAL} ({s.DOC_ENTITY}). {s.USER_ADDRESS}; "
        f"{s.DOC_ADDRESS}. Call {s.PHONE}, mail {s.EMAIL}. Account number {s.ACCOUNT}, "
        f"hint {s.ACCOUNT_HINT}. File {s.PRIVATE_PATH} or /etc/cron.d or ../../outside. "
        f"Template {s.RULE_TEMPLATE} in {s.RULE_FILE_TO}."
    )
    out = pz.text(text)
    s.assert_no_sentinels(out)
    assert "../" not in out
    assert len(PLACEHOLDER_RE.findall(out)) >= 12
    same = pz.text(s.USER_NAME)
    assert PLACEHOLDER_RE.fullmatch(same)
    assert pz.text(s.CONFUSABLE_NAME) == same
    assert pz.text(s.USER_NAME.upper()) == same
    surname = pz.text("Vardabrek")
    assert pz.text(s.FULLWIDTH_SURNAME) == surname == pz.text(s.ZERO_WIDTH_SURNAME)


def test_task_relevant_dates_and_amounts_survive_under_policy() -> None:
    out = _pz().text(
        f"Statement period {s.PERIOD}. Statement date {s.DATE}, also 2026-03-15 and "
        f"Feb 3, 2026. Amount due {s.AMOUNT} or 1,284.37. Tax year 2025. Page 1 of 3."
    )
    for kept in (s.PERIOD, s.DATE, "2026-03-15", "Feb 3, 2026", s.AMOUNT, "1,284.37",
                 "Tax year 2025", "Page 1 of 3"):
        assert kept in out


@pytest.mark.parametrize(("text", "raw"), [
    (f"DOB: {s.DOB_DATE}", s.DOB_DATE),
    ("Date of birth 1981-04-12", "1981-04-12"),
    ("card ending in 2026", "2026"),
    (f"ref {s.AMBIGUOUS_ACCOUNT_DATE}", s.AMBIGUOUS_ACCOUNT_DATE),
    ("Acct # 03/2026", "03/2026"),
    ("Account ****2031", "2031"),  # privacy-preflight: allow-test-fixture
    ("Member no. 1999", "1999"),  # privacy-preflight: allow-test-fixture
    ("2026 1234 5678", "1234"),
    ("13/45/2026-7788", "2026-7788"),
    ("Account # 1,284.37", "1,284.37"),
    ("03/15/8823", "8823"),
])
def test_ambiguous_account_or_date_strings_are_masked(text: str, raw: str) -> None:
    out = _pz().text(text)
    assert raw not in out
    assert PLACEHOLDER_RE.search(out)


@pytest.mark.parametrize("text", [
    f"SSN {s.SSN}",
    f"api key {s.SECRET}",
    "fetch https://synthetic:hunter2pass@example.test/data",
    "-----BEGIN PRIVATE KEY-----",  # privacy-preflight: allow-test-fixture
])
def test_values_policy_cannot_pseudonymize_fail_closed(text: str) -> None:
    with pytest.raises(UnresolvedSensitiveError) as excinfo:
        _pz().text(text)
    assert isinstance(excinfo.value, PrivacyBlocked)
    assert excinfo.value.code == "unresolved_sensitive"
    for raw in (s.SSN, s.SECRET, "hunter2pass"):
        assert raw not in str(excinfo.value)


def test_local_recognizer_hook_detections_are_replaced() -> None:
    def recognizer(text: str):
        return [Detection(m.start(), m.end(), SensitiveType.PERSON, "local")
                for m in re.finditer("Glimmerfen", text)]

    pz = Pseudonymizer(SensitiveDetector.from_config({}, recognizers=[recognizer]))
    out = pz.text("reviewed by glimmerfen and Glimmerfen")
    assert "Glimmerfen" not in out and PLACEHOLDER_RE.search(out)


@pytest.mark.parametrize("recognizer", [
    lambda text: (_ for _ in ()).throw(RuntimeError(f"boom {s.USER_NAME}")),
    lambda text: [Detection(0, 10_000, SensitiveType.PERSON, "local")],
    lambda text: ["not a detection"],
])
def test_detector_failure_is_typed_and_leaks_nothing(recognizer) -> None:
    pz = Pseudonymizer(SensitiveDetector.from_config({}, recognizers=[recognizer]))
    with pytest.raises(DetectorError) as excinfo:
        pz.text("synthetic text")
    assert excinfo.value.code == "detector_failure"
    assert s.USER_NAME not in str(excinfo.value)
