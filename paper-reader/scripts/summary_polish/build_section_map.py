#!/usr/bin/env python3
"""Build a polished Section Map table from catalog metadata and inject it into a summary note."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

EXPECTED_SECTION_TYPES: list[tuple[str, str]] = [
    ("background", "Background"),
    ("model", "Model"),
    ("methods", "Methods"),
    ("theory", "Theory"),
    ("simulation", "Simulation"),
    ("application", "Application"),
    ("discussion", "Discussion"),
]

SECTION_MAP_HEADING = "## Section Map"


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _clean_text(value: Any, default: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or default


def _escape_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ").strip()


def _load_yaml(path: Path) -> Any:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if loaded is not None else {}


def _normalize_section_type(value: Any) -> str:
    section_type = _clean_text(value).lower()
    if section_type == "method":
        return "methods"
    return section_type


def _normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _clean_text(item.get("text") or item.get("note") or item.get("id"))
        else:
            text = _clean_text(item)
        if text:
            out.append(text)
    return out


def _section_has_gap(section: dict[str, Any]) -> bool:
    for key in ("knowledge_gaps", "knowledge_gap", "gaps"):
        value = section.get(key)
        if isinstance(value, list) and any(_clean_text(item) for item in value):
            return True
        if isinstance(value, str) and _clean_text(value):
            return True
        if isinstance(value, bool) and value:
            return True

    notes = _normalize_list(section.get("notes"))
    return any("gap" in note.lower() for note in notes)


def _render_content(section: dict[str, Any]) -> str:
    summary = _clean_text(section.get("summary"))
    if summary:
        return summary

    heading = _clean_text(section.get("heading"))
    status = _clean_text(section.get("comprehension_status"), "unknown")
    if heading:
        return f"{heading} section (status: {status})."
    return f"No summary available (status: {status})."


def _render_key_items(section: dict[str, Any]) -> str:
    key_terms = _normalize_list(section.get("key_terms"))
    if key_terms:
        return "; ".join(key_terms[:3])

    notes = _normalize_list(section.get("notes"))
    if notes:
        return "; ".join(notes[:2])

    section_id = _clean_text(section.get("id"), "unknown")
    return f"No key items extracted yet for {section_id}."


def _build_row(section: dict[str, Any]) -> dict[str, Any]:
    section_label = _clean_text(section.get("heading") or section.get("id"), "Untitled")
    section_id = _clean_text(section.get("id"), "unknown")
    status = _clean_text(section.get("comprehension_status"), "unknown")
    incomplete = status.lower() == "needs_re-read"
    has_gap = _section_has_gap(section)

    note_markers: list[str] = [f"status: {status}"]
    if incomplete:
        note_markers.append("(incomplete)")
    if has_gap:
        note_markers.append("⚠️")

    return {
        "section": section_label,
        "content": _render_content(section),
        "key_items": _render_key_items(section),
        "notes": " ".join(note_markers),
        "source_section_id": section_id,
        "section_type": _normalize_section_type(section.get("section_type")),
        "incomplete": incomplete,
        "has_gap": has_gap,
    }


def _append_expected_missing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    present_types = {
        _normalize_section_type(row.get("section_type"))
        for row in rows
        if isinstance(row, dict)
    }

    merged = list(rows)
    for expected_type, expected_label in EXPECTED_SECTION_TYPES:
        if expected_type in present_types:
            continue
        merged.append(
            {
                "section": f"{expected_label} (expected)",
                "content": "Not applicable",
                "key_items": "Not applicable",
                "notes": "Not applicable",
                "source_section_id": "",
                "section_type": expected_type,
                "incomplete": False,
                "has_gap": False,
            }
        )
    return merged


def _render_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Section | Content | Key Items | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        section = _escape_cell(_clean_text(row.get("section"), "Untitled"))
        content = _escape_cell(_clean_text(row.get("content"), "Not applicable"))
        key_items = _escape_cell(_clean_text(row.get("key_items"), "Not applicable"))
        notes = _escape_cell(_clean_text(row.get("notes"), "Not applicable"))
        lines.append(f"| {section} | {content} | {key_items} | {notes} |")
    return "\n".join(lines)


def _replace_section_map_block(markdown: str, table_markdown: str) -> tuple[str, bool]:
    heading_match = re.search(r"(?m)^## Section Map\s*$", markdown)
    block = (
        "<!-- section-map:start -->\n"
        f"{table_markdown}\n"
        "<!-- section-map:end -->"
    )

    if heading_match is None:
        body = markdown.rstrip()
        if body:
            body += "\n\n"
        updated = f"{body}{SECTION_MAP_HEADING}\n\n{block}\n"
        return updated, updated != markdown

    section_start = heading_match.end()
    next_heading = re.search(r"(?m)^##\s+", markdown[section_start:])
    section_end = section_start + next_heading.start() if next_heading else len(markdown)

    replacement = f"\n\n{block}\n\n"
    updated = markdown[:section_start] + replacement + markdown[section_end:]
    return updated, updated != markdown


def build_section_map(
    work_dir: str | Path,
    catalog: str | Path,
    summary_note: str | Path,
    output: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a section map table from catalog sections and inject it into summary note."""

    work_dir_p = Path(work_dir)
    catalog_p = Path(catalog)
    summary_note_p = Path(summary_note)
    output_p = Path(output)

    missing_inputs: list[str] = []
    if not work_dir_p.exists():
        missing_inputs.append(f"work_dir:{_to_posix(work_dir_p)}")
    elif not work_dir_p.is_dir():
        missing_inputs.append(f"work_dir_not_directory:{_to_posix(work_dir_p)}")
    if not catalog_p.exists():
        missing_inputs.append(f"catalog:{_to_posix(catalog_p)}")
    if not summary_note_p.exists():
        missing_inputs.append(f"summary_note:{_to_posix(summary_note_p)}")

    catalog_data: dict[str, Any] = {}
    catalog_sections: list[dict[str, Any]] = []
    if catalog_p.exists():
        loaded = _load_yaml(catalog_p)
        if isinstance(loaded, dict):
            catalog_data = loaded
        sections = catalog_data.get("sections", [])
        if isinstance(sections, list):
            catalog_sections = [item for item in sections if isinstance(item, dict)]

    rows = [_build_row(section) for section in catalog_sections]
    rows = _append_expected_missing_rows(rows)
    table = _render_table(rows)
    incomplete_rows = sum(1 for row in rows if bool(row.get("incomplete")))

    summary: dict[str, Any] = {
        "inputs_valid": len(missing_inputs) == 0,
        "catalog_sections": len(catalog_sections),
        "rows_rendered": len(rows),
        "incomplete_rows": incomplete_rows,
        "work_dir": _to_posix(work_dir_p),
        "catalog_path": _to_posix(catalog_p),
        "summary_note_path": _to_posix(summary_note_p),
        "output_path": _to_posix(output_p),
        "dry_run": dry_run,
    }
    if missing_inputs:
        summary["missing_inputs"] = missing_inputs

    if dry_run:
        return summary

    if missing_inputs:
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(table + "\n", encoding="utf-8")

    summary_markdown = summary_note_p.read_text(encoding="utf-8")
    updated_markdown, changed = _replace_section_map_block(summary_markdown, table)
    summary_note_p.write_text(updated_markdown, encoding="utf-8")
    summary["summary_note_updated"] = changed
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a polished markdown section-map appendix and inject it into a summary note.",
    )
    parser.add_argument("--work-dir", required=True, help="Paper-bank work directory for the paper")
    parser.add_argument("--catalog", required=True, help="Path to _catalog.yaml")
    parser.add_argument("--summary-note", required=True, help="Path to rendered summary note markdown")
    parser.add_argument("--output", required=True, help="Path to write generated _section_map.md")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and summarize section-map rendering without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = build_section_map(
        work_dir=args.work_dir,
        catalog=args.catalog,
        summary_note=args.summary_note,
        output=args.output,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
