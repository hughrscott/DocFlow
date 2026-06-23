"""OCR: run Tesseract on page images and extract structured signals."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class PageRecord:
    page_number: int          # 1-indexed
    raw_text: str
    institution: str | None
    account_hint: str | None
    period_hint: str | None
    page_of_n: str | None     # e.g. "Page 2 of 4"
    doc_type_hint: str | None
    confidence: float         # 0.0–1.0 OCR quality
    rotation_applied: int     # 0, 90, 180, 270


# ---------------------------------------------------------------------------
# Signal library — maps canonical institution key to keyword variants
# ---------------------------------------------------------------------------
SIGNAL_LIBRARY: dict[str, list[str]] = {
    "pnc": ["pnc bank", "pncbank", "@pncbank", "pnc.com"],
    "guardian": ["guardian life", "guardian anytime", "po box 981572",
                 "guardian dental", "guardian vision"],
    "lloyds": ["lloyds bank", "lloyds bank international"],
    "bettencourt": ["bettencourt tax", "bta"],
    "pesikoff": ["pesikoff", "core primary care", "privia"],
    "frost": ["frost bank", "cullen/frost", "cullen frost"],
    "transnational": ["transnational", "celero", "9550 west higgins"],
    "houston_alarm": ["city of houston", "houston emergency center",
                      "burglar alarm administration", "burglar alarm"],
    "bluecross": ["blue cross", "blue shield", "bcbs",
                  "bluecross blueshield"],
    "corporate_filings": ["corporate filings llc", "corporate filings",
                          "maryland registered agent"],
    "harris_county_tax": ["tax assessor-collector", "harris county",
                          "annette ramirez"],
    "cirro_energy": ["cirro energy", "cirro", "n e r g"],
    "usbank": ["u.s. bank", "us bank", "usb home mortgage",
               "usbankhome.com"],
    "routt_county": ["routt county", "steamboat springs"],
    "rippling": ["rippling", "people center, inc", "people center inc"],
    "tx_workforce": ["texas workforce commission", "labor market and career"],
    "tx_comptroller": ["texas comptroller", "comptroller of public accounts",
                       "notice of forfeiture"],
}

# ---------------------------------------------------------------------------
# Document-type keywords
# ---------------------------------------------------------------------------
DOC_TYPE_KEYWORDS: dict[str, list[str]] = {
    # More specific types MUST come before generic ones (e.g. "checking" before
    # "statement") so that "Checking Account Summary" matches "checking" first.
    "eob": ["explanation of benefits", "eob", "this is not a bill",
            "claim number"],
    "w2": ["form w-2", "wage and tax statement", "w-2"],
    "tax_notice": ["notice of forfeiture", "property tax statement",
                   "tax notice", "tax assessment"],
    "checking": ["checking account", "checking summary", "business checking"],
    "line_of_credit": ["line of credit", "credit line"],
    "visit_summary": ["visit summary", "office visit", "patient visit"],
    "invoice": ["invoice", "amount due", "payment due", "bill to"],
    "bill": ["bill", "balance due", "amount owed", "pay this amount"],
    "receipt": ["receipt", "paid", "transaction receipt"],
    "statement": ["statement", "account summary", "account statement",
                  "monthly statement"],
}

# ---------------------------------------------------------------------------
# Period extraction patterns
# ---------------------------------------------------------------------------
_MONTH_NAMES = (
    r"january|february|march|april|may|june|"
    r"july|august|september|october|november|december"
)
_MONTH_ABBR = (
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
)

PERIOD_PATTERNS: list[re.Pattern[str]] = [
    # "February 2026", "March 2025"
    re.compile(
        rf"({_MONTH_NAMES})\s+(20\d{{2}})", re.IGNORECASE
    ),
    # "Feb 2026"
    re.compile(
        rf"({_MONTH_ABBR})\s+(20\d{{2}})", re.IGNORECASE
    ),
    # "For the period 01/01/2026 - 01/31/2026" → take the end date's month/year
    re.compile(
        r"(?:for the period|period ending|through|ending)\s+"
        r"\d{1,2}[/\-]\d{1,2}[/\-](20\d{2})",
        re.IGNORECASE,
    ),
    # "Statement Date: 02/28/2026" or "Statement Date 02/28/26"
    re.compile(
        r"statement\s+date[:\s]+(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2}|\d{2})",
        re.IGNORECASE,
    ),
    # MM/DD/YYYY anywhere — used as fallback
    re.compile(
        r"(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})",
    ),
    # MM/DD/YY
    re.compile(
        r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})\b",
    ),
]

# "Page X of Y"
PAGE_OF_N_RE = re.compile(
    r"page\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE
)

# Account number fragments — last 4 digits pattern
ACCOUNT_HINT_RE = re.compile(
    r"(?:account|acct)[\s#.:]*(?:number)?[\s#.:]*[^\n]{0,30}?(\d{4})\b", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# OCR confidence estimation
# ---------------------------------------------------------------------------
MIN_CONFIDENCE_FOR_USABLE = 0.30


_COMMON_WORDS = frozenset([
    "the", "and", "for", "you", "your", "this", "that", "with", "from",
    "have", "are", "was", "not", "but", "will", "can", "all", "has",
    "been", "may", "any", "our", "its", "per", "new", "one", "out",
    "due", "date", "page", "total", "amount", "account", "number",
    "bank", "statement", "payment", "balance", "name", "address",
    "phone", "email", "please", "thank", "dear", "sincerely",
    "invoice", "bill", "tax", "insurance", "policy", "claim",
])


def _estimate_confidence(text: str) -> float:
    """Heuristic for OCR quality based on text characteristics and word recognition."""
    if not text or not text.strip():
        return 0.0
    total = len(text)
    alpha_num = sum(1 for c in text if c.isalnum() or c.isspace())
    ratio = alpha_num / total if total else 0.0
    # Short texts are suspicious — real OCR pages have substantial content
    length_factor = min(total / 200, 1.0)
    char_score = ratio * 0.8 * length_factor

    # Check for recognizable English words — this catches upside-down/mirrored text
    # that still has high alphanumeric ratio but no real words
    words = text.lower().split()
    if words:
        recognised = sum(1 for w in words[:100] if w in _COMMON_WORDS)
        word_ratio = recognised / min(len(words), 100)
        # Blend: char_score weighted by word recognition
        # If no common words found, heavily penalise
        word_factor = min(word_ratio * 5, 1.0)  # 20% common words → full score
        return min(round(char_score * (0.3 + 0.7 * word_factor), 3), 1.0)

    return min(round(char_score, 3), 1.0)


# ---------------------------------------------------------------------------
# Signal extraction helpers
# ---------------------------------------------------------------------------

def extract_institution(text: str) -> str | None:
    """Match the first institution signal found in text."""
    text_lower = text.lower()
    for key, variants in SIGNAL_LIBRARY.items():
        for variant in variants:
            if variant in text_lower:
                return key
    return None


def extract_doc_type(text: str) -> str | None:
    """Match the first document-type keyword found in text."""
    text_lower = text.lower()
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return doc_type
    return None


def extract_period(text: str) -> str | None:
    """Extract the most likely period string from OCR text.

    Returns a string like "February2026" or "02/28/2026" depending on
    the pattern that matched first.
    """
    import calendar

    # Pattern 1 & 2: "February 2026" / "Feb 2026" — best signal
    for pat in PERIOD_PATTERNS[:2]:
        m = pat.search(text)
        if m:
            month_str = m.group(1).capitalize()
            year = m.group(2)
            # Normalise abbreviated months to full name
            for i, abbr in enumerate(calendar.month_abbr):
                if abbr and month_str.lower().startswith(abbr.lower()):
                    month_str = calendar.month_name[i]
                    break
            return f"{month_str}{year}"

    # Pattern 3: "for the period ... /2026"
    m = PERIOD_PATTERNS[2].search(text)
    if m:
        year = m.group(1)
        return year  # just the year for now; clustering can refine

    # Pattern 4: "Statement Date: MM/DD/YYYY"
    m = PERIOD_PATTERNS[3].search(text)
    if m:
        month_num, _day, year = m.group(1), m.group(2), m.group(3)
        if len(year) == 2:
            year = f"20{year}"
        try:
            month_name = calendar.month_name[int(month_num)]
            return f"{month_name}{year}"
        except (ValueError, IndexError):
            return f"{month_num}/{year}"

    # Pattern 5 & 6: bare MM/DD/YYYY or MM/DD/YY — weakest signal
    # Validate month (1-12) and year (2019-2030) to avoid OCR garbage
    for pat in PERIOD_PATTERNS[4:]:
        for m in pat.finditer(text):
            month_num = m.group(1)
            year = m.group(3)
            if len(year) == 2:
                year = f"20{year}"
            try:
                month_int = int(month_num)
                year_int = int(year)
                if not (1 <= month_int <= 12 and 2019 <= year_int <= 2030):
                    continue
                month_name = calendar.month_name[month_int]
                return f"{month_name}{year}"
            except (ValueError, IndexError):
                continue

    return None


def extract_page_of_n(text: str) -> str | None:
    """Extract 'Page X of Y' pattern."""
    m = PAGE_OF_N_RE.search(text)
    return m.group(0) if m else None


def extract_account_hint(text: str) -> str | None:
    """Extract last-4-digit account number hints."""
    m = ACCOUNT_HINT_RE.search(text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Core OCR function
# ---------------------------------------------------------------------------

def _ocr_page(image: Image.Image, rotation: int = 0) -> tuple[str, float]:
    """Run Tesseract on a single image at the given rotation.

    Returns (raw_text, confidence).
    """
    if rotation:
        image = image.rotate(-rotation, expand=True)
    text = pytesseract.image_to_string(image)
    confidence = _estimate_confidence(text)
    return text, confidence


def analyze_pages(page_images: list[Image.Image]) -> list[PageRecord]:
    """Run OCR on each image and return a PageRecord per page.

    For pages where initial OCR confidence is below the threshold,
    retries at 180° rotation (upside-down pages are the most common
    real-world issue), then 90° and 270° if still poor.
    """
    records: list[PageRecord] = []

    for i, image in enumerate(page_images):
        page_num = i + 1
        logger.info("OCR page %d/%d", page_num, len(page_images))

        # Try original orientation first
        text, confidence = _ocr_page(image, rotation=0)
        best_text, best_conf, best_rot = text, confidence, 0

        # Always try 180° — upside-down scans are common and the confidence
        # estimator may not catch them if the text is still alphanumeric
        text_180, conf_180 = _ocr_page(image, rotation=180)
        if conf_180 > best_conf:
            best_text, best_conf, best_rot = text_180, conf_180, 180

        # If still poor, try 90° and 270°
        if best_conf < MIN_CONFIDENCE_FOR_USABLE:
            for rot in (90, 270):
                text, confidence = _ocr_page(image, rotation=rot)
                if confidence > best_conf:
                    best_text, best_conf, best_rot = text, confidence, rot
                if best_conf >= MIN_CONFIDENCE_FOR_USABLE:
                    break

        if best_rot != 0:
            logger.info("Page %d: rotation %d° improved confidence to %.2f",
                        page_num, best_rot, best_conf)

        # Extract signals from best OCR result
        record = PageRecord(
            page_number=page_num,
            raw_text=best_text,
            institution=extract_institution(best_text),
            account_hint=extract_account_hint(best_text),
            period_hint=extract_period(best_text),
            page_of_n=extract_page_of_n(best_text),
            doc_type_hint=extract_doc_type(best_text),
            confidence=best_conf,
            rotation_applied=best_rot,
        )
        records.append(record)

        logger.info(
            "Page %d: institution=%s  doc_type=%s  period=%s  confidence=%.2f",
            page_num, record.institution, record.doc_type_hint,
            record.period_hint, record.confidence,
        )

    return records
