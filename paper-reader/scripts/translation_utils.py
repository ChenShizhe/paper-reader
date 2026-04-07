"""Shared utilities for translation pipelines."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


def write_pdf_manifest(
    paper_bank_dir: Path,
    *,
    cite_key: str,
    tool: str,
    translation_timestamp: str,
    page_count: int,
    chunks: list[dict[str, Any]],
    fallback_chunks: list[str],
    chunk_artifacts_dir: str = "pdf_segments",
) -> Path:
    """Write PDF translation manifest to the paper-bank directory.

    Assigns a tier label to every chunk:
      - "tier1"          for prose (not heavy) chunks
      - "tier2"          for heavy chunks where MinerU succeeded
      - "tier1_fallback" for heavy chunks where MinerU fell back to plain text

    Collision-safe filename: writes to ``_translation_manifest_pdf.json`` when
    ``_translation_manifest.json`` already exists (written by the LaTeX/pandoc
    path), so the two manifests never collide.

    Args:
        paper_bank_dir: Path to the paper-bank directory for this cite_key.
        cite_key: Paper citekey.
        tool: Translation tool identifier (must contain "pymupdf").
        translation_timestamp: ISO-format timestamp string.
        page_count: Total page count.
        chunks: List of chunk dicts from extract_tier1 (keys: chunk_id,
            start_page, end_page, is_heavy).
        fallback_chunks: List of chunk_ids where MinerU fell back to tier1.

    Returns:
        Path to the written manifest file.
    """
    fallback_set = set(fallback_chunks)
    tier_assignments: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        is_heavy = chunk.get("is_heavy", False)
        if not is_heavy:
            tier = "tier1"
        elif chunk_id in fallback_set:
            tier = "tier1_fallback"
        else:
            tier = "tier2"
        tier_assignments.append({
            "chunk_id": chunk_id,
            "tier": tier,
            "pages": [chunk["start_page"], chunk["end_page"]],
        })

    manifest: dict[str, Any] = {
        "cite_key": cite_key,
        "tool": tool,
        "source_format": "pdf",
        "translation_timestamp": translation_timestamp,
        "page_count": int(page_count),
        "chunk_count": len(chunks),
        "heavy_chunk_count": sum(1 for c in chunks if c.get("is_heavy", False)),
        "fallback_chunks": list(fallback_chunks),
        "tier_assignments": tier_assignments,
        "chunk_artifacts_dir": chunk_artifacts_dir,
    }

    # Collision-safe filename: _translation_manifest.json may already exist from
    # the LaTeX/pandoc translation path.  Use _translation_manifest_pdf.json to
    # avoid overwriting it.
    latex_manifest = paper_bank_dir / "_translation_manifest.json"
    if latex_manifest.exists():
        manifest_path = paper_bank_dir / "_translation_manifest_pdf.json"
    else:
        manifest_path = paper_bank_dir / "_translation_manifest.json"

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


_MAIN_STEMS = {"main", "paper"}
_SUPPLEMENT_HINT_RE = re.compile(r"(?:^|[_\-.])(supplement|supp|appendix)(?:$|[_\-.])", re.IGNORECASE)
_STRUCTURAL_ELEMENT_RE = re.compile(
    r"\\(?:section|subsection|subsubsection|paragraph|subparagraph|"
    r"begin\{(?:equation|align|gather|theorem|lemma|proposition|corollary)\}|"
    r"input|include)\b"
)


def _stem_token(stem: str) -> str:
    token = re.split(r"[_\-.]", stem.strip().lower())[0]
    return token


def compute_common_root_name(tex_files: list[Path]) -> str | None:
    """Infer the dominant root token across .tex files.

    Supplement-like names are excluded so they do not win the tie-breaker.
    """
    tokens: list[str] = []
    for path in tex_files:
        stem = path.stem.lower()
        token = _stem_token(stem)
        if not token:
            continue
        if _SUPPLEMENT_HINT_RE.search(stem):
            continue
        tokens.append(token)
    if not tokens:
        return None
    counts = Counter(tokens)
    # Stable tie-break: higher frequency first, then shorter token, then lexicographic.
    return sorted(counts.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0][0]


def _score_root_tex(tex_path: Path, *, common_root_name: str | None = None) -> int:
    """Score root TeX candidates, preferring main paper files over supplements.

    The primary signal is structural richness. When tied, the function favors
    conventional main-file names (main/paper/common root token) and penalizes
    supplement-like names.
    """
    try:
        text = tex_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return -10**9

    stem = tex_path.stem.lower()
    token = _stem_token(stem)
    score = 0

    if r"\documentclass" in text:
        score += 200
    if r"\begin{document}" in text:
        score += 120
    if r"\end{document}" in text:
        score += 40

    # Element count dominates size so ties are resolved semantically.
    element_count = len(_STRUCTURAL_ELEMENT_RE.findall(text))
    score += min(element_count, 1200)

    if stem in _MAIN_STEMS:
        score += 35
    elif token in _MAIN_STEMS:
        score += 25

    if common_root_name and token == common_root_name:
        score += 18

    if _SUPPLEMENT_HINT_RE.search(stem):
        score -= 50

    # Path depth and file size stay as weak tie-breakers only.
    score -= len(tex_path.parts)
    score += min(tex_path.stat().st_size // 200_000, 6)
    return score
