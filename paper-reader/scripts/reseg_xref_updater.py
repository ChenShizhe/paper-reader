#!/usr/bin/env python3
"""reseg_xref_updater.py — Cross-Reference Updater for re-segmented papers.

Scans per-section Citadel notes and _xref_index.yaml for stale segment
path references and updates them after re-segmentation.  Appends
idempotent Re-segmentation notices to affected notes.

Usage:
    python3 reseg_xref_updater.py --cite-key <cite_key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

PAPER_BANK = Path.home() / "Documents" / "paper-bank"
CITADEL = Path.home() / "Documents" / "citadel"

SECTION_NOTE_NAMES = [
    "intro.md",
    "model.md",
    "method.md",
    "theory.md",
    "simulation.md",
    "real_data.md",
    "discussion.md",
    "notation.md",
]

RESEG_NOTICE_MARKER = "Re-segmentation notice"

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_yaml(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Archive mapping builders
# ---------------------------------------------------------------------------


def _build_archive_map(plan: dict) -> Dict[str, List[str]]:
    """Build {old_segment_id: [new_segment_id, ...]} from reseg plan."""
    archive_map: Dict[str, List[str]] = {}

    for split in plan.get("splits", []):
        old_id = split.get("segment_id", "")
        children = split.get("proposed_children", [])
        if old_id and children:
            archive_map[old_id] = list(children)

    for merge in plan.get("merges", []):
        new_id = merge.get("proposed_segment_id", "")
        sources = merge.get("source_segments", [])
        for src in sources:
            if src and new_id:
                archive_map[src] = [new_id]

    for rebalance in plan.get("rebalances", []):
        from_seg = rebalance.get("from_segment", "")
        to_seg = rebalance.get("to_segment", "")
        if from_seg and to_seg:
            archive_map[from_seg] = [to_seg]

    return archive_map


def _build_seg_file_map(catalog: dict) -> Dict[str, str]:
    """Build {segment_id: file_path} from catalog segments list."""
    result: Dict[str, str] = {}
    for seg in catalog.get("segments", []):
        if isinstance(seg, dict):
            seg_id = seg.get("id", "")
            seg_file = seg.get("file", "")
            if seg_id and seg_file:
                result[seg_id] = seg_file
    return result


def _build_needs_reread_set(catalog: dict) -> set:
    """Return set of segment IDs whose comprehension_status indicates needs re-read."""
    result: set = set()
    for seg in catalog.get("segments", []):
        if isinstance(seg, dict):
            status = seg.get("comprehension_status", "")
            if "needs_re" in status or "needs-re" in status:
                seg_id = seg.get("id", "")
                if seg_id:
                    result.add(seg_id)
    return result


# ---------------------------------------------------------------------------
# Note scanning helpers
# ---------------------------------------------------------------------------

# Matches segment file path patterns like:
#   segments/seg_id.md  or  paper-bank/cite_key/segments/seg_id.md
_SEG_PATH_RE = re.compile(r"segments/([A-Za-z0-9_\-]+)\.md")


def _find_stale_refs(
    text: str, archive_map: Dict[str, List[str]]
) -> List[Tuple[str, List[str]]]:
    """Return list of (old_seg_id, [new_seg_ids]) for stale refs found in text."""
    found: List[Tuple[str, List[str]]] = []
    seen: set = set()
    for m in _SEG_PATH_RE.finditer(text):
        seg_id = m.group(1)
        if seg_id in archive_map and seg_id not in seen:
            found.append((seg_id, archive_map[seg_id]))
            seen.add(seg_id)
    return found


def _replace_stale_refs(
    text: str,
    archive_map: Dict[str, List[str]],
    seg_file_map: Dict[str, str],
) -> Tuple[str, int]:
    """Replace stale segment path references in text. Returns (updated_text, replace_count)."""
    count = 0

    def replacer(m: re.Match) -> str:
        nonlocal count
        seg_id = m.group(1)
        if seg_id not in archive_map:
            return m.group(0)
        new_ids = archive_map[seg_id]
        count += 1
        if len(new_ids) == 1:
            return seg_file_map.get(new_ids[0], f"segments/{new_ids[0]}.md")
        # Multiple replacements: keep first and annotate siblings
        primary = seg_file_map.get(new_ids[0], f"segments/{new_ids[0]}.md")
        siblings = ", ".join(new_ids[1:])
        return f"{primary} (archived — see {siblings})"

    updated = _SEG_PATH_RE.sub(replacer, text)
    return updated, count


def _split_frontmatter(text: str) -> Tuple[str, str]:
    """Split text into (frontmatter_block, body). frontmatter_block includes closing ---."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[: end + 4]
            body = text[end + 4:]
            return fm, body
    return "", text


def _has_notice(text: str) -> bool:
    """Return True if a Re-segmentation notice is already present in text."""
    return RESEG_NOTICE_MARKER in text


def _build_notice(
    affected_seg_ids: List[str], new_ids_map: Dict[str, List[str]]
) -> str:
    """Build a Re-segmentation notice block for the given segment IDs."""
    lines = []
    for seg_id in affected_seg_ids:
        new_ids = new_ids_map.get(seg_id, [])
        if len(new_ids) > 1:
            siblings = ", ".join(new_ids[1:])
            sibling_note = f" Some content may now reside in a sibling segment ({siblings})."
        else:
            sibling_note = " Some content may now reside in a sibling segment."
        lines.append(
            f"> \u26a0\ufe0f **Re-segmentation notice:** Segment `{seg_id}` was re-segmented."
            f"{sibling_note}"
            " Consider re-reading this note when the new segment is processed."
        )
    return "\n".join(lines) + "\n\n"


def _insert_notice(text: str, notice: str) -> str:
    """Insert notice after frontmatter but before the first ## heading."""
    fm, body = _split_frontmatter(text)
    heading_match = re.search(r"^##\s", body, re.MULTILINE)
    if heading_match:
        insert_pos = heading_match.start()
        new_body = body[:insert_pos] + notice + body[insert_pos:]
    else:
        new_body = notice + body
    return fm + new_body


# ---------------------------------------------------------------------------
# Xref index helpers
# ---------------------------------------------------------------------------


def _update_xref_entries(
    entries: list,
    archive_map: Dict[str, List[str]],
    seg_file_map: Dict[str, str],
) -> Tuple[list, int]:
    """Update segment path refs in a list of xref entries. Returns (updated, count)."""
    count = 0
    updated = []
    for entry in entries:
        if not isinstance(entry, dict):
            updated.append(entry)
            continue
        entry = dict(entry)
        for key in ("segment_file", "source_file", "file", "segment"):
            val = entry.get(key)
            if not isinstance(val, str):
                continue
            m = _SEG_PATH_RE.search(val)
            if m:
                seg_id = m.group(1)
                if seg_id in archive_map:
                    new_ids = archive_map[seg_id]
                    new_file = seg_file_map.get(new_ids[0], f"segments/{new_ids[0]}.md")
                    entry[key] = val.replace(m.group(0), new_file)
                    count += 1
        updated.append(entry)
    return updated, count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_xrefs(cite_key: str, dry_run: bool = False) -> dict:
    """Update stale segment path references for a re-segmented paper.

    In dry_run mode: scans files read-only and returns a planning dict with
    keys: cite_key, notes_scanned, stale_refs_found, notices_to_add,
    inputs_valid, notes_detail, xref_index_entries_to_update.

    In live mode: updates Citadel section notes and _xref_index.yaml,
    then writes _reseg_xref_update_output.json to the paper directory.

    Raises SystemExit(1) with a stderr message when cite_key is not found
    or _catalog.yaml is missing.
    """
    paper_dir = PAPER_BANK / cite_key
    citadel_dir = CITADEL / "literature" / "papers" / cite_key

    if not paper_dir.exists():
        print(
            f"ERROR: Paper directory not found for cite_key '{cite_key}': {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    catalog_path = paper_dir / "_catalog.yaml"
    xref_path = paper_dir / "_xref_index.yaml"
    plan_path = paper_dir / "_reseg_plan.json"

    catalog = _load_yaml(catalog_path)
    if catalog is None:
        print(
            f"ERROR: _catalog.yaml not found or unreadable for '{cite_key}': {catalog_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Plan is optional — absent means no archive mappings (nothing to update)
    plan = _load_json(plan_path) or {}
    archive_map = _build_archive_map(plan)
    seg_file_map = _build_seg_file_map(catalog)
    needs_reread = _build_needs_reread_set(catalog)

    # ── Scan section notes ────────────────────────────────────────────────────
    note_scan_results = []
    notes_scanned = 0
    stale_refs_found = 0
    notices_to_add = 0

    for note_name in SECTION_NOTE_NAMES:
        note_path = citadel_dir / note_name
        if not note_path.exists():
            continue
        notes_scanned += 1
        try:
            text = note_path.read_text(encoding="utf-8")
        except Exception:
            continue

        stale = _find_stale_refs(text, archive_map)
        ref_count = len(stale)
        stale_refs_found += ref_count

        affected_needing_notice = [
            seg_id for seg_id, _ in stale if seg_id in needs_reread
        ]
        add_notice = bool(affected_needing_notice) and not _has_notice(text)
        if add_notice:
            notices_to_add += 1

        if ref_count > 0 or add_notice:
            note_scan_results.append(
                {
                    "note": note_name,
                    "stale_refs": [
                        {"old_seg_id": old, "new_seg_ids": new}
                        for old, new in stale
                    ],
                    "notice_to_add": add_notice,
                }
            )

    # ── Scan xref index (dry-run count only) ─────────────────────────────────
    xref_data = _load_yaml(xref_path) or {}
    xref_entries_to_update = 0
    for section in ("equations", "theorems", "citations", "figures"):
        entries = xref_data.get(section) or []
        _, cnt = _update_xref_entries(entries, archive_map, seg_file_map)
        xref_entries_to_update += cnt

    if dry_run:
        return {
            "cite_key": cite_key,
            "notes_scanned": notes_scanned,
            "stale_refs_found": stale_refs_found,
            "notices_to_add": notices_to_add,
            "inputs_valid": True,
            "notes_detail": note_scan_results,
            "xref_index_entries_to_update": xref_entries_to_update,
        }

    # ── Live mode ─────────────────────────────────────────────────────────────
    notes_with_stale = 0
    refs_updated = 0
    notices_added = 0
    xref_index_entries_updated = 0

    for note_name in SECTION_NOTE_NAMES:
        note_path = citadel_dir / note_name
        if not note_path.exists():
            continue
        try:
            text = note_path.read_text(encoding="utf-8")
        except Exception:
            continue

        stale = _find_stale_refs(text, archive_map)
        if stale:
            notes_with_stale += 1
            updated_text, cnt = _replace_stale_refs(text, archive_map, seg_file_map)
            refs_updated += cnt
        else:
            updated_text = text

        affected_needing_notice = [
            seg_id for seg_id, _ in stale if seg_id in needs_reread
        ]
        if affected_needing_notice and not _has_notice(updated_text):
            new_ids_map = {seg_id: new_ids for seg_id, new_ids in stale}
            notice = _build_notice(affected_needing_notice, new_ids_map)
            updated_text = _insert_notice(updated_text, notice)
            notices_added += 1

        if updated_text != text:
            note_path.write_text(updated_text, encoding="utf-8")

    # ── Update xref index ─────────────────────────────────────────────────────
    xref_data = _load_yaml(xref_path) or {}
    xref_changed = False
    for section in ("equations", "theorems", "citations", "figures"):
        entries = xref_data.get(section) or []
        updated_entries, cnt = _update_xref_entries(entries, archive_map, seg_file_map)
        if cnt > 0:
            xref_data[section] = updated_entries
            xref_index_entries_updated += cnt
            xref_changed = True

    if xref_changed:
        tmp = xref_path.with_suffix(".yaml.tmp")
        with open(tmp, "w") as fh:
            yaml.dump(
                xref_data, fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        tmp.replace(xref_path)

    # ── Write execution summary ───────────────────────────────────────────────
    summary = {
        "cite_key": cite_key,
        "notes_scanned": notes_scanned,
        "notes_with_stale_refs": notes_with_stale,
        "refs_updated": refs_updated,
        "needs_re-read_notices_added": notices_added,
        "xref_index_entries_updated": xref_index_entries_updated,
    }

    output_path = paper_dir / "_reseg_xref_update_output.json"
    with open(output_path, "w") as fh:
        json.dump(summary, fh, indent=2)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Update stale segment path references in Citadel section notes "
            "and _xref_index.yaml after re-segmentation."
        )
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and plan only; do not write any files.",
    )
    args = parser.parse_args()

    result = update_xrefs(args.cite_key, dry_run=args.dry_run)

    if args.dry_run:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
