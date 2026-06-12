"""
Attachment content extraction.

Turns raw attachment bytes into something the LLM can read:

  - PDF        → text via pypdf (page-marked)
  - XLSX       → sheet dumps via openpyxl (tab-separated, row-capped)
  - CSV / TXT  → decoded text
  - EML / ICS  → decoded text (already text formats)
  - images     → no text; stored as an artifact the model views via
                 the built-in ``load_artifacts`` tool
  - anything else → metadata only

pypdf / openpyxl are optional at import time — extraction degrades to a
clear error message instead of crashing the endpoint when they're absent.
"""

from __future__ import annotations

import io
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

# Caps keep one attachment from flooding an artifact / LLM context.
MAX_TEXT_CHARS = 400_000
MAX_SHEET_ROWS = 500

_TEXT_EXTENSIONS = {".txt", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".md", ".log", ".eml", ".ics"}
_EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


@dataclass
class ExtractionResult:
    kind: str  # "text" | "image" | "binary"
    text: str | None = None
    error: str | None = None
    # Cheap structural metadata (page count, sheet names…) so the agent
    # can navigate a document without loading it into context.
    structure: dict | None = None


def guess_mime_type(name: str, content_type: str = "") -> str:
    if content_type and content_type != "application/octet-stream":
        return content_type
    guessed, _ = mimetypes.guess_type(name)
    return guessed or content_type or "application/octet-stream"


def classify_attachment(name: str, content_type: str = "") -> str:
    """Coarse routing: "pdf" | "excel" | "text" | "image" | "binary"."""
    ext = PurePosixPath(name.lower()).suffix
    mime = (content_type or "").lower()
    if ext in _PDF_EXTENSIONS or "pdf" in mime:
        return "pdf"
    if ext in _EXCEL_EXTENSIONS or "spreadsheetml" in mime:
        return "excel"
    if ext in _IMAGE_EXTENSIONS or mime.startswith("image/"):
        return "image"
    if ext in _TEXT_EXTENSIONS or mime.startswith("text/") or "json" in mime:
        return "text"
    if ext == ".xls":
        # Legacy binary Excel — openpyxl can't read it.
        return "legacy_excel"
    return "binary"


def _extract_pdf(data: bytes) -> ExtractionResult:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ExtractionResult(
            kind="binary",
            error="PDF extraction unavailable: the 'pypdf' package is not installed.",
        )
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return ExtractionResult(kind="binary", error="PDF is password-protected.")
        pages: list[str] = []
        total = 0
        for i, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            chunk = f"--- page {i} ---\n{page_text}"
            pages.append(chunk)
            total += len(chunk)
            if total > MAX_TEXT_CHARS:
                pages.append(f"--- truncated after page {i} of {len(reader.pages)} ---")
                break
        text = "\n\n".join(pages)[:MAX_TEXT_CHARS]
        structure = {"pages": len(reader.pages)}
        if not any(p.strip() for p in pages if not p.startswith("---")):
            # All pages empty — likely a scanned/image-only PDF.
            return ExtractionResult(
                kind="text",
                text=text,
                error=(
                    "No extractable text — this PDF appears to be scanned images. "
                    "View it with the load_artifacts tool instead."
                ),
                structure=structure,
            )
        return ExtractionResult(kind="text", text=text, structure=structure)
    except Exception as exc:
        logger.warning("PDF extraction failed: %s", exc)
        return ExtractionResult(kind="binary", error=f"Could not parse PDF: {type(exc).__name__}")


def _extract_excel(data: bytes) -> ExtractionResult:
    try:
        import openpyxl
    except ImportError:
        return ExtractionResult(
            kind="binary",
            error="Excel extraction unavailable: the 'openpyxl' package is not installed.",
        )
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets: list[str] = []
        sheet_meta: list[dict] = []
        total = 0
        for ws in wb.worksheets:
            lines = [f"=== sheet: {ws.title} ==="]
            row_count = 0
            for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                if row_idx >= MAX_SHEET_ROWS:
                    lines.append(f"... truncated at {MAX_SHEET_ROWS} rows ...")
                    break
                cells = ["" if c is None else str(c) for c in row]
                if any(c.strip() for c in cells):
                    lines.append("\t".join(cells))
                    row_count += 1
            sheet_meta.append({"name": ws.title, "rows": row_count})
            chunk = "\n".join(lines)
            sheets.append(chunk)
            total += len(chunk)
            if total > MAX_TEXT_CHARS:
                sheets.append("=== truncated: workbook too large ===")
                break
        wb.close()
        return ExtractionResult(
            kind="text",
            text="\n\n".join(sheets)[:MAX_TEXT_CHARS],
            structure={"sheets": sheet_meta},
        )
    except Exception as exc:
        logger.warning("Excel extraction failed: %s", exc)
        return ExtractionResult(kind="binary", error=f"Could not parse workbook: {type(exc).__name__}")


def extract_attachment_text(data: bytes, name: str, content_type: str = "") -> ExtractionResult:
    """Extract LLM-readable text from attachment bytes.

    Never raises — failures come back as ``ExtractionResult.error``.
    """
    category = classify_attachment(name, content_type)
    if category == "pdf":
        return _extract_pdf(data)
    if category == "excel":
        return _extract_excel(data)
    if category == "text":
        return ExtractionResult(kind="text", text=data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS])
    if category == "image":
        return ExtractionResult(kind="image")
    if category == "legacy_excel":
        return ExtractionResult(
            kind="binary",
            error="Legacy .xls files are not supported — ask the sender for .xlsx or CSV.",
        )
    return ExtractionResult(
        kind="binary",
        error=f"No text extraction available for this file type ({content_type or name}).",
    )
