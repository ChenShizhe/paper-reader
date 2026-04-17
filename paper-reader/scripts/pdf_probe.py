"""Pre-dispatch probe: decide between PyMuPDF and MinerU for a PDF."""

from __future__ import annotations

import random
import sys
from pathlib import Path

BORN_DIGITAL_SUBSTRINGS: frozenset[str] = frozenset({
    "pdftex",
    "latex",
    "xetex",
    "luatex",
    "microsoft",
    "word",
    "adobe acrobat",
    "acrobat distiller",
    "chrome",
    "chromium",
    "webkit",
    "weasyprint",
    "wkhtmltopdf",
    "skia/pdf",
})


def probe_pdf_type(pdf_path: str | Path) -> dict:
    """Inspect *pdf_path* and decide the best translator.

    Returns a dict with:
      translator  — 'pymupdf' or 'mineru'
      reason      — 'born_digital_probe' or 'scan_detected'
      probe_metadata — {producer, creator, sampled_pages, text_layer_usable}

    If fitz.open() raises, logs to stderr and defaults to MinerU.
    """
    try:
        import fitz  # type: ignore
    except ImportError:
        return {
            "translator": "mineru",
            "reason": "scan_detected",
            "probe_metadata": {
                "producer": "",
                "creator": "",
                "sampled_pages": [],
                "text_layer_usable": False,
            },
        }

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        print(f"[probe] fitz.open failed: {exc}; defaulting to MinerU", file=sys.stderr)
        return {
            "translator": "mineru",
            "reason": "scan_detected",
            "probe_metadata": {
                "producer": "",
                "creator": "",
                "sampled_pages": [],
                "text_layer_usable": False,
            },
        }

    try:
        meta = doc.metadata or {}
        producer = (meta.get("producer") or "").lower().strip()
        creator = (meta.get("creator") or "").lower().strip()

        born_digital_by_meta = any(
            sub in producer or sub in creator
            for sub in BORN_DIGITAL_SUBSTRINGS
        )

        page_count = doc.page_count
        sampled_pages: list[int] = []
        text_layer_usable = False

        if page_count > 0:
            candidates = list(range(1, page_count - 1)) if page_count >= 10 else list(range(page_count))
            sample_size = min(3, len(candidates))
            if sample_size > 0:
                sampled_pages = random.sample(candidates, sample_size)
                text_layer_usable = all(
                    len(doc[idx].get_text("text").replace(" ", "").replace("\n", "").replace("\t", "")) > 100
                    for idx in sampled_pages
                )

        translator = "pymupdf" if (born_digital_by_meta or text_layer_usable) else "mineru"
        reason = "born_digital_probe" if translator == "pymupdf" else "scan_detected"

        return {
            "translator": translator,
            "reason": reason,
            "probe_metadata": {
                "producer": producer,
                "creator": creator,
                "sampled_pages": sampled_pages,
                "text_layer_usable": text_layer_usable,
            },
        }
    finally:
        doc.close()
