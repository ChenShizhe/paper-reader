#!/usr/bin/env python3
"""comprehend_empirical.py — M7 empirical comprehension orchestration driver.

Orchestrates empirical reading steps in fixed order:
  Step 6.1 — simulation_reader      (simulation/experiment section reading)
  Step 6.2 — real_data_reader       (real data / application section reading)
  Step 6.3 — discussion_reader      (discussion, conclusion, appendix reading)
  Step 6.4 — ref_resolver           (reference list resolution)
  Step 6.5 — gap_consolidator       (knowledge gap consolidation)
  Step 6.6 — claim_verifier         (final claim verification)

Then performs:
  catalog v4 enrichment     (comprehension_status → empirical_complete)

Prerequisite: catalog must have comprehension_status: technical_complete
(set by comprehend_technical.py / M6).

Importable API
--------------
    from comprehend_empirical import run_empirical_comprehension
    result = run_empirical_comprehension("smith2024neural")

CLI
---
    python3 comprehend_empirical.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Step module imports (all six required by M7)
# ---------------------------------------------------------------------------
from simulation_reader import run_simulation_reading      # noqa: E402  (task 01)
from real_data_reader import run_real_data_reading        # noqa: E402  (task 02)
from discussion_reader import run_discussion_reading      # noqa: E402  (task 03)
from ref_resolver import run_ref_resolution              # noqa: E402  (task 04)
from gap_consolidator import run_gap_consolidation        # noqa: E402  (task 05)
from claim_verifier import run_claim_verification         # noqa: E402  (task 06)

# ---------------------------------------------------------------------------
# Infrastructure imports
# ---------------------------------------------------------------------------
from build_catalog import snapshot_catalog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"
DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")

STEPS_PLANNED = [
    "step_6.1_simulation_reader",
    "step_6.2_real_data_reader",
    "step_6.3_discussion_reader",
    "step_6.4_ref_resolver",
    "step_6.5_gap_consolidator",
    "step_6.6_claim_verifier",
    "catalog_v4_enrichment",
]

# Catalog status constants
_PREREQ_STATUS = "technical_complete"
_TARGET_STATUS = "empirical_complete"


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------


def _load_catalog(catalog_path: Path) -> dict:
    with open(catalog_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _check_prerequisite(catalog: dict) -> bool:
    """Return True when technical_complete is set anywhere in the catalog."""
    # Check top-level comprehension_status
    if catalog.get("comprehension_status") == _PREREQ_STATUS:
        return True
    # Check under paper sub-section (set by comprehend_technical.py)
    paper = catalog.get("paper") or {}
    if paper.get("comprehension_status") == _PREREQ_STATUS:
        return True
    return False


def _validate_inputs(paper_dir: Path) -> list[str]:
    """Return a list of error messages for any missing required inputs."""
    errors: list[str] = []

    catalog_path = paper_dir / "_catalog.yaml"
    if not catalog_path.exists():
        errors.append(f"Missing required file: {catalog_path}")

    return errors


# ---------------------------------------------------------------------------
# Dry-run path
# ---------------------------------------------------------------------------


def _subagent_dispatch(args: argparse.Namespace) -> int:
    """Generate a structured dispatch plan and write it to <paper-bank-dir>/<cite_key>/_dispatch_plan.json.

    Does not call the Anthropic SDK.  The dispatch plan describes which segments
    to read and which output files to produce so that Claude Code can act as the
    comprehension engine.
    """
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    cite_key = args.cite_key
    paper_dir = paper_bank_root / cite_key

    paper_note_dir = Path(os.path.expanduser(args.vault_root)) / "literature" / "papers" / cite_key

    dispatch_plan: dict[str, Any] = {
        "schema_version": "1.0",
        "cite_key": cite_key,
        "script": "comprehend_empirical.py",
        "section_type": "empirical",
        "segment_ids": [
            {
                "section_type": "simulation",
                "description": "Simulation/experiment segments to read in step 6.1",
            },
            {
                "section_type": "real_data",
                "description": "Real data / application segments to read in step 6.2",
            },
            {
                "section_type": "discussion",
                "description": "Discussion, conclusion, and appendix segments to read in step 6.3",
            },
            {
                "section_type": "references",
                "description": "Reference list segments for resolution in step 6.4",
            },
        ],
        "required_reads": [
            str(paper_dir / "_catalog.yaml"),
            str(paper_dir / "segments"),
        ],
        "output_paths": {
            "simulation_md": str(paper_note_dir / "simulation.md"),
            "real_data_md": str(paper_note_dir / "real_data.md"),
            "discussion_md": str(paper_note_dir / "discussion.md"),
            "empirical_md": str(paper_note_dir / "empirical.md"),
            "gaps_md": str(paper_note_dir / "gaps.md"),
            "ref_resolution_json": str(paper_dir / "_ref_resolution_output.json"),
            "gap_consolidation_json": str(paper_dir / "_gap_consolidation_output.json"),
            "claim_verification_json": str(paper_dir / "_claim_verification_output.json"),
            "catalog_yaml": str(paper_dir / "_catalog.yaml"),
        },
        "steps_planned": STEPS_PLANNED,
        "prerequisite_status": _PREREQ_STATUS,
    }

    dispatch_plan_path = paper_dir / "_dispatch_plan.json"
    paper_dir.mkdir(parents=True, exist_ok=True)
    with open(dispatch_plan_path, "w", encoding="utf-8") as fh:
        json.dump(dispatch_plan, fh, indent=2, ensure_ascii=False)

    print(json.dumps(
        {"dispatch_plan_path": str(dispatch_plan_path), "cite_key": cite_key},
        indent=2,
        ensure_ascii=False,
    ))
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    """Validate inputs and print dispatch plan as JSON.

    Exits 0 on success; exits 1 (with stderr) when the paper directory
    does not exist or required files are missing.
    """
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    cite_key = args.cite_key
    paper_dir = paper_bank_root / cite_key

    # Exit 1 when the paper directory itself doesn't exist
    if not paper_dir.exists():
        print(
            f"Error: Paper directory not found for cite_key '{cite_key}': {paper_dir}",
            file=sys.stderr,
        )
        return 1

    # Validate required inputs; exit 1 if any are missing
    errors = _validate_inputs(paper_dir)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    inputs_valid = True
    catalog = _load_catalog(paper_dir / "_catalog.yaml")
    prereq_met = _check_prerequisite(catalog)

    # Build planned output paths
    paper_note_dir = Path(os.path.expanduser(args.vault_root)) / "literature" / "papers" / cite_key
    outputs_planned = [
        str(paper_note_dir / "simulation.md"),
        str(paper_note_dir / "real_data.md"),
        str(paper_note_dir / "discussion.md"),
        str(paper_note_dir / "empirical.md"),
        str(paper_note_dir / "gaps.md"),
        str(paper_dir / "_ref_resolution_output.json"),
        str(paper_dir / "_gap_consolidation_output.json"),
        str(paper_dir / "_claim_verification_output.json"),
        str(paper_dir / "_catalog.yaml"),
        str(paper_dir / "_catalog_v3.yaml"),
        str(paper_dir / "_catalog_v4.yaml"),
    ]

    result: dict[str, Any] = {
        "cite_key": cite_key,
        "steps_planned": STEPS_PLANNED,
        "inputs_valid": inputs_valid,
        "prerequisite_check": {
            "required_status": _PREREQ_STATUS,
            "met": prereq_met,
        },
        "outputs_planned": outputs_planned,
        "target_status": _TARGET_STATUS,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Live run path
# ---------------------------------------------------------------------------


def _live_run(args: argparse.Namespace) -> int:
    """Execute the full empirical comprehension pipeline; return exit code."""
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    vault_root = os.path.expanduser(args.vault_root)
    cite_key = args.cite_key
    model = args.model
    paper_dir = paper_bank_root / cite_key
    paper_note_dir = Path(vault_root) / "literature" / "papers" / cite_key

    # Exit 1 when the paper directory doesn't exist
    if not paper_dir.exists():
        print(
            f"Error: Paper directory not found for cite_key '{cite_key}': {paper_dir}",
            file=sys.stderr,
        )
        return 1

    # Validate required inputs
    errors = _validate_inputs(paper_dir)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    # Enforce technical_complete prerequisite before any empirical work
    catalog = _load_catalog(paper_dir / "_catalog.yaml")
    if not _check_prerequisite(catalog):
        print(
            f"Error: Prerequisite not met for '{cite_key}': catalog must have "
            f"comprehension_status: {_PREREQ_STATUS} before running empirical comprehension.",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # Snapshot catalog BEFORE any catalog modification (saves _catalog_v3.yaml)
    # ------------------------------------------------------------------
    snapshot_path = snapshot_catalog(paper_dir)
    if snapshot_path:
        print(f"Catalog snapshot written: {snapshot_path}", file=sys.stderr)

    artifacts_produced: list[str] = []
    step_results: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step 6.1 — Simulation section reading
    # ------------------------------------------------------------------
    try:
        sim_result = run_simulation_reading(
            cite_key=cite_key,
            paper_bank_root=str(paper_bank_root),
            vault_root=vault_root,
            skill_root=args.skill_root,
            model=model,
        )
        step_results["simulation"] = sim_result
        for key in ("output_json_path", "vault_note_path"):
            if sim_result.get(key):
                artifacts_produced.append(sim_result[key])
    except Exception as exc:
        print(f"Warning: step_6.1_simulation_reader failed: {exc}", file=sys.stderr)
        step_results["simulation"] = {"error": str(exc)}

    # ------------------------------------------------------------------
    # Step 6.2 — Real data section reading
    # ------------------------------------------------------------------
    try:
        real_result = run_real_data_reading(
            cite_key=cite_key,
            paper_bank_root=str(paper_bank_root),
            vault_root=vault_root,
            skill_root=args.skill_root,
            model=model,
        )
        step_results["real_data"] = real_result
        for key in ("output_json_path", "vault_note_path"):
            if real_result.get(key):
                artifacts_produced.append(real_result[key])
    except Exception as exc:
        print(f"Warning: step_6.2_real_data_reader failed: {exc}", file=sys.stderr)
        step_results["real_data"] = {"error": str(exc)}

    # ------------------------------------------------------------------
    # Step 6.3 — Discussion/conclusion/appendix reading
    # ------------------------------------------------------------------
    try:
        disc_result = run_discussion_reading(
            cite_key=cite_key,
            paper_bank_root=str(paper_bank_root),
            vault_root=vault_root,
            skill_root=args.skill_root,
            model=model,
        )
        step_results["discussion"] = disc_result
        for key in ("output_json_path", "vault_note_path"):
            if disc_result.get(key):
                artifacts_produced.append(disc_result[key])
    except Exception as exc:
        print(f"Warning: step_6.3_discussion_reader failed: {exc}", file=sys.stderr)
        step_results["discussion"] = {"error": str(exc)}

    # ------------------------------------------------------------------
    # Consolidate empirical.md from the three section notes
    # ------------------------------------------------------------------
    try:
        paper_note_dir.mkdir(parents=True, exist_ok=True)
        _section_map = [
            ("## Simulation", paper_note_dir / "simulation.md"),
            ("## Real Data Analysis", paper_note_dir / "real_data.md"),
            ("## Discussion", paper_note_dir / "discussion.md"),
        ]
        _empirical_parts: list[str] = []
        for _heading, _note_path in _section_map:
            _empirical_parts.append(_heading)
            if _note_path.exists():
                _empirical_parts.append(_note_path.read_text(encoding="utf-8"))
            else:
                _empirical_parts.append("*(section not available)*")
        empirical_md_path = paper_note_dir / "empirical.md"
        empirical_md_path.write_text("\n\n".join(_empirical_parts), encoding="utf-8")
        artifacts_produced.append(str(empirical_md_path))
        print(f"Consolidated empirical.md written: {empirical_md_path}", file=sys.stderr)
    except Exception as exc:
        print(f"Warning: empirical.md consolidation failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 6.4 — Reference list resolution
    # ------------------------------------------------------------------
    try:
        ref_result = run_ref_resolution(
            cite_key=cite_key,
            paper_bank_root=str(paper_bank_root),
            vault_root=vault_root,
            skill_root=args.skill_root,
            model=model,
        )
        step_results["ref_resolution"] = ref_result
        if ref_result.get("output_json_path"):
            artifacts_produced.append(ref_result["output_json_path"])
    except Exception as exc:
        print(f"Warning: step_6.4_ref_resolver failed: {exc}", file=sys.stderr)
        step_results["ref_resolution"] = {"error": str(exc)}

    # ------------------------------------------------------------------
    # Step 6.5 — Knowledge gap consolidation
    # ------------------------------------------------------------------
    try:
        gap_result = run_gap_consolidation(
            cite_key=cite_key,
            paper_bank_root=str(paper_bank_root),
            vault_root=vault_root,
        )
        step_results["gap_consolidation"] = gap_result
        if gap_result.get("output_json_path"):
            artifacts_produced.append(gap_result["output_json_path"])
    except Exception as exc:
        print(f"Warning: step_6.5_gap_consolidator failed: {exc}", file=sys.stderr)
        step_results["gap_consolidation"] = {"error": str(exc)}

    # ------------------------------------------------------------------
    # Write gaps.md to Citadel vault from gap consolidation output
    # ------------------------------------------------------------------
    try:
        _gap_res = step_results.get("gap_consolidation", {})
        _knowledge_gaps_file = _gap_res.get("knowledge_gaps_file_used") if "error" not in _gap_res else None
        _gaps_md_parts: list[str] = ["# Knowledge Gaps"]
        if _knowledge_gaps_file and Path(_knowledge_gaps_file).exists():
            with open(_knowledge_gaps_file, encoding="utf-8") as _fh:
                _knowledge_gaps_data = yaml.safe_load(_fh) or {}
            _gap_list = _knowledge_gaps_data.get("gaps", [])
            if _gap_list:
                for _g in _gap_list:
                    _gid = _g.get("id", "unknown")
                    _gtype = _g.get("type", "?")
                    _gsev = _g.get("severity", "?")
                    _gsec = _g.get("section", "?")
                    _gdesc = _g.get("description", "(no description)")
                    _gact = _g.get("resolution_action", "")
                    _gaps_md_parts.append(
                        f"\n## {_gid} (Type: {_gtype}, Severity: {_gsev})\n\n"
                        f"- **Section**: {_gsec}\n"
                        f"- **Description**: {_gdesc}\n"
                        f"- **Resolution**: {_gact}"
                    )
            else:
                _gaps_md_parts.append("\n*(no gaps identified)*")
        else:
            _gaps_md_parts.append("\n*(gap consolidation did not produce output)*")
        gaps_md_path = paper_note_dir / "gaps.md"
        gaps_md_path.write_text("\n".join(_gaps_md_parts), encoding="utf-8")
        artifacts_produced.append(str(gaps_md_path))
        print(f"gaps.md written: {gaps_md_path}", file=sys.stderr)
    except Exception as exc:
        print(f"Warning: gaps.md write failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 6.6 — Final claim verification
    # ------------------------------------------------------------------
    try:
        claim_result = run_claim_verification(
            cite_key=cite_key,
            paper_bank_root=str(paper_bank_root),
            vault_root=vault_root,
        )
        step_results["claim_verification"] = claim_result
        if claim_result.get("output_json_path"):
            artifacts_produced.append(claim_result["output_json_path"])
    except Exception as exc:
        print(f"Warning: step_6.6_claim_verifier failed: {exc}", file=sys.stderr)
        step_results["claim_verification"] = {"error": str(exc)}

    # ------------------------------------------------------------------
    # Write catalog v4 enrichment
    # ------------------------------------------------------------------
    catalog_path = paper_dir / "_catalog.yaml"
    with open(catalog_path, encoding="utf-8") as fh:
        catalog_data = yaml.safe_load(fh) or {}

    paper_section = catalog_data.setdefault("paper", {})
    paper_section["catalog_version"] = 4
    paper_section["comprehension_status"] = _TARGET_STATUS

    # Attach step-level summary counts to catalog
    sim_res = step_results.get("simulation", {})
    paper_section["simulation_sections_read"] = sim_res.get("sections_processed", 0)

    real_res = step_results.get("real_data", {})
    paper_section["real_data_sections_read"] = real_res.get("sections_processed", 0)

    disc_res = step_results.get("discussion", {})
    paper_section["discussion_sections_read"] = disc_res.get("sections_processed", 0)

    gap_res = step_results.get("gap_consolidation", {})
    paper_section["gap_count"] = gap_res.get("gap_count", 0)

    claim_res = step_results.get("claim_verification", {})
    paper_section["claims_verified"] = claim_res.get("claims_verified", 0)

    # Set empirical/discussion section comprehension_status markers
    for section in catalog_data.get("sections", []):
        if not isinstance(section, dict):
            continue
        stype = section.get("section_type", "")
        if stype in {
            "simulation", "numerical_experiment", "experiments", "experiment",
            "real_data", "application", "empirical", "case_study", "data_analysis",
            "discussion", "conclusion", "conclusions", "discussion_conclusion",
        }:
            section["comprehension_status"] = _TARGET_STATUS

    with open(catalog_path, "w", encoding="utf-8") as fh:
        yaml.dump(catalog_data, fh, default_flow_style=False, allow_unicode=True)

    artifacts_produced.append(str(catalog_path))

    # ------------------------------------------------------------------
    # Snapshot catalog AFTER update (saves _catalog_v4.yaml)
    # ------------------------------------------------------------------
    v4_snapshot = snapshot_catalog(paper_dir)
    if v4_snapshot:
        artifacts_produced.append(v4_snapshot)
        print(f"Catalog v4 snapshot written: {v4_snapshot}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Emit structured execution summary
    # ------------------------------------------------------------------
    print(
        json.dumps(
            {
                "cite_key": cite_key,
                "status": _TARGET_STATUS,
                "steps_run": list(step_results.keys()),
                "artifacts_produced": artifacts_produced,
                "catalog_updates": {
                    "catalog_version": 4,
                    "comprehension_status": _TARGET_STATUS,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# Public importable API
# ---------------------------------------------------------------------------


def run_empirical_comprehension(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Orchestrate full empirical comprehension for *cite_key*.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    skill_root:
        Path to the paper-reader skill directory; default ``skills/paper-reader``.
    model:
        Anthropic model ID; overridden by ``PAPER_READER_MODEL`` env var.
    dry_run:
        When True, validate inputs and return plan dict without writing files or
        calling LLM.

    Returns
    -------
    dict with keys matching the CLI JSON output.
    """

    class _Args:
        pass

    ns = _Args()
    ns.cite_key = cite_key
    ns.paper_bank_root = paper_bank_root
    ns.vault_root = vault_root
    ns.skill_root = skill_root
    ns.model = model
    ns.dry_run = dry_run

    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        if dry_run:
            code = _dry_run(ns)
        else:
            code = _live_run(ns)

    raw = out_buf.getvalue()
    if raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {"exit_code": code, "raw_output": raw}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M7 empirical comprehension orchestrator (Steps 6.1→6.2→6.3→6.4→6.5→6.6)."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print dispatch plan as JSON; no LLM calls, no file writes.",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Root directory of the paper bank (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    parser.add_argument(
        "--vault-root",
        default=DEFAULT_VAULT_ROOT,
        help=f"Root of the Citadel vault (default: {DEFAULT_VAULT_ROOT}).",
    )
    parser.add_argument(
        "--skill-root",
        default=DEFAULT_SKILL_ROOT,
        help=(
            "Path to the paper-reader skill directory "
            f"(default: {DEFAULT_SKILL_ROOT}, resolved from cwd)."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model ID (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--llm-dispatch",
        choices=["inline", "subagent"],
        default="inline",
        dest="llm_dispatch",
        help=(
            "Dispatch mode. 'inline' runs section reading in-process. "
            "'subagent' generates a structured dispatch plan JSON at "
            "<paper-bank-dir>/<cite_key>/_dispatch_plan.json for agent-driven dispatch."
        ),
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.dry_run:
        sys.exit(_dry_run(args))
    elif args.llm_dispatch == "subagent":
        sys.exit(_subagent_dispatch(args))
    else:
        sys.exit(_live_run(args))


if __name__ == "__main__":
    main()
