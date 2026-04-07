#!/usr/bin/env python3
"""assumption_writer.py — Create/update assumption notes in the Citadel vault.

Reads ``_theory_reading_output.json`` produced by theory_reader.py (Task 03)
and creates or updates assumption notes under
``~/Documents/citadel/literature/assumptions/``.

- New assumptions: full note with frontmatter + section bodies.
- Existing assumptions (slug match): only ``seen_in_papers`` is appended.
- ``--dry-run`` mode: validates inputs, prints planned actions as JSON, no writes.

Importable API
--------------
    from assumption_writer import write_assumption_notes
    result = write_assumption_notes("smith2024neural")

CLI
---
    python3 assumption_writer.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"

THEORY_OUTPUT_NAME = "_theory_reading_output.json"

# Valid taxonomy categories for assumption notes
ASSUMPTION_CATEGORIES = [
    "smoothness",
    "moment_conditions",
    "independence",
    "sparsity",
    "identifiability",
    "stability_stationarity",
    "positivity",
    "dimensionality",
    "regularity_of_design",
    "other",
]

# Valid strength ratings
STRENGTH_RATINGS = ["standard", "moderate", "strong"]

# ---------------------------------------------------------------------------
# Slug utilities
# ---------------------------------------------------------------------------


def _slug(name: str) -> str:
    """Convert assumption name to a filesystem-safe slug.

    Example: "Sobolev smoothness" -> "sobolev-smoothness"
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)   # remove special chars
    s = re.sub(r"\s+", "-", s.strip())    # spaces -> hyphens
    s = re.sub(r"-+", "-", s)             # collapse consecutive hyphens
    return s or "unnamed-assumption"


def _normalize_category(raw: str) -> str:
    """Map LLM-produced category strings to canonical taxonomy values."""
    mapping: dict[str, str] = {
        "regularity": "regularity_of_design",
        "moment": "moment_conditions",
        "mixing": "stability_stationarity",
        "smoothness": "smoothness",
        "sparsity": "sparsity",
        "identifiability": "identifiability",
        "independence": "independence",
        "positivity": "positivity",
        "dimensionality": "dimensionality",
        "stability_stationarity": "stability_stationarity",
        "moment_conditions": "moment_conditions",
        "regularity_of_design": "regularity_of_design",
        "other": "other",
    }
    return mapping.get(raw.lower().strip(), "other")


def _normalize_strength(raw: str) -> str:
    """Map strength string to a canonical value: standard | moderate | strong."""
    s = raw.lower().strip()
    if s in STRENGTH_RATINGS:
        return s
    return "standard"


# ---------------------------------------------------------------------------
# Note I/O helpers
# ---------------------------------------------------------------------------


def _load_theory_output(paper_dir: Path) -> Optional[dict]:
    """Load _theory_reading_output.json; return None if absent or unreadable."""
    path = paper_dir / THEORY_OUTPUT_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _scan_existing_slugs(assumptions_dir: Path) -> dict[str, Path]:
    """Return a mapping slug -> Path for all existing .md notes in *assumptions_dir*."""
    if not assumptions_dir.exists():
        return {}
    return {p.stem: p for p in assumptions_dir.glob("*.md")}


def _read_seen_in_papers(note_path: Path) -> list[str]:
    """Parse ``seen_in_papers`` list from YAML frontmatter of an existing note."""
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    fm_block = text[3:end]

    result: list[str] = []
    in_block_list = False
    for line in fm_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("seen_in_papers:"):
            rest = stripped[len("seen_in_papers:"):].strip()
            if rest.startswith("["):
                # Inline JSON list
                try:
                    result = json.loads(rest)
                except (json.JSONDecodeError, ValueError):
                    result = [
                        x.strip().strip("\"'")
                        for x in rest.strip("[]").split(",")
                        if x.strip()
                    ]
                in_block_list = False
                break
            else:
                # Block list follows on subsequent lines
                in_block_list = True
        elif in_block_list:
            if stripped.startswith("- "):
                result.append(stripped[2:].strip().strip("\"'"))
            else:
                break
    return result


def _update_seen_in_papers(note_path: Path, cite_key: str) -> bool:
    """Append *cite_key* to ``seen_in_papers`` in the note's frontmatter.

    Only appends; never overwrites other frontmatter fields.

    Returns True if the note was actually modified (cite_key was not already listed).
    """
    existing = _read_seen_in_papers(note_path)
    if cite_key in existing:
        return False  # Already recorded — idempotent no-op

    existing.append(cite_key)

    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return False

    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False

    fm_block = text[3:end]
    new_list_str = json.dumps(existing, ensure_ascii=False)

    new_fm_lines: list[str] = []
    replaced = False
    in_block_list = False
    for line in fm_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("seen_in_papers:"):
            rest = stripped[len("seen_in_papers:"):].strip()
            if rest.startswith("["):
                new_fm_lines.append(f"seen_in_papers: {new_list_str}")
            else:
                new_fm_lines.append(f"seen_in_papers: {new_list_str}")
                in_block_list = True  # Subsequent "- " lines will be skipped
            replaced = True
        elif in_block_list and stripped.startswith("- "):
            continue  # Drop old block-list items
        else:
            in_block_list = False
            new_fm_lines.append(line)

    if not replaced:
        new_fm_lines.append(f"seen_in_papers: {new_list_str}")

    new_text = "---\n" + "\n".join(new_fm_lines) + text[end:]
    note_path.write_text(new_text, encoding="utf-8")
    return True


def _build_note(assumption: dict, cite_key: str) -> str:
    """Render a complete assumption note (frontmatter + bodies) for a new assumption."""
    name = assumption.get("name", "unnamed")
    formal_statement = assumption.get("formal_statement", "")
    plain_english = assumption.get("plain_english", "")
    category = _normalize_category(assumption.get("category", "other"))
    strength = _normalize_strength(assumption.get("strength", "standard"))
    typical_notation = assumption.get("typical_notation", "")
    testable = assumption.get("testable", False)
    proof_usage = assumption.get("proof_usage", "")

    seen_in_papers = json.dumps([cite_key], ensure_ascii=False)

    lines = [
        "---",
        f'name: "{name}"',
        f"category: {category}",
        f'typical_notation: "{typical_notation}"',
        f"strength_rating: {strength}",
        f'first_seen_in: "{cite_key}"',
        f"seen_in_papers: {seen_in_papers}",
        "---",
        "",
        "## Formal Statement",
        formal_statement or "(not provided)",
        "",
        "## Plain English",
        plain_english or "(not provided)",
        "",
        "## Notes",
    ]

    notes_parts: list[str] = []
    if proof_usage:
        notes_parts.append(f"Used in proof: {proof_usage}")
    if testable is not None:
        notes_parts.append(f"Testable: {str(testable).lower()}")
    lines.append("\n".join(notes_parts) if notes_parts else "(none)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_assumption_notes(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or update Citadel assumption notes from theory reading output.

    Reads ``_theory_reading_output.json`` for *cite_key* and writes to
    ``<vault_root>/literature/assumptions/``.

    - New assumptions: full note with frontmatter and section bodies is created.
    - Existing assumptions (by slug match): only ``seen_in_papers`` is appended.
    - When ``theory_present`` is false or ``assumptions_extracted`` is empty,
      exits cleanly with zero counts.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    dry_run:
        When True, validate inputs, scan assumptions directory, and return
        planned actions as a dict — no file writes.

    Returns
    -------
    dict with keys:
        - ``cite_key``
        - ``assumptions_found_in_theory_output``
        - ``assumptions_matched_existing``
        - ``assumptions_would_create`` / ``assumptions_created``
        - ``assumptions_would_update`` / ``assumptions_updated``
        - ``assumption_slugs``
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))
    paper_dir = bank_root / cite_key
    assumptions_dir = vroot / "literature" / "assumptions"

    theory_output = _load_theory_output(paper_dir)

    if theory_output is None:
        base: dict[str, Any] = {
            "cite_key": cite_key,
            "assumptions_found_in_theory_output": 0,
            "assumptions_matched_existing": 0,
            "note": "_theory_reading_output.json not found; no assumptions to process",
            "assumption_slugs": [],
        }
        if dry_run:
            return {**base, "assumptions_would_create": 0, "assumptions_would_update": 0}
        return {**base, "assumptions_created": 0, "assumptions_updated": 0}

    if not theory_output.get("theory_present", True):
        base = {
            "cite_key": cite_key,
            "assumptions_found_in_theory_output": 0,
            "assumptions_matched_existing": 0,
            "note": "theory_present is false; no assumptions to process",
            "assumption_slugs": [],
        }
        if dry_run:
            return {**base, "assumptions_would_create": 0, "assumptions_would_update": 0}
        return {**base, "assumptions_created": 0, "assumptions_updated": 0}

    assumptions: list[dict] = theory_output.get("assumptions_extracted", [])

    if not assumptions:
        base = {
            "cite_key": cite_key,
            "assumptions_found_in_theory_output": 0,
            "assumptions_matched_existing": 0,
            "note": "assumptions_extracted is empty; nothing to write",
            "assumption_slugs": [],
        }
        if dry_run:
            return {**base, "assumptions_would_create": 0, "assumptions_would_update": 0}
        return {**base, "assumptions_created": 0, "assumptions_updated": 0}

    existing_slugs = _scan_existing_slugs(assumptions_dir)

    to_create: list[tuple[str, dict]] = []   # (slug, assumption dict)
    to_update: list[tuple[str, Path]] = []   # (slug, note path)
    all_slugs: list[str] = []

    for assumption in assumptions:
        name = assumption.get("name", "")
        if not name:
            continue
        slug = _slug(name)
        all_slugs.append(slug)

        if slug in existing_slugs:
            to_update.append((slug, existing_slugs[slug]))
        else:
            to_create.append((slug, assumption))

    found = len(assumptions)
    matched = len(to_update)

    if dry_run:
        return {
            "cite_key": cite_key,
            "assumptions_found_in_theory_output": found,
            "assumptions_matched_existing": matched,
            "assumptions_would_create": len(to_create),
            "assumptions_would_update": len(to_update),
            "assumption_slugs": all_slugs,
            "would_create_slugs": [s for s, _ in to_create],
            "would_update_slugs": [s for s, _ in to_update],
        }

    # Live run: write files
    assumptions_dir.mkdir(parents=True, exist_ok=True)

    created_count = 0
    for slug, assumption in to_create:
        note_content = _build_note(assumption, cite_key)
        note_path = assumptions_dir / f"{slug}.md"
        note_path.write_text(note_content, encoding="utf-8")
        created_count += 1

    updated_count = 0
    for slug, note_path in to_update:
        if _update_seen_in_papers(note_path, cite_key):
            updated_count += 1

    return {
        "cite_key": cite_key,
        "assumptions_found_in_theory_output": found,
        "assumptions_matched_existing": matched,
        "assumptions_created": created_count,
        "assumptions_updated": updated_count,
        "assumption_slugs": all_slugs,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or update Citadel assumption notes from theory reading output. "
            "Use --dry-run to preview planned actions as JSON without writing files."
        )
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions as JSON without writing any files.",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Root of paper bank (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    parser.add_argument(
        "--vault-root",
        default=DEFAULT_VAULT_ROOT,
        help=f"Root of Citadel vault (default: {DEFAULT_VAULT_ROOT}).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = write_assumption_notes(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        vault_root=args.vault_root,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
