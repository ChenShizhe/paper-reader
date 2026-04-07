#!/usr/bin/env python3
"""xref_writer.py — Merge intro citation entries into _xref_index.yaml.

Reads intro citation lists (from run_step42 / intro_reader.py) and writes
structured entries into the paper's _xref_index.yaml in the paper-bank,
with idempotent duplicate guard: entries with an existing cited_key are
skipped rather than written twice.

Importable API
--------------
    from xref_writer import write_intro_citations
    result = write_intro_citations(citations, cite_key="smith2024neural")

CLI
---
    python3 xref_writer.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slug_from_author_year(author_year: str, idx: int) -> str:
    """Derive a lowercase alphanumeric slug from an author_year string."""
    slug = re.sub(r"[^a-z0-9]", "", author_year.lower())
    return slug if slug else f"unknown_{idx}"


def _load_xref(xref_path: Path, cite_key: str) -> dict:
    """Load existing _xref_index.yaml or return a skeleton dict."""
    if xref_path.exists():
        raw = xref_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    return {
        "cite_key": cite_key,
        "catalog_version": 1,
        "equations": [],
        "theorems": [],
        "figures": [],
        "citations": [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_intro_citations(
    citations: list[dict[str, Any]],
    cite_key: str,
    paper_bank_root: "str | Path" = DEFAULT_PAPER_BANK_ROOT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge intro citation entries into _xref_index.yaml with duplicate guard.

    Each new entry written to the citations list contains exactly these fields:

        cited_key          — slug derived from author_year
        role               — foundation | alternative | background
        importance         — high | medium | low
        context            — section where cited (always "introduction" here)
        description        — one-sentence description from the citation dict
        dummy_note_created — whether a vault dummy note was already created

    Duplicate guard: if a cited_key already appears in the existing citations
    list, that entry is skipped (not written again) to preserve idempotency.

    Args:
        citations: List of citation dicts with keys:
            author_year, role, importance, description, dummy_eligible,
            and optionally dummy_note_created.
        cite_key: cite_key of the citing paper.
        paper_bank_root: Root of the paper bank (default: ~/Documents/paper-bank).
        dry_run: When True, return a plan without writing any files.

    Returns:
        dict with keys:
            cite_key, citations_planned (int), citations_skipped (int),
            xref_path (str), and (when not dry_run) citations_written (int).
    """
    bank_root = Path(os.path.expanduser(str(paper_bank_root)))
    xref_path = bank_root / cite_key / "_xref_index.yaml"

    xref = _load_xref(xref_path, cite_key)
    existing_citations: list[dict] = xref.get("citations") or []

    # Build duplicate-guard set from already-indexed cited_keys
    existing_cited_keys: set[str] = {
        str(e.get("cited_key", "")) for e in existing_citations
    }

    planned: list[dict] = []
    skipped: list[str] = []

    for idx, citation in enumerate(citations):
        author_year = citation.get("author_year", "")
        role = citation.get("role", "background")
        importance = citation.get("importance", "medium")
        description = citation.get("description", "")
        dummy_note_created = bool(citation.get("dummy_note_created", False))

        cited_key = _slug_from_author_year(author_year, idx)

        # Duplicate guard: skip entries whose cited_key is already indexed
        if cited_key in existing_cited_keys:
            skipped.append(cited_key)
            continue

        entry: dict[str, Any] = {
            "cited_key": cited_key,
            "role": role,
            "importance": importance,
            "context": "introduction",
            "description": description,
            "dummy_note_created": dummy_note_created,
        }
        planned.append(entry)

    if dry_run:
        return {
            "cite_key": cite_key,
            "citations_planned": len(planned),
            "citations_skipped": len(skipped),
            "xref_path": str(xref_path),
            "planned_entries": planned,
        }

    # Commit planned entries to the xref index
    for entry in planned:
        existing_citations.append(entry)

    xref["citations"] = existing_citations
    xref_path.parent.mkdir(parents=True, exist_ok=True)
    xref_path.write_text(
        yaml.dump(xref, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    return {
        "cite_key": cite_key,
        "citations_written": len(planned),
        "citations_skipped": len(skipped),
        "citations_planned": len(planned),
        "xref_path": str(xref_path),
    }


# ---------------------------------------------------------------------------
# Demo citations for CLI / dry-run
# ---------------------------------------------------------------------------

def _make_demo_citations(cite_key: str) -> list[dict[str, Any]]:
    """Return representative demo citations suitable for a dry-run."""
    return [
        {
            "author_year": "Baseline 2018",
            "role": "foundation",
            "importance": "high",
            "description": (
                "Introduces a baseline method for sequential event modeling "
                "and proves basic existence and stationarity conditions."
            ),
            "dummy_eligible": True,
            "dummy_note_created": False,
        },
        {
            "author_year": "Baseline 1988",
            "role": "alternative",
            "importance": "medium",
            "description": "Classical maximum-likelihood estimation for sequential event processes.",
            "dummy_eligible": True,
            "dummy_note_created": False,
        },
        {
            "author_year": "Background Survey 2015",
            "role": "background",
            "importance": "low",
            "description": "General survey on stochastic process theory and applications.",
            "dummy_eligible": False,
            "dummy_note_created": False,
        },
    ]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge intro citation entries into _xref_index.yaml. "
            "Duplicate entries (by cited_key) are skipped."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions as JSON without writing any files.",
    )
    parser.add_argument(
        "--cite-key",
        required=True,
        help="cite_key of the citing paper (e.g. smith2024neural).",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Root of paper bank (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    demo_citations = _make_demo_citations(args.cite_key)
    result = write_intro_citations(
        citations=demo_citations,
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
