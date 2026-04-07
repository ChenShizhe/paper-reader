#!/usr/bin/env python3
"""Update catalog/xref vault link pointers from ingestion outcomes.

This module consumes staged ingestion outputs and updates:
- _catalog.yaml
- _xref_index.yaml
- optional external knowledge gaps file referenced by paper.knowledge_gaps_file

In dry-run mode it emits a JSON diff summary and performs no writes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


PAPER_NOTE_RE = re.compile(r"^literature/papers/([^/]+)\.md$")


def _to_posix(value: str | Path) -> str:
    return str(value).replace("\\", "/")


def _load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _yaml_dump_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.tmp.",
    ) as tmp:
        yaml.safe_dump(payload, tmp, sort_keys=False, allow_unicode=True)
        temp_path = Path(tmp.name)
    temp_path.replace(path)


def _extract_cite_key_from_target(target_path: str) -> str | None:
    match = PAPER_NOTE_RE.fullmatch(target_path.strip())
    return match.group(1) if match else None


def _normalize_cite_key(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    from_path = _extract_cite_key_from_target(text)
    if from_path:
        return from_path
    lowered = re.sub(r"[^a-z0-9_-]+", "", text.lower())
    return lowered


def _is_successful_action(action: dict[str, Any], report_dry_run: bool) -> bool:
    status = str(action.get("status") or "").strip().lower()
    if status == "succeeded":
        return True
    if report_dry_run and status in {"planned", "dry-run"}:
        return True
    return False


def _collect_successful_targets(
    ingestion_report: dict[str, Any],
    write_requests: dict[str, Any],
) -> set[str]:
    report_dry_run = bool(ingestion_report.get("dry_run"))
    actions = ingestion_report.get("actions", [])
    successful: set[str] = set()

    if isinstance(actions, list):
        for item in actions:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip().lower()
            if action not in {"create", "upgrade", "update"}:
                continue
            if not _is_successful_action(item, report_dry_run):
                continue
            target = _to_posix(str(item.get("target_path") or "")).strip()
            if target:
                successful.add(target)

    # Dry-run fallback when actions are not present in the report payload.
    if report_dry_run and not successful:
        reqs = write_requests.get("requests", [])
        if isinstance(reqs, list):
            for item in reqs:
                if not isinstance(item, dict):
                    continue
                action = str(item.get("action") or "").strip().lower()
                if action not in {"create", "upgrade", "update"}:
                    continue
                target = _to_posix(str(item.get("target_path") or "")).strip()
                if target:
                    successful.add(target)

    return successful


def _citation_cite_key(entry: dict[str, Any]) -> str:
    for key in ("cite_key", "cited_key", "ref_key"):
        value = _normalize_cite_key(entry.get(key))
        if value:
            return value
    return _normalize_cite_key(entry.get("vault_note"))


def _gap_linked_cite_key(gap: dict[str, Any]) -> str:
    candidates: list[Any] = [
        gap.get("cite_key"),
        gap.get("related_cite_key"),
        gap.get("related_paper"),
        gap.get("cited_key"),
        gap.get("ref_key"),
        gap.get("paper"),
        gap.get("citation"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            nested = candidate.get("cite_key") or candidate.get("cited_key") or candidate.get("ref_key")
            value = _normalize_cite_key(nested)
            if value:
                return value
        value = _normalize_cite_key(candidate)
        if value:
            return value
    return ""


def _apply_knowledge_gap_status_updates(
    gaps: list[Any],
    linked_cite_keys: set[str],
) -> tuple[list[Any], int, list[str]]:
    updated: list[Any] = []
    status_updates = 0
    changed_gap_ids: list[str] = []

    for idx, gap in enumerate(gaps, start=1):
        if not isinstance(gap, dict):
            updated.append(gap)
            continue
        mutable = dict(gap)
        linked_key = _gap_linked_cite_key(mutable)
        if linked_key and linked_key in linked_cite_keys:
            if str(mutable.get("status") or "") != "linked":
                mutable["status"] = "linked"
                status_updates += 1
                gap_id = str(mutable.get("id") or mutable.get("gap_id") or f"gap-{idx:03d}")
                changed_gap_ids.append(gap_id)
        updated.append(mutable)

    return updated, status_updates, changed_gap_ids


def update_vault_links(
    work_dir: str | Path,
    ingestion_report: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update vault link pointers in catalog/xref and optional gap file."""

    work_dir = Path(work_dir).expanduser().resolve()
    ingestion_report_path = Path(ingestion_report).expanduser().resolve()
    catalog_path = work_dir / "_catalog.yaml"
    xref_path = work_dir / "_xref_index.yaml"
    write_requests_path = work_dir / "_vault-write-requests.json"

    missing_inputs: list[str] = []
    for name, path in (
        ("work_dir", work_dir),
        ("catalog", catalog_path),
        ("xref", xref_path),
        ("write_requests", write_requests_path),
        ("ingestion_report", ingestion_report_path),
    ):
        if not path.exists():
            missing_inputs.append(f"{name}:{path}")

    if missing_inputs:
        return {
            "inputs_valid": False,
            "missing_inputs": missing_inputs,
            "catalog_updates": {},
            "xref_updates": {},
            "knowledge_gap_updates": {},
        }

    try:
        catalog = _load_yaml(catalog_path)
        xref_index = _load_yaml(xref_path)
        write_requests = _load_json(write_requests_path)
        ingestion_payload = _load_json(ingestion_report_path)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return {
            "inputs_valid": False,
            "missing_inputs": [f"parse_error:{exc}"],
            "catalog_updates": {},
            "xref_updates": {},
            "knowledge_gap_updates": {},
        }

    warnings: list[str] = []
    paper = catalog.get("paper")
    if not isinstance(paper, dict):
        paper = {}
        catalog["paper"] = paper

    cite_key = str(paper.get("cite_key") or ingestion_payload.get("cite_key") or work_dir.name).strip()
    successful_targets = _collect_successful_targets(ingestion_payload, write_requests)

    related_from_actions: set[str] = set()
    for target in successful_targets:
        target_cite = _extract_cite_key_from_target(target)
        if target_cite and target_cite != cite_key:
            related_from_actions.add(target_cite)

    catalog_changed = False
    xref_changed = False
    external_gaps_changed = False

    target_paper_note = f"literature/papers/{cite_key}.md"
    existing_vault_note = paper.get("vault_note")
    paper_note_linked = target_paper_note in successful_targets or bool(ingestion_payload.get("dry_run"))
    if paper_note_linked and existing_vault_note != target_paper_note:
        paper["vault_note"] = target_paper_note
        catalog_changed = True

    citations = xref_index.get("citations")
    if not isinstance(citations, list):
        citations = []
        xref_index["citations"] = citations

    citation_updates = 0
    citation_linked_keys: set[str] = set()
    for idx, item in enumerate(citations):
        if not isinstance(item, dict):
            continue
        ref_key = _citation_cite_key(item)
        if not ref_key:
            continue
        if ref_key not in related_from_actions:
            continue
        expected_note = f"literature/papers/{ref_key}.md"
        if str(item.get("vault_note") or "") != expected_note:
            updated = dict(item)
            updated["vault_note"] = expected_note
            citations[idx] = updated
            xref_changed = True
            citation_updates += 1
        citation_linked_keys.add(ref_key)

    related_before = paper.get("related_papers")
    related_before_list = related_before if isinstance(related_before, list) else []
    related_set = {str(item).strip() for item in related_before_list if str(item).strip()}
    related_set.update(related_from_actions)
    related_set.update(citation_linked_keys)
    related_set.discard(cite_key)
    related_after = sorted(related_set)
    if related_after != related_before_list:
        paper["related_papers"] = related_after
        catalog_changed = True

    # Route updates according to paper.knowledge_gaps_file.
    knowledge_gaps_file_ptr = paper.get("knowledge_gaps_file")
    knowledge_route = "catalog.paper.knowledge_gaps"
    knowledge_file_path: Path | None = None
    knowledge_gap_data: dict[str, Any] | None = None
    knowledge_gaps_before: list[Any] = []
    if knowledge_gaps_file_ptr:
        knowledge_route = "paper.knowledge_gaps_file"
        knowledge_file_path = work_dir / str(knowledge_gaps_file_ptr)
        if knowledge_file_path.exists():
            try:
                knowledge_gap_data = _load_yaml(knowledge_file_path)
            except (OSError, yaml.YAMLError):
                warnings.append(f"knowledge_gaps_file_unreadable:{knowledge_file_path}")
                knowledge_gap_data = {}
        else:
            warnings.append(f"knowledge_gaps_file_missing:{knowledge_file_path}")
            knowledge_gap_data = {}
        raw_gaps = knowledge_gap_data.get("gaps", []) if isinstance(knowledge_gap_data, dict) else []
        knowledge_gaps_before = raw_gaps if isinstance(raw_gaps, list) else []
    else:
        raw_gaps = paper.get("knowledge_gaps", [])
        knowledge_gaps_before = raw_gaps if isinstance(raw_gaps, list) else []

    updated_gaps, gap_status_updates, changed_gap_ids = _apply_knowledge_gap_status_updates(
        knowledge_gaps_before,
        linked_cite_keys=related_from_actions | citation_linked_keys,
    )

    if knowledge_route == "paper.knowledge_gaps_file":
        if isinstance(knowledge_gap_data, dict):
            before = knowledge_gap_data.get("gaps", [])
            if before != updated_gaps:
                knowledge_gap_data["gaps"] = updated_gaps
                external_gaps_changed = True
    else:
        before = paper.get("knowledge_gaps", [])
        if before != updated_gaps:
            paper["knowledge_gaps"] = updated_gaps
            catalog_changed = True

    if not dry_run:
        if xref_changed:
            _yaml_dump_atomic(xref_path, xref_index)
        if catalog_changed:
            _yaml_dump_atomic(catalog_path, catalog)
        if external_gaps_changed and knowledge_file_path and isinstance(knowledge_gap_data, dict):
            _yaml_dump_atomic(knowledge_file_path, knowledge_gap_data)

    return {
        "cite_key": cite_key,
        "inputs_valid": True,
        "missing_inputs": [],
        "warnings": warnings,
        "dry_run": bool(dry_run),
        "catalog_updates": {
            "catalog_path": _to_posix(catalog_path),
            "vault_note_before": existing_vault_note,
            "vault_note_after": paper.get("vault_note"),
            "vault_note_changed": bool(paper_note_linked and existing_vault_note != paper.get("vault_note")),
            "related_papers_before": related_before_list,
            "related_papers_after": related_after,
            "related_papers_added": sorted(set(related_after) - set(related_before_list)),
            "write_performed": bool(catalog_changed and not dry_run),
        },
        "xref_updates": {
            "xref_path": _to_posix(xref_path),
            "citations_scanned": len(citations),
            "citations_updated": citation_updates,
            "linked_citations": sorted(citation_linked_keys),
            "write_performed": bool(xref_changed and not dry_run),
        },
        "knowledge_gap_updates": {
            "route": knowledge_route,
            "knowledge_gaps_file": _to_posix(knowledge_file_path) if knowledge_file_path else None,
            "gaps_scanned": len(knowledge_gaps_before),
            "status_updates": gap_status_updates,
            "updated_gap_ids": changed_gap_ids,
            "write_performed": bool(external_gaps_changed and not dry_run),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update catalog/xref vault links from ingestion outcomes.",
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--ingestion-report", required=True, help="Path to _vault_ingestion_report.json")
    parser.add_argument("--dry-run", action="store_true", help="Emit diff summary without writing files")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = update_vault_links(
        work_dir=args.work_dir,
        ingestion_report=args.ingestion_report,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    if not result.get("inputs_valid", False) and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
