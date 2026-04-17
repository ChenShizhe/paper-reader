#!/usr/bin/env python3
"""Emit the extraction preflight report required by the paper-reader skill.

v1: core tool/path checks (python3, curl, tar, manage_paper_bank.py, etc.)
v2: MinerU availability, Citadel vault accessibility, paper-bank root, translation_ready flag.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run extraction preflight checks (v1 + v2 readiness)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "v1 checks: python3, curl, tar, manage_paper_bank.py, sync_zotero.py,\n"
            "           zotero-mcp-root, paper-bank-root, pymupdf4llm, magic-pdf, bibtexparser\n"
            "v2 checks: mineru (MinerU structured PDF extraction),\n"
            "           citadel vault path accessibility,\n"
            "           paper-bank root availability,\n"
            "           translation_ready summary key\n"
            "\n"
            "Exit codes: 0 = all required checks passed, 1 = one or more required checks failed.\n"
            "Designed for non-interactive shell automation; actionable diagnostics on failure."
        ),
    )
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


def check_python_package(name: str, required: bool, fallback: str | None = None) -> dict[str, object]:
    spec = importlib.util.find_spec(name)
    result: dict[str, object] = {
        "tool": name,
        "status": "found" if spec else "missing",
        "required": required,
    }
    if spec and spec.origin:
        result["path"] = spec.origin
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


def check_or_create_work_root(work_root: Path) -> dict[str, object]:
    """Ensure WORK_ROOT exists for fresh-run environments."""
    existed_before = work_root.exists()
    try:
        os.makedirs(work_root, exist_ok=True)
    except OSError as exc:
        return {
            "tool": "work-root",
            "status": "missing",
            "required": True,
            "fallback": (
                f"Create {work_root} (e.g. `mkdir -p {work_root}`) "
                "so paper-reader can stage extraction artifacts"
            ),
            "error": str(exc),
        }

    return {
        "tool": "work-root",
        "status": "found",
        "required": True,
        "path": str(work_root),
        "detail": "already-present" if existed_before else "created",
    }


def resolve_vault_root() -> Path:
    """Return the canonical Citadel literature vault path (lowercase)."""
    return Path.home() / "Documents" / "citadel" / "literature"


# ---------------------------------------------------------------------------
# v2 check helpers
# ---------------------------------------------------------------------------


def check_pymupdf() -> dict[str, object]:
    """Check PyMuPDF (fitz) importability for PDF reading and metadata extraction."""
    spec = importlib.util.find_spec("fitz")
    found = spec is not None
    result: dict[str, object] = {
        "tool": "pymupdf",
        "status": "found" if found else "missing",
        "required": False,
        "fallback": "install PyMuPDF (pip install pymupdf) to enable PDF reading and page-level extraction",
    }
    if found and spec.origin:
        result["path"] = spec.origin
    return result


def check_mineru() -> dict[str, object]:
    """Check MinerU availability via magic-pdf CLI or mineru Python package.

    MinerU ships a CLI command (magic-pdf) and/or an importable package (mineru).
    Either form is sufficient for structured PDF extraction.
    """
    cli_path = shutil.which("magic-pdf")
    pkg_spec = importlib.util.find_spec("mineru")
    found = bool(cli_path or pkg_spec)
    result: dict[str, object] = {
        "tool": "mineru",
        "status": "found" if found else "missing",
        "required": False,
        "fallback": "install MinerU (pip install mineru) for structured PDF extraction",
    }
    if cli_path:
        result["path"] = cli_path
        result["detail"] = "magic-pdf CLI"
    elif pkg_spec and pkg_spec.origin:
        result["path"] = pkg_spec.origin
        result["detail"] = "mineru Python package"
    return result


def check_citadel_vault(vault_root: Path) -> dict[str, object]:
    """Check Citadel vault path accessibility.

    The citadel vault at ~/Documents/citadel is the target for note/claims output.
    The literature sub-directory is the default write target for paper-reader.
    """
    citadel_root = vault_root.parent  # ~/Documents/citadel
    literature_path = vault_root      # ~/Documents/citadel/literature

    if literature_path.exists():
        status = "found"
        active_path: Path = literature_path
    elif citadel_root.exists():
        status = "found"
        active_path = citadel_root
    else:
        status = "missing"
        active_path = citadel_root

    result: dict[str, object] = {
        "tool": "citadel-vault",
        "status": status,
        "required": False,
        "fallback": (
            "Create ~/Documents/citadel/literature to enable Citadel vault output; "
            "paper-reader can still extract without it"
        ),
    }
    if status == "found":
        result["path"] = str(active_path)
    return result


def build_report() -> dict[str, object]:
    script_root = Path(__file__).resolve().parent
    zotero_mcp_root = Path(os.environ.get("ZOTERO_MCP_ROOT", str(Path.home() / "Documents" / "MCPs" / "zotero-mcp")))
    paper_bank_root = Path(os.environ.get("PAPER_BANK", str(Path.home() / "Documents" / "paper-bank")))
    work_root = Path(os.environ.get("WORK_ROOT", str(Path.home() / ".research-workdir")))
    vault_root = resolve_vault_root()

    # ------------------------------------------------------------------
    # v1 checks (backward-compatible)
    # ------------------------------------------------------------------
    v1_checks: list[dict[str, object]] = [
        {
            "tool": "python3",
            "status": "found",
            "required": True,
            "path": sys.executable,
        },
        check_or_create_work_root(work_root),
        check_tool("curl", True),
        check_tool("tar", True),
        check_runtime_script(
            "manage_paper_bank.py",
            script_root / "manage_paper_bank.py",
            True,
            "Restore paper-reader/scripts to include paper-bank management",
        ),
        check_runtime_script(
            "sync_zotero.py",
            script_root / "sync_zotero.py",
            False,
            "Restore paper-reader/scripts to include Zotero synchronization",
        ),
        check_path(
            "zotero-mcp-root",
            zotero_mcp_root,
            False,
            "Install Zotero MCP under $ZOTERO_MCP_ROOT or ~/Documents/MCPs/zotero-mcp",
        ),
        check_path(
            "paper-bank-root",
            paper_bank_root,
            True,
            "Create $PAPER_BANK or ~/Documents/paper-bank before running extraction",
        ),
        check_path(
            "citadel-literature-root",
            vault_root,
            False,
            "Create ~/Documents/citadel/literature if notes should be written there",
        ),
        check_python_package(
            "pymupdf4llm",
            False,
            "metadata-only note for PDF-only papers when no arXiv source is available",
        ),
        check_tool("magic-pdf", False, "skip structured PDF extraction mode"),
        check_python_package(
            "bibtexparser",
            False,
            "use regex-based structural checks for refs.bib validation",
        ),
        check_tool(
            "pandoc",
            False,
            "install pandoc (brew install pandoc / choco install pandoc) for LaTeX-to-markdown translation",
        ),
    ]

    # ------------------------------------------------------------------
    # v2 checks: MinerU, Citadel vault accessibility, paper-bank root
    # ------------------------------------------------------------------
    v2_checks: list[dict[str, object]] = [
        check_pymupdf(),
        check_mineru(),
        check_citadel_vault(vault_root),
        check_path(
            "paper-bank-v2",
            paper_bank_root,
            True,
            "Create $PAPER_BANK or ~/Documents/paper-bank; paper-bank root is required for v2 extraction pipeline",
        ),
    ]

    all_checks = v1_checks + v2_checks

    required_ready = all(check["status"] == "found" for check in all_checks if check["required"])
    check_map = {str(check["tool"]): check for check in all_checks}
    legal_paths: list[str] = []
    blocked_paths: list[str] = []

    if required_ready:
        legal_paths.extend(["arxiv-latex", "arxiv-pdf", "manual-download-queue", "paper-bank-storage", "zotero-sync"])
        if check_map["pymupdf4llm"]["status"] == "found":
            legal_paths.append("pdf-fast")
        else:
            legal_paths.append("pdf-metadata-only")
        if check_map["mineru"]["status"] == "found":
            legal_paths.append("pdf-structured")
        else:
            blocked_paths.append("pdf-structured")
    else:
        blocked_paths.extend(
            [
                "arxiv-latex",
                "arxiv-pdf",
                "pdf-fast",
                "pdf-structured",
                "pdf-metadata-only",
                "manual-download-queue",
                "paper-bank-storage",
                "zotero-sync",
            ]
        )

    # ------------------------------------------------------------------
    # v2: translation_ready
    # True when the core pipeline (required checks) passes AND the
    # paper-bank root is accessible.  Citadel vault is desirable but
    # not a hard gate — the extractor can still run without it.
    # ------------------------------------------------------------------
    paper_bank_ok = check_map.get("paper-bank-root", {}).get("status") == "found"
    translation_ready: bool = required_ready and paper_bank_ok

    return {
        "module": "paper-reader",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": [
            "WORK_ROOT defaults to $WORK_ROOT or ~/.research-workdir and is created during preflight when missing",
            "paper-reader writes note/claims artifacts to ~/Documents/citadel/literature by default",
            "Raw source files are expected under $PAPER_BANK (default ~/Documents/paper-bank) with per-cite-key subdirectories",
            "Zotero sync requires the zotero-mcp repository and valid local/remote credentials",
            "Network reachability and Zotero authentication are environment-dependent and are not actively probed",
            "v2: MinerU (magic-pdf CLI or mineru package) enables structured PDF extraction",
            "v2: Citadel vault at ~/Documents/citadel is used for note output",
            "v2: translation_ready=true requires all required checks to pass and paper-bank to be accessible",
        ],
        "checks": all_checks,
        "legal_paths": legal_paths,
        "blocked_paths": blocked_paths,
        "overall": "ready" if required_ready else "blocked",
        "translation_ready": translation_ready,
    }


def main() -> int:
    args = parse_args()
    report = build_report()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"Preflight written: {output_path}")
    print(f"Overall: {report['overall']}")
    print(f"Translation ready: {report['translation_ready']}")

    if report["overall"] != "ready":
        print(
            "WARNING: extraction preflight failed because one or more required dependencies are missing.",
            file=sys.stderr,
        )
        print("Resolve the items below and rerun preflight.", file=sys.stderr)
        missing = [c for c in report["checks"] if c.get("required") and c.get("status") != "found"]
        for c in missing:
            tool = c.get("tool", "?")
            fallback = c.get("fallback", "no fallback available")
            print(f"  MISSING [{tool}]: {fallback}", file=sys.stderr)

    return 0 if report["overall"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
