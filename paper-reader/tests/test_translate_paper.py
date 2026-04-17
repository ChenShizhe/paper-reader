"""Probe and routing tests for translate_paper / pdf_probe."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import translate_paper as _tp  # noqa: E402
from pdf_probe import probe_pdf_type  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture PDF builders (generated at test time, never committed as binaries)
# ---------------------------------------------------------------------------

def _make_born_digital_pdf(path: Path) -> None:
    """5-page PDF with real text via reportlab — probe detects text layer."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    c = canvas.Canvas(str(path), pagesize=letter)
    line = "The quick brown fox jumps over the lazy dog. " * 8
    for _ in range(5):
        c.setFont("Helvetica", 11)
        y = 720
        for i in range(0, min(len(line), 720), 90):
            c.drawString(72, y, line[i : i + 90])
            y -= 16
        c.showPage()
    c.save()


def _make_scan_like_pdf(path: Path) -> None:
    """3-page PDF with blank pages only — fitz inserts no text layer."""
    import fitz  # type: ignore

    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# Mock helpers for downstream translators (prevent any real I/O)
# ---------------------------------------------------------------------------

def _stub_pymupdf(*, cite_key, output_path, pdf_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")
    return output_path


def _stub_mineru(*, cite_key, paper_bank_dir, output_path, pdf_pages_per_group):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_born_digital_routes_to_pymupdf():
    """reportlab PDF (real text on every page) → probe selects pymupdf / born_digital_probe."""
    with tempfile.TemporaryDirectory() as tmpdir:
        paper_dir = Path(tmpdir) / "testkey"
        paper_dir.mkdir()
        _make_born_digital_pdf(paper_dir / "paper.pdf")
        output_path = paper_dir / "translated_full.md"

        with mock.patch.object(_tp, "_translate_pdf_pymupdf", side_effect=_stub_pymupdf):
            _tp._dispatch_pdf_translation(
                cite_key="testkey",
                paper_bank_dir=paper_dir,
                output_path=output_path,
                pdf_pages_per_group=3,
            )

        manifest = json.loads((paper_dir / "_translation_manifest.json").read_text())
        assert manifest["translator_used"] == "pymupdf"
        assert manifest["reason"] == "born_digital_probe"


def test_scan_like_routes_to_mineru():
    """Blank-page PDF (no text layer) → probe selects mineru; no real MinerU subprocess runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        paper_dir = Path(tmpdir) / "scankey"
        paper_dir.mkdir()
        _make_scan_like_pdf(paper_dir / "paper.pdf")
        output_path = paper_dir / "translated_full.md"

        with mock.patch.object(_tp, "_translate_pdf_format", side_effect=_stub_mineru):
            _tp._dispatch_pdf_translation(
                cite_key="scankey",
                paper_bank_dir=paper_dir,
                output_path=output_path,
                pdf_pages_per_group=3,
            )

        manifest = json.loads((paper_dir / "_translation_manifest.json").read_text())
        assert manifest["translator_used"] == "mineru"


def test_force_mineru_overrides_probe():
    """Born-digital PDF + force_mineru=True → mineru selected, reason == 'user_override'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        paper_dir = Path(tmpdir) / "forcekey"
        paper_dir.mkdir()
        _make_born_digital_pdf(paper_dir / "paper.pdf")
        output_path = paper_dir / "translated_full.md"

        with mock.patch.object(_tp, "_translate_pdf_format", side_effect=_stub_mineru):
            _tp._dispatch_pdf_translation(
                cite_key="forcekey",
                paper_bank_dir=paper_dir,
                output_path=output_path,
                pdf_pages_per_group=3,
                force_mineru=True,
            )

        manifest = json.loads((paper_dir / "_translation_manifest.json").read_text())
        assert manifest["translator_used"] == "mineru"
        assert manifest["reason"] == "user_override"


def test_force_pymupdf_overrides_probe():
    """Scan-like PDF + force_pymupdf=True → pymupdf selected, reason == 'user_override'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        paper_dir = Path(tmpdir) / "forcepykey"
        paper_dir.mkdir()
        _make_scan_like_pdf(paper_dir / "paper.pdf")
        output_path = paper_dir / "translated_full.md"

        with mock.patch.object(_tp, "_translate_pdf_pymupdf", side_effect=_stub_pymupdf):
            _tp._dispatch_pdf_translation(
                cite_key="forcepykey",
                paper_bank_dir=paper_dir,
                output_path=output_path,
                pdf_pages_per_group=3,
                force_pymupdf=True,
            )

        manifest = json.loads((paper_dir / "_translation_manifest.json").read_text())
        assert manifest["translator_used"] == "pymupdf"
        assert manifest["reason"] == "user_override"


def test_probe_exception_falls_back_to_mineru(capsys):
    """Nonexistent PDF path → fitz.open raises → probe returns mineru, logs warning to stderr."""
    result = probe_pdf_type(Path("/nonexistent/no_such_paper_xyz123.pdf"))

    assert result["translator"] == "mineru"

    captured = capsys.readouterr()
    assert captured.err, "expected a warning on stderr"
    assert "probe" in captured.err or "mineru" in captured.err.lower()
