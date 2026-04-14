#!/usr/bin/env python3
"""Catalog Lineage Updater — processes executor outputs and updates _catalog.yaml.

Reads the three reseg executor output files (Tasks 02-04), updates _catalog.yaml
with derived_from lineage, reseg_version counters, and correct comprehension
statuses, then writes a catalog v5 snapshot.

Usage:
    python3 reseg_catalog_update.py --cite-key <cite_key> [--dry-run]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

PAPER_BANK = Path.home() / "Documents" / "paper-bank"

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from build_catalog import snapshot_catalog


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Optional[dict]:
    """Load a JSON file, returning None if not found or unreadable."""
    if not path.exists():
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_catalog(catalog_path: Path) -> dict:
    """Load _catalog.yaml from the given path."""
    with open(catalog_path) as fh:
        return yaml.safe_load(fh)


def _save_yaml(path: Path, data: dict) -> None:
    """Write a dict to a YAML file."""
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# ID derivation helpers
# ---------------------------------------------------------------------------


def _seg_id_from_file(file_path: str) -> str:
    """Derive segment_id from a relative file path like 'segments/seg_id.md'."""
    return Path(file_path).stem


def _archived_seg_id(archived_file: str) -> str:
    """Derive segment_id from 'segments/archive/seg_id.v1.md'."""
    stem = Path(archived_file).stem  # e.g. 'seg_id.v1'
    if stem.endswith(".v1"):
        return stem[:-3]
    return stem


# ---------------------------------------------------------------------------
# Catalog structure helpers
# ---------------------------------------------------------------------------


def _build_segment_map(catalog: dict) -> Dict[str, dict]:
    """Map segment_id -> segment entry from catalog.segments flat list."""
    return {seg["id"]: seg for seg in catalog.get("segments", []) if "id" in seg}


def _build_section_for_seg(catalog: dict) -> Dict[str, dict]:
    """Map segment_id -> its parent section dict."""
    result: Dict[str, dict] = {}
    for section in catalog.get("sections", []):
        for seg_id in section.get("segments", []):
            result[seg_id] = section
    return result


def _build_manifest_map(paper_dir: Path) -> Dict[str, dict]:
    """Map segment_id -> manifest entry (for derived_from lookup)."""
    manifest_path = paper_dir / "_segment_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path) as fh:
            manifest = json.load(fh)
    except Exception:
        return {}
    return {
        s["segment_id"]: s
        for s in manifest.get("segments", [])
        if "segment_id" in s
    }


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------


def compute_catalog_diff(
    cite_key: str,
    paper_dir: Path,
    split_output: Optional[dict],
    merge_output: Optional[dict],
    rebalance_output: Optional[dict],
    plan: Optional[dict],
    catalog: dict,
) -> dict:
    """Compute the diff that would be applied to the catalog.

    Returns a dict with the required dry-run keys:
      cite_key, entries_to_remove, entries_to_add, entries_to_update, inputs_valid.
    """
    segment_map = _build_segment_map(catalog)
    section_for_seg = _build_section_for_seg(catalog)
    manifest_map = _build_manifest_map(paper_dir)

    entries_to_remove: List[str] = []
    entries_to_add: List[dict] = []
    entries_to_update: List[dict] = []

    # ── Collect all archived segment IDs ─────────────────────────────────────
    all_archived_files: List[str] = []
    if split_output:
        all_archived_files.extend(split_output.get("archived_files", []))
    if merge_output:
        all_archived_files.extend(merge_output.get("archived_files", []))

    archived_seg_ids = set()
    for af in all_archived_files:
        seg_id = _archived_seg_id(af)
        archived_seg_ids.add(seg_id)
        if seg_id in segment_map:
            entries_to_remove.append(seg_id)

    # ── New segments from splits ──────────────────────────────────────────────
    if split_output:
        new_split_files: List[str] = split_output.get("new_segment_files", [])
        split_archived_ids = set(
            _archived_seg_id(f) for f in split_output.get("archived_files", [])
        )

        # Group children by parent for first/last boundary status logic
        parent_children: Dict[str, List[str]] = {}
        for new_file in new_split_files:
            new_id = _seg_id_from_file(new_file)
            manifest_entry = manifest_map.get(new_id, {})
            parent_id = manifest_entry.get("derived_from", "")
            if not parent_id:
                # Fallback: infer from segment ID prefix pattern
                for archived_id in split_archived_ids:
                    if new_id.startswith(archived_id + "_part_") or new_id.startswith(
                        archived_id + "_a"
                    ) or new_id.startswith(archived_id + "_b"):
                        parent_id = archived_id
                        break
            if not parent_id:
                parent_id = "unknown"
            parent_children.setdefault(parent_id, []).append(new_id)

        for new_file in new_split_files:
            new_id = _seg_id_from_file(new_file)
            manifest_entry = manifest_map.get(new_id, {})
            parent_id = manifest_entry.get("derived_from", "")
            if not parent_id:
                for archived_id in split_archived_ids:
                    if new_id.startswith(archived_id + "_part_") or new_id.startswith(
                        archived_id + "_a"
                    ) or new_id.startswith(archived_id + "_b"):
                        parent_id = archived_id
                        break
            if not parent_id:
                parent_id = "unknown"

            parent_seg = segment_map.get(parent_id, {})
            section_type = parent_seg.get("section_type", "unknown")
            section_id = parent_seg.get("section_id", "")

            # Status: first/last child → comprehended; middle → needs_re-read
            children = parent_children.get(parent_id, [new_id])
            idx = children.index(new_id) if new_id in children else 0
            if len(children) <= 1 or idx == 0 or idx == len(children) - 1:
                status = "comprehended"
            else:
                status = "needs_re-read"

            entries_to_add.append(
                {
                    "segment_id": new_id,
                    "file": new_file,
                    "section_id": section_id,
                    "section_type": section_type,
                    "status": status,
                    "derived_from": f"{parent_id} (v1)",
                    "reseg_version": 2,
                    "source": "split",
                }
            )

    # ── New segments from merges ──────────────────────────────────────────────
    if merge_output:
        new_merge_files: List[str] = merge_output.get("new_segment_files", [])
        merge_archived_ids = set(
            _archived_seg_id(f) for f in merge_output.get("archived_files", [])
        )

        # Build proposed_id -> source_segment_ids from plan
        merge_source_map: Dict[str, List[str]] = {}
        if plan:
            for merge_entry in plan.get("merges", []):
                proposed_id = merge_entry.get("proposed_segment_id", "")
                raw_sources = merge_entry.get("source_segments", [])
                source_ids = [
                    Path(s).stem.replace(".v1", "") if s.endswith(".md") else s
                    for s in raw_sources
                ]
                if proposed_id:
                    merge_source_map[proposed_id] = source_ids

        for new_file in new_merge_files:
            new_id = _seg_id_from_file(new_file)
            source_ids = merge_source_map.get(new_id, list(merge_archived_ids))

            first_source = source_ids[0] if source_ids else None
            section_type = "unknown"
            section_id = ""
            if first_source:
                parent_seg = segment_map.get(first_source, {})
                section_type = parent_seg.get("section_type", "unknown")
                section_id = parent_seg.get("section_id", "")

            derived_str = ", ".join(str(s) for s in source_ids) + " (v1)"

            entries_to_add.append(
                {
                    "segment_id": new_id,
                    "file": new_file,
                    "section_id": section_id,
                    "section_type": section_type,
                    "status": "needs_re-read",
                    "derived_from": derived_str,
                    "reseg_version": 2,
                    "source": "merge",
                }
            )

    # ── Updated segments from rebalances ─────────────────────────────────────
    if rebalance_output:
        for updated_file in rebalance_output.get("updated_segment_files", []):
            seg_id = _seg_id_from_file(updated_file)
            if seg_id in segment_map:
                current_version = segment_map[seg_id].get("reseg_version", 1)
                entries_to_update.append(
                    {
                        "segment_id": seg_id,
                        "status": "needs_re-read",
                        "reseg_version": current_version + 1,
                        "rebalanced": True,
                    }
                )

    return {
        "cite_key": cite_key,
        "entries_to_remove": entries_to_remove,
        "entries_to_add": entries_to_add,
        "entries_to_update": entries_to_update,
        "inputs_valid": True,
    }


# ---------------------------------------------------------------------------
# Diff application
# ---------------------------------------------------------------------------


def apply_catalog_diff(catalog: dict, diff: dict) -> dict:
    """Apply a computed diff to a catalog dict. Returns the modified catalog."""
    catalog = copy.deepcopy(catalog)

    entries_to_remove = set(diff.get("entries_to_remove", []))
    entries_to_add: List[dict] = diff.get("entries_to_add", [])
    entries_to_update: List[dict] = diff.get("entries_to_update", [])

    # ── Remove archived entries from sections.segments lists ─────────────────
    for section in catalog.get("sections", []):
        section["segments"] = [
            s for s in section.get("segments", []) if s not in entries_to_remove
        ]

    # ── Remove archived entries from flat segments list ───────────────────────
    catalog["segments"] = [
        seg for seg in catalog.get("segments", []) if seg.get("id") not in entries_to_remove
    ]

    # ── Add new entries ───────────────────────────────────────────────────────
    section_idx_map = {
        sec["id"]: i for i, sec in enumerate(catalog.get("sections", []))
    }

    for entry in entries_to_add:
        new_seg: dict = {
            "id": entry["segment_id"],
            "file": entry["file"],
            "section_id": entry["section_id"],
            "section_type": entry["section_type"],
            "token_estimate": 0,
            "has_equations": False,
            "has_figures": False,
            "has_tables": False,
            "comprehension_status": entry["status"],
            "derived_from": entry["derived_from"],
            "reseg_version": entry["reseg_version"],
            "translation_tool": None,
            "source_pages": [],
            "source_lines": [],
        }
        catalog.setdefault("segments", []).append(new_seg)

        # Add segment_id to its section's segments list
        sec_id = entry.get("section_id", "")
        if sec_id and sec_id in section_idx_map:
            sec = catalog["sections"][section_idx_map[sec_id]]
            if entry["segment_id"] not in sec.get("segments", []):
                sec.setdefault("segments", []).append(entry["segment_id"])

    # ── Update rebalanced entries ─────────────────────────────────────────────
    seg_id_map = {seg["id"]: seg for seg in catalog.get("segments", [])}
    for update in entries_to_update:
        seg_id = update["segment_id"]
        if seg_id in seg_id_map:
            seg_id_map[seg_id]["comprehension_status"] = update["status"]
            seg_id_map[seg_id]["reseg_version"] = update["reseg_version"]
            seg_id_map[seg_id]["rebalanced"] = update.get("rebalanced", False)

    # ── Update catalog header ─────────────────────────────────────────────────
    now = datetime.now(tz=timezone.utc).isoformat()
    paper = catalog.get("paper", {})
    paper["catalog_version"] = 5
    paper["reseg_pass"] = 1
    paper["reseg_completed_at"] = now
    paper["last_updated"] = now
    catalog["paper"] = paper

    return catalog


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_catalog_lineage(cite_key: str, dry_run: bool = False) -> dict:
    """Update _catalog.yaml with lineage from reseg executor outputs.

    In dry_run mode: returns the diff dict without writing any files.
    In live mode: calls snapshot_catalog, applies changes, writes updated
    _catalog.yaml, saves _catalog_v5.yaml, and writes
    _reseg_catalog_update_output.json.

    Raises SystemExit(1) with stderr on invalid cite_key.
    """
    paper_dir = PAPER_BANK / cite_key
    catalog_path = paper_dir / "_catalog.yaml"

    if not paper_dir.exists():
        print(
            f"ERROR: Paper directory not found for cite_key '{cite_key}': {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not catalog_path.exists():
        print(
            f"ERROR: _catalog.yaml not found in '{paper_dir}'",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load all inputs (executor outputs are optional — absent means no changes)
    catalog = _load_catalog(catalog_path)
    plan = _load_json(paper_dir / "_reseg_plan.json")
    split_output = _load_json(paper_dir / "_reseg_split_output.json")
    merge_output = _load_json(paper_dir / "_reseg_merge_output.json")
    rebalance_output = _load_json(paper_dir / "_reseg_rebalance_output.json")

    # Compute the diff
    diff = compute_catalog_diff(
        cite_key,
        paper_dir,
        split_output,
        merge_output,
        rebalance_output,
        plan,
        catalog,
    )

    if dry_run:
        return diff

    # ── Live mode ─────────────────────────────────────────────────────────────

    # 1. Snapshot pre-update state BEFORE any writes
    pre_snapshot_path = snapshot_catalog(str(paper_dir))

    # 2. Apply diff to catalog
    updated_catalog = apply_catalog_diff(catalog, diff)

    # 3. Write updated _catalog.yaml
    _save_yaml(catalog_path, updated_catalog)

    # 4. Write explicit _catalog_v5.yaml snapshot
    v5_path = paper_dir / "_catalog_v5.yaml"
    _save_yaml(v5_path, updated_catalog)

    # 5. Write execution summary
    summary = {
        "cite_key": cite_key,
        "entries_removed": len(diff["entries_to_remove"]),
        "entries_added": len(diff["entries_to_add"]),
        "entries_updated": len(diff["entries_to_update"]),
        "catalog_version": 5,
        "reseg_pass": 1,
        "snapshot_saved": Path(pre_snapshot_path).name if pre_snapshot_path else None,
        "v5_snapshot_saved": "_catalog_v5.yaml",
    }

    output_path = paper_dir / "_reseg_catalog_update_output.json"
    with open(output_path, "w") as fh:
        json.dump(summary, fh, indent=2)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update _catalog.yaml with lineage from reseg executor outputs."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print diff plan as JSON without writing any files.",
    )
    args = parser.parse_args()

    result = update_catalog_lineage(args.cite_key, dry_run=args.dry_run)

    if args.dry_run:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
