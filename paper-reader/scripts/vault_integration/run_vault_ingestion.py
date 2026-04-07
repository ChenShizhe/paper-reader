#!/usr/bin/env python3
"""Orchestrate staged M10 vault ingestion requests.

This module wires the following knowledge-maester scripts:
- preflight_maester.py
- ingest_paper.py
- polish_note.py
- check_graph.py
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got: {value!r}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _build_script_paths() -> dict[str, Path]:
    base = _repo_root() / "skills" / "knowledge-maester" / "scripts"
    return {
        "preflight": base / "preflight_maester.py",
        "ingest": base / "ingest_paper.py",
        "polish": base / "polish_note.py",
        "graph": base / "check_graph.py",
    }


def _request_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    note_type = str(item.get("note_type") or "")
    action = str(item.get("action") or "")
    note_index = NOTE_TYPE_ORDER.index(note_type) if note_type in NOTE_TYPE_ORDER else len(NOTE_TYPE_ORDER)
    action_index = ACTION_ORDER.index(action) if action in ACTION_ORDER else len(ACTION_ORDER)
    target = str(item.get("target_path") or "")
    source = str(item.get("source_path") or "")
    return (note_index, action_index, target, source)


def _command_to_str(command: list[str]) -> str:
    return " ".join(shlex.quote(piece) for piece in command)


def _run_command(command: list[str], plan_only: bool) -> dict[str, Any]:
    output: dict[str, Any] = {
        "command": _command_to_str(command),
        "plan_only": bool(plan_only),
    }
    if plan_only:
        output.update({"status": "planned", "returncode": None, "stdout": "", "stderr": ""})
        return output

    completed = subprocess.run(command, capture_output=True, text=True)
    output.update(
        {
            "status": "succeeded" if completed.returncode == 0 else "failed",
            "returncode": int(completed.returncode),
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    )
    return output


def _build_ingest_command(
    python_bin: str,
    script_path: Path,
    request: dict[str, Any],
    cite_key: str,
    vault_path: Path,
    paper_bank_path: Path,
    dry_run: bool,
) -> list[str]:
    note_type = str(request.get("note_type") or "paper")
    source_path = str(request.get("source_path") or "")

    command = [
        python_bin,
        str(script_path),
        "--note",
        source_path,
        "--type",
        note_type,
        "--vault-path",
        str(vault_path),
        "--paper-bank-path",
        str(paper_bank_path),
    ]

    if note_type == "paper":
        command.extend(["--cite-key", cite_key])

    if dry_run:
        command.append("--dry-run")

    return command


def _build_polish_command(
    python_bin: str,
    script_path: Path,
    request: dict[str, Any],
    vault_path: Path,
) -> list[str]:
    return [
        python_bin,
        str(script_path),
        "--note-path",
        str(request.get("target_path") or ""),
        "--update",
        str(request.get("source_path") or ""),
        "--vault-path",
        str(vault_path),
    ]


def run_vault_ingestion(
    work_dir: str | Path,
    vault_path: str | Path,
    requests_path: str | Path,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Execute or simulate ingestion from a staged _vault-write-requests.json."""

    work_dir = Path(work_dir).expanduser().resolve()
    vault_path = Path(vault_path).expanduser().resolve()
    requests_path = Path(requests_path).expanduser().resolve()
    paper_bank_path = work_dir.parent
    report_path = work_dir / "_vault_ingestion_report.json"
    graph_report_path = work_dir / "_vault_graph_check.json"

    scripts = _build_script_paths()
    python_bin = sys.executable or "python3"

    report: dict[str, Any] = {
        "schemaVersion": "1.0",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "cite_key": work_dir.name,
        "dry_run": bool(dry_run),
        "work_dir": str(work_dir),
        "vault_path": str(vault_path),
        "requests_path": str(requests_path),
        "preflight": {},
        "actions": [],
        "graph_check": {},
    }

    input_errors: list[str] = []
    if not work_dir.exists():
        input_errors.append(f"work_dir_not_found:{work_dir}")
    if not vault_path.exists():
        input_errors.append(f"vault_path_not_found:{vault_path}")
    if not requests_path.exists():
        input_errors.append(f"requests_not_found:{requests_path}")

    for name, script in scripts.items():
        if not script.exists():
            input_errors.append(f"script_not_found:{name}:{script}")

    if input_errors:
        report["preflight"] = {
            "status": "failed",
            "details": input_errors,
        }
        report["graph_check"] = {
            "enabled": False,
            "status": "skipped",
            "reason": "input validation failed",
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    request_payload = json.loads(requests_path.read_text(encoding="utf-8"))
    report["cite_key"] = str(request_payload.get("cite_key") or work_dir.name)

    raw_requests = request_payload.get("requests", [])
    normalized_requests = [item for item in raw_requests if isinstance(item, dict)]
    sorted_requests = sorted(normalized_requests, key=_request_sort_key)

    preflight_cmd = [
        python_bin,
        str(scripts["preflight"]),
        "--vault-path",
        str(vault_path),
        "--paper-bank-path",
        str(paper_bank_path),
    ]
    preflight_result = _run_command(preflight_cmd, plan_only=False)
    report["preflight"] = preflight_result

    action_results: list[dict[str, Any]] = []
    for index, request in enumerate(sorted_requests, start=1):
        action = str(request.get("action") or "")
        note_type = str(request.get("note_type") or "")

        if action == "create":
            command = _build_ingest_command(
                python_bin=python_bin,
                script_path=scripts["ingest"],
                request=request,
                cite_key=report["cite_key"],
                vault_path=vault_path,
                paper_bank_path=paper_bank_path,
                dry_run=dry_run,
            )
            command_result = _run_command(command, plan_only=False)
            handler = "ingest_paper.py"
        else:
            command = _build_polish_command(
                python_bin=python_bin,
                script_path=scripts["polish"],
                request=request,
                vault_path=vault_path,
            )
            # polish_note.py does not support dry-run, so plan only in dry-run mode.
            command_result = _run_command(command, plan_only=dry_run)
            handler = "polish_note.py"

        action_results.append(
            {
                "index": index,
                "action": action,
                "note_type": note_type,
                "target_path": str(request.get("target_path") or ""),
                "source_path": str(request.get("source_path") or ""),
                "handler": handler,
                **command_result,
            }
        )

    report["actions"] = action_results

    graph_enabled = len(sorted_requests) >= 5
    if graph_enabled:
        graph_cmd = [
            python_bin,
            str(scripts["graph"]),
            "--vault-path",
            str(vault_path),
            "--paper-bank-path",
            str(paper_bank_path),
            "--output",
            str(graph_report_path),
        ]
        # Graph checks can return non-zero on warnings/errors; keep dry-run non-mutating and non-failing.
        graph_result = _run_command(graph_cmd, plan_only=dry_run)
        report["graph_check"] = {
            "enabled": True,
            **graph_result,
            "report_path": str(graph_report_path),
        }
    else:
        report["graph_check"] = {
            "enabled": False,
            "status": "skipped",
            "reason": "action_count_below_threshold",
            "threshold": 5,
            "action_count": len(sorted_requests),
        }

    failed_actions = [entry for entry in action_results if entry.get("status") == "failed"]
    report["summary"] = {
        "total_actions": len(action_results),
        "failed_actions": len(failed_actions),
        "planned_actions": sum(1 for entry in action_results if entry.get("status") == "planned"),
        "preflight_status": report["preflight"].get("status"),
        "graph_check_status": report["graph_check"].get("status"),
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run vault ingestion orchestration for staged write requests.")
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/ work directory.")
    parser.add_argument("--vault-path", required=True, help="Path to Citadel vault root.")
    parser.add_argument("--requests", required=True, help="Path to _vault-write-requests.json.")
    parser.add_argument(
        "--dry-run",
        nargs="?",
        const=True,
        default=True,
        type=_parse_bool,
        metavar="{true,false}",
        help="When true (default), simulate write actions safely and still emit _vault_ingestion_report.json.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    report = run_vault_ingestion(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        requests_path=args.requests,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(report, indent=2))

    if report.get("preflight", {}).get("status") == "failed":
        sys.exit(1)

    if (not report.get("dry_run")) and report.get("summary", {}).get("failed_actions", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
