#!/usr/bin/env python3
"""Re-segmentation Trigger Scanner.

Reads _catalog.yaml for segments annotated with reseg_flags and produces
a structured _reseg_plan.json with split/merge/rebalance buckets.

Usage:
    python3 reseg_trigger_scanner.py --cite-key <cite_key> [--dry-run]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

PAPER_BANK = Path.home() / "Documents" / "paper-bank"

# Flag code prefixes
SPLIT_PREFIXES = {"S1", "S2", "S3", "S4", "S5"}
MERGE_PREFIXES = {"M1", "M2", "M3"}
REBALANCE_PREFIXES = {"R1", "R2"}


def _parse_flag(flag: Any) -> Tuple[str, str]:
    """Return (code, justification) from a flag entry (str or dict)."""
    if isinstance(flag, dict):
        code = flag.get("code") or flag.get("type") or ""
        note = flag.get("note") or flag.get("justification") or ""
        return str(code).strip(), str(note).strip()
    s = str(flag).strip()
    if ":" in s:
        code, _, note = s.partition(":")
        return code.strip(), note.strip()
    return s, ""


def _flag_bucket(code: str) -> Optional[str]:
    """Return 'split', 'merge', 'rebalance', or None."""
    prefix = code[:2] if len(code) >= 2 else code
    if prefix in SPLIT_PREFIXES:
        return "split"
    if prefix in MERGE_PREFIXES:
        return "merge"
    if prefix in REBALANCE_PREFIXES:
        return "rebalance"
    return None


def _load_manifest_order(paper_dir: Path) -> List[str]:
    """Return ordered segment IDs from _segment_manifest.json, or []."""
    manifest_path = paper_dir / "_segment_manifest.json"
    if not manifest_path.exists():
        return []
    with open(manifest_path) as f:
        data = json.load(f)
    segs = data.get("segments", [])
    return [s["segment_id"] if isinstance(s, dict) else str(s) for s in segs]


def scan_reseg_triggers(cite_key: str, dry_run: bool = False) -> dict:
    """Scan _catalog.yaml for reseg_flags and return a reseg plan dict.

    In dry_run mode returns a summary dict without writing any files.
    In live mode builds the full plan, writes _reseg_plan.json, and returns it.

    Raises SystemExit(1) with a stderr message when cite_key is not found or
    the catalog cannot be parsed.
    """
    paper_dir = PAPER_BANK / cite_key
    catalog_path = paper_dir / "_catalog.yaml"

    if not paper_dir.exists() or not catalog_path.exists():
        print(
            f"ERROR: No catalog found for cite_key '{cite_key}': {catalog_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f)
    except Exception as exc:
        print(
            f"ERROR: Failed to parse _catalog.yaml for '{cite_key}': {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(catalog, dict):
        print(
            f"ERROR: _catalog.yaml for '{cite_key}' is not a mapping.",
            file=sys.stderr,
        )
        sys.exit(1)

    segment_entries: List[dict] = catalog.get("segments") or []
    manifest_order = _load_manifest_order(paper_dir)

    # Build a position index: segment_id -> int
    if manifest_order:
        seg_index: Dict[str, int] = {sid: i for i, sid in enumerate(manifest_order)}
    else:
        seg_index = {
            s["id"]: i
            for i, s in enumerate(segment_entries)
            if isinstance(s, dict) and s.get("id")
        }

    # ── Collect flagged segments ──────────────────────────────────────────────
    # flagged[seg_id] = {"split": [(code, note), ...], "merge": [...], "rebalance": [...], "file": str}
    flagged: Dict[str, Dict] = {}
    seg_file_map: Dict[str, str] = {}

    for seg in segment_entries:
        if not isinstance(seg, dict):
            continue
        seg_id: str = seg.get("id", "")
        seg_file: str = seg.get("file", "")
        seg_file_map[seg_id] = seg_file

        raw_flags = seg.get("reseg_flags") or []
        if not raw_flags:
            continue

        buckets: Dict[str, List[Tuple[str, str]]] = {
            "split": [], "merge": [], "rebalance": []
        }
        for flag in raw_flags:
            code, note = _parse_flag(flag)
            bucket = _flag_bucket(code)
            if bucket:
                buckets[bucket].append((code, note))

        if any(buckets.values()):
            flagged[seg_id] = {**buckets, "file": seg_file}

    segments_scanned = len(segment_entries)
    flags_found = sum(
        len(v["split"]) + len(v["merge"]) + len(v["rebalance"])
        for v in flagged.values()
    )

    # ── Build splits ──────────────────────────────────────────────────────────
    splits = []
    for seg_id, fdata in flagged.items():
        if not fdata["split"]:
            continue
        codes = [c for c, _ in fdata["split"]]
        justification = "; ".join(n for _, n in fdata["split"] if n) or "Split trigger"
        splits.append({
            "trigger_codes": codes,
            "segment_id": seg_id,
            "source_file": fdata["file"],
            "proposed_children": [f"{seg_id}_part_a", f"{seg_id}_part_b"],
            "justification": justification,
        })

    # ── Build merges (adjacency-validated) ───────────────────────────────────
    merges = []
    merge_ids = sorted(
        [sid for sid, fdata in flagged.items() if fdata["merge"]],
        key=lambda x: seg_index.get(x, float("inf")),
    )
    visited: set = set()
    i = 0
    while i < len(merge_ids):
        seg_a = merge_ids[i]
        if seg_a in visited:
            i += 1
            continue
        idx_a = seg_index.get(seg_a)
        merged = False
        if i + 1 < len(merge_ids):
            seg_b = merge_ids[i + 1]
            idx_b = seg_index.get(seg_b)
            if idx_a is not None and idx_b is not None and abs(idx_b - idx_a) == 1:
                codes_a = [c for c, _ in flagged[seg_a]["merge"]]
                codes_b = [c for c, _ in flagged[seg_b]["merge"]]
                just_a = "; ".join(n for _, n in flagged[seg_a]["merge"] if n)
                just_b = "; ".join(n for _, n in flagged[seg_b]["merge"] if n)
                justification = "; ".join(filter(None, [just_a, just_b])) or "Merge trigger"
                merges.append({
                    "trigger_codes": codes_a + codes_b,
                    "source_segments": [seg_a, seg_b],
                    "proposed_segment_id": f"{seg_a}_merged",
                    "label": f"{seg_a} + {seg_b}",
                    "justification": justification,
                })
                visited.add(seg_a)
                visited.add(seg_b)
                i += 2
                merged = True
        if not merged:
            print(
                f"WARNING: M-trigger on '{seg_a}' skipped: no adjacent M-flagged segment found.",
                file=sys.stderr,
            )
            i += 1

    # ── Build rebalances ──────────────────────────────────────────────────────
    rebalances = []
    rebalance_ids = sorted(
        [sid for sid, fdata in flagged.items() if fdata["rebalance"]],
        key=lambda x: seg_index.get(x, float("inf")),
    )
    for seg_id in rebalance_ids:
        fdata = flagged[seg_id]
        codes = [c for c, _ in fdata["rebalance"]]
        justification = (
            "; ".join(n for _, n in fdata["rebalance"] if n) or "Rebalance trigger"
        )
        idx = seg_index.get(seg_id)
        to_segment = ""
        if idx is not None and manifest_order and idx + 1 < len(manifest_order):
            to_segment = manifest_order[idx + 1]
        rebalances.append({
            "trigger_codes": codes,
            "from_segment": seg_id,
            "to_segment": to_segment,
            "n_paragraphs_to_move": 1,
            "justification": justification,
        })

    # ── no_change ─────────────────────────────────────────────────────────────
    flagged_ids = set(flagged.keys())
    all_ids = [
        s["id"]
        for s in segment_entries
        if isinstance(s, dict) and s.get("id")
    ]
    no_change = [sid for sid in all_ids if sid not in flagged_ids]

    # ── Dry-run output ────────────────────────────────────────────────────────
    if dry_run:
        return {
            "cite_key": cite_key,
            "segments_scanned": segments_scanned,
            "flags_found": flags_found,
            "splits_proposed": len(splits),
            "merges_proposed": len(merges),
            "rebalances_proposed": len(rebalances),
            "inputs_valid": True,
        }

    # ── Live run: write plan ──────────────────────────────────────────────────
    plan = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_segments_scanned": segments_scanned,
        "total_flags_found": flags_found,
        "splits": splits,
        "merges": merges,
        "rebalances": rebalances,
        "no_change": no_change,
    }

    plan_path = paper_dir / "_reseg_plan.json"
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=2)

    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan _catalog.yaml for reseg_flags and produce _reseg_plan.json."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and scan only; do not write any files.",
    )
    args = parser.parse_args()

    result = scan_reseg_triggers(args.cite_key, dry_run=args.dry_run)

    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Plan written to: {PAPER_BANK / args.cite_key / '_reseg_plan.json'}"
        )
        print(
            json.dumps(
                {
                    "cite_key": result["cite_key"],
                    "segments_scanned": result["total_segments_scanned"],
                    "flags_found": result["total_flags_found"],
                    "splits_proposed": len(result["splits"]),
                    "merges_proposed": len(result["merges"]),
                    "rebalances_proposed": len(result["rebalances"]),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
