#!/usr/bin/env python3
"""Extract a reading queue from actionable knowledge gaps."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ACTION_ADD_TO_READING_QUEUE = "add_to_reading_queue"
VALID_PRIORITIES = {"high", "medium", "low"}
SEVERITY_TO_PRIORITY = {
    "high": "high",
    "medium": "medium",
    "low": "low",
}


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _load_yaml(path: Path) -> Any:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if loaded is not None else {}


def _normalize_gap_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _extract_wikilink(value: str) -> str:
    match = re.search(r"\[\[([^\]]+)\]\]", value)
    if not match:
        return ""
    return _clean_text(match.group(1))


def _extract_citekey(gap: dict[str, Any]) -> str:
    direct_candidates = [
        gap.get("citekey"),
        gap.get("cite_key"),
        gap.get("target_citekey"),
        gap.get("target_cite_key"),
    ]
    for candidate in direct_candidates:
        cleaned = _clean_text(candidate)
        if cleaned:
            return cleaned

    for field in ("resolution_target", "title", "description", "gap_description"):
        raw = _clean_text(gap.get(field))
        if not raw:
            continue
        if raw.startswith("[[") and raw.endswith("]]"):
            return _extract_wikilink(raw)
        wikilink = _extract_wikilink(raw)
        if wikilink:
            return wikilink

    return ""


def _extract_priority(gap: dict[str, Any]) -> str:
    explicit = _clean_text(gap.get("priority")).lower()
    if explicit in VALID_PRIORITIES:
        return explicit

    severity = _clean_text(gap.get("severity")).lower()
    if severity in SEVERITY_TO_PRIORITY:
        return SEVERITY_TO_PRIORITY[severity]
    return "medium"


def _extract_notes(gap: dict[str, Any]) -> str:
    explicit = _clean_text(gap.get("notes"))
    if explicit:
        return explicit

    fragments: list[str] = []
    section = _clean_text(gap.get("section"))
    if section:
        fragments.append(f"section: {section}")
    linked_claims = gap.get("linked_claims")
    if isinstance(linked_claims, list):
        claims = [_clean_text(item) for item in linked_claims if _clean_text(item)]
        if claims:
            fragments.append(f"linked_claims: {', '.join(claims[:3])}")
    resolution_action = _clean_text(gap.get("resolution_action"))
    if resolution_action:
        fragments.append(f"resolution_action: {resolution_action}")
    return "; ".join(fragments)


def _build_queue_item(gap: dict[str, Any]) -> dict[str, str] | None:
    citekey = _extract_citekey(gap)
    if not citekey:
        return None

    title = _clean_text(gap.get("title"))
    if not title:
        resolution_target = _clean_text(gap.get("resolution_target"))
        if resolution_target.startswith("[[") and resolution_target.endswith("]]"):
            title = _extract_wikilink(resolution_target)
    if not title:
        title = citekey

    gap_category = _clean_text(gap.get("gap_category") or gap.get("category") or gap.get("type"))
    if not gap_category:
        gap_category = "unknown"

    gap_description = _clean_text(gap.get("gap_description") or gap.get("description") or gap.get("gap"))
    if not gap_description:
        gap_description = f"Follow-up reading requested for {citekey}."

    return {
        "citekey": citekey,
        "title": title,
        "gap_category": gap_category,
        "gap_description": gap_description,
        "priority": _extract_priority(gap),
        "notes": _extract_notes(gap),
    }


def _merge_items(primary: dict[str, str], candidate: dict[str, str]) -> dict[str, str]:
    merged = dict(primary)
    for key in ("title", "gap_category", "gap_description", "priority"):
        if not _clean_text(merged.get(key)) and _clean_text(candidate.get(key)):
            merged[key] = candidate[key]

    notes_primary = _clean_text(primary.get("notes"))
    notes_candidate = _clean_text(candidate.get("notes"))
    if notes_candidate and notes_candidate != notes_primary:
        merged["notes"] = f"{notes_primary}; {notes_candidate}" if notes_primary else notes_candidate
    return merged


def _load_gaps_from_catalog(catalog_data: dict[str, Any]) -> list[dict[str, Any]]:
    paper = catalog_data.get("paper")
    if isinstance(paper, dict):
        paper_gaps = _normalize_gap_entries(paper.get("knowledge_gaps"))
        if paper_gaps:
            return paper_gaps
    return _normalize_gap_entries(catalog_data.get("knowledge_gaps"))


def _load_gap_entries(
    catalog_data: dict[str, Any],
    work_dir: Path,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    paper = catalog_data.get("paper")
    paper_meta = paper if isinstance(paper, dict) else {}
    pointer = _clean_text(paper_meta.get("knowledge_gaps_file"))

    warnings: list[str] = []
    if pointer:
        pointer_path = Path(pointer)
        if not pointer_path.is_absolute():
            pointer_path = work_dir / pointer_path
        if not pointer_path.exists():
            warnings.append(f"knowledge_gaps_file_missing:{_to_posix(pointer_path)}")
            return [], f"paper.knowledge_gaps_file:{_to_posix(pointer_path)}", warnings
        try:
            loaded = _load_yaml(pointer_path)
        except (OSError, yaml.YAMLError):
            warnings.append(f"knowledge_gaps_file_unreadable:{_to_posix(pointer_path)}")
            return [], f"paper.knowledge_gaps_file:{_to_posix(pointer_path)}", warnings

        gaps = _normalize_gap_entries(loaded.get("gaps") if isinstance(loaded, dict) else [])
        return gaps, f"paper.knowledge_gaps_file:{_to_posix(pointer_path)}", warnings

    return _load_gaps_from_catalog(catalog_data), "catalog.paper.knowledge_gaps", warnings


def extract_reading_queue(
    work_dir: str | Path,
    catalog: str | Path,
    gaps: str | Path,
    cite_key: str,
    output: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Extract actionable queue entries and optionally write reading_queue.yaml."""

    work_dir_p = Path(work_dir)
    catalog_p = Path(catalog)
    gaps_p = Path(gaps)
    output_p = Path(output)

    missing_inputs: list[str] = []
    if not work_dir_p.exists():
        missing_inputs.append(f"work_dir:{_to_posix(work_dir_p)}")
    elif not work_dir_p.is_dir():
        missing_inputs.append(f"work_dir_not_directory:{_to_posix(work_dir_p)}")
    if not catalog_p.exists():
        missing_inputs.append(f"catalog:{_to_posix(catalog_p)}")

    catalog_data: dict[str, Any] = {}
    gap_entries: list[dict[str, Any]] = []
    gap_source = "unknown"
    warnings: list[str] = []

    if catalog_p.exists():
        try:
            loaded = _load_yaml(catalog_p)
            catalog_data = loaded if isinstance(loaded, dict) else {}
        except (OSError, yaml.YAMLError):
            missing_inputs.append(f"catalog_unreadable:{_to_posix(catalog_p)}")

    if catalog_data:
        gap_entries, gap_source, gap_warnings = _load_gap_entries(catalog_data, work_dir_p)
        warnings.extend(gap_warnings)

    actionable_items: list[dict[str, str]] = []
    skipped_without_citekey = 0
    for gap in gap_entries:
        action = _clean_text(gap.get("next_action")).lower()
        if action != ACTION_ADD_TO_READING_QUEUE:
            continue
        item = _build_queue_item(gap)
        if item is None:
            skipped_without_citekey += 1
            continue
        actionable_items.append(item)

    deduped: dict[str, dict[str, str]] = {}
    for item in actionable_items:
        dedupe_key = _clean_text(item.get("citekey")).lower()
        if dedupe_key in deduped:
            deduped[dedupe_key] = _merge_items(deduped[dedupe_key], item)
            continue
        deduped[dedupe_key] = item

    items = sorted(deduped.values(), key=lambda entry: _clean_text(entry.get("citekey")).lower())

    summary: dict[str, Any] = {
        "inputs_valid": len(missing_inputs) == 0,
        "gap_source": gap_source,
        "items_count": len(items),
        "work_dir": _to_posix(work_dir_p),
        "catalog": _to_posix(catalog_p),
        "gaps": _to_posix(gaps_p),
        "output": _to_posix(output_p),
        "cite_key": cite_key,
        "actionable_gaps_found": len(actionable_items),
        "deduped_count": len(actionable_items) - len(items),
        "skipped_without_citekey": skipped_without_citekey,
        "dry_run": dry_run,
    }
    if warnings:
        summary["warnings"] = warnings
    if missing_inputs:
        summary["missing_inputs"] = missing_inputs

    if dry_run:
        return summary

    if missing_inputs:
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    payload = {
        "reading_queue": {
            "source_paper": cite_key,
            "generated": datetime.now(tz=timezone.utc).date().isoformat(),
            "items": items,
        }
    }

    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    summary["written"] = _to_posix(output_p)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract actionable reading queue items from knowledge gaps.",
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--catalog", required=True, help="Path to _catalog.yaml")
    parser.add_argument("--gaps", required=True, help="Path to _knowledge_gaps.yaml (used when configured)")
    parser.add_argument("--cite-key", required=True, help="Source paper cite key")
    parser.add_argument("--output", required=True, help="Path to write reading_queue.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and emit JSON summary without writing files.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = extract_reading_queue(
        work_dir=args.work_dir,
        catalog=args.catalog,
        gaps=args.gaps,
        cite_key=args.cite_key,
        output=args.output,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    if not result.get("inputs_valid", False) and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
