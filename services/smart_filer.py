"""
Smart Filer for DocFlow Pipeline v2.

Directory-aware filing: scans the real folder tree, sends it to the LLM
alongside document metadata, and gets back a filing proposal with
rationale.  Replaces the hardcoded category mappings in folder_router.py.
"""

import os
import logging
import json
from typing import Dict, Any, List, Optional
from pathlib import Path

from llm.llm_manager import LLMManager
from config.settings import settings

logger = logging.getLogger(__name__)


class SmartFiler:
    """Proposes filing locations by giving the LLM the real directory tree."""

    def __init__(self, llm_manager: LLMManager, documents_dir: Optional[str] = None):
        self.llm = llm_manager
        self.root_dir = Path(documents_dir or settings.documents_dir)
        self.max_entries = settings.filing_directory_scan_max_entries

    # ── Public API ────────────────────────────────────────────────────

    async def propose_filing(
        self,
        metadata: Dict[str, Any],
        learning_corrections: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Propose a folder and filename for a classified sub-document.

        Args:
            metadata: Classification result with document_type, institution,
                      classification_metadata, etc.
            learning_corrections: Recent filing corrections to inject

        Returns:
            dict with: proposed_folder, proposed_filename, is_new_folder,
            rationale, alternatives, confidence
        """
        directory_tree = self.scan_directory_tree()
        prompt = self._build_filing_prompt(metadata, directory_tree, learning_corrections)

        response = await self.llm.process_text("", prompt, max_tokens=1500)

        if response.success:
            parsed = self._parse_filing_response(response.content)
            if parsed:
                parsed["is_new_folder"] = not self._folder_exists(parsed.get("proposed_folder", ""))
                return parsed

        # Fallback: build a sensible path from metadata
        return self._fallback_proposal(metadata)

    # ── Directory scanning ────────────────────────────────────────────

    def scan_directory_tree(self) -> str:
        """
        Walk the documents directory and produce a text tree representation.

        Capped at ``max_entries`` folder entries.  Samples up to 3 files
        per folder so the LLM can see naming conventions.
        """
        self.root_dir.mkdir(parents=True, exist_ok=True)

        lines: List[str] = []
        entry_count = 0

        for root, dirs, files in os.walk(self.root_dir):
            if entry_count >= self.max_entries:
                lines.append("... (truncated)")
                break

            # Skip hidden/cache folders
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            rel = os.path.relpath(root, self.root_dir)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            indent = "  " * depth
            folder_name = os.path.basename(root) if rel != "." else "[root]"

            pdf_files = [f for f in files if f.lower().endswith(".pdf")]
            lines.append(f"{indent}{folder_name}/ ({len(pdf_files)} files)")

            # Sample up to 3 files
            for sample in pdf_files[:3]:
                lines.append(f"{indent}  - {sample}")

            entry_count += 1

        return "\n".join(lines) if lines else "[empty directory]"

    # ── Prompt building ───────────────────────────────────────────────

    def _build_filing_prompt(
        self,
        metadata: Dict[str, Any],
        directory_tree: str,
        corrections: Optional[List[Dict[str, Any]]],
    ) -> str:
        doc_type = metadata.get("document_type", "unknown")
        institution = metadata.get("institution", "Unknown")
        category = metadata.get("document_category", "Documents")
        class_meta = metadata.get("classification_metadata", {})

        meta_summary = json.dumps(class_meta, indent=2, default=str) if class_meta else "{}"

        learning_context = self._inject_filing_corrections(corrections, metadata)

        return f"""You are a document filing assistant. Given a document's metadata and
the current folder structure, propose the best filing location.

DOCUMENT METADATA:
- Type: {doc_type}
- Category: {category}
- Institution: {institution}
- Details: {meta_summary}

CURRENT FOLDER STRUCTURE:
{directory_tree}

{learning_context}

RULES:
1. Prefer placing documents in existing folders that match.
2. Only propose a new folder if no existing folder is a good fit.
3. Follow the existing naming conventions you see in the tree.
4. Filenames should include: institution, document type, date, and any
   key identifier (account last 4, invoice number, etc.).
5. Use hyphens to separate filename parts. Extension should be .pdf.

Respond with ONLY a JSON object:
{{
  "proposed_folder": "<relative path from root, e.g. Banking/Personal/PNC>",
  "proposed_filename": "<filename.pdf>",
  "rationale": "<1-2 sentences explaining your choice>",
  "alternatives": [
    {{"folder": "<alt path>", "reason": "<why this could also work>"}}
  ],
  "confidence": <float 0.0-1.0>
}}

Return valid JSON only."""

    def _inject_filing_corrections(
        self,
        corrections: Optional[List[Dict[str, Any]]],
        metadata: Dict[str, Any],
    ) -> str:
        if not corrections:
            return ""

        # Filter to filing-relevant corrections
        relevant = [
            c for c in corrections
            if c.get("correction_type") in ("filing", "filename")
        ][:settings.max_learning_corrections_in_prompt]

        if not relevant:
            return ""

        lines = ["PAST FILING CORRECTIONS (follow these patterns):"]
        for c in relevant:
            proposed = c.get("proposed_folder", "?")
            actual = c.get("actual_folder", "?")
            reason = c.get("user_rationale", "")
            doc_type = c.get("proposed_document_type") or c.get("actual_document_type") or "document"
            line = f"- For {doc_type}: proposed '{proposed}' -> user chose '{actual}'"
            if reason:
                line += f"\n  (reason: {reason})"
            lines.append(line)

        return "\n".join(lines)

    # ── Response parsing ──────────────────────────────────────────────

    @staticmethod
    def _parse_filing_response(content: str) -> Optional[Dict[str, Any]]:
        content = content.strip()
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            data = json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            return None

        # Validate required fields
        if "proposed_folder" not in data or "proposed_filename" not in data:
            return None

        return {
            "proposed_folder": str(data["proposed_folder"]).strip("/"),
            "proposed_filename": str(data["proposed_filename"]),
            "rationale": str(data.get("rationale", "")),
            "alternatives": data.get("alternatives", []),
            "confidence": float(data.get("confidence", 0.5)),
            "is_new_folder": False,  # set by caller
        }

    # ── Fallback ──────────────────────────────────────────────────────

    @staticmethod
    def _fallback_proposal(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Build a reasonable proposal without LLM assistance."""
        doc_type = metadata.get("document_type", "unknown")
        category = metadata.get("document_category", "Documents")
        institution = metadata.get("institution", "Unknown")
        class_meta = metadata.get("classification_metadata", {})

        # Build folder
        parts = [category]
        if institution and institution != "Unknown":
            clean = institution.replace(" ", "-").replace("/", "-")
            parts.append(clean)
        folder = "/".join(parts)

        # Build filename
        name_parts = []
        if institution and institution != "Unknown":
            name_parts.append(institution.replace(" ", "-"))
        name_parts.append(doc_type.replace("_", "-").title())

        date = class_meta.get("statement_period") or class_meta.get("date") or class_meta.get("billing_period")
        if date:
            name_parts.append(str(date))

        filename = "-".join(name_parts) + ".pdf" if name_parts else "document.pdf"

        return {
            "proposed_folder": folder,
            "proposed_filename": filename,
            "is_new_folder": True,
            "rationale": "Fallback: LLM unavailable, using category-based routing",
            "alternatives": [],
            "confidence": 0.3,
        }

    def _folder_exists(self, folder_path: str) -> bool:
        if not folder_path:
            return False
        return (self.root_dir / folder_path).is_dir()
