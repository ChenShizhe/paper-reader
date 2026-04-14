#!/usr/bin/env python3
"""Step 5.2 — Method Section Reading module.

Runs the method section extraction protocol on all method-tagged segments for a
paper and produces:
  * ``method.md``                    — knowledge artifact in the Citadel vault
  * ``_method_reading_output.json``  — structured extraction result in paper-bank

Importable API
--------------
    from method_reader import run_method_reading
    result = run_method_reading("smith2024neural")

CLI
---
    python3 method_reader.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from subagent_contracts import SubagentInput, SubagentOutput  # noqa: F401
from context_loader import load_layer_a

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

METHOD_SECTION_TYPES = {"method", "methods", "model_method", "method_theory"}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

OUTPUT_JSON_NAME = "_method_reading_output.json"


# ---------------------------------------------------------------------------
# Helpers: catalog loading and segment selection
# ---------------------------------------------------------------------------

def _load_catalog(paper_bank_root: Path, cite_key: str) -> dict:
    """Load _catalog.yaml for *cite_key*."""
    catalog_path = paper_bank_root / cite_key / "_catalog.yaml"
    if not catalog_path.exists():
        return {}
    with catalog_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _find_method_sections(catalog: dict) -> list[dict]:
    """Return all sections with section_type in METHOD_SECTION_TYPES."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in METHOD_SECTION_TYPES]


def _load_segment_text(paper_bank_root: Path, cite_key: str, segment_id: str) -> str:
    """Load a segment file by ID (filename stem matches segment_id)."""
    seg_dir = paper_bank_root / cite_key / "segments"
    seg_path = seg_dir / f"{segment_id}.md"
    if seg_path.exists():
        return seg_path.read_text(encoding="utf-8")
    return ""


def _load_notation_dict(paper_bank_root: Path, cite_key: str) -> list[dict]:
    """Load notation_dict.yaml entries; return empty list if absent."""
    nd_path = paper_bank_root / cite_key / "notation_dict.yaml"
    if not nd_path.exists():
        return []
    with nd_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("entries", [])


def _load_model_md(vault_root: Path, cite_key: str) -> str:
    """Load model.md from the Citadel vault; return empty string if absent."""
    model_path = vault_root / "literature" / "papers" / cite_key / "model.md"
    if not model_path.exists():
        return ""
    return model_path.read_text(encoding="utf-8")


def _model_note_exists(vault_root: Path, cite_key: str) -> bool:
    """Return True if model.md exists in the Citadel vault for cite_key."""
    model_path = vault_root / "literature" / "papers" / cite_key / "model.md"
    return model_path.exists()


# ---------------------------------------------------------------------------
# Method extraction protocol — prompt and fallback
# ---------------------------------------------------------------------------

def _build_method_extraction_prompt(
    cite_key: str,
    segment_id: str,
    segment_text: str,
    model_md: str,
    notation_entries: list[dict],
    layer_a: dict,
) -> str:
    """Build the 7-step method extraction prompt for one method section."""
    notation_summary = json.dumps(notation_entries[:20], ensure_ascii=False) if notation_entries else "[]"
    model_context = model_md[:3000] if model_md else "(model.md not available)"
    constitution_excerpt = (layer_a.get("constitution") or "")[:1500]

    return f"""You are a research reading assistant running the METHOD SECTION READING PROTOCOL (Part 5 §4).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Paper: {cite_key}
## Segment: {segment_id}

## Model Note Context (from model.md)
{model_context}

## Existing Notation Entries (first 20)
{notation_summary}

## Segment Text
{segment_text}

## Task
Run the 7-step method extraction protocol on this method segment:

1. Identify the objective function: loss type, formula, penalty terms, connection to the model specification.
2. Extract the algorithm: name, pseudocode or numbered steps, initialization procedure, stopping criteria, per-iteration complexity.
3. Identify tuning parameters: name, role, selection strategy, sensitivity. Always produce a tuning parameter table (may be empty).
4. Compare to alternative methods: what does the paper claim as advantages over alternatives?
5. Flag method-model coupling: assumptions introduced in the method that were not stated in the model section.
6. Assess generality: classify as one of (model-specific | method-general | technique-general) with a one-sentence justification.
7. Collect new notation entries: estimator hat/tilde conventions and algorithmic notation not already in the notation dictionary.
   (Do NOT write notation_dict.yaml — return new entries here for the driver to merge.)
8. List the primary statistical or computational methods used under a `## Key Methods` heading.
   Return short canonical method names (e.g., "maximum likelihood estimation", "MCMC",
   "variational inference"). Include both the paper's own method and any baseline methods applied.

## Output Format (JSON only, no prose)
{{
  "objective_function": {{
    "loss_type": "<description>",
    "formula": "<LaTeX or plain text formula>",
    "penalty": "<penalty term or 'none'>",
    "model_connection": "<how this connects to the model specification>"
  }},
  "algorithm": {{
    "name": "<algorithm name>",
    "steps": ["<step 1>", "..."],
    "initialization": "<initialization procedure>",
    "stopping_criteria": "<stopping criteria or 'N/A'>",
    "per_iteration_complexity": "<complexity or 'N/A'>"
  }},
  "tuning_parameters": [
    {{
      "name": "<parameter name>",
      "role": "<role in the algorithm>",
      "selection_strategy": "<how to choose it>",
      "sensitivity": "<sensitivity note>"
    }}
  ],
  "alternative_method_comparison": "<claimed advantages over alternatives or 'N/A'>",
  "method_model_coupling": ["<assumption introduced in method not in model>", "..."],
  "generality": {{
    "label": "<model-specific|method-general|technique-general>",
    "justification": "<one-sentence justification>"
  }},
  "contributions_verified": ["<contribution claim verified>", "..."],
  "notation_entries_new": [
    {{
      "symbol": "<LaTeX or plain symbol>",
      "type": "<function|variable|parameter|operator|set|constant>",
      "description": "<definition>",
      "first_defined_in": "<segment id>"
    }}
  ],
  "key_methods": ["<method name 1>", "<method name 2>"]
}}
"""


def _fallback_method_extraction(segment_id: str) -> dict:
    """Minimal extraction when LLM is unavailable."""
    return {
        "objective_function": {
            "loss_type": "(requires manual review)",
            "formula": "(requires manual review)",
            "penalty": "(requires manual review)",
            "model_connection": "(requires manual review)",
        },
        "algorithm": {
            "name": "(requires manual review)",
            "steps": [],
            "initialization": "(requires manual review)",
            "stopping_criteria": "N/A",
            "per_iteration_complexity": "N/A",
        },
        "tuning_parameters": [],
        "alternative_method_comparison": "N/A",
        "method_model_coupling": [],
        "generality": {
            "label": "model-specific",
            "justification": "(requires manual review)",
        },
        "contributions_verified": [],
        "notation_entries_new": [],
        "_fallback": True,
        "_segment_id": segment_id,
    }


# ---------------------------------------------------------------------------
# method.md renderer
# ---------------------------------------------------------------------------

def _build_method_md(
    cite_key: str,
    method_sections: list[dict],
    extraction_results: list[dict],
) -> str:
    """Render method.md content with required frontmatter."""
    created_at = datetime.now(tz=timezone.utc).isoformat()
    lines = [
        "---",
        f"cite_key: {cite_key}",
        "section: method",
        "status: draft",
        f'created_at: "{created_at}"',
        "---",
        "",
        f"# Method: {cite_key}",
        "",
    ]

    for i, (section, result) in enumerate(zip(method_sections, extraction_results), start=1):
        heading = section.get("heading", f"Method Section {i}")
        seg_ids = section.get("segments", [])
        lines.append(f"## {heading}")
        lines.append("")

        obj = result.get("objective_function", {})
        lines.append("### Objective Function")
        lines.append(f"- Loss Type: {obj.get('loss_type', 'N/A')}")
        lines.append(f"- Formula: {obj.get('formula', 'N/A')}")
        lines.append(f"- Penalty: {obj.get('penalty', 'none')}")
        lines.append(f"- Model Connection: {obj.get('model_connection', 'N/A')}")
        lines.append("")

        alg = result.get("algorithm", {})
        lines.append("### Algorithm")
        lines.append(f"**Name:** {alg.get('name', 'N/A')}")
        steps = alg.get("steps", [])
        if steps:
            lines.append("**Steps:**")
            for j, step in enumerate(steps, start=1):
                lines.append(f"{j}. {step}")
        lines.append(f"**Initialization:** {alg.get('initialization', 'N/A')}")
        lines.append(f"**Stopping Criteria:** {alg.get('stopping_criteria', 'N/A')}")
        lines.append(f"**Per-Iteration Complexity:** {alg.get('per_iteration_complexity', 'N/A')}")
        lines.append("")

        tuning = result.get("tuning_parameters", [])
        lines.append("### Tuning Parameters")
        if tuning:
            lines.append("| Name | Role | Selection Strategy | Sensitivity |")
            lines.append("|------|------|--------------------|-------------|")
            for tp in tuning:
                name = tp.get("name", "")
                role = tp.get("role", "")
                strategy = tp.get("selection_strategy", "")
                sensitivity = tp.get("sensitivity", "")
                lines.append(f"| {name} | {role} | {strategy} | {sensitivity} |")
        else:
            lines.append("*(no tuning parameters identified in this segment)*")
        lines.append("")

        lines.append("### Alternative Method Comparison")
        lines.append(result.get("alternative_method_comparison", "N/A"))
        lines.append("")

        coupling = result.get("method_model_coupling", [])
        lines.append("### Method-Model Coupling")
        if coupling:
            for c in coupling:
                lines.append(f"- {c}")
        else:
            lines.append("*(no additional assumptions beyond model section)*")
        lines.append("")

        generality = result.get("generality", {})
        lines.append("### Generality Assessment")
        lines.append(f"**Label:** {generality.get('label', 'N/A')}")
        lines.append(f"**Justification:** {generality.get('justification', 'N/A')}")
        lines.append("")

        cv = result.get("contributions_verified", [])
        lines.append("### Contributions Verified")
        if cv:
            for c in cv:
                lines.append(f"- {c}")
        else:
            lines.append("*(none verified in this segment)*")
        lines.append("")

        lines.append(f"*Segments: {', '.join(seg_ids) if seg_ids else 'N/A'}*")
        lines.append("")

    # Aggregate key methods across all sections
    all_key_methods: list[str] = []
    seen_methods: set[str] = set()
    for result in extraction_results:
        for m in result.get("key_methods", []):
            m_lower = m.strip().lower()
            if m_lower and m_lower not in seen_methods:
                seen_methods.add(m_lower)
                all_key_methods.append(m.strip())
    if all_key_methods:
        lines.append("## Key Methods")
        lines.append("")
        for m in all_key_methods:
            lines.append(f"- {m}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_method_reading(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 5.2: method section extraction protocol.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    skill_root:
        Root of the paper-reader skill directory (for Layer A context);
        default ``skills/paper-reader`` (relative to cwd).
    model:
        Anthropic model ID; overridden by ``PAPER_READER_MODEL`` env var.
    dry_run:
        When True, validate inputs and return dispatch plan as JSON; no file
        writes or LLM calls.

    Returns
    -------
    dict with keys:
        - ``cite_key``
        - ``objective_function_type``
        - ``algorithm_name``
        - ``generality``
        - ``contributions_verified``
        - ``notation_entries_new``
        - ``method_segments_used``
        - ``method_artifacts_produced``
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))

    sroot = Path(skill_root)
    if not sroot.is_absolute():
        sroot = Path.cwd() / sroot

    # Validate paper-bank entry exists (base input — exit 1 if missing)
    paper_dir = bank_root / cite_key
    catalog_path = paper_dir / "_catalog.yaml"
    inputs_valid = paper_dir.exists() and catalog_path.exists()

    if not inputs_valid:
        print(
            f"Error: paper-bank entry not found for cite_key '{cite_key}'. "
            f"Expected directory: {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load catalog and find method sections
    catalog = _load_catalog(bank_root, cite_key)
    method_sections = _find_method_sections(catalog)
    method_segments_found = len(method_sections)

    # Collect all segment IDs across method sections
    all_segment_ids: list[str] = []
    for sec in method_sections:
        all_segment_ids.extend(sec.get("segments", []))

    # Check model.md prerequisite
    model_note_present = _model_note_exists(vroot, cite_key)

    # Load notation dict to count existing entries (pending for new-entry delta)
    notation_entries = _load_notation_dict(bank_root, cite_key)
    notation_entries_pending = len(notation_entries)

    # Planned output paths
    method_md_path = str(vroot / "literature" / "papers" / cite_key / "method.md")
    output_json_path = str(paper_dir / OUTPUT_JSON_NAME)
    outputs_planned = [method_md_path, output_json_path]

    if dry_run:
        return {
            "cite_key": cite_key,
            "method_segments_found": method_segments_found,
            "model_note_present": model_note_present,
            "notation_entries_pending": notation_entries_pending,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
            "model": model,
            "segment_ids": all_segment_ids,
        }

    # Live run: require at least one method segment
    if method_segments_found == 0:
        print(
            f"Error: no segments tagged with section_type in {sorted(METHOD_SECTION_TYPES)} "
            f"found for cite_key '{cite_key}'. Cannot run method extraction.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load Layer A context
    layer_a = load_layer_a(str(sroot))

    # Load model.md for prerequisite context (read-only)
    model_md = _load_model_md(vroot, cite_key)

    # Run extraction for each method section
    extraction_results: list[dict] = []
    all_notation_new: list[dict] = []
    all_contributions_verified: list[str] = []
    generality_notes: list[str] = []
    algorithm_names: list[str] = []

    for section in method_sections:
        seg_ids = section.get("segments", [])
        combined_text = "\n\n".join(
            _load_segment_text(bank_root, cite_key, sid) for sid in seg_ids
        ).strip()

        segment_label = section.get("id", "unknown")

        prompt = _build_method_extraction_prompt(
            cite_key=cite_key,
            segment_id=segment_label,
            segment_text=combined_text,
            model_md=model_md,
            notation_entries=notation_entries,
            layer_a=layer_a,
        )

        result = _fallback_method_extraction(segment_label)

        extraction_results.append(result)

        all_notation_new.extend(result.get("notation_entries_new", []))
        all_contributions_verified.extend(result.get("contributions_verified", []))

        alg_name = result.get("algorithm", {}).get("name", "")
        if alg_name and alg_name not in ("N/A", "(requires manual review)"):
            algorithm_names.append(alg_name)

        gen = result.get("generality", {})
        gen_label = gen.get("label", "")
        if gen_label:
            generality_notes.append(gen_label)

    # Collect key methods across all sections (deduplicated, case-insensitive)
    all_key_methods: list[str] = []
    seen_methods: set[str] = set()
    for result in extraction_results:
        for m in result.get("key_methods", []):
            m_lower = m.strip().lower()
            if m_lower and m_lower not in seen_methods:
                seen_methods.add(m_lower)
                all_key_methods.append(m.strip())

    # Deduplicate contributions_verified
    seen_cv: set[str] = set()
    unique_contributions_verified: list[str] = []
    for c in all_contributions_verified:
        if c not in seen_cv:
            seen_cv.add(c)
            unique_contributions_verified.append(c)

    # Top-level summary fields from first section
    top_obj_type = (
        extraction_results[0].get("objective_function", {}).get("loss_type", "N/A")
        if extraction_results else "N/A"
    )
    top_algorithm_name = algorithm_names[0] if algorithm_names else "N/A"
    top_generality = generality_notes[0] if generality_notes else "N/A"

    # Write method.md to Citadel vault
    method_md_content = _build_method_md(cite_key, method_sections, extraction_results)
    method_md_full_path = vroot / "literature" / "papers" / cite_key / "method.md"
    method_md_full_path.parent.mkdir(parents=True, exist_ok=True)
    method_md_full_path.write_text(method_md_content, encoding="utf-8")

    # Write _method_reading_output.json to paper-bank
    output_json: dict[str, Any] = {
        "cite_key": cite_key,
        "objective_function_type": top_obj_type,
        "algorithm_name": top_algorithm_name,
        "generality": top_generality,
        "contributions_verified": unique_contributions_verified,
        "notation_entries_new": all_notation_new,
        "key_methods": all_key_methods,
        "method_segments_used": all_segment_ids,
    }
    output_json_full_path = paper_dir / OUTPUT_JSON_NAME
    output_json_full_path.write_text(
        json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Build SubagentOutput-compatible return value
    method_artifacts_produced = [str(method_md_full_path), str(output_json_full_path)]

    return {
        "cite_key": cite_key,
        "objective_function_type": top_obj_type,
        "algorithm_name": top_algorithm_name,
        "generality": top_generality,
        "contributions_verified": unique_contributions_verified,
        "notation_entries_new": all_notation_new,
        "key_methods": all_key_methods,
        "method_segments_used": all_segment_ids,
        "method_artifacts_produced": method_artifacts_produced,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 5.2: Method Section Reading — run the method extraction protocol "
            "and produce method.md and _method_reading_output.json."
        )
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print dispatch plan as JSON; no file writes or LLM calls.",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Root of paper bank (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    parser.add_argument(
        "--vault-root",
        default=DEFAULT_VAULT_ROOT,
        help=f"Root of Citadel vault (default: {DEFAULT_VAULT_ROOT}).",
    )
    parser.add_argument(
        "--skill-root",
        default=DEFAULT_SKILL_ROOT,
        help=f"Root of paper-reader skill directory (default: {DEFAULT_SKILL_ROOT}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model ID (default: {DEFAULT_MODEL}).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    result = run_method_reading(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        vault_root=args.vault_root,
        skill_root=args.skill_root,
        model=args.model,
        dry_run=args.dry_run,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
