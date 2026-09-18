#!/usr/bin/env python3
"""Scan only the current git-tracked tree for likely secrets and PII.

Findings deliberately contain only category, relative path, and count. Matched
values and line content are never emitted.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.test"}
IGNORED_LINE_MARKER = "privacy-preflight: allow-test-fixture"
SAFE_FINANCIAL_FIXTURES = {
    "0339", "1618", "2481", "2718", "2818", "3141", "4102",
    "5390", "5678", "5926", "7364",
}
PROHIBITED_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".pdf", ".tif", ".tiff"}
PROHIBITED_NAMES = {
    ".env",
    "auth.json",
    "auth.yaml",
    "credentials.json",
    "lookup.json",
    "lookup-map.json",
}

PATTERNS = {
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-](\d{3})[ .-](\d{4})(?!\d)"),
    "ssn": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "private_path": re.compile(r"/(?:Users|home)/[^/\s\"']+"),
    "street_address": re.compile(
        r"(?i)\b\d{1,6}\s+(?:[A-Z][\w.-]*\s+){0,5}"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Parkway|Pkwy)\b\.?"
    ),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "cloud_credential": re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|"
        r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
    ),
    "hardcoded_secret": re.compile(
        r"(?im)^\s*(?:api[_-]?key|secret|access[_-]?token|password)\s*[:=]\s*"
        r"[\"']([^\"']{12,})[\"']\s*(?:#.*)?$"
    ),
    "financial_identifier": re.compile(
        r"(?i)\b(?:routing|account|acct|member|policy)\s*(?:number|no\.?|#|ending(?:\s+in)?)?"
        r"\s*[:#-]?\s*(?:[*xX-]*)(\d{4,17})\b"
    ),
}


def tracked_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    return [Path(item.decode("utf-8", "surrogateescape")) for item in result.stdout.split(b"\0") if item]


def load_forbidden(path: Path | None) -> list[str]:
    if path is None:
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    values = raw.get("forbidden_literals", raw) if isinstance(raw, dict) else raw
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("forbidden file must be a JSON list or contain forbidden_literals")
    return sorted({value for value in values if value}, key=len, reverse=True)


def finding_count(category: str, text: str) -> int:
    matches = list(PATTERNS[category].finditer(text))
    if category == "email":
        return sum(match.group(1).lower() not in SAFE_EMAIL_DOMAINS for match in matches)
    if category == "phone":
        return sum(not (match.group(1) == "555" or match.group(2) == "555") for match in matches)
    if category == "street_address":
        return sum("example" not in match.group(0).lower() for match in matches)
    if category == "private_path":
        return sum(
            not match.group(0).startswith(("/home/example", "/Users/example"))
            for match in matches
        )
    if category == "hardcoded_secret":
        safe_markers = ("example", "placeholder", "changeme", "your_", "${", "<")
        return sum(not any(marker in match.group(1).lower() for marker in safe_markers) for match in matches)
    if category == "financial_identifier":
        return sum(
            match.group(1) not in SAFE_FINANCIAL_FIXTURES
            and "example" not in text[max(0, match.start() - 80):match.end() + 80].lower()
            for match in matches
        )
    return len(matches)


def scan(root: Path, forbidden: Iterable[str]) -> dict[str, object]:
    findings: Counter[tuple[str, str]] = Counter()
    scanned_files = 0
    binary_files = 0
    paths = tracked_paths(root)
    for rel in paths:
        lower_name = rel.name.lower()
        if lower_name in PROHIBITED_NAMES or (lower_name.startswith(".env") and lower_name != ".env.example"):
            findings[("prohibited_file", rel.as_posix())] += 1
        if rel.suffix.lower() in PROHIBITED_SUFFIXES:
            findings[("prohibited_file", rel.as_posix())] += 1
        blob = (root / rel).read_bytes()
        if b"\0" in blob[:8192]:
            binary_files += 1
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            binary_files += 1
            continue
        scanned_files += 1
        scan_text = "\n".join(
            line for line in text.splitlines() if IGNORED_LINE_MARKER not in line
        )
        for literal in forbidden:
            count = scan_text.count(literal)
            if count:
                findings[("forbidden_literal", rel.as_posix())] += count
        for category in PATTERNS:
            count = finding_count(category, scan_text)
            if count:
                findings[(category, rel.as_posix())] += count

    rows = [
        {"category": category, "path": path, "count": count}
        for (category, path), count in sorted(findings.items())
    ]
    category_counts = Counter()
    for row in rows:
        category_counts[str(row["category"])] += int(row["count"])
    return {
        "scanned_file_count": scanned_files,
        "binary_file_count": binary_files,
        "tracked_file_count": len(paths),
        "finding_count": sum(category_counts.values()),
        "finding_counts_by_category": dict(sorted(category_counts.items())),
        "findings": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--forbidden-file", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        forbidden = load_forbidden(args.forbidden_file)
        result = scan(args.root.resolve(), forbidden)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"privacy preflight error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for row in result["findings"]:
            print(f"{row['category']}\t{row['path']}\t{row['count']}")
        print(
            "summary\t"
            f"tracked={result['tracked_file_count']}\t"
            f"scanned={result['scanned_file_count']}\t"
            f"findings={result['finding_count']}"
        )
    return 1 if result["finding_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
