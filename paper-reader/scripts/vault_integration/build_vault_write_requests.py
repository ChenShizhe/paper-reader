#!/usr/bin/env python3
"""Build a staged vault write request batch payload.

This module plans writes only; it never mutates the Citadel vault directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


DIR_TO_NOTE_TYPE: dict[str, str] = {
    "concepts": "concept",
    "assumptions": "assumption",
    "proof-patterns": "proof-pattern",
    "authors": "author",
    "comparisons": "comparison",
    "stubs": "stub",
}

NOTE_TYPE_ORDER = [
    "paper",
    "concept",
    "assumption",
    "proof-pattern",
    "author",
    "comparison",
    "stub",
]

ACTION_ORDER = ["create", "update", "upgrade"]

NOTE_TYPE_TO_VAULT_DIR: dict[str, str] = {
    "paper": "literature/papers",
    "concept": "literature/concepts",
    "assumption": "literature/assumptions",
    "proof-pattern": "literature/proof-patterns",
    "author": "literature/authors",
    "comparison": "literature/comparisons",
    "stub": "literature/papers",
}


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _parse_frontmatter(markdown: str) -> dict[str, Any]:
    if not markdown.startswith("---\n"):
        return {}
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return {}
    raw = markdown[4:end]
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _remove_known_suffixes(stem: str) -> str:
    for suffix in ("-seen-in-update", "-update", "-stub"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _collect_existing_targets(search_results: dict[str, Any]) -> dict[str, set[str]]:
    results = search_results.get("results", {}) if isinstance(search_results, dict) else {}
    mapped: dict[str, set[str]] = {
        "paper": set(),
        "concept": set(),
        "assumption": set(),
        "proof-pattern": set(),
    }
    key_to_type = {
        "papers": "paper",
        "concepts": "concept",
        "assumptions": "assumption",
        "proof_patterns": "proof-pattern",
    }
    for key, note_type in key_to_type.items():
        for item in results.get(key, []) or []:
            note_path = item.get("note_path") if isinstance(item, dict) else None
            if note_path:
                mapped[note_type].add(_to_posix(note_path))
    return mapped


def _infer_target_path(note_type: str, source_path: Path, frontmatter: dict[str, Any]) -> str:
    explicit_target = frontmatter.get("target_vault_path")
    if explicit_target:
        return _to_posix(str(explicit_target))

    vault_dir = NOTE_TYPE_TO_VAULT_DIR[note_type]
    slug = _remove_known_suffixes(source_path.stem)
    return f"{vault_dir}/{slug}.md"


def _infer_action(
    note_type: str,
    source_path: Path,
    frontmatter: dict[str, Any],
    target_path: str,
    existing_targets: dict[str, set[str]],
) -> str:
    file_name = source_path.name.lower()
    patch_type = frontmatter.get("patch_type")
    is_patch = bool(patch_type) or file_name.endswith("-update.md") or ("seen-in-update" in file_name)

    if note_type == "concept":
        if is_patch or target_path in existing_targets["concept"]:
            return "upgrade"
        return "create"

    if note_type == "stub":
        if is_patch or ("seen-in-update" in file_name) or target_path in existing_targets["paper"]:
            return "upgrade"
        return "create"

    if is_patch:
        return "update"

    return "create"


def _build_reason(note_type: str, action: str, source_path: Path) -> str:
    if note_type == "paper":
        return "Ingest main staged paper note."
    if action == "upgrade" and note_type == "concept":
        return "Upgrade existing concept from staged update or vault match."
    if action == "upgrade" and note_type == "stub":
        return "Upgrade existing stub in place to preserve Seen In backlinks."
    if action == "update":
        return f"Apply staged update patch for {note_type} note."
    if action == "create" and note_type == "stub":
        return "Create new stub note for cited paper."
    if action == "create":
        return f"Create new {note_type} note from staged artifact."
    return f"Apply {action} action for {note_type} from {source_path.name}."


def _request_sort_key(request: dict[str, Any]) -> tuple[int, str, int, str]:
    note_type = str(request.get("note_type") or "")
    action = str(request.get("action") or "")
    note_order = NOTE_TYPE_ORDER.index(note_type) if note_type in NOTE_TYPE_ORDER else len(NOTE_TYPE_ORDER)
    action_order = ACTION_ORDER.index(action) if action in ACTION_ORDER else len(ACTION_ORDER)
    return (
        note_order,
        str(request.get("target_path") or ""),
        action_order,
        str(request.get("source_path") or ""),
    )


def _empty_summary(cite_key: str, inputs_valid: bool, missing_inputs: list[str]) -> dict[str, Any]:
    counts_by_action = {action: 0 for action in ACTION_ORDER}
    counts_by_note_type = {note_type: 0 for note_type in NOTE_TYPE_ORDER}
    return {
        "cite_key": cite_key,
        "inputs_valid": inputs_valid,
        "missing_inputs": missing_inputs,
        "total_requests": 0,
        "counts_by_action": counts_by_action,
        "counts_by_note_type": counts_by_note_type,
    }


def _collect_staged_requests(
    work_dir: Path,
    existing_targets: dict[str, set[str]],
) -> list[dict[str, str]]:
    requests: list[dict[str, str]] = []
    for folder, note_type in DIR_TO_NOTE_TYPE.items():
        folder_path = work_dir / folder
        if not folder_path.exists() or not folder_path.is_dir():
            continue
        for source_path in sorted(folder_path.glob("*.md")):
            if not source_path.is_file():
                continue
            text = source_path.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(text)
            target_path = _infer_target_path(note_type, source_path, frontmatter)
            action = _infer_action(note_type, source_path, frontmatter, target_path, existing_targets)
            requests.append(
                {
                    "action": action,
                    "note_type": note_type,
                    "target_path": _to_posix(target_path),
                    "source_path": _to_posix(source_path.resolve()),
                    "reason": _build_reason(note_type, action, source_path),
                }
            )
    return requests


def build_vault_write_requests(
    work_dir: str | Path,
    vault_path: str | Path,
    output: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a staged vault write request payload.

    In dry_run mode, no files are written and a summary is returned.
    In live mode, writes ``output`` and returns the payload.
    """

    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    output = Path(output)

    paper_note_path = work_dir / "_vault_paper_note.md"
    search_results_path = work_dir / "_vault_search_results.json"

    missing_inputs: list[str] = []
    if not work_dir.exists():
        missing_inputs.append(f"work_dir:{work_dir}")
    if not vault_path.exists():
        missing_inputs.append(f"vault_path:{vault_path}")
    if not paper_note_path.exists():
        missing_inputs.append(f"paper_note:{paper_note_path}")
    if not search_results_path.exists():
        missing_inputs.append(f"search_results:{search_results_path}")

    fallback_cite_key = work_dir.name
    if missing_inputs:
        if dry_run:
            return _empty_summary(fallback_cite_key, inputs_valid=False, missing_inputs=missing_inputs)
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    search_results = json.loads(search_results_path.read_text(encoding="utf-8"))
    cite_key = str(search_results.get("cite_key") or fallback_cite_key)
    existing_targets = _collect_existing_targets(search_results)

    requests: list[dict[str, str]] = [
        {
            "action": "create",
            "note_type": "paper",
            "target_path": f"literature/papers/{cite_key}.md",
            "source_path": _to_posix(paper_note_path.resolve()),
            "reason": "Ingest main staged paper note.",
        }
    ]
    requests.extend(_collect_staged_requests(work_dir, existing_targets))
    requests = sorted(requests, key=_request_sort_key)

    action_counts = Counter(req["action"] for req in requests)
    type_counts = Counter(req["note_type"] for req in requests)
    counts_by_action = {action: int(action_counts.get(action, 0)) for action in ACTION_ORDER}
    counts_by_note_type = {note_type: int(type_counts.get(note_type, 0)) for note_type in NOTE_TYPE_ORDER}

    if dry_run:
        return {
            "cite_key": cite_key,
            "inputs_valid": True,
            "total_requests": len(requests),
            "counts_by_action": counts_by_action,
            "counts_by_note_type": counts_by_note_type,
        }

    payload = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "requests": requests,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build staged _vault-write-requests.json for M10 vault ingestion.",
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--vault-path", required=True, help="Path to Citadel vault root")
    parser.add_argument("--output", required=True, help="Path to write _vault-write-requests.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print request counts without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = build_vault_write_requests(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        output=args.output,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
