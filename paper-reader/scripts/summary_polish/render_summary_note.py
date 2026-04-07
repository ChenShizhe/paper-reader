#!/usr/bin/env python3
"""Render the Part 9 literature summary note from synthesis layers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REQUIRED_HEADINGS = [
    "## TL;DR",
    "## Problem & Motivation",
    "## Claimed Contributions",
    "## Model & Setting",
    "## Proposed Method",
    "## Theoretical Guarantees",
    "## Empirical Evidence",
    "## Connections to Prior Work",
    "## Knowledge Gaps",
    "## Reading Quality",
    "## Section Map",
]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _to_posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> Any:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if loaded is not None else {}


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _clean_text(
                item.get("id")
                or item.get("gap_id")
                or item.get("title")
                or item.get("description")
            )
        else:
            text = _clean_text(item)
        if text and text not in items:
            items.append(text)
    return items


def _dedupe(values: list[str], limit: int | None = None) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
        if limit is not None and len(unique) >= limit:
            break
    return unique


def _collect_row_points(rows: list[dict[str, Any]], l3_points: list[dict[str, Any]]) -> list[str]:
    section_ids = {str(row.get("section_id") or "") for row in rows}
    points: list[str] = []

    for entry in l3_points:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("section_id") or "") not in section_ids:
            continue
        text = _clean_text(entry.get("point"))
        if text:
            points.append(text)

    for row in rows:
        claims = row.get("key_claims")
        if isinstance(claims, list):
            for claim in claims:
                text = _clean_text(claim)
                if text:
                    points.append(text)

    return _dedupe(points)


def _select_rows(
    l2_rows: list[dict[str, Any]],
    section_types: set[str],
    mapped_notes: set[str] | None = None,
) -> list[dict[str, Any]]:
    notes = mapped_notes or set()
    picked: list[dict[str, Any]] = []
    for row in l2_rows:
        if not isinstance(row, dict):
            continue
        row_type = _clean_text(row.get("section_type")).lower()
        row_note = _clean_text(row.get("mapped_note")).lower()
        if row_type in section_types or row_note in notes:
            picked.append(row)
    return picked


def _render_bullets(points: list[str], empty_line: str) -> tuple[str, bool]:
    if not points:
        return f"- {empty_line}", False
    return "\n".join(f"- {point}" for point in points), True


def _render_numbered(points: list[str], empty_line: str) -> tuple[str, bool]:
    if not points:
        return f"1. {empty_line}", False
    return "\n".join(f"{idx}. {point}" for idx, point in enumerate(points, start=1)), True


def _build_frontmatter(
    cite_key: str,
    paper_meta: dict[str, Any],
    l2_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    related = _normalize_string_list(paper_meta.get("related_papers"))
    agrees_with = _normalize_string_list(paper_meta.get("agrees_with"))
    contradicts = _normalize_string_list(paper_meta.get("contradicts"))
    extends = _normalize_string_list(paper_meta.get("extends"))

    knowledge_gaps = _normalize_string_list(paper_meta.get("knowledge_gaps"))
    for row in l2_rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("has_knowledge_gap")):
            continue
        section_label = _clean_text(row.get("section_heading") or row.get("section_id"))
        if section_label and section_label not in knowledge_gaps:
            knowledge_gaps.append(section_label)

    revision_history_file = _clean_text(paper_meta.get("revision_history_file"))
    if not revision_history_file:
        revision_history_file = "_revision_history.md"

    return {
        "citekey": cite_key,
        "status": "draft",
        "related": related,
        "agrees_with": agrees_with,
        "contradicts": contradicts,
        "extends": extends,
        "knowledge_gaps": knowledge_gaps,
        "last_reviewed": datetime.now(tz=timezone.utc).date().isoformat(),
        "revision_history_file": revision_history_file,
    }


def _render_section_map(l2_rows: list[dict[str, Any]]) -> tuple[str, bool]:
    if not l2_rows:
        return "No catalog sections available from synthesis layers.", False

    lines = [
        "| Section ID | Heading | Type | Status | Mapped Note | Key Claim |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in l2_rows:
        section_id = _clean_text(row.get("section_id")) or "unknown"
        heading = _clean_text(row.get("section_heading")) or "Untitled"
        section_type = _clean_text(row.get("section_type")) or "unknown"
        status = _clean_text(row.get("comprehension_status")) or "unknown"
        mapped_note = _clean_text(row.get("mapped_note")) or "unknown"
        claims = row.get("key_claims") if isinstance(row.get("key_claims"), list) else []
        claim = _clean_text(claims[0]) if claims else "No synthesized claim yet."
        escaped = [
            section_id.replace("|", r"\|"),
            heading.replace("|", r"\|"),
            section_type.replace("|", r"\|"),
            status.replace("|", r"\|"),
            mapped_note.replace("|", r"\|"),
            claim.replace("|", r"\|"),
        ]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines), True


def _build_sections(
    l1_notes: dict[str, Any],
    l2_rows: list[dict[str, Any]],
    l3_points: list[dict[str, Any]],
    related: list[str],
) -> tuple[dict[str, str], list[str]]:
    all_points = _collect_row_points(l2_rows, l3_points)
    background_rows = _select_rows(l2_rows, {"background", "intro", "introduction", "section"}, {"intro"})
    model_rows = _select_rows(l2_rows, {"model", "models"}, {"model"})
    method_rows = _select_rows(l2_rows, {"method", "methods"}, {"method"})
    theory_rows = _select_rows(l2_rows, {"theory"}, {"theory"})
    empirical_rows = _select_rows(
        l2_rows,
        {"application", "empirical", "real_data", "simulation", "simulations"},
        {"real_data", "simulation"},
    )

    sections: dict[str, str] = {}
    missing_sections: list[str] = []

    tldr, ok_tldr = _render_bullets(
        _dedupe(all_points, limit=3),
        "No synthesized claims yet; run upstream extraction passes before final polishing.",
    )
    sections["## TL;DR"] = tldr
    if not ok_tldr:
        missing_sections.append("## TL;DR")

    problem_points = _collect_row_points(background_rows, l3_points)
    problem, ok_problem = _render_bullets(
        _dedupe(problem_points, limit=4),
        "Problem framing is pending in synthesis layers.",
    )
    sections["## Problem & Motivation"] = problem
    if not ok_problem:
        missing_sections.append("## Problem & Motivation")

    contribution_rows = method_rows + theory_rows + empirical_rows
    contributions, ok_contrib = _render_numbered(
        _dedupe(_collect_row_points(contribution_rows, l3_points), limit=5),
        "Claimed contributions are not yet populated in the synthesis traces.",
    )
    sections["## Claimed Contributions"] = contributions
    if not ok_contrib:
        missing_sections.append("## Claimed Contributions")

    model_points = _collect_row_points(model_rows, l3_points)
    if not model_points:
        model_l1 = l1_notes.get("model") if isinstance(l1_notes, dict) else {}
        model_status = _clean_text(model_l1.get("status")) if isinstance(model_l1, dict) else "missing"
        if model_status:
            model_points = [f"Model note status in L1: {model_status}."]
    model_text, ok_model = _render_bullets(
        _dedupe(model_points, limit=4),
        "Model assumptions and setup are not yet synthesized.",
    )
    sections["## Model & Setting"] = model_text
    if not ok_model:
        missing_sections.append("## Model & Setting")

    method_text, ok_method = _render_bullets(
        _dedupe(_collect_row_points(method_rows, l3_points), limit=4),
        "Method details are not yet synthesized in layers.",
    )
    sections["## Proposed Method"] = method_text
    if not ok_method:
        missing_sections.append("## Proposed Method")

    theory_text, ok_theory = _render_bullets(
        _dedupe(_collect_row_points(theory_rows, l3_points), limit=4),
        "Theoretical guarantees are not yet synthesized in layers.",
    )
    sections["## Theoretical Guarantees"] = theory_text
    if not ok_theory:
        missing_sections.append("## Theoretical Guarantees")

    empirical_text, ok_empirical = _render_bullets(
        _dedupe(_collect_row_points(empirical_rows, l3_points), limit=4),
        "Empirical evidence traces are not yet synthesized in layers.",
    )
    sections["## Empirical Evidence"] = empirical_text
    if not ok_empirical:
        missing_sections.append("## Empirical Evidence")

    connections_lines: list[str] = []
    if related:
        connections_lines.append("- Related papers: " + ", ".join(f"[[{item}]]" for item in related))
    citation_total = sum(
        int(row.get("citation_count") or 0) for row in l2_rows if isinstance(row, dict)
    )
    connections_lines.append(f"- Cross-reference citations counted in layers: {citation_total}.")
    if not related and citation_total == 0:
        connections_lines.append("- Prior-work links are currently sparse; enrich xref and related paper metadata.")
        missing_sections.append("## Connections to Prior Work")
    sections["## Connections to Prior Work"] = "\n".join(connections_lines)

    gap_lines: list[str] = []
    for row in l2_rows:
        if not isinstance(row, dict):
            continue
        if bool(row.get("has_knowledge_gap")):
            label = _clean_text(row.get("section_heading") or row.get("section_id"))
            if label:
                gap_lines.append(f"Knowledge gap flagged in section: {label}.")
    for note_name, payload in (l1_notes.items() if isinstance(l1_notes, dict) else []):
        if not isinstance(payload, dict):
            continue
        if _clean_text(payload.get("status")).lower() == "missing":
            gap_lines.append(f"Missing section-note synthesis: {note_name}.")
    gap_text, ok_gaps = _render_bullets(
        _dedupe(gap_lines, limit=8),
        "No explicit knowledge gaps were flagged in the current synthesis layers.",
    )
    sections["## Knowledge Gaps"] = gap_text
    if not ok_gaps:
        missing_sections.append("## Knowledge Gaps")

    pending_count = sum(
        1
        for row in l2_rows
        if isinstance(row, dict) and _clean_text(row.get("comprehension_status")).lower() == "pending"
    )
    missing_l1 = [
        name
        for name, payload in (l1_notes.items() if isinstance(l1_notes, dict) else [])
        if isinstance(payload, dict) and _clean_text(payload.get("status")).lower() == "missing"
    ]
    quality_lines = [
        f"- Sections synthesized from L2 map: {len(l2_rows)}.",
        f"- Summary points traced from L3: {len(l3_points)}.",
        f"- Sections still marked pending: {pending_count}.",
        "- Missing L1 section notes: "
        + (", ".join(sorted(missing_l1)) if missing_l1 else "none."),
    ]
    sections["## Reading Quality"] = "\n".join(quality_lines)

    section_map, ok_map = _render_section_map(l2_rows)
    sections["## Section Map"] = section_map
    if not ok_map:
        missing_sections.append("## Section Map")

    return sections, _dedupe(missing_sections)


def _build_note_markdown(
    title: str,
    frontmatter: dict[str, Any],
    section_content: dict[str, str],
) -> str:
    frontmatter_block = (
        "---\n"
        + yaml.safe_dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
        + "\n---\n"
    )

    body: list[str] = [frontmatter_block, f"# {title}", ""]
    for heading in REQUIRED_HEADINGS:
        body.append(heading)
        body.append("")
        body.append(section_content.get(heading, "No content generated."))
        body.append("")
    return "\n".join(body).rstrip() + "\n"


def render_summary_note(
    work_dir: str | Path,
    cite_key: str,
    vault_path: str | Path,
    layers: str | Path,
    output: str | Path | None = None,
    topic: str = "papers",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Render a literature summary note from `_summary_layers.json` traces."""

    work_dir_p = Path(work_dir)
    vault_path_p = Path(vault_path)
    layers_p = Path(layers)
    target_path = Path(output) if output else (vault_path_p / "literature" / topic / f"{cite_key}.md")
    catalog_path = work_dir_p / "_catalog.yaml"

    missing_inputs: list[str] = []
    if not work_dir_p.exists():
        missing_inputs.append(f"work_dir:{_to_posix(work_dir_p)}")
    if not vault_path_p.exists():
        missing_inputs.append(f"vault_path:{_to_posix(vault_path_p)}")
    if not catalog_path.exists():
        missing_inputs.append(f"catalog:{_to_posix(catalog_path)}")
    if not layers_p.exists():
        missing_inputs.append(f"layers:{_to_posix(layers_p)}")

    inputs_valid = len(missing_inputs) == 0
    catalog: dict[str, Any] = {}
    layer_data: dict[str, Any] = {}

    if catalog_path.exists():
        loaded_catalog = _load_yaml(catalog_path)
        if isinstance(loaded_catalog, dict):
            catalog = loaded_catalog
    if layers_p.exists():
        loaded_layers = _load_json(layers_p)
        if isinstance(loaded_layers, dict):
            layer_data = loaded_layers

    l1_notes = layer_data.get("l1_notes")
    l2_rows = layer_data.get("l2_section_map")
    l3_points = layer_data.get("l3_summary_points")

    l1_notes = l1_notes if isinstance(l1_notes, dict) else {}
    l2_rows = l2_rows if isinstance(l2_rows, list) else []
    l3_points = l3_points if isinstance(l3_points, list) else []

    paper_meta = catalog.get("paper") if isinstance(catalog.get("paper"), dict) else {}
    if not isinstance(paper_meta, dict):
        paper_meta = {}
    title = _clean_text(paper_meta.get("title")) or cite_key
    related = _normalize_string_list(paper_meta.get("related_papers"))

    sections, missing_sections = _build_sections(
        l1_notes=l1_notes,
        l2_rows=l2_rows,
        l3_points=l3_points,
        related=related,
    )
    frontmatter = _build_frontmatter(cite_key=cite_key, paper_meta=paper_meta, l2_rows=l2_rows)

    summary = {
        "inputs_valid": inputs_valid,
        "target_path": _to_posix(target_path),
        "sections_rendered": REQUIRED_HEADINGS,
        "missing_sections": missing_sections,
    }

    if dry_run:
        if missing_inputs:
            summary["missing_inputs"] = missing_inputs
        return summary

    if not inputs_valid:
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    rendered = _build_note_markdown(title=title, frontmatter=frontmatter, section_content=sections)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(rendered, encoding="utf-8")
    return {
        **summary,
        "output_path": _to_posix(target_path),
        "bytes_written": len(rendered.encode("utf-8")),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render Part 9 summary note markdown from _summary_layers.json.")
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--cite-key", required=True, help="Paper cite key")
    parser.add_argument("--vault-path", required=True, help="Path to citadel vault root")
    parser.add_argument("--layers", required=True, help="Path to _summary_layers.json")
    parser.add_argument("--output", required=False, default=None, help="Output markdown path")
    parser.add_argument("--topic", required=False, default="papers", help="Vault literature topic slug")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and emit JSON summary without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = render_summary_note(
        work_dir=args.work_dir,
        cite_key=args.cite_key,
        vault_path=args.vault_path,
        layers=args.layers,
        output=args.output,
        topic=args.topic,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
