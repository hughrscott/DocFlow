"""R05: input stabilization. Unstable or unavailable inputs stay retryable and unmodified."""
from __future__ import annotations

import os
from pathlib import Path

from docflow.ingestion.stability import UF_DATALESS, InputState, check_input
from tests.reliability.synthetic import sha256_file, write_image_pdf


def _snapshot(path: Path) -> tuple[str, int, list[str]]:
    return sha256_file(path), path.stat().st_mtime_ns, sorted(os.listdir(path.parent))


def test_stable_pdf_is_ready_after_bounded_observations(tmp_path: Path) -> None:
    source = write_image_pdf(tmp_path / "inbox" / "scan.pdf", [1, 2, 3])
    before = _snapshot(source)
    sleeps: list[float] = []

    result = check_input(source, observations=3, interval=0.25, sleep=sleeps.append)

    assert result.state is InputState.READY
    assert not result.retryable
    assert result.page_count == 3
    assert result.raw_sha256 == before[0]
    assert sleeps == [0.25, 0.25]
    assert _snapshot(source) == before


def test_missing_input_is_retryable(tmp_path: Path) -> None:
    (tmp_path / "inbox").mkdir()
    result = check_input(tmp_path / "inbox" / "scan.pdf", sleep=lambda s: None)
    assert result.state is InputState.MISSING
    assert result.retryable
    assert os.listdir(tmp_path / "inbox") == []


def test_zero_byte_input_is_retryable_and_unmodified(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "scan.pdf"
    source.parent.mkdir()
    source.write_bytes(b"")
    before = _snapshot(source)
    result = check_input(source, sleep=lambda s: None)
    assert result.state is InputState.ZERO_BYTE
    assert result.retryable
    assert _snapshot(source) == before


def test_malformed_input_is_retryable_and_unmodified(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "scan.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.4\nnot really a pdf body")
    before = _snapshot(source)
    result = check_input(source, sleep=lambda s: None)
    assert result.state is InputState.MALFORMED
    assert result.retryable
    assert _snapshot(source) == before


def test_copying_input_with_growing_size_is_retryable(tmp_path: Path) -> None:
    source = write_image_pdf(tmp_path / "inbox" / "scan.pdf", [1, 2])
    expected = bytearray(source.read_bytes())

    def copier_appends(_: float) -> None:
        expected.extend(b"\n% still copying\n")
        source.write_bytes(bytes(expected))

    result = check_input(source, observations=3, sleep=copier_appends)
    assert result.state is InputState.CHANGING
    assert result.retryable
    assert source.read_bytes() == bytes(expected)
    assert os.listdir(source.parent) == ["scan.pdf"]


def test_rewrite_in_place_with_changing_mtime_is_retryable(tmp_path: Path) -> None:
    source = write_image_pdf(tmp_path / "inbox" / "scan.pdf", [1, 2])
    stamps = iter([1_700_000_000, 1_700_000_100])

    def touch(_: float) -> None:
        stamp = next(stamps)
        os.utime(source, (stamp, stamp))

    result = check_input(source, observations=3, sleep=touch)
    assert result.state is InputState.CHANGING


def test_unstable_page_count_is_retryable(tmp_path: Path) -> None:
    source = write_image_pdf(tmp_path / "inbox" / "scan.pdf", [1, 2])
    before = _snapshot(source)
    counts = iter([2, 2, 3])
    result = check_input(source, observations=3, sleep=lambda s: None,
                         count_pages=lambda path: next(counts))
    assert result.state is InputState.CHANGING
    assert _snapshot(source) == before


def test_partial_download_names_are_not_ready(tmp_path: Path) -> None:
    for name in ("scan.pdf.crdownload", "scan.pdf.part", "scan.pdf.download"):
        source = write_image_pdf(tmp_path / name / "x" / name, [1])
        before = _snapshot(source)
        result = check_input(source, sleep=lambda s: None)
        assert result.state is InputState.DOWNLOAD_NOT_READY, name
        assert result.retryable
        assert _snapshot(source) == before


def test_sibling_partial_download_marker_is_not_ready(tmp_path: Path) -> None:
    source = write_image_pdf(tmp_path / "inbox" / "scan.pdf", [1])
    (source.parent / "scan.pdf.part").write_bytes(b"partial")
    before = _snapshot(source)
    result = check_input(source, sleep=lambda s: None)
    assert result.state is InputState.DOWNLOAD_NOT_READY
    assert _snapshot(source) == before


def test_icloud_placeholder_names_are_unavailable(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    placeholder = inbox / ".scan.pdf.icloud"
    placeholder.write_bytes(b"bplist00 synthetic placeholder")
    before = _snapshot(placeholder)

    direct = check_input(placeholder, sleep=lambda s: None)
    evicted = check_input(inbox / "scan.pdf", sleep=lambda s: None)

    assert direct.state is evicted.state is InputState.UNAVAILABLE
    assert direct.retryable and evicted.retryable
    assert _snapshot(placeholder) == before


def test_macos_dataless_flag_is_unavailable(tmp_path: Path) -> None:
    source = write_image_pdf(tmp_path / "inbox" / "scan.pdf", [1])
    before = _snapshot(source)
    pages_read: list[Path] = []

    class DatalessStat:
        def __init__(self, real: os.stat_result) -> None:
            self.st_size, self.st_mtime_ns = real.st_size, real.st_mtime_ns
            self.st_flags = UF_DATALESS

    result = check_input(source, sleep=lambda s: None,
                         stat=lambda path: DatalessStat(os.stat(path)),
                         count_pages=lambda path: pages_read.append(path) or 1)
    assert result.state is InputState.UNAVAILABLE
    assert pages_read == []  # a dataless file is never opened (that would force a download)
    assert _snapshot(source) == before


def test_change_during_final_hash_is_retryable(tmp_path: Path) -> None:
    source = write_image_pdf(tmp_path / "inbox" / "scan.pdf", [1])
    calls = {"n": 0}

    class Grown:
        def __init__(self, real: os.stat_result) -> None:
            self.st_size, self.st_mtime_ns = real.st_size + 1, real.st_mtime_ns

    def stat(path):
        calls["n"] += 1
        real = os.stat(path)
        return Grown(real) if calls["n"] > 2 else real

    result = check_input(source, observations=2, sleep=lambda s: None, stat=stat)
    assert calls["n"] == 3
    assert result.state is InputState.CHANGING
