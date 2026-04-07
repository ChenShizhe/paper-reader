#!/usr/bin/env python3
"""Build L1/L2/L3 summary synthesis layers from curated artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SECTION_NOTE_ORDER = [
    "intro",
    "model",
    "method",
    "theory",
    "empirical",
    "gaps",
    "discussion",
]

SECTION_TYPE_TO_NOTE = {
    "background": "intro",
    "intro": "intro",
    "introduction": "intro",
    "model": "model",
    "models": "model",
    "method": "method",
    "methods": "method",
    "theory": "theory",
    "simulation": "empirical",
    "simulations": "empirical",
    "application": "empirical",
    "empirical": "empirical",
    "real_data": "empirical",
    "gaps": "gaps",
    "knowledge_gaps": "gaps",
    "discussion": "discussion",
    "conclusion": "discussion",
}

HEADING_TO_NOTE_HINTS = {
    "model": "model",
    "method": "method",
    "theory": "theory",
    "simulation": "empirical",
    "empirical": "empirical",
    "application": "empirical",
    "gaps": "gaps",
    "discussion": "discussion",
    "conclusion": "discussion",
}


def _load_yaml(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    return loaded if loaded is not None else {}


def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return markdown
    return markdown[end + 5 :]


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _extract_note_headings_and_points(markdown: str) -> tuple[list[str], list[str]]:
    body = _strip_frontmatter(markdown)
    headings: list[str] = []
    points: list[str] = []
    fallback_lines: list[str] = []

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        heading_match = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading_match:
            heading = _normalize_line(heading_match.group(1))
            if heading and heading not in headings:
                headings.append(heading)
            continue

        bullet_match = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if bullet_match:
            point = _normalize_line(bullet_match.group(1))
            if point:
                points.append(point)
            continue

        if not line.startswith("|"):
            fallback_lines.append(_normalize_line(line))

    if not points:
        points = [line for line in fallback_lines if line][:3]

    return headings[:8], points[:8]


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _note_path_for(cite_key: str, note_name: str) -> str:
    return f"literature/papers/{cite_key}/{note_name}.md"


def _map_section_to_note(section_type: str, heading: str) -> str:
    stype = (section_type or "").strip().lower()
    if stype in SECTION_TYPE_TO_NOTE:
        return SECTION_TYPE_TO_NOTE[stype]

    heading_l = (heading or "").lower()
    for hint, mapped in HEADING_TO_NOTE_HINTS.items():
        if hint in heading_l:
            return mapped

    return "intro"


def _knowledge_gap_matches(section_id: str, heading: str, node: Any) -> bool:
    needles = {section_id.lower(), heading.lower()}

    def _walk(value: Any) -> bool:
        if isinstance(value, dict):
            return any(_walk(v) or _walk(k) for k, v in value.items())
        if isinstance(value, list):
            return any(_walk(item) for item in value)
        if isinstance(value, str):
            low = value.lower()
            return any(needle and needle in low for needle in needles)
        return False

    return _walk(node)


def _collect_note_data(vault_notes_root: Path, cite_key: str) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    note_data: dict[str, dict[str, Any]] = {}
    notes_found: list[str] = []
    missing_notes: list[str] = []

    for note_name in SECTION_NOTE_ORDER:
        abs_path = vault_notes_root / f"{note_name}.md"
        rel_path = _note_path_for(cite_key, note_name)
        exists = abs_path.exists() and abs_path.is_file()

        headings: list[str] = []
        points: list[str] = []
        char_count = 0

        if exists:
            markdown = abs_path.read_text(encoding="utf-8")
            char_count = len(markdown)
            headings, points = _extract_note_headings_and_points(markdown)
            notes_found.append(note_name)
        else:
            missing_notes.append(note_name)

        note_data[note_name] = {
            "name": note_name,
            "exists": exists,
            "absolute_path": _to_posix(abs_path),
            "relative_path": rel_path,
            "headings": headings,
            "points": points,
            "char_count": char_count,
            "status": "present" if exists else "missing",
        }

    return note_data, notes_found, missing_notes


def _build_l1_notes(note_data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    l1_notes: dict[str, dict[str, Any]] = {}
    for note_name in SECTION_NOTE_ORDER:
        item = note_data[note_name]
        l1_notes[note_name] = {
            "status": item["status"],
            "source_note": item["relative_path"],
            "headings": item["headings"],
            "point_count": len(item["points"]),
            "char_count": item["char_count"],
        }
    return l1_notes


def _build_l2_section_map(
    catalog: dict[str, Any],
    note_data: dict[str, dict[str, Any]],
    xref_index: dict[str, Any],
    knowledge_gaps: Any,
) -> list[dict[str, Any]]:
    sections = catalog.get("sections") if isinstance(catalog, dict) else []
    if not isinstance(sections, list):
        return []

    citations = xref_index.get("citations", []) if isinstance(xref_index, dict) else []
    citation_count = len(citations) if isinstance(citations, list) else 0

    l2_rows: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue

        section_id = str(section.get("id") or "")
        heading = str(section.get("heading") or section_id or "Untitled")
        section_type = str(section.get("section_type") or "")
        mapped_note = _map_section_to_note(section_type, heading)
        mapped_note_item = note_data.get(mapped_note, {})

        key_claims: list[str] = []
        note_points = mapped_note_item.get("points", []) if isinstance(mapped_note_item, dict) else []
        if isinstance(note_points, list):
            key_claims = [str(point) for point in note_points[:3] if str(point).strip()]

        if not key_claims:
            summary = section.get("summary")
            if isinstance(summary, str) and summary.strip():
                key_claims = [summary.strip()]

        if not key_claims:
            key_claims = [f"Catalog section '{heading}' is marked as pending synthesis."]

        section_segments = section.get("segments", [])
        segment_count = len(section_segments) if isinstance(section_segments, list) else 0

        has_gap = _knowledge_gap_matches(section_id, heading, knowledge_gaps)

        l2_rows.append(
            {
                "section_id": section_id,
                "section_heading": heading,
                "section_type": section_type,
                "comprehension_status": str(section.get("comprehension_status") or "unknown"),
                "mapped_note": mapped_note,
                "note_status": mapped_note_item.get("status", "missing"),
                "segment_count": segment_count,
                "citation_count": citation_count,
                "has_knowledge_gap": has_gap,
                "key_claims": key_claims,
            }
        )

    return l2_rows


def _build_l3_summary_points(
    l2_section_map: list[dict[str, Any]],
    note_data: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []

    for row in l2_section_map:
        section_heading = str(row.get("section_heading") or "Untitled")
        mapped_note = str(row.get("mapped_note") or "intro")
        note_item = note_data.get(mapped_note, {})

        note_headings = note_item.get("headings") if isinstance(note_item, dict) else []
        note_points = note_item.get("points") if isinstance(note_item, dict) else []

        source_note = str(note_item.get("relative_path") or "")
        source_heading = (
            str(note_headings[0])
            if isinstance(note_headings, list) and note_headings
            else section_heading
        )

        candidate_points: list[str] = []
        if isinstance(note_points, list):
            candidate_points.extend(str(item) for item in note_points[:2] if str(item).strip())

        if not candidate_points:
            key_claims = row.get("key_claims", [])
            if isinstance(key_claims, list):
                candidate_points.extend(str(item) for item in key_claims[:1] if str(item).strip())

        for text in candidate_points:
            points.append(
                {
                    "section_id": str(row.get("section_id") or ""),
                    "section_heading": section_heading,
                    "section_type": str(row.get("section_type") or ""),
                    "point": text,
                    "status": str(row.get("comprehension_status") or "unknown"),
                    "source_note": source_note,
                    "source_heading": source_heading,
                }
            )

    return points


def _resolve_paths(work_dir: Path, cite_key: str, vault_path: Path) -> dict[str, Path]:
    return {
        "catalog": work_dir / "_catalog.yaml",
        "xref": work_dir / "_xref_index.yaml",
        "knowledge_gaps": work_dir / "_knowledge_gaps.yaml",
        "vault_notes_root": vault_path / "literature" / "papers" / cite_key,
    }


def build_summary_layers(
    work_dir: str | Path,
    cite_key: str,
    vault_path: str | Path,
    output: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build or validate progressive summary synthesis layers.

    The function reads only summary synthesis artifacts and section notes.
    In dry-run mode, it validates inputs and prints a summary without writes.
    """

    work_dir_p = Path(work_dir)
    vault_path_p = Path(vault_path)
    output_p = Path(output)

    paths = _resolve_paths(work_dir_p, cite_key, vault_path_p)

    missing_inputs: list[str] = []
    if not work_dir_p.exists():
        missing_inputs.append(f"work_dir:{_to_posix(work_dir_p)}")
    if not vault_path_p.exists():
        missing_inputs.append(f"vault_path:{_to_posix(vault_path_p)}")
    if not paths["catalog"].exists():
        missing_inputs.append(f"catalog:{_to_posix(paths['catalog'])}")
    if not paths["xref"].exists():
        missing_inputs.append(f"xref:{_to_posix(paths['xref'])}")

    note_data, notes_found, missing_notes = _collect_note_data(paths["vault_notes_root"], cite_key)
    missing_inputs.extend(
        f"note:{note}:{_to_posix(paths['vault_notes_root'] / f'{note}.md')}" for note in missing_notes
    )

    dry_run_summary = {
        "inputs_valid": len(missing_inputs) == len(missing_notes),
        "notes_found": notes_found,
        "missing_notes": missing_notes,
        "raw_segment_reads": [],
    }

    if dry_run:
        return dry_run_summary

    if any(item.startswith("work_dir:") or item.startswith("vault_path:") or item.startswith("catalog:") or item.startswith("xref:") for item in missing_inputs):
        for item in missing_inputs:
            if item.startswith(("work_dir:", "vault_path:", "catalog:", "xref:")):
                print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    catalog = _load_yaml(paths["catalog"])
    xref_index = _load_yaml(paths["xref"])
    knowledge_gaps: Any = {}
    if paths["knowledge_gaps"].exists():
        knowledge_gaps = _load_yaml(paths["knowledge_gaps"])

    l1_notes = _build_l1_notes(note_data)
    l2_section_map = _build_l2_section_map(catalog, note_data, xref_index, knowledge_gaps)
    l3_summary_points = _build_l3_summary_points(l2_section_map, note_data)

    payload = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "l1_notes": l1_notes,
        "l2_section_map": l2_section_map,
        "l3_summary_points": l3_summary_points,
        "missing_inputs": sorted(missing_inputs),
    }

    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build _summary_layers.json from catalog/xref and per-section notes.",
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--cite-key", required=True, help="Paper cite key")
    parser.add_argument("--vault-path", required=True, help="Path to Citadel vault root")
    parser.add_argument("--output", required=True, help="Path for _summary_layers.json output")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print dry-run JSON summary without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = build_summary_layers(
        work_dir=args.work_dir,
        cite_key=args.cite_key,
        vault_path=args.vault_path,
        output=args.output,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
