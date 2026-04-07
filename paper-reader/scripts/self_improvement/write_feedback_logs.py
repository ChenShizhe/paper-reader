"""
write_feedback_logs.py — M12 feedback/quiz log routing to paper-bank.

Writes _feedback.yaml and _quiz_failures.yaml to the paper-bank directory
for a given cite_key (derived from --work-dir).  Supports dry-run mode that
returns a JSON-serialisable preview without touching the filesystem.

Hard boundary: never writes under skills/paper-reader/.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

FEEDBACK_YAML = "_feedback.yaml"
QUIZ_FAILURES_YAML = "_quiz_failures.yaml"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_yaml_safe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_json_safe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_quiz_failures(quiz_report: dict[str, Any]) -> list[str]:
    """Return missing sections from a quiz coverage report."""
    return list(quiz_report.get("missing_sections", []))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_feedback_logs(
    cite_key: str,
    work_dir: Path,
    feedback: dict[str, Any],
    quiz_report_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Write (or preview) _feedback.yaml and _quiz_failures.yaml to *work_dir*.

    Parameters
    ----------
    cite_key:         Paper identifier.
    work_dir:         Paper-bank directory for this cite_key (absolute path).
    feedback:         Normalised feedback dict (faithfulness, coverage,
                      usefulness, verdict, optional revision_request).
    quiz_report_path: Optional path to _quiz_coverage_report.json.
    dry_run:          When True, returns a preview dict without writing files.

    Returns
    -------
    dict with:
      feedback_path        — absolute path of _feedback.yaml
      quiz_failures_path   — absolute path of _quiz_failures.yaml
      feedback_record_count — total entry count after this write
      quiz_failure_count   — total failure-entry count after this write
    """
    feedback_path = work_dir / FEEDBACK_YAML
    quiz_failures_path = work_dir / QUIZ_FAILURES_YAML

    # ---- feedback entries --------------------------------------------------
    existing_feedback = _load_yaml_safe(feedback_path)
    entries: list[dict[str, Any]] = existing_feedback.get("entries", [])

    new_feedback_entry: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "verdict": feedback.get("verdict", "unknown"),
    }
    for dim in ("faithfulness", "coverage", "usefulness"):
        if dim in feedback:
            new_feedback_entry[dim] = feedback[dim]
    if "revision_request" in feedback:
        new_feedback_entry["revision_request"] = feedback["revision_request"]

    entries.append(new_feedback_entry)
    feedback_record_count = len(entries)

    # ---- quiz failure entries -----------------------------------------------
    quiz_report: dict[str, Any] = (
        _load_json_safe(quiz_report_path) if quiz_report_path else {}
    )
    missing_sections = _extract_quiz_failures(quiz_report)

    existing_quiz = _load_yaml_safe(quiz_failures_path)
    failure_entries: list[dict[str, Any]] = existing_quiz.get("entries", [])

    new_quiz_entry: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "missing_sections": missing_sections,
    }
    if "question_count" in quiz_report:
        new_quiz_entry["question_count"] = quiz_report["question_count"]
    failure_entries.append(new_quiz_entry)
    quiz_failure_count = len(failure_entries)

    result: dict[str, Any] = {
        "feedback_path": str(feedback_path),
        "quiz_failures_path": str(quiz_failures_path),
        "feedback_record_count": feedback_record_count,
        "quiz_failure_count": quiz_failure_count,
    }

    if dry_run:
        return result

    # ---- live write: _feedback.yaml ----------------------------------------
    feedback_data: dict[str, Any] = dict(existing_feedback)
    feedback_data["cite_key"] = cite_key
    feedback_data["last_verdict"] = feedback.get("verdict", "unknown")
    feedback_data["verdict"] = feedback.get("verdict", "unknown")
    dim_keys = ("faithfulness", "coverage", "usefulness")
    feedback_data["last_feedback"] = {
        k: feedback[k] for k in dim_keys if k in feedback
    }
    feedback_data["entries"] = entries

    feedback_path.write_text(
        yaml.dump(feedback_data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    # ---- live write: _quiz_failures.yaml ------------------------------------
    quiz_data: dict[str, Any] = dict(existing_quiz)
    quiz_data["cite_key"] = cite_key
    quiz_data["entries"] = failure_entries

    quiz_failures_path.write_text(
        yaml.dump(quiz_data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    return result
