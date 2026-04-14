#!/usr/bin/env python3
"""comprehend_technical.py — M6 technical comprehension orchestration driver.

Orchestrates technical reading steps in fixed order:
  Step 5.1 — model_reader      (model section reading)
  Step 5.2 — method_reader     (method section reading)
  Step 5.3 — theory_reader     (theory section reading)
  Step 5.4 — convergence_rate_extractor (downstream structural transform)
  Step 5.5 — assumption_writer  (downstream structural transform)
  Step 5.6 — xref_tech_writer  (xref update)

Then performs:
  notation_dict.yaml merge  (skip-on-collision strategy)
  catalog v3 enrichment     (comprehension_status → technical_complete)

Importable API
--------------
    from comprehend_technical import run_technical_comprehension
    result = run_technical_comprehension("smith2024neural")

CLI
---
    python3 comprehend_technical.py --cite-key <key> [--dry-run]
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
# Step module imports (all six required by M6)
# ---------------------------------------------------------------------------
from model_reader import run_model_reading          # noqa: E402  (task 01)
from method_reader import run_method_reading        # noqa: E402  (task 02)
from theory_reader import run_theory_reading        # noqa: E402  (task 03)
from convergence_rate_extractor import extract_convergence_rates  # noqa: E402  (task 04)
from assumption_writer import write_assumption_notes  # noqa: E402  (task 05)
from xref_tech_writer import write_technical_xrefs  # noqa: E402  (task 06)

# ---------------------------------------------------------------------------
# Infrastructure imports
# ---------------------------------------------------------------------------
from build_catalog import snapshot_catalog
from context_loader import load_layer_a
from meta_note_query import query_meta_notes
from subagent_contracts import SubagentOutput

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"
DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
_SEGMENT_MANIFEST_REL_PATH = Path("segments/_segment_manifest.json")

STEPS_PLANNED = [
    "step_5.1_model_reader",
    "step_5.2_method_reader",
    "step_5.3_theory_reader",
    "step_5.4_convergence_rate_extractor",
    "step_5.5_assumption_writer",
    "step_5.6_xref_tech_writer",
    "notation_dict_merge",
    "catalog_v3_enrichment",
]

# Generic section_type values whose labels may be refined after technical reading
_REFINABLE_SECTION_TYPES = {"section", "background", "other", "unknown"}


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def _validate_inputs(paper_dir: Path) -> list[str]:
    """Return a list of error messages for any missing required inputs."""
    errors: list[str] = []

    catalog_path = paper_dir / "_catalog.yaml"
    if not catalog_path.exists():
        errors.append(f"Missing required file: {catalog_path}")

    manifest_path = paper_dir / _SEGMENT_MANIFEST_REL_PATH
    if not manifest_path.exists():
        errors.append(f"Missing required file: {manifest_path}")

    seg_dir = paper_dir / "segments"
    if not seg_dir.exists() or not any(seg_dir.glob("*.md")):
        errors.append(f"Missing segment files in: {seg_dir}")

    return errors


# ---------------------------------------------------------------------------
# Catalog helpers
# ---------------------------------------------------------------------------


def _load_catalog(catalog_path: Path) -> dict:
    with open(catalog_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _extract_paper_meta(catalog: dict) -> dict:
    return catalog.get("paper") or {}


def _get_domain_tags(catalog: dict) -> list[str]:
    paper_meta = _extract_paper_meta(catalog)
    return (
        paper_meta.get("vault_tags")
        or catalog.get("domain_tags")
        or catalog.get("tags")
        or []
    )


def _count_section_type_refinements(catalog: dict) -> int:
    """Count sections whose section_type label may be refined after technical reading.

    Sections with generic labels ('section', 'background', 'other', 'unknown')
    are candidates for reclassification (e.g. 'section' → 'theory' or 'method')
    once the model/method/theory readers scan their content.
    """
    sections = catalog.get("sections") or []
    return sum(
        1
        for sec in sections
        if isinstance(sec, dict) and sec.get("section_type") in _REFINABLE_SECTION_TYPES
    )


# ---------------------------------------------------------------------------
# Notation merge helper
# ---------------------------------------------------------------------------


def _merge_notation_dict(paper_dir: Path, new_entries: list[dict]) -> dict:
    """Merge *new_entries* into notation_dict.yaml using a skip-on-collision strategy.

    Collision detection: if a symbol (the ``symbol`` key) already exists in
    notation_dict.yaml, the existing entry wins and the new entry is skipped
    (skip collision — no overwrite).  New symbols are appended.

    Parameters
    ----------
    paper_dir:
        Paper directory that contains (or will contain) ``notation_dict.yaml``.
    new_entries:
        List of notation entry dicts to merge in.

    Returns
    -------
    dict with keys:
        entries_merged, entries_skipped, collision_count, notation_dict_path
    """
    notation_path = paper_dir / "notation_dict.yaml"
    existing_entries: list[dict] = []

    if notation_path.exists():
        raw = yaml.safe_load(notation_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            existing_entries = raw
        elif isinstance(raw, dict):
            existing_entries = raw.get("entries") or raw.get("notation") or []

    # Build set of already-known symbols for fast collision detection
    existing_symbols: set[str] = {
        str(entry.get("symbol", "")).strip()
        for entry in existing_entries
        if isinstance(entry, dict) and entry.get("symbol")
    }

    merged_count = 0
    skipped_count = 0
    collision_count = 0

    for entry in new_entries:
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("symbol", "")).strip()
        if not symbol:
            continue

        if symbol in existing_symbols:
            # skip collision — existing entry is kept, new entry is not written
            collision_count += 1
            skipped_count += 1
        else:
            existing_entries.append(entry)
            existing_symbols.add(symbol)
            merged_count += 1

    notation_path.write_text(
        yaml.dump(existing_entries, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    return {
        "entries_merged": merged_count,
        "entries_skipped": skipped_count,
        "collision_count": collision_count,
        "notation_dict_path": str(notation_path),
    }


# ---------------------------------------------------------------------------
# Skill-root resolution
# ---------------------------------------------------------------------------


def _resolve_skill_root(skill_root_arg: str) -> Path:
    sroot = Path(skill_root_arg)
    if not sroot.is_absolute():
        sroot = Path.cwd() / sroot
    return sroot


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

    vroot = Path(os.path.expanduser(args.vault_root))
    paper_note_dir = vroot / "literature" / "papers" / cite_key

    dispatch_plan: dict[str, Any] = {
        "schema_version": "1.0",
        "cite_key": cite_key,
        "script": "comprehend_technical.py",
        "section_type": "technical",
        "segment_ids": [
            {
                "section_type": "model",
                "description": "Model-type segments to read in step 5.1",
            },
            {
                "section_type": "method",
                "description": "Method-type segments to read in step 5.2",
            },
            {
                "section_type": "theory",
                "description": "Theory-type segments to read in step 5.3",
            },
        ],
        "required_reads": [
            str(paper_dir / "_catalog.yaml"),
            str(paper_dir / "segments" / "_segment_manifest.json"),
            str(paper_dir / "segments"),
        ],
        "output_paths": {
            "model_md": str(paper_note_dir / "model.md"),
            "method_md": str(paper_note_dir / "method.md"),
            "theory_md": str(paper_note_dir / "theory.md"),
            "convergence_rates_yaml": str(paper_dir / "convergence_rates.yaml"),
            "notation_dict_yaml": str(paper_dir / "notation_dict.yaml"),
            "xref_index_yaml": str(paper_dir / "_xref_index.yaml"),
            "catalog_yaml": str(paper_dir / "_catalog.yaml"),
        },
        "steps_planned": STEPS_PLANNED,
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

    Exits 0 on success; exits 1 (with stderr) when required inputs are missing
    or the paper directory does not exist.
    """
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    vault_root = os.path.expanduser(args.vault_root)
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
    domain_tags = _get_domain_tags(catalog)
    section_type_refinements_found = _count_section_type_refinements(catalog)

    # Try to load Layer A context (non-fatal in dry-run)
    layer_a_loaded = False
    layer_b_notes_found = 0
    try:
        skill_root = _resolve_skill_root(args.skill_root)
        load_layer_a(str(skill_root))
        layer_a_loaded = True

        # Query Layer B meta-notes for technical section type
        layer_b_notes = query_meta_notes(
            vault_root=vault_root,
            domain_tags=domain_tags,
            section_type="theory",
        )
        layer_b_notes_found = len(layer_b_notes)
    except Exception:
        pass

    # Build planned output paths
    vroot = Path(os.path.expanduser(args.vault_root))
    paper_note_dir = vroot / "literature" / "papers" / cite_key
    outputs_planned = [
        str(paper_note_dir / "model.md"),
        str(paper_note_dir / "method.md"),
        str(paper_note_dir / "theory.md"),
        str(paper_dir / "convergence_rates.yaml"),
        str(paper_dir / "notation_dict.yaml"),
        str(paper_dir / "_xref_index.yaml"),
        str(paper_dir / "_catalog.yaml"),
    ]

    result: dict[str, Any] = {
        "cite_key": cite_key,
        "steps_planned": STEPS_PLANNED,
        "inputs_valid": inputs_valid,
        "layer_a_loaded": layer_a_loaded,
        "layer_b_notes_found": layer_b_notes_found,
        "outputs_planned": outputs_planned,
        "section_type_refinements_found": section_type_refinements_found,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Live run path
# ---------------------------------------------------------------------------


def _live_run(args: argparse.Namespace) -> int:
    """Execute the full technical comprehension pipeline; return exit code."""
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    vault_root = os.path.expanduser(args.vault_root)
    cite_key = args.cite_key
    model = args.model
    paper_dir = paper_bank_root / cite_key

    # Exit 1 with stderr when the paper directory doesn't exist
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

    # Load Layer A context
    skill_root = _resolve_skill_root(args.skill_root)
    try:
        load_layer_a(str(skill_root))
    except FileNotFoundError as exc:
        print(f"Error loading Layer A context: {exc}", file=sys.stderr)
        return 1

    # Load catalog and query Layer B meta-notes
    catalog = _load_catalog(paper_dir / "_catalog.yaml")
    domain_tags = _get_domain_tags(catalog)

    layer_b_notes = query_meta_notes(
        vault_root=vault_root,
        domain_tags=domain_tags,
        section_type="theory",
    )

    artifacts_produced: list[str] = []

    # ------------------------------------------------------------------
    # Step 5.1 — Model section reading
    # ------------------------------------------------------------------
    model_result = run_model_reading(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
        skill_root=str(skill_root),
        model=model,
    )
    for key in ("output_json_path", "vault_note_path"):
        if model_result.get(key):
            artifacts_produced.append(model_result[key])

    # ------------------------------------------------------------------
    # Step 5.2 — Method section reading
    # ------------------------------------------------------------------
    method_result = run_method_reading(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
        skill_root=str(skill_root),
        model=model,
    )
    for key in ("output_json_path", "vault_note_path"):
        if method_result.get(key):
            artifacts_produced.append(method_result[key])

    # ------------------------------------------------------------------
    # Step 5.3 — Theory section reading
    # ------------------------------------------------------------------
    theory_result = run_theory_reading(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
        skill_root=str(skill_root),
        model=model,
    )
    for key in ("output_json_path", "vault_note_path"):
        if theory_result.get(key):
            artifacts_produced.append(theory_result[key])

    # ------------------------------------------------------------------
    # Step 5.4 — Convergence rate extraction (downstream structural transform)
    # ------------------------------------------------------------------
    convergence_result = extract_convergence_rates(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
    )
    if convergence_result.get("convergence_rates_path"):
        artifacts_produced.append(convergence_result["convergence_rates_path"])

    # ------------------------------------------------------------------
    # Step 5.5 — Assumption notes (downstream structural transform)
    # ------------------------------------------------------------------
    write_assumption_notes(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
    )

    # ------------------------------------------------------------------
    # Snapshot catalog BEFORE any catalog modification
    # ------------------------------------------------------------------
    snapshot_path = snapshot_catalog(paper_dir)
    if snapshot_path:
        print(f"Catalog snapshot written: {snapshot_path}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 5.6 — XRef update (technical equations and theorems)
    # ------------------------------------------------------------------
    xref_result = write_technical_xrefs(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
    )
    if xref_result.get("xref_path"):
        artifacts_produced.append(xref_result["xref_path"])

    # ------------------------------------------------------------------
    # Notation dict merge (skip-on-collision strategy)
    #
    # Collect new notation entries produced by the model/method/theory
    # reading steps and merge them into notation_dict.yaml.
    # When a symbol already exists in notation_dict.yaml the existing
    # entry wins — the new entry is skipped (no overwrite).
    # ------------------------------------------------------------------
    new_notation_entries: list[dict] = []
    for step_result in (model_result, method_result, theory_result):
        for entry in step_result.get("notation_entries_new", []):
            if isinstance(entry, dict):
                new_notation_entries.append(entry)
        # Also accept the key used by some step modules
        for entry in step_result.get("notation_entries", []):
            if isinstance(entry, dict):
                new_notation_entries.append(entry)

    merge_result = _merge_notation_dict(paper_dir, new_notation_entries)
    artifacts_produced.append(merge_result["notation_dict_path"])

    # ------------------------------------------------------------------
    # Write catalog v3 enrichment
    # ------------------------------------------------------------------
    catalog_path = paper_dir / "_catalog.yaml"
    with open(catalog_path, encoding="utf-8") as fh:
        catalog_data = yaml.safe_load(fh) or {}

    paper_section = catalog_data.setdefault("paper", {})
    paper_section["catalog_version"] = 3
    paper_section["comprehension_status"] = "technical_complete"
    catalog_data["comprehension_status"] = "technical_complete"
    paper_section["model_sections_read"] = model_result.get("sections_processed", 0)
    paper_section["method_sections_read"] = method_result.get("sections_processed", 0)
    paper_section["theory_sections_read"] = theory_result.get("sections_processed", 0)
    paper_section["convergence_rates_extracted"] = convergence_result.get("rate_count", 0)
    paper_section["xref_equations_added"] = xref_result.get("equations_added", 0)
    paper_section["xref_theorems_added"] = xref_result.get("theorems_added", 0)
    paper_section["notation_entries_merged"] = merge_result["entries_merged"]
    paper_section["notation_collision_count"] = merge_result["collision_count"]

    with open(catalog_path, "w", encoding="utf-8") as fh:
        yaml.dump(catalog_data, fh, default_flow_style=False, allow_unicode=True)

    artifacts_produced.append(str(catalog_path))

    # ------------------------------------------------------------------
    # Build SubagentOutput and emit result
    # ------------------------------------------------------------------
    subagent_out = SubagentOutput(
        cite_key=cite_key,
        section_type="technical",
        status="technical_complete",
        notes_written=artifacts_produced,
        catalog_updates={
            "catalog_version": 3,
            "comprehension_status": "technical_complete",
        },
        flags=[],
        extra={
            "technical_artifacts_produced": artifacts_produced,
            "layer_b_notes_used": len(layer_b_notes),
            "notation_collision_count": merge_result["collision_count"],
        },
    )

    print(
        json.dumps(
            {
                "cite_key": subagent_out.cite_key,
                "status": subagent_out.status,
                "technical_artifacts_produced": subagent_out.extra[
                    "technical_artifacts_produced"
                ],
                "catalog_updates": subagent_out.catalog_updates,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# Public importable API
# ---------------------------------------------------------------------------


def run_technical_comprehension(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Orchestrate full technical comprehension for *cite_key*.

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
        description="M6 technical comprehension orchestrator (Steps 5.1→5.2→5.3→5.4→5.5→5.6)."
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
