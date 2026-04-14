#!/usr/bin/env python3
"""summarize_paper.py — Author the final two-level literature note for a paper.

Reads inputs from the paper-bank directory and Citadel per-section reading
notes, then produces a two-level literature note at:
  <vault-path>/literature/papers/<cite_key>.md

Level 1 (top): A 1–3 paragraph abstract-level summary covering the paper's
  contribution, methods, key results, and significance. Synthesized from the
  full set of section notes (not a paraphrase of the abstract). Written for a
  reader who wants the gist in under 2 minutes.

Level 2 (body): A section-by-section detailed summary that preserves key
  definitions, equations (in $$ ... $$ delimiters), main theorems (from
  theory.md), and empirical findings (from empirical.md). Each section
  heading links back to the corresponding Citadel reading note using Obsidian
  wikilink syntax: [[cite_key/section-note]].

Inputs consumed from the paper-bank directory:
  _catalog.yaml         — title, authors, year, abstract
  notation_dict.yaml    — notation entries
  _xref_index.yaml      — cross-reference index (optional enrichment)
  _theorem_index.json   — theorem/proposition index (optional; seeds key results)

Reading notes consumed from Citadel vault (<vault>/literature/papers/<key>/):
  intro.md, model.md, theory.md, empirical.md, gaps.md

The output note uses schema v2 frontmatter (compatible with ingest_paper.py).

Notation externalization: The notation section is written to a separate file
  <cite_key>-notation.md in the same Citadel directory. A wikilink
  [[<cite_key>-notation]] is inserted in place of the inline notation section.
  This pattern applies to any section classified as a reference-lookup type
  (notation, glossary) or exceeding 300 words.

Usage:
  python3 summarize_paper.py --cite-key <key> --paper-bank-dir <path>
  python3 summarize_paper.py --cite-key <key> --paper-bank-dir <path> \\
      --vault-path ~/Documents/citadel [--dry-run] [--output <path>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ─── Constants ───────────────────────────────────────────────────────────────

DEFAULT_VAULT_PATH = Path("~/Documents/citadel").expanduser()

# Ordered list of per-section reading note names (summary_polish ordering).
# These correspond to files consumed from the vault:
#   intro.md, model.md, theory.md, empirical.md
SECTION_NOTES = ["intro", "model", "theory", "empirical"]

# Filenames for each reading note consumed from the Citadel vault.
NOTE_FILENAMES: dict[str, str] = {
    "intro": "intro.md",
    "model": "model.md",
    "theory": "theory.md",
    "empirical": "empirical.md",
    "gaps": "gaps.md",
}

SECTION_NOTE_LABELS: dict[str, str] = {
    "intro": "Introduction",
    "model": "Model & Setting",
    "theory": "Theory & Proofs",
    "empirical": "Empirical Findings",
}

# Sections classified as reference-lookup type — always externalized.
REFERENCE_LOOKUP_SECTIONS = frozenset({"notation", "glossary"})

# Word count threshold above which a section is externalized to a separate file.
EXTERNALIZE_WORD_THRESHOLD = 300


# ─── I/O helpers ──────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> Any:
    """Load a YAML file; return {} if missing or empty."""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    return loaded if loaded is not None else {}


def _load_json(path: Path) -> Any:
    """Load a JSON file; return None if missing or parse error."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_md(path: Path) -> str:
    """Read a Markdown file; return '' if missing."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block from a Markdown string."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5:]


def _to_posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


# ─── Text extraction helpers ──────────────────────────────────────────────────


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _format_authors(authors: Any) -> str:
    if isinstance(authors, list):
        return ", ".join(_clean(a) for a in authors if _clean(a))
    return _clean(authors)


def _extract_key_points(text: str, limit: int = 5) -> list[str]:
    """Extract leading bullet points from Markdown text."""
    points: list[str] = []
    for line in text.splitlines():
        m = re.match(r'^[-*+]\s+(.+)$', line.strip())
        if m:
            text_part = m.group(1).strip()
            if text_part:
                points.append(text_part)
        if len(points) >= limit:
            break
    return points


def _should_externalize_section(section_type: str, content: str) -> bool:
    """Return True when a section should be written to a separate external file.

    Reference-lookup sections (notation, glossary) are always externalized.
    Any other section is externalized if its rendered content exceeds
    EXTERNALIZE_WORD_THRESHOLD words.
    """
    if section_type.lower() in REFERENCE_LOOKUP_SECTIONS:
        return True
    return len(content.split()) > EXTERNALIZE_WORD_THRESHOLD


# ─── Key results extraction ───────────────────────────────────────────────────


def _extract_key_results(
    catalog: dict[str, Any],
    note_contents: dict[str, str],
    theorem_index: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Extract key results from abstract, introduction, and theorem sections.

    Explicitly avoids simulation and empirical sections so that theoretical
    contributions are not overshadowed by data-heavy simulation results.

    If theorem_index is provided (from _theorem_index.json in the paper-bank),
    seeds the candidate list from its theorem and proposition entries first.
    """
    key_results: list[str] = []

    # Seed from _theorem_index.json theorem/proposition entries if available.
    if theorem_index:
        for entry in theorem_index:
            if not isinstance(entry, dict):
                continue
            kind = _clean(entry.get("type") or entry.get("kind") or "").lower()
            if kind in ("theorem", "proposition", "corollary", "lemma"):
                label = _clean(entry.get("label") or entry.get("name") or "")
                statement = _clean(
                    entry.get("statement") or entry.get("body") or ""
                )
                if label and statement:
                    key_results.append(f"{label}: {statement[:120]}")
                elif label:
                    key_results.append(label)
            if len(key_results) >= 4:
                break

    # Source from intro note (theoretical framing and contributions).
    intro_body = _strip_frontmatter(note_contents.get("intro", ""))
    key_results.extend(_extract_key_points(intro_body, limit=2))

    # Source from theory note (main theorems and propositions).
    theory_body = _strip_frontmatter(note_contents.get("theory", ""))
    key_results.extend(_extract_key_points(theory_body, limit=3))

    # NOTE: Do NOT draw from simulation or empirical sections.
    # Those sections are data-dense and would skew key_results toward
    # quantitative outcomes rather than theoretical contributions.

    return key_results[:5]


# ─── Level 1: Abstract-level summary ─────────────────────────────────────────


def _build_level1_summary(
    catalog: dict[str, Any],
    note_contents: dict[str, str],
    key_results: list[str] | None = None,
) -> str:
    """Build the Level 1 (abstract-level) summary paragraphs.

    Synthesizes contribution, methods, and significance from the full set of
    section notes. Does NOT paraphrase the abstract — section notes are the
    primary context. This function must be called after all section notes have
    been assembled.

    Prompt intent: 'Do not paraphrase the abstract. Synthesize the
    contribution, methods, and significance from the full set of section
    notes below.'

    Covers: contribution, methods, key results, significance.
    Target: under 2 minutes reading time.
    """
    paper = catalog.get("paper") if isinstance(catalog, dict) else {}
    if not isinstance(paper, dict):
        paper = {}

    title = _clean(paper.get("title") or catalog.get("title") or "")
    year = _clean(paper.get("year") or catalog.get("year") or "")

    paras: list[str] = []

    # Paragraph 1: Contribution & approach — synthesize from intro and model notes.
    # Do not paraphrase the abstract; section notes are the primary context.
    intro_body = _strip_frontmatter(note_contents.get("intro", ""))
    model_body = _strip_frontmatter(note_contents.get("model", ""))

    intro_points = _extract_key_points(intro_body, limit=2)
    model_points = _extract_key_points(model_body, limit=1)

    combined = intro_points + model_points
    if combined:
        paras.append(" ".join(combined[:3]))
    elif title:
        year_str = f" ({year})" if year else ""
        paras.append(f"This paper{year_str} presents work on: {title}.")

    # Paragraph 2: Key results — from dedicated extraction step (avoids
    # simulation/empirical sections — see _extract_key_results).
    if key_results:
        paras.append("Key results: " + "; ".join(key_results[:4]) + ".")
    else:
        theory_body = _strip_frontmatter(note_contents.get("theory", ""))
        theory_points = _extract_key_points(theory_body, limit=2)
        if theory_points:
            paras.append("Key results: " + "; ".join(theory_points) + ".")

    # Paragraph 3: Significance — from catalog metadata or section notes.
    significance = _clean(
        paper.get("significance") or paper.get("contribution") or
        catalog.get("significance") or ""
    )
    if not significance:
        gaps_body = _strip_frontmatter(note_contents.get("gaps", ""))
        gap_points = _extract_key_points(gaps_body, limit=1)
        if gap_points:
            significance = gap_points[0]
    if significance:
        paras.append(significance)

    if not paras:
        paras = [
            f"Literature note for {title or 'this paper'}. "
            "See section-by-section detail below."
        ]

    return "\n\n".join(paras)


# ─── Opening synthesis validation ────────────────────────────────────────────


def _validate_opening_synthesis(synthesis: str, cite_key: str) -> None:
    """Warn if the opening synthesis appears malformed.

    Checks:
    - Fewer than 3 distinct sentences: indicates the synthesis is too thin
      to serve as a meaningful abstract-level summary.
    - Raw ' · ' separator runs: indicates the synthesis was assembled from
      bullet fragments joined with separators rather than composed as prose.
    """
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', synthesis) if s.strip()]
    if len(sentences) < 3:
        print(
            f"WARNING [{cite_key}]: Opening synthesis has fewer than 3 distinct sentences "
            f"({len(sentences)} found) — malformed output, synthesis may be too thin.",
            file=sys.stderr,
        )
    if " · " in synthesis:
        print(
            f"WARNING [{cite_key}]: Opening synthesis contains raw ' · ' separator runs "
            "— malformed output, expected prose sentences not bullet fragments.",
            file=sys.stderr,
        )


# ─── Level 2: Section-by-section detailed summary ────────────────────────────


def _build_section_detail(
    cite_key: str,
    note_name: str,
    note_content: str,
) -> str:
    """Build Level 2 detailed content for one section note.

    Preserves the original reading-note body verbatim so that equations
    ($$ ... $$ blocks), theorem statements, and empirical findings survive
    into the final literature note without truncation.
    """
    body = _strip_frontmatter(note_content)
    if not body.strip():
        return f"*Reading note [[{cite_key}/{note_name}]] has no content yet.*"

    lines = body.splitlines()
    # Drop a leading H1 if present — the section heading above replaces it.
    if lines and re.match(r'^#\s+', lines[0]):
        lines = lines[1:]

    # Strip all remaining markdown headings (H2–H6). Section notes are embedded
    # under an H3 anchor (### Label — [[wikilink]]), so any H2 or lower headings
    # inside the note would invert the heading hierarchy (PDF-ISSUE-016). STRIP
    # approach: remove heading lines entirely so content renders as body text.
    lines = [line for line in lines if not line.startswith('#')]

    return "\n".join(lines).strip()


# ─── Notation section ─────────────────────────────────────────────────────────


def _build_notation_section(notation_dict: Any) -> str:
    """Build the Notation section content from notation_dict.yaml entries."""
    if not notation_dict:
        return "*No notation dictionary available.*"

    entries: list[str] = []

    if isinstance(notation_dict, dict):
        items: Any = (
            notation_dict.get("entries")
            or notation_dict.get("notation")
            or notation_dict
        )
        if isinstance(items, dict):
            for symbol, desc in items.items():
                sym = _clean(symbol)
                dsc = _clean(desc)
                if sym:
                    entries.append(f"- **{sym}**" + (f": {dsc}" if dsc else ""))
        elif isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    sym = _clean(
                        item.get("symbol") or item.get("notation") or
                        item.get("name") or ""
                    )
                    dsc = _clean(
                        item.get("description") or item.get("meaning") or
                        item.get("definition") or ""
                    )
                    if sym:
                        entries.append(f"- **{sym}**" + (f": {dsc}" if dsc else ""))
                elif isinstance(item, str):
                    val = _clean(item)
                    if val:
                        entries.append(f"- {val}")

    if not entries:
        return "*Notation dictionary is present but contains no readable entries.*"
    return "\n".join(entries)


# ─── Knowledge Gaps section ───────────────────────────────────────────────────


def _build_gaps_section(cite_key: str, gaps_content: str) -> str:
    """Build the Knowledge Gaps section from the gaps.md reading note.

    Sub-category headings (Theoretical, Methodological, Empirical, Broader
    Open Questions, etc.) must be H3 (###) under the parent ## Knowledge Gaps
    heading — NOT H2 (##). Using H2 would make sub-categories siblings of the
    parent section rather than children, breaking the document outline.

    This function demotes any H2 headings found in the gaps body to H3 and
    logs a malformed-output warning so the structural problem is visible.
    """
    body = _strip_frontmatter(gaps_content)
    if not body.strip():
        return f"*Knowledge gaps note [[{cite_key}/gaps]] has no content yet.*"

    lines = body.splitlines()
    # Drop leading H1 if present.
    if lines and re.match(r'^#\s+', lines[0]):
        lines = lines[1:]

    # Structural check: H2 headings inside gaps are sub-category headings that
    # must be H3. Demote them and warn so the gap note can be corrected.
    normalized: list[str] = []
    h2_demoted: list[str] = []
    for line in lines:
        if re.match(r'^##\s+', line):
            # ## SubCategory → ### SubCategory (add one leading #)
            h2_demoted.append(line.strip())
            normalized.append('#' + line)
        else:
            normalized.append(line)

    if h2_demoted:
        print(
            f"WARNING [{cite_key}]: Knowledge Gaps note contains H2 sub-category "
            f"headings that were demoted to H3: {h2_demoted}. "
            "Sub-categories must use ### (H3) under ## Knowledge Gaps, not ## (H2).",
            file=sys.stderr,
        )

    stripped = "\n".join(normalized).strip()
    return stripped if stripped else f"*Knowledge gaps note [[{cite_key}/gaps]] exists but is empty.*"


# ─── Frontmatter (schema v2) ──────────────────────────────────────────────────


def _load_author_keywords(paper_bank_paper_dir: Path) -> list[str]:
    """Load author keywords from author_keywords.txt in the paper-bank dir."""
    kw_path = paper_bank_paper_dir / "author_keywords.txt"
    if not kw_path.exists():
        return []
    return [line.strip() for line in kw_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _extract_methods_from_notes(note_contents: dict[str, str]) -> list[str]:
    """Extract key methods from model.md and method.md reading notes.

    Looks for a '## Key Methods' section and collects bullet items.
    """
    methods: list[str] = []
    seen: set[str] = set()
    for note_name in ("model", "method"):
        content = note_contents.get(note_name, "")
        if not content:
            continue
        # Find ## Key Methods section
        match = re.search(r'^## Key Methods\s*\n(.*?)(?=\n##|\Z)', content, re.MULTILINE | re.DOTALL)
        if not match:
            continue
        for line in match.group(1).splitlines():
            m = re.match(r'^[-*+]\s+(.+)$', line.strip())
            if m:
                method_name = m.group(1).strip()
                if method_name.lower() not in seen:
                    seen.add(method_name.lower())
                    methods.append(method_name)
    return methods


def _build_tldr_summary(
    catalog: dict[str, Any],
    note_contents: dict[str, str],
) -> str:
    """Build a one-to-two sentence TL;DR summary from section notes.

    Synthesized from intro and model/method notes, not a paraphrase of the
    abstract. Falls back to abstract if section notes are insufficient.
    """
    paper = catalog.get("paper") if isinstance(catalog, dict) else {}
    if not isinstance(paper, dict):
        paper = {}

    # Gather key contribution sentences from intro
    intro_body = _strip_frontmatter(note_contents.get("intro", ""))
    intro_points = _extract_key_points(intro_body, limit=2)

    # Gather method context
    model_body = _strip_frontmatter(note_contents.get("model", ""))
    model_points = _extract_key_points(model_body, limit=1)

    combined = intro_points + model_points
    if combined:
        return " ".join(combined[:2])

    # Fallback to abstract from catalog
    abstract = _clean(paper.get("abstract") or catalog.get("abstract") or "")
    if abstract:
        # Take first two sentences
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', abstract) if s.strip()]
        return " ".join(sentences[:2])

    title = _clean(paper.get("title") or catalog.get("title") or "")
    return f"This paper presents work on: {title}." if title else ""


def _build_frontmatter(
    cite_key: str,
    catalog: dict[str, Any],
    generated_at: str,
    author_keywords: list[str] | None = None,
    summary: str = "",
    methods: list[str] | None = None,
) -> dict[str, Any]:
    """Build schema v2 YAML frontmatter for the literature note.

    Field set is compatible with knowledge-maester ingest_paper.py schema v2.
    Includes additive fields: author_keywords, summary, methods.
    """
    paper = catalog.get("paper") if isinstance(catalog, dict) else {}
    if not isinstance(paper, dict):
        paper = {}

    title = _clean(paper.get("title") or catalog.get("title") or cite_key)
    authors_raw = paper.get("authors") or catalog.get("authors") or []
    authors = authors_raw if isinstance(authors_raw, list) else [_clean(authors_raw)]
    year = _clean(paper.get("year") or catalog.get("year") or "")
    canonical_id = _clean(
        paper.get("doi") or paper.get("arxiv_id") or
        catalog.get("doi") or catalog.get("arxiv_id") or ""
    )
    today = generated_at[:10]  # ISO date only

    fm: dict[str, Any] = {
        "type": "paper",
        "schema_version": "2",
        "cite_key": cite_key,
        "title": title,
        "authors": authors,
        "year": year,
        "canonical_id": canonical_id,
        "date": today,
        "last_updated": today,
        "review_status": "draft",
        "content_status": "summarized",
        "tags": [],
        "bank_path": f"{cite_key}/",
        "status": "active",
        "extraction_confidence": 0.9,
        "validation_status": "pending",
        "source_type": "reading_notes",
        "source_path": f"literature/papers/{cite_key}/",
        "source_parse_status": "complete",
        "bibliography_status": "pending",
        "auto_block_hash": "",
    }

    # Additive fields — only included when non-empty
    if author_keywords:
        fm["author_keywords"] = author_keywords
    if summary:
        fm["summary"] = summary
    if methods:
        fm["methods"] = methods

    return fm


# ─── Note renderer ────────────────────────────────────────────────────────────


def _render_note(
    cite_key: str,
    catalog: dict[str, Any],
    note_contents: dict[str, str],
    notation_dict: Any,
    generated_at: str,
    theorem_index: list[dict[str, Any]] | None = None,
    author_keywords: list[str] | None = None,
    methods: list[str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Render the full two-level literature note as Markdown.

    The opening summary is generated LAST, after all section notes have been
    assembled, so it can synthesize from the full body of section content
    rather than paraphrasing the abstract.

    Returns:
        (main_note_content, external_files) where external_files maps
        filename -> content for files to write alongside the main note
        (e.g., {'<cite_key>-notation.md': notation_content}).
    """
    paper = catalog.get("paper") if isinstance(catalog, dict) else {}
    if not isinstance(paper, dict):
        paper = {}
    title = _clean(paper.get("title") or catalog.get("title") or cite_key)

    # Build TL;DR summary from section notes
    tldr_summary = _build_tldr_summary(catalog, note_contents)

    # Frontmatter dict — auto_block_hash is filled in after the body is assembled.
    fm = _build_frontmatter(
        cite_key, catalog, generated_at,
        author_keywords=author_keywords,
        summary=tldr_summary,
        methods=methods,
    )

    # ── Step 1: Build section-by-section detailed summary ─────────────────────
    # Sections are assembled before the opening summary so that level1 can
    # synthesize from the complete set of section notes.
    section_parts: list[str] = [
        "## Detailed Section-by-Section Summary",
        "",
        (
            "*Each section heading links to its Citadel reading note via Obsidian wikilink. "
            "Equations are preserved in `$$ … $$` blocks. "
            "Theorems and key definitions are reproduced verbatim from the theory note.*"
        ),
        "",
    ]

    for note_name in SECTION_NOTES:
        label = SECTION_NOTE_LABELS.get(note_name, note_name.capitalize())
        wikilink = f"[[{cite_key}/{note_name}]]"
        section_parts.append(f"### {label} — {wikilink}")
        section_parts.append("")
        content = note_contents.get(note_name, "")
        section_parts.append(_build_section_detail(cite_key, note_name, content))
        section_parts.append("")

    # ── Step 2: Extract key results (avoids simulation/empirical sections) ─────
    key_results = _extract_key_results(catalog, note_contents, theorem_index)

    # ── Step 3: Build opening summary LAST, synthesizing from section notes ────
    # Prompt intent: 'Do not paraphrase the abstract. Synthesize the
    # contribution, methods, and significance from the full set of section
    # notes below.'
    level1_summary = _build_level1_summary(catalog, note_contents, key_results)
    _validate_opening_synthesis(level1_summary, cite_key)

    # ── Step 4: Notation externalization ──────────────────────────────────────
    # Reference-lookup sections (notation, glossary) and sections exceeding
    # EXTERNALIZE_WORD_THRESHOLD words are written to a separate file.
    # A wikilink [[<cite_key>-notation]] is inserted in the main note in place
    # of the inline notation content.
    notation_content = _build_notation_section(notation_dict)
    notation_file_name = f"{cite_key}-notation.md"
    external_files: dict[str, str] = {}

    if _should_externalize_section("notation", notation_content):
        external_files[notation_file_name] = (
            f"# Notation — {cite_key}\n\n{notation_content}\n"
        )
        notation_in_main = (
            f"*See [[{cite_key}-notation]] for the full notation reference.*"
        )
    else:
        notation_in_main = notation_content

    # ── Step 5: Assemble body (level1 at top, generated last) ─────────────────
    body_parts: list[str] = [f"# {title}", ""]

    # Opening summary — generated last (Step 3), placed at top of note body.
    body_parts += [
        level1_summary,
        "",
        "## Abstract-Level Summary",
        "",
        "> *Gist in under 2 minutes — contribution, methods, key results, significance.*",
        "",
    ]

    # Section-by-section detail (assembled in Step 1).
    body_parts += section_parts

    # Notation section (externalized wikilink or inline fallback).
    body_parts += [
        "## Notation",
        "",
        notation_in_main,
        "",
    ]

    # Knowledge gaps.
    gaps_wikilink = f"[[{cite_key}/gaps]]"
    body_parts += [
        f"## Knowledge Gaps — {gaps_wikilink}",
        "",
        _build_gaps_section(cite_key, note_contents.get("gaps", "")),
        "",
    ]

    # ── Step 6: Wrap body in AUTO-GENERATED markers and compute SHA-256 hash ──
    body_text = "\n".join(body_parts).rstrip()
    auto_block = (
        f"<!-- AUTO-GENERATED:BEGIN -->\n{body_text}\n<!-- AUTO-GENERATED:END -->"
    )
    block_hash = hashlib.sha256(auto_block.encode("utf-8")).hexdigest()

    # ── Step 7: Update frontmatter with hash and serialize ────────────────────
    fm["auto_block_hash"] = block_hash
    fm_yaml = yaml.safe_dump(
        fm, allow_unicode=True, default_flow_style=False, sort_keys=False
    ).strip()
    fm_block = f"---\n{fm_yaml}\n---\n"

    return f"{fm_block}\n{auto_block}\n", external_files


# ─── Main orchestration ───────────────────────────────────────────────────────


def summarize_paper(
    cite_key: str,
    paper_bank_dir: Path,
    vault_path: Path,
    output: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Produce the final two-level literature note for *cite_key*.

    Reads from *paper_bank_dir* (tries <dir>/<cite_key>/ first, then <dir>
    directly) and the corresponding Citadel vault reading notes directory.
    Writes a schema-v2 Markdown note to the vault literature/papers/ tree.
    Notation is written to a separate <cite_key>-notation.md file.

    Returns a summary dict that is also printed as JSON to stdout.
    """
    # Resolve paper-bank per-paper directory.
    # Check for _catalog.yaml at the nested path (not just directory existence)
    # so that a spurious <cite_key>/<cite_key>/ directory without _catalog.yaml
    # does not prevent the fallback to <paper_bank_dir>/_catalog.yaml (I-005).
    paper_bank_paper_dir: Path
    if (paper_bank_dir / cite_key / "_catalog.yaml").is_file():
        paper_bank_paper_dir = paper_bank_dir / cite_key
    else:
        paper_bank_paper_dir = paper_bank_dir

    vault_notes_dir = vault_path / "literature" / "papers" / cite_key
    output_path = output or (vault_path / "literature" / "papers" / f"{cite_key}.md")

    # ── Input paths ──────────────────────────────────────────────────────────
    catalog_path = paper_bank_paper_dir / "_catalog.yaml"
    notation_path = paper_bank_paper_dir / "notation_dict.yaml"
    xref_path = paper_bank_paper_dir / "_xref_index.yaml"
    theorem_index_path = paper_bank_paper_dir / "_theorem_index.json"

    # Reading notes: intro.md, model.md, theory.md, empirical.md, gaps.md
    reading_note_paths: dict[str, Path] = {
        name: vault_notes_dir / filename
        for name, filename in NOTE_FILENAMES.items()
    }

    missing_required: list[str] = []
    if not catalog_path.exists():
        missing_required.append(f"_catalog.yaml not found at {_to_posix(catalog_path)}")

    missing_optional: list[str] = []
    if not notation_path.exists():
        missing_optional.append(f"notation_dict.yaml:{_to_posix(notation_path)}")
    if not xref_path.exists():
        missing_optional.append(f"_xref_index.yaml:{_to_posix(xref_path)}")
    if not theorem_index_path.exists():
        missing_optional.append(f"_theorem_index.json:{_to_posix(theorem_index_path)}")
    for name, p in reading_note_paths.items():
        if not p.exists():
            missing_optional.append(f"{NOTE_FILENAMES[name]}:{_to_posix(p)}")

    summary: dict[str, Any] = {
        "cite_key": cite_key,
        "paper_bank_dir": _to_posix(paper_bank_paper_dir),
        "vault_path": _to_posix(vault_path),
        "output_path": _to_posix(output_path),
        "inputs_valid": len(missing_required) == 0,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "dry_run": dry_run,
    }

    if dry_run:
        return summary

    if missing_required:
        for msg in missing_required:
            print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    # ── Load data ─────────────────────────────────────────────────────────────
    catalog = _load_yaml(catalog_path)
    notation_dict = _load_yaml(notation_path)
    # _xref_index.yaml is reserved for future cross-reference enrichment

    note_contents: dict[str, str] = {
        name: _read_md(p) for name, p in reading_note_paths.items()
    }

    # Load theorem index for key-results seeding (optional).
    # Supports list format or dict-wrapped {theorems: [...], entries: [...], items: [...]}.
    theorem_index_raw = _load_json(theorem_index_path)
    theorem_index: list[dict[str, Any]] | None = None
    if isinstance(theorem_index_raw, list):
        theorem_index = theorem_index_raw
    elif isinstance(theorem_index_raw, dict):
        candidates = (
            theorem_index_raw.get("theorems") or
            theorem_index_raw.get("entries") or
            theorem_index_raw.get("items")
        )
        if isinstance(candidates, list):
            theorem_index = candidates

    # Load additive extraction fields
    author_keywords = _load_author_keywords(paper_bank_paper_dir)
    methods = _extract_methods_from_notes(note_contents)

    generated_at = datetime.now(tz=timezone.utc).isoformat()

    # ── Render & write ────────────────────────────────────────────────────────
    rendered, external_files = _render_note(
        cite_key=cite_key,
        catalog=catalog,
        note_contents=note_contents,
        notation_dict=notation_dict,
        generated_at=generated_at,
        theorem_index=theorem_index,
        author_keywords=author_keywords,
        methods=methods,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"WRITTEN: {_to_posix(output_path)}", file=sys.stderr)

    # Write external files (e.g., <cite_key>-notation.md alongside main note).
    external_paths: list[str] = []
    for filename, content in external_files.items():
        ext_path = output_path.parent / filename
        ext_path.write_text(content, encoding="utf-8")
        print(f"WRITTEN: {_to_posix(ext_path)}", file=sys.stderr)
        external_paths.append(_to_posix(ext_path))

    return {
        **summary,
        "bytes_written": len(rendered.encode("utf-8")),
        "generated_at": generated_at,
        "output_path": _to_posix(output_path),
        "external_files": external_paths,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="summarize_paper.py",
        description=(
            "Author the final two-level literature note for a paper in the Citadel vault.\n\n"
            "LEVEL 1 (top): A 1–3 paragraph abstract-level summary synthesized from the\n"
            "  full set of section notes — contribution, methods, key results, significance.\n"
            "  The opening summary is generated last, after all section notes are assembled.\n"
            "  It does not paraphrase the abstract.\n\n"
            "LEVEL 2 (body): A section-by-section detailed summary that preserves key\n"
            "  definitions, equations ($$ … $$ delimiters), main theorems (from theory.md),\n"
            "  and empirical findings (from empirical.md). Each section heading links back\n"
            "  to the corresponding Citadel reading note via Obsidian [[wikilink]] syntax.\n\n"
            "NOTATION: Written to a separate <cite_key>-notation.md file. A [[wikilink]]\n"
            "  is inserted in the main note in place of the inline notation section.\n"
            "  Any section classified as reference-lookup (notation, glossary) or exceeding\n"
            "  300 words is also externalized."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cite-key",
        required=True,
        metavar="CITE_KEY",
        help=(
            "Paper cite key (e.g. demo2026paper). Used to locate paper-bank inputs "
            "and Citadel reading notes."
        ),
    )
    parser.add_argument(
        "--paper-bank-dir",
        required=True,
        metavar="DIR",
        help=(
            "Path to the paper-bank directory. The script looks for "
            "<DIR>/<cite_key>/_catalog.yaml first; if absent it tries "
            "<DIR>/_catalog.yaml directly."
        ),
    )
    parser.add_argument(
        "--vault-path",
        default=str(DEFAULT_VAULT_PATH),
        metavar="DIR",
        help=(
            f"Path to the Citadel vault root. "
            f"Default: {DEFAULT_VAULT_PATH}"
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help=(
            "Override the output path for the literature note. "
            "Default: <vault-path>/literature/papers/<cite-key>.md"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and print a JSON summary without writing any files.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    result = summarize_paper(
        cite_key=args.cite_key,
        paper_bank_dir=Path(args.paper_bank_dir).expanduser().resolve(),
        vault_path=Path(args.vault_path).expanduser().resolve(),
        output=Path(args.output).expanduser().resolve() if args.output else None,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
