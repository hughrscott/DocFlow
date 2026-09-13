"""Synthetic image-only PDFs and byte-identity helpers (no real documents)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

PAGE_SIZE = (120, 160)  # pixels at 150 DPI


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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in sorted(root.rglob("*")) if p.is_file()}
