"""
level3_meta_analysis.py — Level 3 cross-paper meta-analysis scaffold.

Standalone CLI: scans paper-bank _feedback.yaml files, aggregates counts
by section/verdict/failure-mode, enforces a >= N paper threshold gate,
and produces a JSON report with triggered/not_triggered result and
proposed_updates. Proposal-only output; never mutates source files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "1.0.0"
DEFAULT_THRESHOLD = 5
DEFAULT_OUTPUT = "/tmp/m12_level3_report.json"

# Score at-or-below this value counts as a failure for that dimension.
_SCORE_FAIL_THRESHOLD = 3

# Dimension → section tag mapping (mirrors level2_session_learning).
_DIM_TO_SECTION: dict[str, str] = {
    "faithfulness": "R-FAITHFULNESS",
    "coverage": "R-APPENDIX",
    "usefulness": "R-SYNTHESIS",
}


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------


def _load_feedback_yaml(path: Path) -> dict[str, Any] | None:
    """Load a _feedback.yaml file; return None on missing or parse error."""
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _scan_paper_bank(paper_bank_root: Path) -> list[dict[str, Any]]:
    """
    Walk paper-bank directories and collect parsed _feedback.yaml records.

    Directories without _feedback.yaml or with malformed files are skipped
    without aborting.
    """
    records: list[dict[str, Any]] = []
    if not paper_bank_root.is_dir():
        return records

    for entry in sorted(paper_bank_root.iterdir()):
        if not entry.is_dir():
            continue
        feedback_path = entry / "_feedback.yaml"
        data = _load_feedback_yaml(feedback_path)
        if data is None:
            continue
        # Attach cite_key from directory name if absent in data.
        if "cite_key" not in data:
            data = dict(data)
            data["cite_key"] = entry.name
        records.append(data)

    return records


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate counts from per-paper feedback records.

    Returns:
      by_verdict:       {verdict_str: count}
      by_section:       {section_tag: count}  (de-duplicated per paper)
      by_failure_mode:  {dimension: count}    (papers with score <= threshold)
    """
    by_verdict: dict[str, int] = {}
    by_section: dict[str, int] = {}
    by_failure_mode: dict[str, int] = {}

    for rec in records:
        # Verdict counts.
        verdict = str(rec.get("last_verdict", "unknown"))
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1

        # Dimension / failure-mode counts (from last_feedback scores).
        last_feedback = rec.get("last_feedback") or {}
        if isinstance(last_feedback, dict):
            for dim in ("faithfulness", "coverage", "usefulness"):
                score = last_feedback.get(dim)
                if isinstance(score, (int, float)) and score <= _SCORE_FAIL_THRESHOLD:
                    by_failure_mode[dim] = by_failure_mode.get(dim, 0) + 1

        # Section counts (from level2_proposals, de-duplicated per paper).
        proposals = rec.get("level2_proposals") or []
        if isinstance(proposals, list):
            seen_sections: set[str] = set()
            for prop in proposals:
                if not isinstance(prop, dict):
                    continue
                section_tag = prop.get("section_tag", "")
                if section_tag and section_tag not in seen_sections:
                    by_section[section_tag] = by_section.get(section_tag, 0) + 1
                    seen_sections.add(section_tag)

    return {
        "by_verdict": by_verdict,
        "by_section": by_section,
        "by_failure_mode": by_failure_mode,
    }


# ---------------------------------------------------------------------------
# Proposal generation (triggered path only)
# ---------------------------------------------------------------------------


def _build_proposed_updates(
    aggregate_stats: dict[str, Any],
    paper_count: int,
) -> list[dict[str, Any]]:
    """Build cross-paper rule proposals from aggregate stats."""
    proposals: list[dict[str, Any]] = []

    by_failure_mode: dict[str, int] = aggregate_stats.get("by_failure_mode", {})

    for dim, count in by_failure_mode.items():
        rate = count / paper_count if paper_count else 0.0
        section_tag = _DIM_TO_SECTION.get(dim, "R-GENERAL")
        proposals.append(
            {
                "proposal_id": f"l3-meta-{dim}",
                "section_tag": section_tag,
                "dimension": dim,
                "affected_paper_count": count,
                "affected_rate": round(rate, 3),
                "proposal": (
                    f"Cross-paper pattern: {count}/{paper_count} papers have low "
                    f"{dim} scores. Strengthen {section_tag} guidance to address "
                    f"systematic {dim} failures."
                ),
                "source": "level3-meta-analysis",
            }
        )

    return proposals


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------


def run_meta_analysis(
    paper_bank_root: str | Path,
    threshold: int = DEFAULT_THRESHOLD,
    output_path: str | Path = DEFAULT_OUTPUT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Run Level 3 meta-analysis over the paper bank.

    Always writes the report to *output_path* (even in dry-run mode).
    Returns the report dict.
    """
    paper_bank_root = Path(paper_bank_root)
    output_path = Path(output_path)

    records = _scan_paper_bank(paper_bank_root)
    paper_count = len(records)
    gate_triggered = paper_count >= threshold

    aggregate_stats = _aggregate(records)

    proposed_updates = (
        _build_proposed_updates(aggregate_stats, paper_count)
        if gate_triggered
        else []
    )

    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "paper_count": paper_count,
        "threshold": threshold,
        "triggered": gate_triggered,
        "not_triggered": not gate_triggered,
        "aggregate_stats": aggregate_stats,
        "proposed_updates": proposed_updates,
        "dry_run": dry_run,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if dry_run:
        print(json.dumps(report, indent=2))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Level 3 cross-paper meta-analysis for paper-reader M12.",
    )
    p.add_argument(
        "--paper-bank-root",
        default=os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank")),
        help="Root directory of the paper bank.",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"Minimum paper count to trigger proposed updates (default: {DEFAULT_THRESHOLD}).",
    )
    p.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON report path (default: {DEFAULT_OUTPUT}).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print report to stdout and write to --output; never mutates source files.",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    run_meta_analysis(
        paper_bank_root=args.paper_bank_root,
        threshold=args.threshold,
        output_path=args.output,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
