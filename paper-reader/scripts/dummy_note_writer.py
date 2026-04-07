#!/usr/bin/env python3
"""Create or append dummy paper notes in the Citadel vault for foundation/alternative citations."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

# Default vault path — override via argument or parameter
DEFAULT_VAULT_PAPERS_DIR = Path.home() / "Documents" / "citadel" / "literature" / "papers"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
SEEN_SECTION_HEADER = "## Seen in Other Papers"

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "this", "that", "these", "those",
    "it", "its", "as", "into", "which", "their", "they", "we", "our", "such",
    "via", "also", "both", "other", "using", "used", "show", "shows", "paper",
    "work", "model", "method", "approach", "technique", "based", "between",
    "through", "over", "under", "about", "around", "without",
}


def _extract_frontmatter_field(text: str, key: str) -> str | None:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+)$", re.MULTILINE)
    m = pattern.search(match.group(1))
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _resolve_cite_key(author_year: str, vault_dir: Path) -> str | None:
    """Scan vault_dir for a .md file whose cite_key frontmatter matches author_year slug."""
    if not vault_dir.exists():
        return None
    slug = re.sub(r"[^a-z0-9]", "", author_year.lower())
    for note_path in vault_dir.glob("*.md"):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        existing_key = _extract_frontmatter_field(text, "cite_key")
        if existing_key:
            existing_slug = re.sub(r"[^a-z0-9]", "", existing_key.lower())
            if existing_slug == slug:
                return existing_key
    return None


def _extract_keywords(description: str) -> list[str]:
    """Rule-based extraction of 2–4 topic keywords from a description string."""
    words = re.findall(r"\b[a-zA-Z][a-zA-Z\-]{3,}\b", description)
    seen: set[str] = set()
    keywords: list[str] = []
    for w in words:
        lower = w.lower()
        if lower not in _STOP_WORDS and lower not in seen:
            seen.add(lower)
            keywords.append(lower)
        if len(keywords) >= 4:
            break
    if len(keywords) < 2:
        raw = re.findall(r"\b[a-zA-Z]{2,}\b", description)
        for w in raw:
            lower = w.lower()
            if lower not in _STOP_WORDS and lower not in seen:
                seen.add(lower)
                keywords.append(lower)
            if len(keywords) >= 2:
                break
    return keywords[:4] if keywords else ["unknown"]


def _build_seen_subsection(citing_cite_key: str, description: str, role: str) -> str:
    return (
        f"### In {citing_cite_key} (Introduction)\n\n"
        f"**Role**: {role}\n\n"
        f"{description.strip()}\n"
    )


def _build_dummy_note(
    ref_cite_key: str,
    importance: str,
    citing_cite_key: str,
    role: str,
    description: str,
) -> str:
    keywords = _extract_keywords(description)
    hints_yaml = "\n".join(f"  - {kw}" for kw in keywords)
    frontmatter = (
        "---\n"
        f"cite_key: {ref_cite_key}\n"
        f"status: dummy\n"
        f"importance: {importance}\n"
        f"cited_by: {citing_cite_key}\n"
        f"cited_as: {role}\n"
        f"section_relevance_hints:\n"
        f"{hints_yaml}\n"
        "---"
    )
    seen_subsection = _build_seen_subsection(citing_cite_key, description, role)
    priority_map = {"high": "Read soon", "medium": "Read when relevant", "low": "Read if needed"}
    priority_text = priority_map.get(importance, "Read when relevant")
    reading_priority = f"## Reading Priority\n{priority_text}"
    return f"{frontmatter}\n\n{SEEN_SECTION_HEADER}\n\n{seen_subsection}\n\n{reading_priority}\n"


def _append_seen_subsection(
    existing_text: str, citing_cite_key: str, description: str, role: str
) -> str:
    """Append a new subsection under ## Seen in Other Papers without touching other sections."""
    subsection = _build_seen_subsection(citing_cite_key, description, role)
    header_pos = existing_text.find(SEEN_SECTION_HEADER)
    if header_pos == -1:
        # Section absent — append it at the end
        return existing_text.rstrip() + f"\n\n{SEEN_SECTION_HEADER}\n\n{subsection}"
    # Find the end of the ## Seen in Other Papers section (next ## heading or EOF)
    after_header = existing_text[header_pos + len(SEEN_SECTION_HEADER):]
    next_h2 = re.search(r"\n## ", after_header)
    if next_h2:
        insert_pos = header_pos + len(SEEN_SECTION_HEADER) + next_h2.start()
        return existing_text[:insert_pos].rstrip() + f"\n\n{subsection}" + existing_text[insert_pos:]
    return existing_text.rstrip() + f"\n\n{subsection}"


def _update_xref_dummy_created(xref_path: Path, ref_cite_key: str) -> None:
    """Set dummy_note_created: True for *ref_cite_key* in *xref_path*.

    Loads _xref_index.yaml, finds the citation entry matching *ref_cite_key*,
    sets dummy_note_created to True, and writes the file back.  No-ops if the
    file is absent or the entry does not yet exist.
    """
    if not xref_path.exists():
        return
    try:
        with open(xref_path, encoding="utf-8") as fh:
            xref = yaml.safe_load(fh) or {}
        changed = False
        for entry in xref.get("citations", []):
            if entry.get("cited_key") == ref_cite_key:
                entry["dummy_note_created"] = True
                changed = True
        if changed:
            with open(xref_path, "w", encoding="utf-8") as fh:
                yaml.dump(xref, fh, default_flow_style=False, allow_unicode=True)
    except Exception:
        pass


def create_dummy_notes(
    citations: list[dict[str, Any]],
    citing_cite_key: str,
    vault_papers_dir: "Path | str | None" = None,
    dry_run: bool = False,
    paper_bank_root: "str | None" = None,
) -> dict[str, Any]:
    """Create or append dummy paper notes for foundation/alternative citations.

    Args:
        citations: List of citation dicts (from run_step42) with keys:
            author_year, role, importance, description, dummy_eligible.
        citing_cite_key: cite_key of the paper that contains these citations.
        vault_papers_dir: Override path to citadel/literature/papers/.
            Defaults to ~/Documents/citadel/literature/papers/.
        dry_run: If True, print planned actions without writing any files.

    Returns:
        dict with keys: notes_created, notes_updated, notes_skipped, cite_key_resolutions.
    """
    vault_dir = Path(vault_papers_dir) if vault_papers_dir is not None else DEFAULT_VAULT_PAPERS_DIR

    notes_created = 0
    notes_updated = 0
    notes_skipped = 0
    cite_key_resolutions: dict[str, str] = {}

    eligible = [c for c in citations if c.get("dummy_eligible")]
    skipped_count = len(citations) - len(eligible)

    for idx, citation in enumerate(eligible):
        author_year = citation.get("author_year", "")
        role = citation.get("role", "foundation")
        importance = citation.get("importance", "medium")
        description = citation.get("description", "")

        # Best-effort cite_key resolution: scan vault for matching frontmatter cite_key
        resolved = _resolve_cite_key(author_year, vault_dir)
        if resolved:
            ref_cite_key = resolved
        else:
            slug = re.sub(r"[^a-z0-9]", "", author_year.lower())
            ref_cite_key = slug if slug else f"unknown_{idx}"
        cite_key_resolutions[author_year] = ref_cite_key

        note_path = vault_dir / f"{ref_cite_key}.md"

        if dry_run:
            if note_path.exists():
                print(f"[dry-run] append {note_path} (cite_key={ref_cite_key})")
            else:
                print(f"[dry-run] create {note_path} (cite_key={ref_cite_key})")
            continue

        if note_path.is_file():
            existing = note_path.read_text(encoding="utf-8")
            updated = _append_seen_subsection(existing, citing_cite_key, description, role)
            note_path.write_text(updated, encoding="utf-8")
            notes_updated += 1
            if paper_bank_root is not None:
                _xref_path = Path(os.path.expanduser(str(paper_bank_root))) / citing_cite_key / "_xref_index.yaml"
                _update_xref_dummy_created(_xref_path, ref_cite_key)
        else:
            vault_dir.mkdir(parents=True, exist_ok=True)
            content = _build_dummy_note(ref_cite_key, importance, citing_cite_key, role, description)
            note_path.write_text(content, encoding="utf-8")
            notes_created += 1
            if paper_bank_root is not None:
                _xref_path = Path(os.path.expanduser(str(paper_bank_root))) / citing_cite_key / "_xref_index.yaml"
                _update_xref_dummy_created(_xref_path, ref_cite_key)

    if not dry_run:
        notes_skipped = skipped_count

    return {
        "notes_created": notes_created,
        "notes_updated": notes_updated,
        "notes_skipped": notes_skipped,
        "cite_key_resolutions": cite_key_resolutions,
    }


# ---------------------------------------------------------------------------
# CLI entry point (dry-run / demo mode)
# ---------------------------------------------------------------------------

def _make_demo_citations(cite_key: str) -> list[dict[str, Any]]:
    return [
        {
            "author_year": f"{cite_key} 2020",
            "role": "foundation",
            "importance": "high",
            "description": (
                "Introduces neural sequence models for event prediction and analysis "
                "with neural network intensity estimation."
            ),
            "dummy_eligible": True,
        },
        {
            "author_year": "baseline1988 1988",
            "role": "alternative",
            "importance": "medium",
            "description": "Classical statistical estimation for self-exciting point processes.",
            "dummy_eligible": True,
        },
        {
            "author_year": "background_survey 2015",
            "role": "background",
            "importance": "low",
            "description": "General survey on stochastic processes.",
            "dummy_eligible": False,
        },
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or append dummy vault notes for intro citations."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without writing any files.",
    )
    parser.add_argument(
        "--cite-key",
        required=True,
        help="cite_key of the citing paper (e.g. smith2024neural).",
    )
    parser.add_argument(
        "--vault-papers-dir",
        default=None,
        help="Override path to citadel/literature/papers/.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    demo_citations = _make_demo_citations(args.cite_key)
    vault_papers_dir = Path(args.vault_papers_dir) if args.vault_papers_dir else None
    result = create_dummy_notes(
        citations=demo_citations,
        citing_cite_key=args.cite_key,
        vault_papers_dir=vault_papers_dir,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        import json
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
