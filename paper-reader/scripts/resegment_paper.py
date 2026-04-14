#!/usr/bin/env python3
"""resegment_paper.py — Re-segmentation Driver for Milestone 8.

Orchestrates all six M8 re-segmentation steps in order, enforces the M7
prerequisite gate (comprehension_status: empirical_complete), and produces
a _reseg_log.md audit trail.

Steps:
  R.1  Trigger scan      reseg_trigger_scanner.scan_reseg_triggers
  R.2  Split execution   reseg_split.execute_splits           [if splits]
  R.3  Merge execution   reseg_merge.execute_merges           [if merges]
  R.4  Rebalance exec    reseg_rebalance.execute_rebalances   [if rebalances]
  R.5  Catalog lineage   reseg_catalog_update.update_catalog_lineage
  R.6  XRef update       reseg_xref_updater.update_xrefs

Usage:
    python3 resegment_paper.py --cite-key <cite_key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ── M8 module imports ────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from reseg_trigger_scanner import scan_reseg_triggers  # noqa: E402
from reseg_split import execute_splits                  # noqa: E402
from reseg_merge import execute_merges                  # noqa: E402
from reseg_rebalance import execute_rebalances          # noqa: E402
from reseg_catalog_update import update_catalog_lineage # noqa: E402
from reseg_xref_updater import update_xrefs             # noqa: E402

PAPER_BANK = Path.home() / "Documents" / "paper-bank"
RESEG_LOG_FILENAME = "_reseg_log.md"


# ── Prerequisite gate ────────────────────────────────────────────────────────

def _check_prerequisite(cite_key: str) -> tuple[bool, str]:
    """Return (ok, reason) after checking comprehension_status: empirical_complete."""
    catalog_path = PAPER_BANK / cite_key / "_catalog.yaml"
    if not catalog_path.exists():
        return False, f"No _catalog.yaml found for '{cite_key}'"
    try:
        with open(catalog_path) as fh:
            catalog = yaml.safe_load(fh)
    except Exception as exc:
        return False, f"Failed to parse _catalog.yaml: {exc}"
    if not isinstance(catalog, dict):
        return False, "_catalog.yaml is not a YAML mapping"
    status = catalog.get("comprehension_status", "")
    if status != "empirical_complete":
        return False, (
            f"comprehension_status is '{status}', expected 'empirical_complete'"
        )
    return True, "ok"


# ── Log writer ───────────────────────────────────────────────────────────────

def _write_reseg_log(cite_key: str, plan: dict) -> Path:
    """Write RE-SEGMENTATION LOG to <paper_dir>/_reseg_log.md and return the path."""
    paper_dir = PAPER_BANK / cite_key
    log_path = paper_dir / RESEG_LOG_FILENAME

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    splits = plan.get("splits", [])
    merges = plan.get("merges", [])
    rebalances = plan.get("rebalances", [])
    no_change = plan.get("no_change", [])
    is_noop = not splits and not merges and not rebalances

    lines: list[str] = [
        "RE-SEGMENTATION LOG — Pass 1",
        "=========================",
        f"Paper: {cite_key}",
        f"Generated: {now_iso}",
        "",
    ]

    if is_noop:
        segments_scanned = plan.get("total_segments_scanned", 0)
        lines += [
            "no triggers detected",
            "",
            f"Segments scanned: {segments_scanned}",
            "All segments are within size bounds; no splits, merges, or rebalances required.",
            "",
        ]
    else:
        total_changes = len(splits) + len(merges) + len(rebalances)
        lines.append(f"Changes applied ({total_changes} total):")
        idx = 1
        for s in splits:
            seg_id = s.get("segment_id", "?")
            children = s.get("proposed_children", [f"{seg_id}_part_a", f"{seg_id}_part_b"])
            triggers = ", ".join(s.get("trigger_codes", []))
            benefit = s.get("justification", "")
            child_a = children[0] if len(children) > 0 else f"{seg_id}_part_a"
            child_b = children[1] if len(children) > 1 else f"{seg_id}_part_b"
            lines += [
                f"  [{idx}] Split {seg_id} → {child_a} + {child_b}",
                f"      Trigger: {triggers}",
                f"      Benefit: {benefit}",
                "",
            ]
            idx += 1
        for m in merges:
            segs = m.get("source_segments", [])
            proposed = m.get("proposed_segment_id", "merged")
            triggers = ", ".join(m.get("trigger_codes", []))
            benefit = m.get("justification", "")
            lines += [
                f"  [{idx}] Merge {' + '.join(segs)} → {proposed}",
                f"      Trigger: {triggers}",
                f"      Benefit: {benefit}",
                "",
            ]
            idx += 1
        for r in rebalances:
            from_seg = r.get("from_segment", "?")
            to_seg = r.get("to_segment", "?")
            triggers = ", ".join(r.get("trigger_codes", []))
            benefit = r.get("justification", "")
            lines += [
                f"  [{idx}] Rebalance {from_seg} → {to_seg}",
                f"      Trigger: {triggers}",
                f"      Benefit: {benefit}",
                "",
            ]
            idx += 1

        if no_change:
            lines.append(
                "Segments with no flags (no changes): " + ", ".join(no_change)
            )
            lines.append("")

        notes_updated = len(splits) + len(merges) + len(rebalances)
        lines.append(
            f"Cost: {total_changes} new segment files + catalog update "
            f"+ {notes_updated} per-section notes updated."
        )

    log_path.write_text("\n".join(lines) + "\n")
    return log_path


# ── Main driver ──────────────────────────────────────────────────────────────

def run_resegment(cite_key: str, dry_run: bool = False) -> dict:
    """Orchestrate M8 re-segmentation or emit a dry-run plan.

    Returns a SubagentOutput-compatible dict.
    Exits 1 with a stderr message on error.
    """
    paper_dir = PAPER_BANK / cite_key

    # Validate cite_key resolves to a real paper directory.
    if not paper_dir.exists():
        print(
            f"ERROR: Paper directory not found for cite_key '{cite_key}': {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Prerequisite check ───────────────────────────────────────────────────
    prereq_ok, prereq_reason = _check_prerequisite(cite_key)

    if not dry_run and not prereq_ok:
        print(
            f"ERROR: Prerequisite not met for '{cite_key}': {prereq_reason}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Dry-run path — Step R.1 only, no file writes ─────────────────────────
    if dry_run:
        scan_summary = scan_reseg_triggers(cite_key, dry_run=True)
        splits_n = scan_summary.get("splits_proposed", 0)
        merges_n = scan_summary.get("merges_proposed", 0)
        rebalances_n = scan_summary.get("rebalances_proposed", 0)
        is_noop = splits_n == 0 and merges_n == 0 and rebalances_n == 0

        steps_planned: list[str] = []
        if splits_n:
            steps_planned.append("R.2-splits")
        if merges_n:
            steps_planned.append("R.3-merges")
        if rebalances_n:
            steps_planned.append("R.4-rebalances")
        steps_planned.extend(["R.5-catalog-lineage", "R.6-xref-update"])

        return {
            "cite_key": cite_key,
            "steps_planned": steps_planned,
            "no_op_path": is_noop,
            "prerequisite_check": prereq_ok,
            "inputs_valid": scan_summary.get("inputs_valid", True),
            "splits_proposed": splits_n,
            "merges_proposed": merges_n,
            "rebalances_proposed": rebalances_n,
        }

    # ── Live run ─────────────────────────────────────────────────────────────
    # Step R.1 — Trigger scan (writes _reseg_plan.json)
    plan = scan_reseg_triggers(cite_key, dry_run=False)

    splits = plan.get("splits", [])
    merges = plan.get("merges", [])
    rebalances = plan.get("rebalances", [])
    is_noop = not splits and not merges and not rebalances

    files_written: list[str] = []

    if is_noop:
        # No-op path: write log, run R.5 for catalog bump, skip R.2–R.4 and R.6.
        log_path = _write_reseg_log(cite_key, plan)
        files_written.append(str(log_path))
        update_catalog_lineage(cite_key, dry_run=False)
        return {
            "cite_key": cite_key,
            "no_op_path": True,
            "prerequisite_check": prereq_ok,
            "steps_executed": ["R.1-trigger-scan", "R.5-catalog-lineage"],
            "files_written": files_written,
            "status": "completed",
        }

    # Steps R.2–R.4 — conditionally execute
    steps_executed = ["R.1-trigger-scan"]
    if splits:
        execute_splits(cite_key, dry_run=False)
        steps_executed.append("R.2-splits")
    if merges:
        execute_merges(cite_key, dry_run=False)
        steps_executed.append("R.3-merges")
    if rebalances:
        execute_rebalances(cite_key, dry_run=False)
        steps_executed.append("R.4-rebalances")

    # Step R.5 — Catalog lineage (always runs)
    update_catalog_lineage(cite_key, dry_run=False)
    steps_executed.append("R.5-catalog-lineage")

    # Step R.6 — Cross-reference update (always runs)
    update_xrefs(cite_key, dry_run=False)
    steps_executed.append("R.6-xref-update")

    # Write audit log
    log_path = _write_reseg_log(cite_key, plan)
    files_written.append(str(log_path))

    return {
        "cite_key": cite_key,
        "no_op_path": False,
        "prerequisite_check": prereq_ok,
        "steps_executed": steps_executed,
        "files_written": files_written,
        "status": "completed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orchestrate M8 re-segmentation steps for a paper."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan triggers only; emit plan JSON without executing any changes.",
    )
    args = parser.parse_args()

    result = run_resegment(args.cite_key, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
