"""
Tests for the attachment tools and text extraction.

The fetch tool queues an action (same contract as outlook_actions);
read_attachment pages through text stored as an ADK artifact. We use a
minimal fake ToolContext so no runner is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outlook_king.tools.outlook_actions import PENDING_ACTIONS_KEY
from outlook_king.tools.attachment_tools import (
    FETCHED_ATTACHMENTS_KEY,
    DEFAULT_READ_CHARS,
    MAX_READ_CHARS,
    fetch_attachment,
    list_fetched_attachments,
    read_attachment,
    search_attachment,
)
from outlook_king.utils.attachment_extract import (
    classify_attachment,
    extract_attachment_text,
    guess_mime_type,
)


class FakePart:
    def __init__(self, text: str):
        self.text = text


class FakeCtx:
    def __init__(self, initial=None, artifacts=None):
        self.state = dict(initial or {})
        self._artifacts = dict(artifacts or {})

    async def load_artifact(self, filename):
        return self._artifacts.get(filename)


# ── fetch_attachment ────────────────────────────────────────────────


def test_fetch_attachment_queues_action():
    ctx = FakeCtx()
    out = fetch_attachment(ctx, attachment_id="att-1", name="report.pdf")
    assert out["status"] == "queued"
    action = ctx.state[PENDING_ACTIONS_KEY][-1]
    assert action["type"] == "fetch_attachment"
    assert action["params"] == {"attachment_id": "att-1", "name": "report.pdf"}
    assert action["requires_mode"] == "any"


def test_fetch_attachment_requires_id_and_name():
    ctx = FakeCtx()
    assert fetch_attachment(ctx, attachment_id="", name="x.pdf")["status"] == "error"
    assert fetch_attachment(ctx, attachment_id="a", name="")["status"] == "error"
    assert PENDING_ACTIONS_KEY not in ctx.state


def test_fetch_attachment_skips_already_fetched():
    ctx = FakeCtx({FETCHED_ATTACHMENTS_KEY: {"report.pdf": {"name": "report.pdf"}}})
    out = fetch_attachment(ctx, attachment_id="att-1", name="report.pdf")
    assert out["status"] == "already_fetched"
    assert PENDING_ACTIONS_KEY not in ctx.state


# ── list / read ─────────────────────────────────────────────────────


def test_list_fetched_attachments_empty():
    out = list_fetched_attachments(FakeCtx())
    assert out == {"status": "ok", "count": 0, "attachments": []}


async def test_read_attachment_not_fetched():
    out = await read_attachment(FakeCtx(), name="missing.pdf")
    assert out["status"] == "not_fetched"
    assert "fetch_attachment" in out["message"]


async def test_read_attachment_image_redirects_to_load_artifacts():
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "photo.png": {"name": "photo.png", "kind": "image", "artifact": "attachment:photo.png"}
            }
        }
    )
    out = await read_attachment(ctx, name="photo.png")
    assert out["status"] == "is_image"
    assert out["artifact"] == "attachment:photo.png"
    assert "load_artifacts" in out["message"]


async def test_read_attachment_returns_text():
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "data.csv": {
                    "name": "data.csv",
                    "kind": "text",
                    "artifact": "attachment:data.csv",
                    "text_artifact": "attachment_text:data.csv",
                }
            }
        },
        artifacts={"attachment_text:data.csv": FakePart("name,qty\nfoo,1")},
    )
    out = await read_attachment(ctx, name="data.csv")
    assert out["status"] == "ok"
    assert out["text"] == "name,qty\nfoo,1"
    assert out["has_more"] is False
    assert out["next_offset"] is None
    # the original file stays loadable for structure-preserving viewing
    assert out["raw_artifact"] == "attachment:data.csv"


async def test_read_attachment_pages_long_text():
    long_text = "x" * (DEFAULT_READ_CHARS + 10)
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "big.txt": {"name": "big.txt", "kind": "text", "text_artifact": "attachment_text:big.txt"}
            }
        },
        artifacts={"attachment_text:big.txt": FakePart(long_text)},
    )
    first = await read_attachment(ctx, name="big.txt")
    assert first["has_more"] is True
    assert first["next_offset"] == DEFAULT_READ_CHARS
    second = await read_attachment(ctx, name="big.txt", offset=first["next_offset"])
    assert second["status"] == "ok"
    assert second["text"] == "x" * 10
    assert second["has_more"] is False


async def test_read_attachment_limit_is_clamped():
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "big.txt": {"name": "big.txt", "kind": "text", "text_artifact": "attachment_text:big.txt"}
            }
        },
        artifacts={"attachment_text:big.txt": FakePart("y" * (MAX_READ_CHARS + 500))},
    )
    out = await read_attachment(ctx, name="big.txt", limit=10_000_000)
    assert len(out["text"]) == MAX_READ_CHARS
    assert out["has_more"] is True


async def test_search_attachment_finds_snippets_with_offsets():
    text = ("filler " * 500) + "The termination clause requires 30 days notice." + (" filler" * 500)
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "contract.pdf": {
                    "name": "contract.pdf",
                    "kind": "text",
                    "text_artifact": "attachment_text:contract.pdf",
                }
            }
        },
        artifacts={"attachment_text:contract.pdf": FakePart(text)},
    )
    out = await search_attachment(ctx, name="contract.pdf", query="termination clause")
    assert out["status"] == "ok"
    assert out["hit_count"] == 1
    hit = out["hits"][0]
    assert "30 days notice" in hit["snippet"]
    assert text[hit["offset"] :].startswith("The termination clause") or text[
        hit["offset"] :
    ].lower().startswith("termination clause")
    # snippets stay small — that's the point
    assert len(hit["snippet"]) < 1000


async def test_search_attachment_invalid_regex_falls_back_to_literal():
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "n.txt": {"name": "n.txt", "kind": "text", "text_artifact": "attachment_text:n.txt"}
            }
        },
        artifacts={"attachment_text:n.txt": FakePart("price is $10 (net)")},
    )
    out = await search_attachment(ctx, name="n.txt", query="$10 (net")
    assert out["status"] == "ok"
    assert out["hit_count"] == 1


async def test_search_attachment_no_match():
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "n.txt": {"name": "n.txt", "kind": "text", "text_artifact": "attachment_text:n.txt"}
            }
        },
        artifacts={"attachment_text:n.txt": FakePart("nothing relevant here")},
    )
    out = await search_attachment(ctx, name="n.txt", query="zebra")
    assert out["status"] == "ok"
    assert out["hit_count"] == 0
    assert out["hits"] == []


async def test_search_attachment_requires_query_and_fetch():
    assert (await search_attachment(FakeCtx(), name="x.pdf", query=" "))["status"] == "error"
    assert (await search_attachment(FakeCtx(), name="x.pdf", query="foo"))["status"] == "not_fetched"


async def test_read_attachment_no_text_surfaces_extraction_error():
    ctx = FakeCtx(
        {
            FETCHED_ATTACHMENTS_KEY: {
                "old.xls": {
                    "name": "old.xls",
                    "kind": "binary",
                    "text_artifact": None,
                    "extraction_error": "Legacy .xls files are not supported",
                }
            }
        }
    )
    out = await read_attachment(ctx, name="old.xls")
    assert out["status"] == "no_text"
    assert ".xls" in out["message"]


# ── extraction ──────────────────────────────────────────────────────


def test_classify_attachment_by_extension_and_mime():
    assert classify_attachment("report.pdf") == "pdf"
    assert classify_attachment("sheet.xlsx") == "excel"
    assert classify_attachment("notes", "application/pdf") == "pdf"
    assert classify_attachment("photo.png") == "image"
    assert classify_attachment("photo", "image/jpeg") == "image"
    assert classify_attachment("data.csv") == "text"
    assert classify_attachment("legacy.xls") == "legacy_excel"
    assert classify_attachment("blob.bin") == "binary"


def test_guess_mime_type_falls_back_to_extension():
    assert guess_mime_type("doc.pdf", "") == "application/pdf"
    assert guess_mime_type("doc.pdf", "application/octet-stream") == "application/pdf"
    assert guess_mime_type("doc.pdf", "application/pdf") == "application/pdf"


def test_extract_text_from_csv_bytes():
    result = extract_attachment_text(b"name,qty\nfoo,1\n", "data.csv", "text/csv")
    assert result.kind == "text"
    assert "foo,1" in result.text


def test_extract_image_has_no_text():
    result = extract_attachment_text(b"\x89PNG...", "photo.png", "image/png")
    assert result.kind == "image"
    assert result.text is None


def test_extract_legacy_xls_returns_clear_error():
    result = extract_attachment_text(b"\xd0\xcf\x11\xe0", "old.xls", "")
    assert result.kind == "binary"
    assert ".xls" in result.error


def test_extract_corrupt_pdf_does_not_raise():
    pytest.importorskip("pypdf")
    result = extract_attachment_text(b"not a pdf at all", "broken.pdf", "application/pdf")
    assert result.kind in ("binary", "text")
    assert result.error or result.text is not None


def test_extract_xlsx_roundtrip():
    openpyxl = pytest.importorskip("openpyxl")
    import io

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["item", "cost"])
    ws.append(["server", 120])
    buf = io.BytesIO()
    wb.save(buf)

    result = extract_attachment_text(buf.getvalue(), "budget.xlsx", "")
    assert result.kind == "text"
    assert "Budget" in result.text
    assert "server\t120" in result.text
