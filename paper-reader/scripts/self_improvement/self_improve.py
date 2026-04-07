"""
self_improve.py — M12 self-improvement scaffold.

CLI entry-point and importable run_self_improve() function.
Parses feedback, builds a normalized execution summary with
Level 1/2/3 candidate placeholders, and (in live mode) writes
_self_improve_report.json to the work directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure sibling modules are importable when this script is run directly.
_SELF_IMPROVE_DIR = str(Path(__file__).parent)
if _SELF_IMPROVE_DIR not in sys.path:
    sys.path.insert(0, _SELF_IMPROVE_DIR)

import level1_faithfulness  # noqa: E402
import level2_session_learning  # noqa: E402
import write_feedback_logs  # noqa: E402

SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Feedback normalization
# ---------------------------------------------------------------------------

def _normalize_feedback(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized feedback record with default fill-ins."""
    normalized: dict[str, Any] = {
        "faithfulness": int(raw.get("faithfulness", 0)),
        "coverage": int(raw.get("coverage", 0)),
        "usefulness": int(raw.get("usefulness", 0)),
        "verdict": str(raw.get("verdict", "unknown")),
    }
    if "revision_request" in raw:
        normalized["revision_request"] = str(raw["revision_request"])
    return normalized


# ---------------------------------------------------------------------------
# Candidate builders (placeholders — later tasks populate these)
# ---------------------------------------------------------------------------


def _build_level2_candidates(
    cite_key: str,
    feedback: dict[str, Any],
    work_dir: Path,
    skill_root: Path,
) -> list[dict[str, Any]]:
    """Level 2: session-end feedback → section-targeted rule proposals."""
    return level2_session_learning.generate_candidates(cite_key, feedback)


def _build_level3_candidates(
    cite_key: str,
    feedback: dict[str, Any],
    work_dir: Path,
    skill_root: Path,
) -> list[dict[str, Any]]:
    """Level 3: structural / schema improvements (placeholders)."""
    return []


# ---------------------------------------------------------------------------
# Core run function
# ---------------------------------------------------------------------------

def run_self_improve(
    cite_key: str,
    work_dir: str | Path,
    skill_root: str | Path,
    feedback_raw: dict[str, Any],
    faithfulness_report_path: str | Path | None = None,
    quiz_report_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Execute self-improvement analysis for *cite_key*.

    Parameters
    ----------
    cite_key:     Paper identifier.
    work_dir:     Path to the paper's work directory (paper-bank entry).
    skill_root:   Root of the paper-reader skill tree.
    feedback_raw: Raw feedback dict (faithfulness, coverage, usefulness, verdict).
    dry_run:      When True, no files are written; stdout receives the summary JSON.

    Returns
    -------
    dict: Execution summary (always returned; also written to disk in live mode).
    """
    work_dir = Path(work_dir)
    skill_root = Path(skill_root)
    feedback = _normalize_feedback(feedback_raw)
    quiz_report_path_resolved = Path(quiz_report_path) if quiz_report_path else None

    level1, faithfulness_flags_processed = level1_faithfulness.generate_candidates(
        faithfulness_report_path
    )
    level2 = _build_level2_candidates(cite_key, feedback, work_dir, skill_root)
    level3 = _build_level3_candidates(cite_key, feedback, work_dir, skill_root)

    feedback_log_preview = write_feedback_logs.write_feedback_logs(
        cite_key=cite_key,
        work_dir=work_dir,
        feedback=feedback,
        quiz_report_path=quiz_report_path_resolved,
        dry_run=True,
    )

    if dry_run:
        dry_summary: dict[str, Any] = {
            "cite_key": cite_key,
            "level1_candidates": level1,
            "level2_candidates": level2,
            "level2_triggered": feedback.get("verdict") == level2_session_learning.VERDICT_TRIGGER,
            "level3_candidates": level3,
            "faithfulness_flags_processed": faithfulness_flags_processed,
            "would_write": str(work_dir / "_self_improve_report.json"),
            "feedback_path": feedback_log_preview["feedback_path"],
            "quiz_failures_path": feedback_log_preview["quiz_failures_path"],
            "feedback_record_count": feedback_log_preview["feedback_record_count"],
            "quiz_failure_count": feedback_log_preview["quiz_failure_count"],
        }
        print(json.dumps(dry_summary, indent=2))
        return dry_summary

    # Live mode — append proposals then write report
    proposals_path = skill_root / "reading-constitution-proposals.md"
    level1_faithfulness.append_to_proposals(level1, proposals_path, cite_key)
    level2_session_learning.append_to_proposals(
        level2,
        proposals_path,
        cite_key,
        revision_request=feedback.get("revision_request", ""),
    )
    level2_session_learning.update_feedback_yaml(level2, work_dir, cite_key, feedback)
    write_feedback_logs.write_feedback_logs(
        cite_key=cite_key,
        work_dir=work_dir,
        feedback=feedback,
        quiz_report_path=quiz_report_path_resolved,
        dry_run=False,
    )

    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "cite_key": cite_key,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feedback_record": feedback,
        "proposal_counts": {
            "level1": len(level1),
            "level2": len(level2),
            "level3": len(level3),
        },
        "faithfulness_flags_processed": faithfulness_flags_processed,
        "dry_run": dry_run,
    }

    out_path = work_dir / "_self_improve_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="M12 self-improvement analysis for a single paper.",
    )
    p.add_argument("--cite-key", required=True, help="Paper cite key.")
    p.add_argument(
        "--work-dir",
        required=True,
        help="Path to the paper's work directory (paper-bank entry).",
    )
    p.add_argument(
        "--skill-root",
        required=True,
        help="Root of the paper-reader skill tree.",
    )
    p.add_argument(
        "--feedback",
        required=True,
        help='JSON string with feedback keys: faithfulness, coverage, usefulness, verdict.',
    )
    p.add_argument(
        "--faithfulness-report",
        default=None,
        help="Path to _faithfulness_report.json (optional); enables Level 1 candidates.",
    )
    p.add_argument(
        "--quiz-report",
        default=None,
        help="Path to _quiz_coverage_report.json (optional); feeds _quiz_failures.yaml.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print execution summary to stdout; do not write any files.",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        feedback_raw: dict[str, Any] = json.loads(args.feedback)
    except json.JSONDecodeError as exc:
        parser.error(f"--feedback is not valid JSON: {exc}")

    run_self_improve(
        cite_key=args.cite_key,
        work_dir=args.work_dir,
        skill_root=args.skill_root,
        feedback_raw=feedback_raw,
        faithfulness_report_path=args.faithfulness_report,
        quiz_report_path=args.quiz_report,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
