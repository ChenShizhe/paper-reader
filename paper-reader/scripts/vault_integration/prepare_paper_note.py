#!/usr/bin/env python3
"""Prepare a staged main vault paper note for ingestion.

This module builds ``_vault_paper_note.md`` in paper-bank from catalog,
cross-reference, vault-search, and optional per-section Citadel notes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SECTION_FILENAMES: dict[str, str] = {
    "intro": "intro.md",
    "model": "model.md",
    "method": "method.md",
    "theory": "theory.md",
    "simulation": "simulation.md",
    "real_data": "real_data.md",
    "discussion": "discussion.md",
}

REQUIRED_HEADINGS = [
    "## Summary",
    "## Claimed Contributions",
    "## Key Claims",
    "## Methodology",
    "## Knowledge Gaps",
    "## Links",
]

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
NUMBERED_ITEM_RE = re.compile(r"^\s*(\d+)[\.)]\s+(.+?)\s*$")
BULLET_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
VERIFY_LABEL_RE = re.compile(r"\s*\[(?:Supported|Partially Supported|Unsupported|Uncertain)\]\s*$")


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _split_frontmatter(markdown: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    raw_frontmatter = match.group(1)
    try:
        loaded = yaml.safe_load(raw_frontmatter) or {}
        frontmatter = loaded if isinstance(loaded, dict) else {}
    except yaml.YAMLError:
        frontmatter = {}
    return frontmatter, markdown[match.end():]


def _extract_heading_block(markdown: str, heading: str) -> str:
    """Return the content block for a heading title, case-insensitive."""
    headings = list(HEADING_RE.finditer(markdown))
    target = heading.strip().lower()
    for idx, found in enumerate(headings):
        title = found.group(2).strip().lower()
        if title != target:
            continue
        start = found.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(markdown)
        return markdown[start:end].strip()
    return ""


def _extract_first_paragraph(markdown: str) -> str:
    lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith("#"):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def _collect_section_summaries(catalog: dict, max_items: int = 3) -> list[str]:
    summaries: list[str] = []
    for section in catalog.get("sections", []) or []:
        summary = section.get("summary") if isinstance(section, dict) else None
        if summary:
            cleaned = re.sub(r"\s+", " ", str(summary)).strip()
            if cleaned:
                summaries.append(cleaned)
        if len(summaries) >= max_items:
            break
    return summaries


def _extract_claimed_contributions(intro_markdown: str | None, catalog: dict) -> tuple[list[str], str]:
    if intro_markdown:
        _, body = _split_frontmatter(intro_markdown)
        contributions_block = ""
        for _heading in ("Claimed Contributions", "Main Contributions", "Contributions"):
            contributions_block = _extract_heading_block(body, _heading)
            if contributions_block:
                break
        contributions: list[str] = []
        for line in contributions_block.splitlines():
            matched = NUMBERED_ITEM_RE.match(line)
            if not matched:
                continue
            claim = VERIFY_LABEL_RE.sub("", matched.group(2)).strip()
            if claim:
                contributions.append(claim)
        if contributions:
            return contributions, "intro.md"

    fallback = _collect_section_summaries(catalog, max_items=3)
    if fallback:
        return [f"[fallback: catalog] {item}" for item in fallback], "catalog"
    return ["[fallback: unavailable] No claimed contributions extracted from intro.md or catalog summaries."], "none"


def _extract_summary(intro_markdown: str | None, catalog: dict) -> tuple[str, str]:
    if intro_markdown:
        _, body = _split_frontmatter(intro_markdown)
        positioning = _extract_heading_block(body, "Positioning")
        if positioning:
            first_paragraph = _extract_first_paragraph(positioning)
            if first_paragraph:
                return first_paragraph, "intro.md"
        first_paragraph = _extract_first_paragraph(body)
        if first_paragraph:
            return first_paragraph, "intro.md"

    fallback_summaries = _collect_section_summaries(catalog, max_items=1)
    if fallback_summaries:
        return f"[fallback: catalog] {fallback_summaries[0]}", "catalog"
    return "[fallback: unavailable] Summary not available from intro.md or catalog.", "none"


def _load_knowledge_gaps(catalog: dict, work_dir: Path) -> tuple[list[dict], str]:
    paper = catalog.get("paper") or {}
    if not isinstance(paper, dict):
        paper = {}

    ptr = paper.get("knowledge_gaps_file")
    if ptr:
        gaps_path = work_dir / str(ptr)
        if gaps_path.exists():
            try:
                loaded = _load_yaml(gaps_path) or {}
            except (OSError, yaml.YAMLError):
                return [], "knowledge_gaps_yaml_invalid"
            gaps = loaded.get("gaps", []) if isinstance(loaded, dict) else []
            valid = [g for g in gaps if isinstance(g, dict)]
            return valid, "knowledge_gaps_yaml"
        return [], "knowledge_gaps_yaml_missing"

    embedded = paper.get("knowledge_gaps", [])
    if isinstance(embedded, list):
        valid = [g for g in embedded if isinstance(g, dict)]
        if valid:
            return valid, "catalog_embedded"
    return [], "none"


def _render_key_claims_table(xref_index: dict) -> str:
    rows: list[tuple[str, str, str, str]] = []

    for theorem in (xref_index.get("theorems") or [])[:3]:
        if not isinstance(theorem, dict):
            continue
        claim = str(theorem.get("plain_english") or theorem.get("result_id") or "").strip()
        if not claim:
            continue
        rows.append((claim, "Theory", "❓ Cannot verify", "From _xref_index.yaml theorem entries."))

    for equation in (xref_index.get("equations") or [])[:2]:
        if not isinstance(equation, dict):
            continue
        claim = str(equation.get("description") or equation.get("eq_id") or "").strip()
        if not claim:
            continue
        rows.append((claim, "Methodology", "❓ Cannot verify", "From _xref_index.yaml equation entries."))

    if not rows:
        rows.append(
            (
                "[fallback] No structured key claims available.",
                "N/A",
                "❓ Cannot verify",
                "Populate after claim verification outputs are available.",
            )
        )

    lines = [
        "| Claim | Section | Vault status | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for claim, section, status, notes in rows:
        c = claim.replace("|", r"\|")
        n = notes.replace("|", r"\|")
        lines.append(f"| {c} | {section} | {status} | {n} |")
    return "\n".join(lines)


def _render_methodology(
    section_notes: dict[str, str],
    catalog: dict,
) -> str:
    method_md = section_notes.get("method")
    model_md = section_notes.get("model")
    theory_md = section_notes.get("theory")

    lines: list[str] = []
    if method_md:
        _, body = _split_frontmatter(method_md)
        algo = _extract_heading_block(body, "Algorithm")
        methodology = _extract_heading_block(body, "Methodology")
        method_text = _extract_first_paragraph(algo or methodology or body)
        if method_text:
            lines.append(f"- Method note: {method_text}")

    if model_md:
        _, body = _split_frontmatter(model_md)
        model_text = _extract_first_paragraph(_extract_heading_block(body, "Model Formulation") or body)
        if model_text:
            lines.append(f"- Model note: {model_text}")

    if theory_md:
        _, body = _split_frontmatter(theory_md)
        theory_text = _extract_first_paragraph(_extract_heading_block(body, "Main Theorem") or body)
        if theory_text:
            lines.append(f"- Theory note: {theory_text}")

    if lines:
        return "\n".join(lines)

    method_summaries: list[str] = []
    for section in catalog.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        if str(section.get("section_type") or "").lower() != "methods":
            continue
        summary = section.get("summary")
        if summary:
            method_summaries.append(re.sub(r"\s+", " ", str(summary)).strip())
    if method_summaries:
        return "\n".join([f"- [fallback: catalog] {item}" for item in method_summaries[:3]])

    return "- [fallback: unavailable] Methodology details are not yet available."


def _render_knowledge_gaps(gaps: list[dict], source: str) -> str:
    if not gaps:
        return "- No knowledge gaps identified."

    lines: list[str] = []
    for idx, gap in enumerate(gaps, start=1):
        gap_id = str(gap.get("id") or gap.get("gap_id") or f"gap-{idx:03d}")
        desc = str(gap.get("description") or gap.get("gap") or gap.get("title") or "").strip()
        if not desc:
            desc = "Gap description missing."
        gap_type = str(gap.get("type") or "unknown")
        severity = str(gap.get("severity") or "unknown")
        status = str(gap.get("status") or "open")
        next_action = str(gap.get("next_action") or "TBD")
        lines.append(
            f"- {gap_id} [{gap_type}/{severity}, status: {status}]: {desc} "
            f"(next_action: {next_action})"
        )
    return "\n".join(lines)


def _extract_related_papers_from_search(vault_search: dict, cite_key: str) -> list[str]:
    related: list[str] = []
    papers = (vault_search.get("results") or {}).get("papers", []) if isinstance(vault_search, dict) else []
    for hit in papers:
        if not isinstance(hit, dict):
            continue
        note_path = str(hit.get("note_path") or "").strip()
        if not note_path:
            continue
        note_obj = Path(note_path)
        candidate = note_obj.stem
        if candidate in {
            "index",
            "intro",
            "model",
            "method",
            "theory",
            "simulation",
            "real_data",
            "discussion",
            "notation",
        }:
            candidate = note_obj.parent.name
        if candidate and candidate != cite_key and candidate not in related:
            related.append(candidate)
    return related


def _collect_concepts_for_frontmatter(work_dir: Path) -> list[str]:
    concepts_dir = work_dir / "concepts"
    if not concepts_dir.exists():
        return []
    slugs: list[str] = []
    for path in sorted(concepts_dir.glob("*.md")):
        if path.stem not in slugs:
            slugs.append(path.stem)
    return slugs


def _render_links(
    related_papers: list[str],
    xref_index: dict,
    concepts: list[str],
    authors: list[str],
    work_dir: Path,
) -> str:
    citations = []
    for item in xref_index.get("citations", []) or []:
        if isinstance(item, dict):
            cite_key = str(item.get("cite_key") or "").strip()
            if cite_key and cite_key not in citations:
                citations.append(cite_key)

    author_links = []
    for author in authors:
        normalized = re.sub(r"[^a-z0-9]+", "-", author.lower()).strip("-")
        if normalized:
            author_links.append(f"[[{normalized}]]")

    related_links = ", ".join([f"[[{p}]]" for p in related_papers]) if related_papers else "(none)"
    cite_links = ", ".join([f"[[{c}]]" for c in citations]) if citations else "(none)"
    concept_links = ", ".join([f"[[{c}]]" for c in concepts]) if concepts else "(none)"
    author_links_text = ", ".join(author_links) if author_links else "(none)"
    notation_path = work_dir / "notation_dict.yaml"
    notation_text = str(notation_path) if notation_path.exists() else "(missing)"

    return "\n".join(
        [
            f"- Related: {related_links}",
            f"- Cites: {cite_links}",
            f"- Concepts: {concept_links}",
            f"- Authors: {author_links_text}",
            f"- Notation: {notation_text}",
        ]
    )


def _build_frontmatter(
    cite_key: str,
    paper_meta: dict,
    related_papers: list[str],
    concepts: list[str],
    section_notes_found: dict[str, bool],
) -> str:
    title = str(paper_meta.get("title") or cite_key)
    authors = paper_meta.get("authors") if isinstance(paper_meta.get("authors"), list) else []
    year = paper_meta.get("year")
    journal = str(paper_meta.get("journal") or "")
    tags = paper_meta.get("vault_tags") if isinstance(paper_meta.get("vault_tags"), list) else []
    canonical_id = str(paper_meta.get("canonical_id") or "")
    content_status = "full-text" if any(section_notes_found.values()) else "metadata-only"
    review_status = "reviewed" if all(section_notes_found.values()) else "draft"

    data: dict[str, Any] = {
        "type": "paper",
        "cite_key": cite_key,
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "canonical_id": canonical_id,
        "content_status": content_status,
        "review_status": review_status,
        "bank_path": f"{cite_key}/",
        "tags": tags,
        "last_updated": datetime.now(tz=timezone.utc).date().isoformat(),
        "status": "active",
        "related_papers": related_papers,
        "concepts": concepts,
        "vault_note_version": 1,
    }
    return "---\n" + yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False) + "---\n"


def _build_note_markdown(
    cite_key: str,
    title: str,
    summary_text: str,
    claimed_contributions: list[str],
    key_claims_table: str,
    methodology_text: str,
    knowledge_gaps_text: str,
    links_text: str,
    frontmatter_text: str,
) -> str:
    contribution_lines = [f"{idx}. {claim}" for idx, claim in enumerate(claimed_contributions, start=1)]
    return (
        frontmatter_text
        + f"\n# {title}\n\n"
        + "## Summary\n\n"
        + summary_text
        + "\n\n"
        + "## Claimed Contributions\n\n"
        + ("\n".join(contribution_lines) if contribution_lines else "1. [fallback: unavailable] No contributions found.")
        + "\n\n"
        + "## Key Claims\n\n"
        + key_claims_table
        + "\n\n"
        + "## Methodology\n\n"
        + methodology_text
        + "\n\n"
        + "## Knowledge Gaps\n\n"
        + knowledge_gaps_text
        + "\n\n"
        + "## Links\n\n"
        + links_text
        + "\n"
    )


def prepare_paper_note(
    work_dir: str | Path,
    cite_key: str,
    vault_path: str | Path,
    output: str | Path,
    dry_run: bool = False,
) -> dict:
    """Prepare `_vault_paper_note.md` from staged paper-bank and vault inputs."""
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    output = Path(output)

    catalog_path = work_dir / "_catalog.yaml"
    xref_path = work_dir / "_xref_index.yaml"
    vault_search_path = work_dir / "_vault_search_results.json"

    if not catalog_path.exists():
        if dry_run:
            return {
                "cite_key": cite_key,
                "inputs_valid": False,
                "sections_found": {},
                "claimed_contributions_count": 0,
                "knowledge_gaps_source": "none",
                "missing_required_inputs": [str(catalog_path)],
            }
        print(f"ERROR: required file missing: {catalog_path}", file=sys.stderr)
        sys.exit(1)

    try:
        catalog_loaded = _load_yaml(catalog_path) or {}
    except (OSError, yaml.YAMLError):
        if dry_run:
            return {
                "cite_key": cite_key,
                "inputs_valid": False,
                "sections_found": {},
                "claimed_contributions_count": 0,
                "knowledge_gaps_source": "none",
                "missing_required_inputs": [str(catalog_path)],
            }
        print(f"ERROR: invalid YAML in {catalog_path}", file=sys.stderr)
        sys.exit(1)

    catalog = catalog_loaded if isinstance(catalog_loaded, dict) else {}
    paper_meta = catalog.get("paper") if isinstance(catalog.get("paper"), dict) else {}
    if not isinstance(paper_meta, dict):
        paper_meta = {}

    xref_index: dict = {}
    if xref_path.exists():
        try:
            loaded = _load_yaml(xref_path) or {}
            if isinstance(loaded, dict):
                xref_index = loaded
        except (OSError, yaml.YAMLError):
            xref_index = {}

    vault_search: dict = {}
    if vault_search_path.exists():
        try:
            parsed = json.loads(_read_text(vault_search_path))
            if isinstance(parsed, dict):
                vault_search = parsed
        except (json.JSONDecodeError, OSError):
            vault_search = {}

    note_dir = vault_path / "literature" / "papers" / cite_key
    section_notes: dict[str, str] = {}
    section_notes_found: dict[str, bool] = {}
    for key, filename in SECTION_FILENAMES.items():
        path = note_dir / filename
        if path.exists():
            try:
                section_notes[key] = _read_text(path)
                section_notes_found[key] = True
            except OSError:
                section_notes_found[key] = False
        else:
            section_notes_found[key] = False

    summary_text, summary_source = _extract_summary(section_notes.get("intro"), catalog)
    claimed_contributions, claimed_source = _extract_claimed_contributions(section_notes.get("intro"), catalog)
    knowledge_gaps, knowledge_gaps_source = _load_knowledge_gaps(catalog, work_dir)

    related_from_catalog = paper_meta.get("related_papers") if isinstance(paper_meta.get("related_papers"), list) else []
    related_papers = [str(item) for item in related_from_catalog if item]
    for found in _extract_related_papers_from_search(vault_search, cite_key):
        if found not in related_papers:
            related_papers.append(found)

    concepts = _collect_concepts_for_frontmatter(work_dir)
    authors = [str(a) for a in (paper_meta.get("authors") or []) if a]

    frontmatter_text = _build_frontmatter(
        cite_key=cite_key,
        paper_meta=paper_meta,
        related_papers=related_papers,
        concepts=concepts,
        section_notes_found=section_notes_found,
    )
    title = str(paper_meta.get("title") or cite_key)
    key_claims_table = _render_key_claims_table(xref_index)
    methodology_text = _render_methodology(section_notes, catalog)
    knowledge_gaps_text = _render_knowledge_gaps(knowledge_gaps, knowledge_gaps_source)
    links_text = _render_links(
        related_papers=related_papers,
        xref_index=xref_index,
        concepts=concepts,
        authors=authors,
        work_dir=work_dir,
    )
    note_markdown = _build_note_markdown(
        cite_key=cite_key,
        title=title,
        summary_text=summary_text,
        claimed_contributions=claimed_contributions,
        key_claims_table=key_claims_table,
        methodology_text=methodology_text,
        knowledge_gaps_text=knowledge_gaps_text,
        links_text=links_text,
        frontmatter_text=frontmatter_text,
    )

    if "[fallback:" in note_markdown:
        raise RuntimeError(
            f"Unresolved [fallback:] token detected in rendered vault note for cite_key={cite_key!r}. "
            "Inspect the note-building pipeline: at least one data source returned a fallback placeholder "
            "that was not resolved before output. Check contributions, summary, methodology, and knowledge-gaps sources."
        )

    sections_found = {
        "section_notes_present": [name for name, present in section_notes_found.items() if present],
        "required_headings": REQUIRED_HEADINGS,
    }
    dry_run_summary = {
        "cite_key": cite_key,
        "inputs_valid": True,
        "sections_found": sections_found,
        "claimed_contributions_count": len(claimed_contributions),
        "knowledge_gaps_source": knowledge_gaps_source,
        "summary_source": summary_source,
        "claimed_contributions_source": claimed_source,
        "would_write": str(output),
    }

    if dry_run:
        return dry_run_summary

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(note_markdown, encoding="utf-8")
    return {
        **dry_run_summary,
        "output_path": str(output),
        "bytes_written": len(note_markdown.encode("utf-8")),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare staged _vault_paper_note.md with extended paper schema sections."
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--cite-key", required=True, help="Paper cite key")
    parser.add_argument("--vault-path", required=True, help="Path to citadel vault root")
    parser.add_argument("--output", required=True, help="Output path for staged _vault_paper_note.md")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and print JSON summary without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = prepare_paper_note(
        work_dir=args.work_dir,
        cite_key=args.cite_key,
        vault_path=args.vault_path,
        output=args.output,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
