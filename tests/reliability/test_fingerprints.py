"""R01: canonical rendered-pixel fingerprints for pages and documents."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from docflow.ingestion.loader import (
    NEAR_DUPLICATE_MAX_DISTANCE,
    document_sha256,
    fingerprint_pdf,
    hamming_distance,
    page_content_sha256,
)
from tests.reliability.synthetic import sha256_file, write_image_pdf


def test_r01_distinct_equal_dimension_image_only_pdfs_do_not_collide(tmp_path: Path) -> None:
    first = fingerprint_pdf(write_image_pdf(tmp_path / "a.pdf", [1, 2]))
    second = fingerprint_pdf(write_image_pdf(tmp_path / "b.pdf", [1, 3]))

    assert first.page_count == second.page_count == 2
    assert first.pages[0].content_sha256 == second.pages[0].content_sha256
    assert first.pages[1].content_sha256 != second.pages[1].content_sha256
    assert first.document_sha256 != second.document_sha256


def test_page_hash_frames_dimensions_and_mode_around_pixel_bytes() -> None:
    pixels = bytes(range(8))
    wide = Image.frombytes("L", (4, 2), pixels)
    tall = Image.frombytes("L", (2, 4), pixels)
    as_rgb = Image.frombytes("L", (4, 2), pixels).convert("RGB")

    assert wide.tobytes() == tall.tobytes()
    assert page_content_sha256(wide) != page_content_sha256(tall)
    assert page_content_sha256(wide) != page_content_sha256(as_rgb)


def test_document_hash_depends_on_page_order_and_count() -> None:
    a, b = "11" * 32, "22" * 32
    assert document_sha256([a, b]) != document_sha256([b, a])
    assert document_sha256([a]) != document_sha256([a, a])
    assert document_sha256([]) != document_sha256([a])


def test_raw_file_sha256_is_evidence_not_the_semantic_key(tmp_path: Path) -> None:
    plain = fingerprint_pdf(write_image_pdf(tmp_path / "plain.pdf", [4, 5]))
    titled = fingerprint_pdf(write_image_pdf(tmp_path / "titled.pdf", [4, 5], title="Synthetic"))

    assert plain.raw_sha256 != titled.raw_sha256
    assert plain.raw_sha256 == sha256_file(tmp_path / "plain.pdf")
    assert plain.document_sha256 == titled.document_sha256


def test_dhash_marks_near_duplicates_as_candidates_only(tmp_path: Path) -> None:
    base = fingerprint_pdf(write_image_pdf(tmp_path / "base.pdf", [6]))
    nudged = fingerprint_pdf(write_image_pdf(tmp_path / "nudged.pdf", [6], nudge=1))
    other = fingerprint_pdf(write_image_pdf(tmp_path / "other.pdf", [14]))

    assert len(base.pages[0].dhash64) == 16
    int(base.pages[0].dhash64, 16)
    assert base.pages[0].content_sha256 != nudged.pages[0].content_sha256
    assert hamming_distance(base.pages[0].dhash64, nudged.pages[0].dhash64) <= NEAR_DUPLICATE_MAX_DISTANCE
    assert hamming_distance(base.pages[0].dhash64, other.pages[0].dhash64) > NEAR_DUPLICATE_MAX_DISTANCE
    assert NEAR_DUPLICATE_MAX_DISTANCE == 2


def test_fingerprint_canonicalizes_render_to_150_dpi_grayscale(tmp_path: Path, monkeypatch) -> None:
    from docflow.ingestion import loader

    gray = Image.frombytes("L", (4, 2), bytes(range(8)))
    calls: list[dict] = []

    def fake_render(path, **kwargs):
        calls.append(kwargs)
        return [gray.convert("RGB")]

    source = write_image_pdf(tmp_path / "one.pdf", [1])
    monkeypatch.setattr(loader, "convert_from_path", fake_render)
    fingerprint = fingerprint_pdf(source)

    assert calls == [{"dpi": 150, "grayscale": True}]
    assert fingerprint.pages[0].content_sha256 == page_content_sha256(gray)
