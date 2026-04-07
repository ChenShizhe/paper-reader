#!/usr/bin/env python3
"""Validate the discovery manifest against the local v1 contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ALLOWED_SEARCH_SOURCES = {"zotero", "arxiv", "openalex", "web", "pubmed"}
SEARCH_SOURCE_ORDER = ("zotero", "arxiv", "openalex", "web", "pubmed")
CANONICAL_ID_RE = re.compile(r"^(arxiv:\S+|doi:\S+|openalex:W\d+|manual:[0-9a-f]{8})$")
CITE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate discovery manifest")
    parser.add_argument("manifest_path", help="Path to paper_manifest.json")
    return parser.parse_args()


def is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def entry_sort_key(entry: dict[str, Any]) -> tuple[float, int, int, int, str]:
    seed_distance = entry.get("seed_distance")
    citation_count = entry.get("citation_count")
    year = entry.get("year")
    return (
        -float(entry.get("relevance_score", 0.0)),
        seed_distance if isinstance(seed_distance, int) else 99,
        -(citation_count if isinstance(citation_count, int) else -1),
        -(year if isinstance(year, int) else 0),
        str(entry.get("canonical_id", "")),
    )


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if manifest.get("schema_version") != "1":
        errors.append("schema_version must be '1'")
    if not isinstance(manifest.get("topic"), str) or not manifest["topic"].strip():
        errors.append("topic must be a non-empty string")
    if not isinstance(manifest.get("created_at"), str) or not TIMESTAMP_RE.fullmatch(manifest["created_at"]):
        errors.append("created_at must be an ISO 8601 UTC timestamp")
    search_sources = manifest.get("search_sources")
    if not is_string_list(search_sources):
        errors.append("search_sources must be a list of strings")
    elif any(source not in ALLOWED_SEARCH_SOURCES for source in search_sources):
        errors.append("search_sources contains an unknown source")
    elif len(search_sources) != len(set(search_sources)):
        errors.append("search_sources must not contain duplicates")
    elif search_sources != [source for source in SEARCH_SOURCE_ORDER if source in search_sources]:
        errors.append("search_sources must follow the canonical source order")

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        errors.append("entries must be a list")
        return errors

    seen_canonical_ids: set[str] = set()
    seen_cite_keys: set[str] = set()
    seen_arxiv_ids: set[str] = set()
    seen_dois: set[str] = set()
    seen_openalex_ids: set[str] = set()
    seen_pmids: set[str] = set()
    previous_sort_key: tuple[float, int, int, int, str] | None = None

    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue

        canonical_id = entry.get("canonical_id")
        if not isinstance(canonical_id, str) or not CANONICAL_ID_RE.fullmatch(canonical_id):
            errors.append(f"{prefix}.canonical_id is invalid")
        elif canonical_id in seen_canonical_ids:
            errors.append(f"{prefix}.canonical_id duplicates a prior entry")
        else:
            seen_canonical_ids.add(canonical_id)

        cite_key = entry.get("cite_key")
        if not isinstance(cite_key, str) or not CITE_KEY_RE.fullmatch(cite_key):
            errors.append(f"{prefix}.cite_key is invalid")
        elif cite_key in seen_cite_keys:
            errors.append(f"{prefix}.cite_key duplicates a prior entry")
        else:
            seen_cite_keys.add(cite_key)

        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            errors.append(f"{prefix}.title must be a non-empty string")
        if not is_string_list(entry.get("authors")):
            errors.append(f"{prefix}.authors must be a list of strings")
        if not is_string_list(entry.get("categories")):
            errors.append(f"{prefix}.categories must be a list of strings")
        if not isinstance(entry.get("search_source"), str) or entry["search_source"] not in ALLOWED_SEARCH_SOURCES:
            errors.append(f"{prefix}.search_source is invalid")
        elif isinstance(search_sources, list) and entry["search_source"] not in search_sources:
            errors.append(f"{prefix}.search_source must appear in top-level search_sources")

        year = entry.get("year")
        if year is not None and not isinstance(year, int):
            errors.append(f"{prefix}.year must be an integer or null")

        relevance = entry.get("relevance_score")
        if not isinstance(relevance, (int, float)) or not 0.0 <= float(relevance) <= 1.0:
            errors.append(f"{prefix}.relevance_score must be between 0 and 1")

        seed_distance = entry.get("seed_distance")
        if seed_distance is not None and (not isinstance(seed_distance, int) or seed_distance < 0):
            errors.append(f"{prefix}.seed_distance must be a non-negative integer or null")

        citation_count = entry.get("citation_count")
        if citation_count is not None and not isinstance(citation_count, int):
            errors.append(f"{prefix}.citation_count must be an integer or null")

        for optional_key in ("arxiv_id", "openalex_id", "doi", "pmid", "pdf_url", "abstract"):
            value = entry.get(optional_key)
            if value is not None and not isinstance(value, str):
                errors.append(f"{prefix}.{optional_key} must be a string or null")

        arxiv_id = entry.get("arxiv_id")
        doi = entry.get("doi")
        openalex_id = entry.get("openalex_id")
        pmid = entry.get("pmid")
        if isinstance(arxiv_id, str):
            if arxiv_id in seen_arxiv_ids:
                errors.append(f"{prefix}.arxiv_id duplicates a prior entry")
            else:
                seen_arxiv_ids.add(arxiv_id)
        if isinstance(doi, str):
            if doi in seen_dois:
                errors.append(f"{prefix}.doi duplicates a prior entry")
            else:
                seen_dois.add(doi)
        if isinstance(openalex_id, str):
            if openalex_id in seen_openalex_ids:
                errors.append(f"{prefix}.openalex_id duplicates a prior entry")
            else:
                seen_openalex_ids.add(openalex_id)
        if isinstance(pmid, str):
            if pmid in seen_pmids:
                errors.append(f"{prefix}.pmid duplicates a prior entry")
            else:
                seen_pmids.add(pmid)

        if isinstance(canonical_id, str):
            if canonical_id.startswith("arxiv:") and entry.get("arxiv_id") != canonical_id.split(":", 1)[1]:
                errors.append(f"{prefix}.arxiv_id must match canonical_id")
            if canonical_id.startswith("doi:") and entry.get("doi") != canonical_id.split(":", 1)[1]:
                errors.append(f"{prefix}.doi must match canonical_id")
            if canonical_id.startswith("openalex:") and entry.get("openalex_id") != canonical_id.split(":", 1)[1]:
                errors.append(f"{prefix}.openalex_id must match canonical_id")
            if canonical_id.startswith("manual:") and any(entry.get(key) for key in ("arxiv_id", "doi", "openalex_id")):
                errors.append(f"{prefix}.manual canonical_id cannot coexist with stronger identifiers")

        current_sort_key = entry_sort_key(entry)
        if previous_sort_key is not None and current_sort_key < previous_sort_key:
            errors.append("entries are not sorted deterministically")
            break
        previous_sort_key = current_sort_key

    return errors


def main() -> int:
    args = parse_args()
    manifest = json.loads(Path(args.manifest_path).read_text(encoding="utf-8"))
    errors = validate_manifest(manifest)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"Manifest valid: {args.manifest_path}")
    print(f"Entries: {len(manifest.get('entries', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
