#!/usr/bin/env python3
"""Promote per-section note frontmatter status from draft to active."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

TARGET_NOTES = [
    "intro",
    "model",
    "method",
    "theory",
    "simulation",
    "real_data",
    "discussion",
    "notation",
]

_STATUS_DRAFT_RE = re.compile(
    r"^(?P<indent>\s*)status\s*:\s*(?P<quote>[\"']?)draft(?P=quote)\s*(?P<comment>#.*)?\s*$",
    re.IGNORECASE,
)


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _find_frontmatter(lines: list[str]) -> tuple[int, int] | None:
    if not lines or lines[0].strip() != "---":
        return None

    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return (1, idx)
    return None


def _promote_frontmatter_status(frontmatter_lines: list[str]) -> tuple[list[str], bool]:
    promoted = False
    updated: list[str] = []

    for raw_line in frontmatter_lines:
        line_no_newline = raw_line.rstrip("\r\n")
        match = _STATUS_DRAFT_RE.match(line_no_newline)
        if not match:
            updated.append(raw_line)
            continue

        promoted = True
        indent = match.group("indent") or ""
        comment = match.group("comment") or ""
        suffix = f" {comment}" if comment else ""
        updated.append(f"{indent}status: active{suffix}{_line_ending(raw_line)}")

    return updated, promoted


def _promote_note(path: Path, dry_run: bool) -> tuple[str, bool]:
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    frontmatter_bounds = _find_frontmatter(lines)
    if frontmatter_bounds is None:
        return "no_frontmatter", False

    start_idx, end_idx = frontmatter_bounds
    current_frontmatter = lines[start_idx:end_idx]
    updated_frontmatter, promoted = _promote_frontmatter_status(current_frontmatter)
    if not promoted:
        return "status_not_draft", False

    if not dry_run:
        updated_lines = list(lines)
        updated_lines[start_idx:end_idx] = updated_frontmatter
        path.write_text("".join(updated_lines), encoding="utf-8")
    return "promoted", True


def promote_section_status(
    vault_path: str | Path,
    cite_key: str,
    notes_dir: str | Path,
    report: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Promote draft frontmatter status values to active for known section notes."""

    vault_path_p = Path(vault_path)
    notes_dir_p = Path(notes_dir)
    report_p = Path(report)
    expected_notes_dir = vault_path_p / "literature" / "papers" / cite_key

    missing_inputs: list[str] = []
    if not vault_path_p.exists():
        missing_inputs.append(f"vault_path:{_to_posix(vault_path_p)}")
    if not notes_dir_p.exists():
        missing_inputs.append(f"notes_dir:{_to_posix(notes_dir_p)}")
    elif not notes_dir_p.is_dir():
        missing_inputs.append(f"notes_dir_not_directory:{_to_posix(notes_dir_p)}")

    notes_dir_warning = None
    try:
        if notes_dir_p.resolve() != expected_notes_dir.resolve():
            notes_dir_warning = (
                "notes_dir does not match vault_path/cite_key target: "
                f"{_to_posix(expected_notes_dir)}"
            )
    except OSError:
        notes_dir_warning = (
            "notes_dir could not be resolved against expected target: "
            f"{_to_posix(expected_notes_dir)}"
        )

    promoted_notes: list[str] = []
    unchanged_notes: list[str] = []
    missing_notes: list[str] = []
    note_results: list[dict[str, Any]] = []

    for note_name in TARGET_NOTES:
        note_path = notes_dir_p / f"{note_name}.md"
        note_record = {
            "note": note_name,
            "path": _to_posix(note_path),
        }

        if not note_path.exists():
            missing_notes.append(note_name)
            note_results.append({**note_record, "result": "missing"})
            continue

        result, was_promoted = _promote_note(note_path, dry_run=dry_run)
        if was_promoted:
            promoted_notes.append(note_name)
        else:
            unchanged_notes.append(note_name)
        note_results.append({**note_record, "result": result})

    summary: dict[str, Any] = {
        "inputs_valid": len(missing_inputs) == 0,
        "vault_path": _to_posix(vault_path_p),
        "cite_key": cite_key,
        "notes_dir": _to_posix(notes_dir_p),
        "expected_notes_dir": _to_posix(expected_notes_dir),
        "notes_scanned": len(promoted_notes) + len(unchanged_notes),
        "promoted_count": len(promoted_notes),
        "unchanged_count": len(unchanged_notes),
        "missing_count": len(missing_notes),
        "promoted_notes": promoted_notes,
        "unchanged_notes": unchanged_notes,
        "missing_notes": missing_notes,
        "report_path": _to_posix(report_p),
        "dry_run": dry_run,
    }

    if missing_inputs:
        summary["missing_inputs"] = missing_inputs
    if notes_dir_warning:
        summary["notes_dir_warning"] = notes_dir_warning
    summary["note_results"] = note_results

    if dry_run:
        return summary

    if missing_inputs:
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    report_p.parent.mkdir(parents=True, exist_ok=True)
    report_p.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Promote per-section note status frontmatter values from draft to active.",
    )
    parser.add_argument("--vault-path", required=True, help="Path to Citadel vault root")
    parser.add_argument("--cite-key", required=True, help="Paper cite key")
    parser.add_argument(
        "--notes-dir",
        required=True,
        help="Path to per-section notes directory under literature/papers/<cite_key>/",
    )
    parser.add_argument("--report", required=True, help="Path to write _status_promotion_report.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and summarize promotions without writing note or report files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = promote_section_status(
        vault_path=args.vault_path,
        cite_key=args.cite_key,
        notes_dir=args.notes_dir,
        report=args.report,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
