#!/usr/bin/env python3
"""Step 5.1 — Model Section Reading module.

Runs the model section extraction protocol on all model-tagged segments for a
paper and produces:
  * ``model.md``                    — knowledge artifact in the Citadel vault
  * ``_model_reading_output.json``  — structured extraction result in paper-bank

Importable API
--------------
    from model_reader import run_model_reading
    result = run_model_reading("smith2024neural")

CLI
---
    python3 model_reader.py --cite-key <key> [--dry-run]
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

MODEL_SECTION_TYPES = {"model", "model_method", "model_theory"}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

OUTPUT_JSON_NAME = "_model_reading_output.json"


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


def _find_model_sections(catalog: dict) -> list[dict]:
    """Return all sections with section_type in MODEL_SECTION_TYPES."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in MODEL_SECTION_TYPES]


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


def _load_intro_md(vault_root: Path, cite_key: str) -> str:
    """Load intro.md from the Citadel vault; return empty string if absent."""
    intro_path = vault_root / "literature" / "papers" / cite_key / "intro.md"
    if not intro_path.exists():
        return ""
    return intro_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# XRef index helper
# ---------------------------------------------------------------------------


def _update_xref_index(paper_bank_root: Path, cite_key: str, equations: list[dict]) -> None:
    """Append equation metadata to _xref_index.yaml, skipping duplicates by eq_id."""
    xref_path = paper_bank_root / cite_key / "_xref_index.yaml"
    if xref_path.exists():
        data = yaml.safe_load(xref_path.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    existing_equations: list[dict] = data.get("equations", [])
    existing_ids: set[str] = {
        str(eq.get("eq_id", "")) for eq in existing_equations if isinstance(eq, dict)
    }
    for eq in equations:
        if not isinstance(eq, dict):
            continue
        eq_id = str(eq.get("eq_id", "")).strip()
        if eq_id and eq_id in existing_ids:
            continue
        existing_equations.append(eq)
        if eq_id:
            existing_ids.add(eq_id)
    data["equations"] = existing_equations
    xref_path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8"
    )




# ---------------------------------------------------------------------------
# Model extraction protocol — prompt and fallback
# ---------------------------------------------------------------------------

def _build_model_extraction_prompt(
    cite_key: str,
    segment_id: str,
    segment_text: str,
    intro_md: str,
    notation_entries: list[dict],
    layer_a: dict,
) -> str:
    """Build the 6-step model extraction prompt for one model section."""
    notation_summary = json.dumps(notation_entries[:20], ensure_ascii=False) if notation_entries else "[]"
    contributions_context = intro_md[:2000] if intro_md else "(intro.md not available)"
    constitution_excerpt = (layer_a.get("constitution") or "")[:1500]

    return f"""You are a research reading assistant running the MODEL SECTION READING PROTOCOL (Part 5 §3).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Paper: {cite_key}
## Segment: {segment_id}

## Claimed Contributions (from intro.md)
{contributions_context}

## Existing Notation Entries (first 20)
{notation_summary}

## Segment Text
{segment_text}

## Task
Run the 6-step model extraction protocol on this model segment:

1. Identify model class along each dimension: parametric/nonparametric, generative/discriminative, static/dynamic.
2. Extract formal model specification: core equations, parameters, parameter space, data generating process.
3. Compare to prior models: note differences from standard/baseline models (reference intro claimed contributions).
4. Verify contributions: which claimed contributions from intro.md are realized in this model section?
5. Mark model→method boundary: where does the model specification end and method/algorithm begin?
6. Collect new notation entries: symbols introduced in this segment not already in the notation dictionary.
   (Do NOT write notation_dict.yaml — return new entries here for the driver to merge.)
7. List the primary statistical or computational methods used under a `## Key Methods` heading.
   Return short canonical method names (e.g., "point process model", "Bayesian inference",
   "kernel density estimation"). Include the paper's core modelling approach and any named frameworks.

## Output Format (JSON only, no prose)
{{
  "model_class": {{
    "parametric_nonparametric": "<parametric|nonparametric|both|unclear>",
    "generative_discriminative": "<generative|discriminative|both|unclear>",
    "static_dynamic": "<static|dynamic|both|unclear>"
  }},
  "formal_specification": {{
    "core_equations": ["<equation 1>", "..."],
    "parameters": ["<param 1>", "..."],
    "parameter_space": "<description>",
    "data_generating_process": "<description>"
  }},
  "prior_model_comparison": "<brief comparison or 'N/A'>",
  "contributions_verified": ["<contribution claim verified>", "..."],
  "model_method_boundary": "<where model spec ends and method begins>",
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


def _fallback_model_extraction(segment_id: str) -> dict:
    """Minimal extraction when LLM is unavailable."""
    return {
        "model_class": {
            "parametric_nonparametric": "unclear",
            "generative_discriminative": "unclear",
            "static_dynamic": "unclear",
        },
        "formal_specification": {
            "core_equations": [],
            "parameters": [],
            "parameter_space": "(requires manual review)",
            "data_generating_process": "(requires manual review)",
        },
        "prior_model_comparison": "N/A",
        "contributions_verified": [],
        "model_method_boundary": "(requires manual review)",
        "notation_entries_new": [],
        "_fallback": True,
        "_segment_id": segment_id,
    }


# ---------------------------------------------------------------------------
# model.md renderer
# ---------------------------------------------------------------------------

def _build_model_md(
    cite_key: str,
    model_sections: list[dict],
    extraction_results: list[dict],
) -> str:
    """Render model.md content with required frontmatter."""
    created_at = datetime.now(tz=timezone.utc).isoformat()
    lines = [
        "---",
        f"cite_key: {cite_key}",
        "section: model",
        "status: draft",
        f'created_at: "{created_at}"',
        "---",
        "",
        f"# Model: {cite_key}",
        "",
    ]

    for i, (section, result) in enumerate(zip(model_sections, extraction_results), start=1):
        heading = section.get("heading", f"Model Section {i}")
        seg_ids = section.get("segments", [])
        lines.append(f"## {heading}")
        lines.append("")

        mc = result.get("model_class", {})
        lines.append("### Model Class")
        lines.append(f"- Parametric/Nonparametric: {mc.get('parametric_nonparametric', 'unclear')}")
        lines.append(f"- Generative/Discriminative: {mc.get('generative_discriminative', 'unclear')}")
        lines.append(f"- Static/Dynamic: {mc.get('static_dynamic', 'unclear')}")
        lines.append("")

        spec = result.get("formal_specification", {})
        lines.append("### Formal Specification")
        eqs = spec.get("core_equations", [])
        if eqs:
            lines.append("**Core Equations:**")
            for eq in eqs:
                lines.append(f"- {eq}")
        params = spec.get("parameters", [])
        if params:
            lines.append("**Parameters:**")
            for p in params:
                lines.append(f"- {p}")
        lines.append(f"**Parameter Space:** {spec.get('parameter_space', 'N/A')}")
        lines.append(f"**Data Generating Process:** {spec.get('data_generating_process', 'N/A')}")
        lines.append("")

        lines.append("### Prior Model Comparison")
        lines.append(result.get("prior_model_comparison", "N/A"))
        lines.append("")

        lines.append("### Contributions Verified")
        cv = result.get("contributions_verified", [])
        if cv:
            for c in cv:
                lines.append(f"- {c}")
        else:
            lines.append("*(none verified in this segment)*")
        lines.append("")

        lines.append("### Model→Method Boundary")
        lines.append(result.get("model_method_boundary", "N/A"))
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

def run_model_reading(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 5.1: model section extraction protocol.

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
        - ``model_class``
        - ``parameters_extracted``
        - ``contributions_verified``
        - ``model_method_boundary``
        - ``notation_entries_new``
        - ``model_segments_used``
        - ``model_artifacts_produced``
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))

    sroot = Path(skill_root)
    if not sroot.is_absolute():
        sroot = Path.cwd() / sroot

    # Validate paper-bank entry exists
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

    # Load catalog and find model sections
    catalog = _load_catalog(bank_root, cite_key)
    model_sections = _find_model_sections(catalog)
    model_segments_found = len(model_sections)

    # Collect all segment IDs across model sections
    all_segment_ids: list[str] = []
    for sec in model_sections:
        all_segment_ids.extend(sec.get("segments", []))

    # Load notation dict to count existing entries (pending for new-entry delta)
    notation_entries = _load_notation_dict(bank_root, cite_key)
    notation_entries_pending = len(notation_entries)

    # Planned output paths
    model_md_path = str(vroot / "literature" / "papers" / cite_key / "model.md")
    output_json_path = str(paper_dir / OUTPUT_JSON_NAME)
    outputs_planned = [model_md_path, output_json_path]

    if dry_run:
        return {
            "cite_key": cite_key,
            "model_segments_found": model_segments_found,
            "notation_entries_pending": notation_entries_pending,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
            "model": model,
            "segment_ids": all_segment_ids,
        }

    # Live run: require at least one model segment
    if model_segments_found == 0:
        print(
            f"Error: no segments tagged with section_type in {sorted(MODEL_SECTION_TYPES)} "
            f"found for cite_key '{cite_key}'. Cannot run model extraction.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load Layer A context
    layer_a = load_layer_a(str(sroot))

    # Load intro.md for claimed contributions context (read-only)
    intro_md = _load_intro_md(vroot, cite_key)

    # Run extraction for each model section
    extraction_results: list[dict] = []
    all_notation_new: list[dict] = []
    all_contributions_verified: list[str] = []
    all_parameters: list[str] = []
    model_method_boundary_notes: list[str] = []

    for section in model_sections:
        seg_ids = section.get("segments", [])
        combined_text = "\n\n".join(
            _load_segment_text(bank_root, cite_key, sid) for sid in seg_ids
        ).strip()

        segment_label = section.get("id", "unknown")

        prompt = _build_model_extraction_prompt(
            cite_key=cite_key,
            segment_id=segment_label,
            segment_text=combined_text,
            intro_md=intro_md,
            notation_entries=notation_entries,
            layer_a=layer_a,
        )

        result = _fallback_model_extraction(segment_label)

        extraction_results.append(result)

        all_notation_new.extend(result.get("notation_entries_new", []))
        all_contributions_verified.extend(result.get("contributions_verified", []))
        spec = result.get("formal_specification", {})
        all_parameters.extend(spec.get("parameters", []))
        boundary = result.get("model_method_boundary", "")
        if boundary and boundary not in ("N/A", "(requires manual review)"):
            model_method_boundary_notes.append(f"[{segment_label}] {boundary}")

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

    # Top-level model_class from first section
    top_model_class = (
        extraction_results[0].get("model_class", {}) if extraction_results else {}
    )

    # Write model.md to Citadel vault
    model_md_content = _build_model_md(cite_key, model_sections, extraction_results)
    model_md_full_path = vroot / "literature" / "papers" / cite_key / "model.md"
    model_md_full_path.parent.mkdir(parents=True, exist_ok=True)
    model_md_full_path.write_text(model_md_content, encoding="utf-8")

    # Write _model_reading_output.json to paper-bank
    output_json: dict[str, Any] = {
        "cite_key": cite_key,
        "model_class": top_model_class,
        "parameters_extracted": all_parameters,
        "contributions_verified": unique_contributions_verified,
        "model_method_boundary": "; ".join(model_method_boundary_notes) or "N/A",
        "notation_entries_new": all_notation_new,
        "key_methods": all_key_methods,
        "model_segments_used": all_segment_ids,
    }
    output_json_full_path = paper_dir / OUTPUT_JSON_NAME
    output_json_full_path.write_text(
        json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Update _xref_index.yaml with extracted model equations
    xref_equations: list[dict] = []
    for i, (section, result) in enumerate(zip(model_sections, extraction_results)):
        spec = result.get("formal_specification", {})
        seg_id = section.get("id", f"model_{i}")
        for j, eq_str in enumerate(spec.get("core_equations", [])):
            xref_equations.append({
                "eq_id": f"{cite_key}_model_{seg_id}_eq{j}",
                "latex": eq_str,
                "description": f"Core equation from model section {seg_id}",
                "source": "model_reader",
                "cite_key": cite_key,
            })
    _update_xref_index(bank_root, cite_key, xref_equations)

    # Build SubagentOutput-compatible return value
    model_artifacts_produced = [str(model_md_full_path), str(output_json_full_path)]

    return {
        "cite_key": cite_key,
        "model_class": top_model_class,
        "parameters_extracted": all_parameters,
        "contributions_verified": unique_contributions_verified,
        "model_method_boundary": "; ".join(model_method_boundary_notes) or "N/A",
        "notation_entries_new": all_notation_new,
        "key_methods": all_key_methods,
        "model_segments_used": all_segment_ids,
        "model_artifacts_produced": model_artifacts_produced,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 5.1: Model Section Reading — run the model extraction protocol "
            "and produce model.md and _model_reading_output.json."
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

    result = run_model_reading(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        vault_root=args.vault_root,
        skill_root=args.skill_root,
        model=args.model,
        dry_run=args.dry_run,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
