"""
AI-Powered Document Splitter for DocFlow Pipeline v2.

Detects logical sub-document boundaries within a PDF.

- **Claude path**: sends batches of low-res page thumbnails in a single
  multi-image API call and asks Claude to identify where one document
  ends and the next begins.
- **Ollama path**: extracts text from all pages via pypdf, sends a single
  text block with page markers to Ollama's text model.
- **Fallback**: purely text-based boundary detection when vision fails.
"""

import io
import logging
from typing import List, Dict, Any, Optional

from PyPDF2 import PdfReader, PdfWriter
from config.settings import settings
from llm.llm_manager import LLMManager
from services.pii_protector import should_redact, redact_text

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────

class SplitBoundary:
    """Represents a detected sub-document boundary."""

    def __init__(
        self,
        start_page: int,
        end_page: int,
        document_type_hint: str = "unknown",
        confidence: float = 0.5,
        rationale: str = "",
    ):
        self.start_page = start_page
        self.end_page = end_page
        self.document_type_hint = document_type_hint
        self.confidence = confidence
        self.rationale = rationale

    def __repr__(self) -> str:
        return (
            f"SplitBoundary(pages={self.start_page}-{self.end_page}, "
            f"type={self.document_type_hint}, conf={self.confidence:.2f})"
        )


class DocumentSplitter:
    """Detects logical sub-document boundaries within a PDF."""

    def __init__(self, llm_manager: LLMManager):
        self.llm = llm_manager
        self.batch_size = settings.max_pages_per_split_batch
        self.thumbnail_dpi = settings.split_thumbnail_dpi

    # ── Public API ────────────────────────────────────────────────────

    async def split_document(
        self,
        pdf_data: bytes,
        page_images: Optional[List[bytes]] = None,
    ) -> List[SplitBoundary]:
        """
        Detect sub-document boundaries in *pdf_data*.

        If *page_images* are provided (one PNG per page), the multi-image
        vision path is attempted first.  Otherwise text-only splitting is
        used.

        Returns a list of :class:`SplitBoundary` objects covering every
        page in the PDF.
        """
        reader = PdfReader(io.BytesIO(pdf_data))
        total_pages = len(reader.pages)

        if total_pages <= 1:
            return [SplitBoundary(start_page=1, end_page=1, confidence=1.0, rationale="single page")]

        provider_name = self.llm.get_provider_name()

        # Try vision-based splitting first
        if page_images and provider_name in ("claude",):
            try:
                return await self._split_with_multi_image(page_images, total_pages)
            except Exception as e:
                logger.warning(f"Multi-image split failed, falling back to text: {e}")

        # Try text-based splitting (works with any provider)
        try:
            return await self._split_with_text(pdf_data, total_pages, provider_name)
        except Exception as e:
            logger.warning(f"Text-based split failed, using single-document fallback: {e}")

        # Ultimate fallback: treat entire PDF as one document
        return [SplitBoundary(start_page=1, end_page=total_pages, confidence=0.3, rationale="fallback: entire PDF as one document")]

    # ── Multi-image path (Claude) ────────────────────────────────────

    async def _split_with_multi_image(
        self,
        page_images: List[bytes],
        total_pages: int,
    ) -> List[SplitBoundary]:
        """Send page thumbnails to Claude in batches and merge results."""
        all_boundaries: List[SplitBoundary] = []

        for batch_start in range(0, total_pages, self.batch_size):
            batch_end = min(batch_start + self.batch_size, total_pages)
            overlap_start = max(0, batch_start - 2) if batch_start > 0 else 0

            images_with_labels = []
            for i in range(overlap_start, batch_end):
                label = f"[Page {i + 1}]"
                images_with_labels.append((page_images[i], label))

            prompt = self._build_split_prompt(overlap_start + 1, batch_end, total_pages)
            response = await self.llm.analyze_images(images_with_labels, prompt, max_tokens=2000)

            if response.success:
                batch_boundaries = self._parse_split_response(response.content, overlap_start + 1)
                all_boundaries = self._merge_batch_boundaries(all_boundaries, batch_boundaries)

        if not all_boundaries:
            return [SplitBoundary(start_page=1, end_page=total_pages, confidence=0.5, rationale="vision returned no boundaries")]

        return self._fill_gaps(all_boundaries, total_pages)

    # ── Text path (any provider) ──────────────────────────────────────

    async def _split_with_text(
        self,
        pdf_data: bytes,
        total_pages: int,
        provider_name: str,
    ) -> List[SplitBoundary]:
        """Extract text from all pages and ask LLM to find boundaries."""
        reader = PdfReader(io.BytesIO(pdf_data))
        page_texts = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            page_texts.append(f"--- PAGE {i + 1} ---\n{text[:2000]}")

        combined = "\n\n".join(page_texts)

        # Redact PII if needed
        if should_redact(provider_name):
            combined, _ = redact_text(combined)

        prompt = self._build_text_split_prompt(total_pages)
        response = await self.llm.process_text(combined, prompt, max_tokens=2000)

        if response.success:
            boundaries = self._parse_split_response(response.content, 1)
            if boundaries:
                return self._fill_gaps(boundaries, total_pages)

        return [SplitBoundary(start_page=1, end_page=total_pages, confidence=0.4, rationale="text analysis returned no boundaries")]

    # ── Prompt builders ───────────────────────────────────────────────

    def _build_split_prompt(self, start_page: int, end_page: int, total_pages: int) -> str:
        return f"""You are analyzing pages {start_page}-{end_page} of a {total_pages}-page PDF.

Identify where one logical document ends and a new one begins.
Look for: letterheads, new headers, different formatting, date changes,
different institutions, "Page 1 of N" indicators.

Respond with ONLY a JSON array. Each element:
{{
  "start_page": <int>,
  "end_page": <int>,
  "document_type_hint": "<string, e.g. bank_statement, letter, invoice>",
  "confidence": <float 0-1>,
  "rationale": "<brief reason>"
}}

If all shown pages belong to ONE document, return a single element
spanning start_page to end_page.  Return valid JSON only."""

    def _build_text_split_prompt(self, total_pages: int) -> str:
        return f"""You are analyzing the text of a {total_pages}-page PDF.

Each page is separated by "--- PAGE N ---".
Identify where one logical document ends and a new one begins.
Look for: different institutions, new letterheads/headers, different dates,
"Page 1 of N" markers, subject changes.

Respond with ONLY a JSON array. Each element:
{{
  "start_page": <int>,
  "end_page": <int>,
  "document_type_hint": "<string, e.g. bank_statement, letter, invoice>",
  "confidence": <float 0-1>,
  "rationale": "<brief reason>"
}}

If all pages belong to ONE document, return a single element.
Return valid JSON only."""

    # ── Response parsing ──────────────────────────────────────────────

    def _parse_split_response(self, content: str, page_offset: int) -> List[SplitBoundary]:
        """Parse JSON array from LLM response into SplitBoundary objects."""
        import json

        # Extract JSON array from response
        content = content.strip()
        start = content.find("[")
        end = content.rfind("]")
        if start == -1 or end == -1:
            logger.warning("No JSON array found in split response")
            return []

        try:
            items = json.loads(content[start:end + 1])
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse split response JSON: {e}")
            return []

        boundaries = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                boundaries.append(SplitBoundary(
                    start_page=int(item["start_page"]),
                    end_page=int(item["end_page"]),
                    document_type_hint=str(item.get("document_type_hint", "unknown")),
                    confidence=float(item.get("confidence", 0.5)),
                    rationale=str(item.get("rationale", "")),
                ))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Skipping malformed boundary item: {e}")

        return boundaries

    def _merge_batch_boundaries(
        self,
        existing: List[SplitBoundary],
        new_batch: List[SplitBoundary],
    ) -> List[SplitBoundary]:
        """Merge new batch boundaries into existing, handling overlap."""
        if not existing:
            return new_batch

        # Remove overlap: if last existing boundary overlaps with first new,
        # extend the existing one or keep the higher-confidence version
        merged = list(existing)
        for nb in new_batch:
            overlaps = False
            for i, eb in enumerate(merged):
                if nb.start_page <= eb.end_page and nb.end_page >= eb.start_page:
                    # Overlap detected — keep higher confidence
                    if nb.confidence > eb.confidence:
                        merged[i] = nb
                    overlaps = True
                    break
            if not overlaps:
                merged.append(nb)

        merged.sort(key=lambda b: b.start_page)
        return merged

    def _fill_gaps(self, boundaries: List[SplitBoundary], total_pages: int) -> List[SplitBoundary]:
        """Ensure every page from 1..total_pages is covered."""
        boundaries.sort(key=lambda b: b.start_page)
        filled: List[SplitBoundary] = []

        expected_start = 1
        for b in boundaries:
            if b.start_page > expected_start:
                filled.append(SplitBoundary(
                    start_page=expected_start,
                    end_page=b.start_page - 1,
                    confidence=0.3,
                    rationale="gap-fill",
                ))
            filled.append(b)
            expected_start = b.end_page + 1

        if expected_start <= total_pages:
            filled.append(SplitBoundary(
                start_page=expected_start,
                end_page=total_pages,
                confidence=0.3,
                rationale="gap-fill",
            ))

        return filled

    # ── PDF extraction ────────────────────────────────────────────────

    @staticmethod
    def extract_sub_document_pdf(pdf_data: bytes, start_page: int, end_page: int) -> bytes:
        """
        Extract pages *start_page* through *end_page* (1-indexed) from
        *pdf_data* and return a new PDF as bytes.
        """
        reader = PdfReader(io.BytesIO(pdf_data))
        writer = PdfWriter()

        for i in range(start_page - 1, min(end_page, len(reader.pages))):
            writer.add_page(reader.pages[i])

        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
