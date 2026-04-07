#!/usr/bin/env python3
"""Create or update author notes in the Citadel vault with deterministic ASCII-safe slugs."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

# Default vault path — override via argument or parameter
DEFAULT_VAULT_AUTHORS_DIR = Path.home() / "Documents" / "citadel" / "literature" / "authors"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)

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

_PAPER_TYPE_KEYWORDS: dict[str, list[str]] = {
    "theory": ["theoretical analysis", "formal proofs", "mathematical foundations"],
    "empirical": ["empirical evaluation", "experimental results", "benchmarking"],
    "survey": ["literature review", "comparative analysis", "survey methodology"],
    "methodology": ["algorithmic design", "system methodology", "technical framework"],
    "application": ["applied research", "domain applications", "practical systems"],
}


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def make_author_slug(full_name: str) -> str:
    """Return a deterministic ASCII-safe slug for an author name.

    Format: <last_name>_<first_initial> — all lowercase, accent-stripped.
    """
    # Strip accents via Unicode NFKD decomposition
    normalized = unicodedata.normalize("NFKD", full_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")

    # Split into tokens, remove empty strings
    tokens = [t.strip() for t in re.split(r"[\s,]+", ascii_name) if t.strip()]
    if not tokens:
        return "unknown_u"

    # Heuristic: if comma present in original, assume "Last, First" order
    if "," in full_name:
        last = tokens[0]
        first_initial = tokens[1][0] if len(tokens) > 1 else "u"
    else:
        last = tokens[-1]
        first_initial = tokens[0][0] if len(tokens) > 1 else "u"

    # Keep only alphanumeric characters
    last_clean = re.sub(r"[^a-z0-9]", "", last.lower())
    first_clean = re.sub(r"[^a-z0-9]", "", first_initial.lower())
    if not last_clean:
        last_clean = "unknown"
    if not first_clean:
        first_clean = "u"
    return f"{last_clean}_{first_clean}"


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

def _extract_frontmatter_field(text: str, key: str) -> str | None:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+)$", re.MULTILINE)
    m = pattern.search(match.group(1))
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _extract_frontmatter_list(text: str, key: str) -> list[str]:
    """Extract a YAML list field from frontmatter, returning a list of strings."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return []
    fm_block = match.group(1)
    # Find the key and collect indented list items
    key_pattern = re.compile(rf"^{re.escape(key)}:\s*$", re.MULTILINE)
    km = key_pattern.search(fm_block)
    if not km:
        return []
    after_key = fm_block[km.end():]
    items: list[str] = []
    for line in after_key.splitlines():
        if not line.strip():
            continue
        if re.match(r"^\s+-\s+", line):
            items.append(re.sub(r"^\s+-\s+", "", line).strip().strip('"').strip("'"))
        elif line and not line[0].isspace():
            break
    return items


def _split_frontmatter(text: str) -> tuple[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return "", text
    return match.group(0), text[match.end():]


# ---------------------------------------------------------------------------
# Research area inference (rule-based, no LLM)
# ---------------------------------------------------------------------------

def _infer_research_areas(paper_type: str, title: str) -> list[str]:
    """Infer 2–5 research area keyword phrases from paper_type and title."""
    areas: list[str] = []

    # Add paper-type-based areas first
    pt_lower = (paper_type or "").lower()
    for ptype, keywords in _PAPER_TYPE_KEYWORDS.items():
        if ptype in pt_lower:
            areas.extend(keywords[:2])
            break

    # Extract meaningful words from title
    words = re.findall(r"\b[a-zA-Z][a-zA-Z\-]{3,}\b", title or "")
    title_keywords: list[str] = []
    seen: set[str] = set()
    for w in words:
        lower = w.lower()
        if lower not in _STOP_WORDS and lower not in seen:
            seen.add(lower)
            title_keywords.append(lower)
        if len(title_keywords) >= 4:
            break
    areas.extend(title_keywords)

    # Deduplicate while preserving order
    deduped: list[str] = []
    seen_areas: set[str] = set()
    for area in areas:
        key = area.lower()
        if key not in seen_areas:
            seen_areas.add(key)
            deduped.append(area)

    return deduped[:5] if deduped else ["research"]


# ---------------------------------------------------------------------------
# Note building
# ---------------------------------------------------------------------------

def _build_author_note(
    slug: str,
    full_name: str,
    co_authors: list[str],
    cite_key: str,
    title: str,
    year: str | int,
    paper_type: str,
) -> str:
    """Build the full content for a new author note."""
    co_author_yaml = "\n".join(f"  - {c}" for c in co_authors) if co_authors else "  []"
    if not co_authors:
        known_collaborators_block = "known_collaborators: []"
    else:
        known_collaborators_block = "known_collaborators:\n" + co_author_yaml

    known_papers_block = (
        "known_papers:\n"
        f"  - cite_key: {cite_key}\n"
        f"    title: {title}\n"
        f"    year: {year}"
    )

    frontmatter = (
        "---\n"
        f"author_key: {slug}\n"
        f"full_name: {full_name}\n"
        f"status: active\n"
        f"{known_collaborators_block}\n"
        f"{known_papers_block}\n"
        "---"
    )

    research_areas = _infer_research_areas(paper_type, title)
    research_area_lines = "\n".join(f"- {area}" for area in research_areas)

    return (
        f"{frontmatter}\n\n"
        f"## Research Areas\n\n"
        f"{research_area_lines}\n\n"
        f"## Observed Notation Conventions\n\n"
        f"_No conventions recorded yet._\n\n"
        f"## Papers in Vault\n\n"
        f"- {cite_key}: {title} ({year})\n"
    )


def _update_author_note(
    existing_text: str,
    co_authors: list[str],
    cite_key: str,
    title: str,
    year: str | int,
) -> str:
    """Update an existing author note: append to Papers in Vault, update known_collaborators."""
    # Check if this paper is already in Papers in Vault
    papers_section_marker = "## Papers in Vault"
    entry = f"- {cite_key}: {title} ({year})"

    if entry in existing_text:
        # Paper already listed — only update collaborators if needed
        updated = existing_text
    else:
        # Append to Papers in Vault section
        header_pos = existing_text.find(papers_section_marker)
        if header_pos == -1:
            # Section absent — append at end
            updated = existing_text.rstrip() + f"\n\n{papers_section_marker}\n\n{entry}\n"
        else:
            # Find end of this section (next ## heading or EOF)
            after_header = existing_text[header_pos + len(papers_section_marker):]
            next_h2 = re.search(r"\n## ", after_header)
            if next_h2:
                insert_pos = header_pos + len(papers_section_marker) + next_h2.start()
                updated = existing_text[:insert_pos].rstrip() + f"\n{entry}\n" + existing_text[insert_pos:]
            else:
                updated = existing_text.rstrip() + f"\n{entry}\n"
    # Update known_collaborators in frontmatter if needed
    fm_str, body = _split_frontmatter(updated)
    if not fm_str:
        return updated

    existing_collaborators = _extract_frontmatter_list(updated, "known_collaborators")
    new_collaborators = [c for c in co_authors if c not in existing_collaborators]
    if not new_collaborators:
        return updated

    all_collaborators = existing_collaborators + new_collaborators
    collab_yaml = "\n".join(f"  - {c}" for c in all_collaborators)
    new_collab_block = f"known_collaborators:\n{collab_yaml}"

    # Replace the known_collaborators block in frontmatter
    fm_inner = fm_str
    # Handle both "known_collaborators: []" and "known_collaborators:\n  - ..." forms
    old_inline = re.search(r"known_collaborators: \[\]", fm_inner)
    old_list = re.search(r"known_collaborators:\n(?:  - .+\n)*", fm_inner)
    if old_inline:
        fm_inner = fm_inner[:old_inline.start()] + new_collab_block + fm_inner[old_inline.end():]
    elif old_list:
        fm_inner = fm_inner[:old_list.start()] + new_collab_block + "\n" + fm_inner[old_list.end():]
    else:
        # Inject before known_papers or at end of frontmatter
        known_papers_pos = fm_inner.find("known_papers:")
        if known_papers_pos != -1:
            fm_inner = fm_inner[:known_papers_pos] + new_collab_block + "\n" + fm_inner[known_papers_pos:]

    return fm_inner + body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_author_notes(
    metadata: dict[str, Any],
    cite_key: str,
    vault_authors_dir: "Path | str | None" = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or update author notes for all authors in metadata.

    Args:
        metadata: Dict with keys: cite_key, title, authors (list of str),
                  year, venue, paper_type.
        cite_key: The paper's cite_key (used as the canonical identifier).
        vault_authors_dir: Override path to citadel/literature/authors/.
            Defaults to ~/Documents/citadel/literature/authors/.
        dry_run: If True, print planned actions without writing any files.

    Returns:
        dict with keys: authors_created (int), authors_updated (int),
                        author_slugs (list of str).
    """
    vault_dir = Path(vault_authors_dir) if vault_authors_dir is not None else DEFAULT_VAULT_AUTHORS_DIR

    title = str(metadata.get("title", ""))
    year = metadata.get("year", "")
    paper_type = str(metadata.get("paper_type", ""))
    authors: list[str] = metadata.get("authors", [])
    if not isinstance(authors, list):
        authors = [str(authors)] if authors else []

    authors_created = 0
    authors_updated = 0
    author_slugs: list[str] = []

    for author_name in authors:
        slug = make_author_slug(str(author_name))
        author_slugs.append(slug)

        # Co-authors are all other authors (by full name)
        co_authors = [str(a) for a in authors if a != author_name]

        note_path = vault_dir / f"{slug}.md"

        if dry_run:
            if note_path.exists():
                print(f"[dry-run] update {note_path} (slug={slug})")
            else:
                print(f"[dry-run] create {note_path} (slug={slug})")
            continue

        if note_path.is_file():
            existing = note_path.read_text(encoding="utf-8")
            updated = _update_author_note(existing, co_authors, cite_key, title, year)
            note_path.write_text(updated, encoding="utf-8")
            authors_updated += 1
        else:
            vault_dir.mkdir(parents=True, exist_ok=True)
            content = _build_author_note(
                slug=slug,
                full_name=str(author_name),
                co_authors=co_authors,
                cite_key=cite_key,
                title=title,
                year=year,
                paper_type=paper_type,
            )
            note_path.write_text(content, encoding="utf-8")
            authors_created += 1

    return {
        "authors_created": authors_created,
        "authors_updated": authors_updated,
        "author_slugs": author_slugs,
    }


# ---------------------------------------------------------------------------
# CLI entry point (dry-run / demo mode)
# ---------------------------------------------------------------------------

def _make_demo_metadata(cite_key: str) -> dict[str, Any]:
    """Construct plausible demo metadata from a cite_key string."""
    # Attempt to extract a surname from the cite_key (leading lowercase letters)
    match = re.match(r"([a-z]+)", cite_key)
    surname_raw = match.group(1).capitalize() if match else "Author"
    return {
        "cite_key": cite_key,
        "title": f"Demo Paper: {cite_key}",
        "authors": [f"{surname_raw}, A.", "Collaborator, B."],
        "year": 2024,
        "venue": "Demo Conference",
        "paper_type": "methodology",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update author notes in the Citadel vault."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without writing any files.",
    )
    parser.add_argument(
        "--cite-key",
        required=True,
        help="cite_key of the paper (e.g. smith2024neural).",
    )
    parser.add_argument(
        "--vault-authors-dir",
        default=None,
        help="Override path to citadel/literature/authors/.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    demo_metadata = _make_demo_metadata(args.cite_key)
    vault_authors_dir = Path(args.vault_authors_dir) if args.vault_authors_dir else None
    result = create_author_notes(
        metadata=demo_metadata,
        cite_key=args.cite_key,
        vault_authors_dir=vault_authors_dir,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        import json
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
