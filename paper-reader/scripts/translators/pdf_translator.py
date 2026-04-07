"""Tier 1 & 2 PDF extraction: group pages into chunks, classify heavy vs. prose,
and assemble a translated full-PDF markdown with YAML frontmatter."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
    _BACKEND = "fitz"
except ImportError:
    import pdfplumber as fitz  # type: ignore[no-redef]
    _BACKEND = "pdfplumber"

_MINERU_PAGE_CACHE: dict[str, dict[int, str]] = {}
_MINERU_PREFETCH_ATTEMPTED: set[str] = set()
_MINERU_DISABLED_REASON: str | None = None
CHUNK_ARTIFACTS_DIR_NAME = "pdf_segments"
CHUNK_SOURCE_DIR_NAME = "source_chunks"
CHUNK_TRANSLATED_DIR_NAME = "translated_chunks"


def _is_heavy_chunk(text: str) -> bool:
    """Return True if the chunk text appears equation-heavy or table-heavy."""
    if not text:
        return False

    # (a) $ or \[ density >= 1 per 400 characters
    math_markers = len(re.findall(r'\$|\\\[', text))
    if len(text) > 0 and math_markers / len(text) >= 1 / 400:
        return True

    # (b) >= 3 consecutive lines shorter than 30 characters (display-math lines)
    lines = text.splitlines()
    consecutive_short = 0
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) < 30:
            consecutive_short += 1
            if consecutive_short >= 3:
                return True
        else:
            consecutive_short = 0

    # (c) LaTeX environment marker
    if r"\begin{" in text:
        return True

    return False


def _extract_text_fitz(pdf_path: str, page_indices: list[int]) -> str:
    doc = fitz.open(pdf_path)
    parts: list[str] = []
    for idx in page_indices:
        if idx < doc.page_count:
            parts.append(doc[idx].get_text())
    doc.close()
    return "\n".join(parts)


def _extract_text_pdfplumber(pdf_path: str, page_indices: list[int]) -> str:
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx in page_indices:
            if idx < len(pdf.pages):
                text = pdf.pages[idx].extract_text() or ""
                parts.append(text)
    return "\n".join(parts)


def _tier1_to_markdown(text: str) -> str:
    """Promote numbered equation blocks in Tier-1 plain text to $$ display-equation markers.

    Detects lines that consist solely of an equation label like "(1)" and wraps
    the label plus the immediately following equation line in $$ delimiters.
    """
    converted = re.sub(
        r"(?m)^(\(\d+\))\n([^\n]+)",
        r"$$\n\1\n\2\n$$",
        text,
    )
    converted = re.sub(
        r"(?m)^([^\n]{1,80}=)\n([^\n]*[=+\-*/^_\\()\[\]{}∫∑λΛωΩ][^\n]{0,89})$",
        r"$$\n\1\n\2\n$$",
        converted,
    )
    return converted


def _write_chunk_pdf(
    pdf_path: str,
    start_page: int,
    end_page: int,
    output_path: str,
) -> bool:
    if _BACKEND != "fitz":
        return False

    src = fitz.open(pdf_path)
    dst = fitz.open()
    try:
        from_page = max(0, start_page - 1)
        to_page = max(from_page, end_page - 1)
        upper_bound = src.page_count - 1
        if from_page > upper_bound:
            return False
        to_page = min(to_page, upper_bound)
        for page_index in range(from_page, to_page + 1):
            dst.insert_pdf(src, from_page=page_index, to_page=page_index)
        if dst.page_count == 0:
            return False
        dst.save(output_path)
        return True
    finally:
        dst.close()
        src.close()


def _is_mineru_permission_error(message: str) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return (
        "operation not permitted" in lowered
        or "permissionerror" in lowered
        or "sc_sem_nsems_max" in lowered
    )


def _mineru_env(tmpdir: str) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("MINERU_DEVICE_MODE", "cpu")
    env.setdefault("MPLCONFIGDIR", os.path.join(tmpdir, "mplconfig"))
    env.setdefault("YOLO_CONFIG_DIR", os.path.join(tmpdir, "yolo-config"))
    os.makedirs(env["MPLCONFIGDIR"], exist_ok=True)
    os.makedirs(env["YOLO_CONFIG_DIR"], exist_ok=True)
    return env


def _load_mineru_page_map(output_dir: str) -> dict[int, str]:
    content_list_files = [
        os.path.join(root, file_name)
        for root, _, files in os.walk(output_dir)
        for file_name in files
        if file_name.endswith("_content_list.json")
    ]
    if not content_list_files:
        return {}

    try:
        with open(content_list_files[0], encoding="utf-8") as file_handle:
            content_items = json.loads(file_handle.read())
    except Exception:
        return {}

    if not isinstance(content_items, list):
        return {}

    page_blocks: dict[int, list[str]] = defaultdict(list)
    for item in content_items:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        text = (item.get("text") or "").strip()
        if not isinstance(page_idx, int) or not text:
            continue
        if item.get("type") == "equation" and "$$" not in text:
            text = f"$$\n{text}\n$$"
        page_blocks[page_idx].append(text)

    return {
        page_idx: "\n\n".join(blocks)
        for page_idx, blocks in page_blocks.items()
        if blocks
    }


def _read_mineru_markdown_file(output_dir: str) -> str | None:
    candidates = sorted(
        os.path.join(root, file_name)
        for root, _, files in os.walk(output_dir)
        for file_name in files
        if file_name.endswith(".md")
    )
    if not candidates:
        candidates = sorted(
            os.path.join(root, file_name)
            for root, _, files in os.walk(output_dir)
            for file_name in files
            if file_name.endswith(".txt")
        )
    if not candidates:
        return None
    try:
        with open(candidates[0], encoding="utf-8") as file_handle:
            return file_handle.read()
    except Exception:
        return None


def _run_mineru_cli(
    mineru_binary: str,
    pdf_path: str,
    *,
    start_page: int | None = None,
    end_page: int | None = None,
    timeout: int = 420,
) -> tuple[str | None, dict[int, str], str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = pdf_path
        page_offset = 0
        if start_page is not None and end_page is not None:
            chunk_pdf_path = os.path.join(tmpdir, "chunk.pdf")
            if _write_chunk_pdf(pdf_path, start_page, end_page, chunk_pdf_path):
                input_path = chunk_pdf_path
                page_offset = max(0, start_page - 1)

        output_dir = os.path.join(tmpdir, "mineru-output")
        os.makedirs(output_dir, exist_ok=True)

        command = [
            mineru_binary,
            "-p", input_path,
            "-o", output_dir,
            "-b", "pipeline",
            "-m", "txt",
        ]
        if input_path == pdf_path and start_page is not None and end_page is not None:
            command.extend(
                ["--start", str(start_page - 1), "--end", str(end_page - 1)]
            )

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_mineru_env(tmpdir),
        )

        combined_logs = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0:
            return None, {}, combined_logs

        page_map = _load_mineru_page_map(output_dir)
        if page_map and page_offset:
            page_map = {
                page_index + page_offset: markdown
                for page_index, markdown in page_map.items()
            }
        if page_map:
            if start_page is not None and end_page is not None:
                selected_pages = range(start_page - 1, end_page)
                parts = [
                    page_map.get(page_index, "").strip()
                    for page_index in selected_pages
                    if page_map.get(page_index, "").strip()
                ]
                if parts:
                    return "\n\n".join(parts), page_map, combined_logs
            else:
                parts = [
                    page_map[page_index]
                    for page_index in sorted(page_map.keys())
                    if page_map[page_index].strip()
                ]
                if parts:
                    return "\n\n".join(parts), page_map, combined_logs

        markdown = _read_mineru_markdown_file(output_dir)
        return markdown, page_map, combined_logs


def _prefetch_mineru_pages(
    mineru_binary: str,
    pdf_path: str,
) -> dict[int, str] | None:
    global _MINERU_DISABLED_REASON

    if _MINERU_DISABLED_REASON:
        return None
    if os.getenv("PAPER_READER_MINERU_PREFETCH", "0") != "1":
        return None

    pdf_key = os.path.abspath(os.path.expanduser(pdf_path))
    if pdf_key in _MINERU_PAGE_CACHE:
        return _MINERU_PAGE_CACHE[pdf_key]
    if pdf_key in _MINERU_PREFETCH_ATTEMPTED:
        return None

    _MINERU_PREFETCH_ATTEMPTED.add(pdf_key)
    _, page_map, logs = _run_mineru_cli(
        mineru_binary,
        pdf_key,
        timeout=900,
    )
    if page_map:
        _MINERU_PAGE_CACHE[pdf_key] = page_map
        return page_map

    if _is_mineru_permission_error(logs):
        _MINERU_DISABLED_REASON = logs[-1000:]
    return None


def extract_tier1(pdf_path: str, pages_per_chunk: int = 3) -> list[dict[str, Any]]:
    """Extract Tier 1 chunks from a PDF.

    Args:
        pdf_path: Path to the PDF file. Tilde (~) is expanded automatically.
        pages_per_chunk: Number of pages per chunk (default 3).

    Returns:
        List of chunk dicts with keys:
            chunk_id (str): Zero-padded identifier, e.g. 'chunk_001'.
            start_page (int): 1-indexed first page of the chunk.
            end_page (int): 1-indexed last page of the chunk.
            is_heavy (bool): True when the chunk is equation- or table-heavy.
            raw_text (str): Plain text extracted from the chunk pages.
    """
    pdf_path = os.path.expanduser(pdf_path)

    if _BACKEND == "fitz":
        doc = fitz.open(pdf_path)
        total_pages = doc.page_count
        doc.close()
    else:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

    chunks: list[dict[str, Any]] = []
    chunk_number = 1

    for start_idx in range(0, total_pages, pages_per_chunk):
        end_idx = min(start_idx + pages_per_chunk - 1, total_pages - 1)
        page_indices = list(range(start_idx, end_idx + 1))

        if _BACKEND == "fitz":
            raw_text = _extract_text_fitz(pdf_path, page_indices)
        else:
            raw_text = _extract_text_pdfplumber(pdf_path, page_indices)

        chunks.append({
            "chunk_id": f"chunk_{chunk_number:03d}",
            "start_page": start_idx + 1,
            "end_page": end_idx + 1,
            "is_heavy": _is_heavy_chunk(raw_text),
            "raw_text": raw_text,
        })
        chunk_number += 1

    return chunks


def extract_tier2(pdf_path: str, chunk: dict[str, Any]) -> dict[str, Any]:
    """Extract a single chunk using MinerU for high-fidelity equation rendering.

    # MinerU invoked via: CLI subprocess (pipeline/txt) -> python API fallback -> tier1 fallback

    Attempts MinerU extraction in priority order:
      1. mineru CLI via subprocess (pipeline + txt backend)
      2. magic_pdf Python API (UNIPipe) fallback
      3. Tier-1 plain text with basic equation-delimiter promotion (fallback)

    Any exception from MinerU is caught and the next attempt is tried so that
    a single-chunk failure never aborts the full assembly.

    Args:
        pdf_path: Absolute path to the PDF (tilde already expanded by caller).
        chunk: Tier-1 chunk dict with keys chunk_id, start_page, end_page, raw_text.

    Returns:
        Dict with keys:
            markdown (str): Extracted or fallback markdown content.
            used_fallback (bool): True when MinerU was unavailable for this chunk.
    """
    global _MINERU_DISABLED_REASON

    chunk_start = int(chunk.get("start_page", 1))
    chunk_end = int(chunk.get("end_page", chunk_start))
    mineru_binary = shutil.which("mineru") or os.path.expanduser("~/.local/bin/mineru")
    mineru_available = os.path.exists(mineru_binary)

    chunk_pdf_path = chunk.get("chunk_pdf_path")
    if not isinstance(chunk_pdf_path, str) or not os.path.exists(chunk_pdf_path):
        chunk_pdf_path = None

    # --- Attempt 1: MinerU CLI ---
    if mineru_available and not _MINERU_DISABLED_REASON:
        page_cache = None
        if chunk_pdf_path is None:
            page_cache = _prefetch_mineru_pages(mineru_binary, pdf_path)
        if page_cache:
            page_range = range(chunk_start - 1, chunk_end)
            cached_parts = [
                page_cache.get(page_index, "").strip()
                for page_index in page_range
                if page_cache.get(page_index, "").strip()
            ]
            if cached_parts:
                return {"markdown": "\n\n".join(cached_parts), "used_fallback": False}

        try:
            if chunk_pdf_path is not None:
                markdown, _, logs = _run_mineru_cli(
                    mineru_binary,
                    chunk_pdf_path,
                    timeout=420,
                )
            else:
                markdown, _, logs = _run_mineru_cli(
                    mineru_binary,
                    pdf_path,
                    start_page=chunk_start,
                    end_page=chunk_end,
                    timeout=420,
                )
            if markdown and markdown.strip():
                return {"markdown": markdown, "used_fallback": False}
            if _is_mineru_permission_error(logs):
                _MINERU_DISABLED_REASON = logs[-1000:]
        except Exception:
            pass

    # --- Attempt 2: MinerU Python API fallback ---
    if not mineru_available:
        try:
            from magic_pdf.pipe.UNIPipe import UNIPipe  # type: ignore
            from magic_pdf.data.data_reader_writer import FileBasedDataWriter  # type: ignore

            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = chunk_pdf_path or pdf_path
                if chunk_pdf_path is None:
                    generated_chunk_pdf_path = os.path.join(tmpdir, "chunk.pdf")
                    if _write_chunk_pdf(pdf_path, chunk_start, chunk_end, generated_chunk_pdf_path):
                        input_path = generated_chunk_pdf_path

                image_dir = os.path.join(tmpdir, "images")
                os.makedirs(image_dir, exist_ok=True)
                writer = FileBasedDataWriter(image_dir)
                with open(input_path, "rb") as fh:
                    pdf_bytes = fh.read()
                pipe = UNIPipe(pdf_bytes, {"parse_method": "auto"}, writer)
                pipe.pipe_classify()
                pipe.pipe_analyze()
                pipe.pipe_parse()
                md_content = pipe.pipe_mk_markdown(image_dir, drop_mode="none")
                if isinstance(md_content, str) and md_content.strip():
                    return {"markdown": md_content, "used_fallback": False}
                md_files = [
                    os.path.join(r, f)
                    for r, _, fs in os.walk(tmpdir)
                    for f in fs
                    if f.endswith(".md")
                ]
                if md_files:
                    with open(md_files[0], encoding="utf-8") as file_handle:
                        return {"markdown": file_handle.read(), "used_fallback": False}
        except Exception:
            pass

    # --- Fallback: Tier-1 text with equation delimiter promotion ---
    raw = chunk.get("raw_text", "")
    return {"markdown": _tier1_to_markdown(raw), "used_fallback": True}


def assemble_pdf_translation(
    pdf_path: str,
    cite_key: str,
    output_path: str,
    pages_per_chunk: int = 3,
) -> None:
    """Orchestrate full two-tier PDF translation and write assembled markdown.

    Calls extract_tier1 for all chunks, calls extract_tier2 for heavy chunks,
    merges outputs in page order, prepends YAML frontmatter, and writes the
    result to output_path.

    Chunk artifacts are persisted under:
      <paper-bank>/<cite_key>/pdf_segments/source_chunks/*.pdf
      <paper-bank>/<cite_key>/pdf_segments/translated_chunks/*.md

    Note: this function always writes to translated_full_pdf.md and will never
    overwrite translated_full.md (the LaTeX translation path).

    YAML frontmatter fields:
        cite_key, source_format, translation_tool, translation_timestamp,
        translation_version, page_count, chunk_count, heavy_chunk_count,
        fallback_chunks.

    Args:
        pdf_path: Path to the PDF file. Tilde (~) is expanded automatically.
        cite_key: Citekey for the paper (used in frontmatter and output filename).
        output_path: Destination .md file path. Tilde (~) is expanded automatically.
        pages_per_chunk: Pages per extraction chunk (default 3).
    """
    pdf_path = os.path.expanduser(pdf_path)
    output_path = os.path.expanduser(output_path)

    # Step 1: Tier-1 extraction for all chunks
    chunks = extract_tier1(pdf_path, pages_per_chunk)

    output_root = Path(output_path).resolve().parent
    chunk_root = output_root / CHUNK_ARTIFACTS_DIR_NAME
    chunk_source_root = chunk_root / CHUNK_SOURCE_DIR_NAME
    chunk_translated_root = chunk_root / CHUNK_TRANSLATED_DIR_NAME
    chunk_source_root.mkdir(parents=True, exist_ok=True)
    chunk_translated_root.mkdir(parents=True, exist_ok=True)
    for stale in chunk_source_root.glob("chunk_*.pdf"):
        stale.unlink(missing_ok=True)
    for stale in chunk_translated_root.glob("chunk_*.md"):
        stale.unlink(missing_ok=True)

    total_pages = chunks[-1]["end_page"] if chunks else 0
    heavy_count = sum(1 for c in chunks if c["is_heavy"])
    fallback_chunks: list[str] = []

    # Step 2: Tier-2 for heavy chunks; plain markdown for prose chunks
    chunk_markdowns: dict[str, str] = {}
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        start_page = int(chunk["start_page"])
        end_page = int(chunk["end_page"])
        chunk_pdf_path = (
            chunk_source_root / f"{chunk_id}_p{start_page:03d}-{end_page:03d}.pdf"
        )
        chunk_payload = dict(chunk)
        if _write_chunk_pdf(pdf_path, start_page, end_page, str(chunk_pdf_path)):
            chunk_payload["chunk_pdf_path"] = str(chunk_pdf_path)
        elif chunk_pdf_path.exists():
            chunk_pdf_path.unlink(missing_ok=True)

        if chunk["is_heavy"]:
            result = extract_tier2(pdf_path, chunk_payload)
            md = result["markdown"]
            if result["used_fallback"]:
                cid = chunk_id
                fallback_chunks.append(cid)
                md = f"<!-- tier2_fallback: {cid} -->\n\n{md}"
            tier = "tier1_fallback" if result["used_fallback"] else "tier2"
            chunk_markdowns[chunk["chunk_id"]] = md
        else:
            chunk_markdowns[chunk["chunk_id"]] = _tier1_to_markdown(
                chunk["raw_text"]
            )
            tier = "tier1"

        chunk_md_path = chunk_translated_root / f"{chunk_id}.md"
        chunk_md_path.write_text(
            (
                f"<!-- chunk_id: {chunk_id} -->\n"
                f"<!-- pages: {start_page}-{end_page} -->\n"
                f"<!-- tier: {tier} -->\n\n"
                f"{chunk_markdowns[chunk_id]}"
            ),
            encoding="utf-8",
        )

    # Step 3: Build YAML frontmatter
    if heavy_count == 0 or len(fallback_chunks) == 0:
        translation_tool = "pymupdf+mineru" if heavy_count > 0 else "pymupdf"
    elif len(fallback_chunks) < heavy_count:
        translation_tool = "pymupdf+mineru"
    else:
        translation_tool = "pymupdf+tier1_fallback"

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if fallback_chunks:
        fallback_yaml = "\n" + "\n".join(f"  - {c}" for c in fallback_chunks)
    else:
        fallback_yaml = " []"

    frontmatter = (
        f"---\n"
        f"cite_key: {cite_key}\n"
        f"source_format: pdf\n"
        f"translation_tool: {translation_tool}\n"
        f"translation_timestamp: {timestamp}\n"
        f"translation_version: 1\n"
        f"page_count: {total_pages}\n"
        f"chunk_count: {len(chunks)}\n"
        f"heavy_chunk_count: {heavy_count}\n"
        f"chunk_artifacts_dir: {CHUNK_ARTIFACTS_DIR_NAME}\n"
        f"fallback_chunks:{fallback_yaml}\n"
        f"---\n\n"
    )

    # Step 4: Assemble body in page order
    body_parts = [chunk_markdowns[c["chunk_id"]] for c in chunks]
    body = "\n\n---\n\n".join(body_parts)

    # Step 5: Write output
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(frontmatter + body)
