#!/usr/bin/env python3
"""Emit the discovery preflight report required by the paper-discovery plan."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run discovery preflight checks")
    parser.add_argument("--output", default="preflight_report.json", help="Output JSON path")
    return parser.parse_args()


def check_tool(tool: str, required: bool, fallback: str | None = None) -> dict[str, object]:
    path = shutil.which(tool)
    result: dict[str, object] = {
        "tool": tool,
        "status": "found" if path else "missing",
        "required": required,
    }
    if path:
        result["path"] = path
    if fallback:
        result["fallback"] = fallback
    return result


def check_runtime_script(name: str, path: Path, required: bool, fallback: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "tool": name,
        "status": "found" if path.exists() else "missing",
        "required": required,
    }
    if path.exists():
        result["path"] = str(path)
    if fallback:
        result["fallback"] = fallback
    return result


def check_path(name: str, path: Path, required: bool, fallback: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "tool": name,
        "status": "found" if path.exists() else "missing",
        "required": required,
    }
    if path.exists():
        result["path"] = str(path)
    if fallback:
        result["fallback"] = fallback
    return result


def build_report() -> dict[str, object]:
    script_root = Path(__file__).resolve().parent
    zotero_mcp_root = Path(os.environ.get("ZOTERO_MCP_ROOT", str(Path.home() / "Documents" / "MCPs" / "zotero-mcp")))
    checks = [
        {
            "tool": "python3",
            "status": "found",
            "required": True,
            "path": sys.executable,
        },
        check_runtime_script(
            "search_arxiv.py",
            script_root / "search_arxiv.py",
            True,
            "Restore the discovery scripts directory before invoking live arXiv search",
        ),
        check_runtime_script(
            "search_zotero.py",
            script_root / "search_zotero.py",
            True,
            "Restore the discovery scripts directory before invoking Zotero search",
        ),
        check_path(
            "zotero-mcp-root",
            zotero_mcp_root,
            True,
            "Install Zotero MCP and set ZOTERO_MCP_ROOT, or install under ~/Documents/MCPs/zotero-mcp",
        ),
        check_tool(
            "zotero-mcp",
            False,
            "Use the bundled venv command in the zotero-mcp .venv/bin/ directory",
        ),
        check_tool("jq", False, "Use Python-based JSON inspection for ad hoc checks"),
    ]
    ready = all(check["status"] == "found" for check in checks if check["required"])

    return {
        "module": "discovery",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": [
            "Paper-discovery uses local scripts for arXiv + Zotero acquisition, then builds and validates a schema-v1 manifest",
            "Zotero checks verify the MCP payload exists at the expected path; credentials/local connectivity are environment dependent",
            "Live OpenAlex, web, and PubMed acquisition may still be handled by upstream wrappers or pre-saved JSON artifacts",
            "Network reachability is environment-dependent and is not actively probed by this preflight",
        ],
        "checks": checks,
        "legal_paths": [
            "manifest-from-saved-artifacts",
            "stdlib-arxiv-search",
            "zotero-library-search",
        ]
        if ready
        else [],
        "blocked_paths": [] if ready else ["manifest-from-saved-artifacts", "stdlib-arxiv-search", "zotero-library-search"],
        "overall": "ready" if ready else "blocked",
    }


def main() -> int:
    args = parse_args()
    report = build_report()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"Preflight written: {output_path}")
    print(f"Overall: {report['overall']}")
    return 0 if report["overall"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
