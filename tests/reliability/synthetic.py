"""Synthetic image-only PDFs and byte-identity helpers (no real documents)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PAGE_SIZE = (120, 160)  # pixels at 150 DPI
TEXT_PAGE_SIZE = (1275, 1650)  # US Letter at 150 DPI, big enough for real OCR
TEXT_DPI = 150


def page_image(mark: int, *, nudge: int = 0) -> Image.Image:
    """A blank page with one black box whose position encodes ``mark``."""
    image = Image.new("L", PAGE_SIZE, 255)
    x = 8 + (mark % 5) * 20
    y = 8 + (mark // 5) * 24
    ImageDraw.Draw(image).rectangle([x, y, x + 16, y + 20], fill=0)
    if nudge:
        image.putpixel((PAGE_SIZE[0] - 2, PAGE_SIZE[1] - 2), 0)
    return image


def write_image_pdf(path: Path, marks: list[int], *, title: str | None = None,
                    nudge: int = 0) -> Path:
    """Write an image-only PDF with one page per mark; equal page dimensions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = [page_image(m, nudge=nudge).convert("RGB") for m in marks]
    extra = {"title": title} if title else {}
    pages[0].save(path, "PDF", resolution=150, save_all=True, append_images=pages[1:], **extra)
    return path


def text_page_image(lines: list[str]) -> Image.Image:
    """A synthetic page rendering ``lines`` as pixels; no text layer exists."""
    image = Image.new("L", TEXT_PAGE_SIZE, 255)
    if not lines:
        return image
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=44)  # scalable, no system font needed
    for index, line in enumerate(lines):
        draw.text((90, 130 + index * 84), line, fill=0, font=font)
    return image


def write_text_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Write an image-only PDF whose rendered pages real Tesseract can read.

    Each entry of ``pages`` is the list of lines drawn on that page; an empty
    list produces a blank page with no recoverable text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [text_page_image(lines).convert("RGB") for lines in pages]
    images[0].save(path, "PDF", resolution=TEXT_DPI, save_all=True,
                   append_images=images[1:])
    return path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in sorted(root.rglob("*")) if p.is_file()}
