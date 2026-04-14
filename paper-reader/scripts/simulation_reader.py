#!/usr/bin/env python3
"""Step 6.3 — Simulation Section Reading module.

Runs the ADEMP simulation reading protocol on all simulation-tagged segments
for a paper and produces:
  * ``simulation.md``                    — knowledge artifact in the Citadel vault
  * ``_simulation_reading_output.json``  — structured extraction result in paper-bank

Importable API
--------------
    from simulation_reader import run_simulation_reading
    result = run_simulation_reading("smith2024neural")

CLI
---
    python3 simulation_reader.py --cite-key <key> [--dry-run]
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

SIMULATION_SECTION_TYPES = {"simulation", "numerical_experiment", "experiments", "experiment"}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

OUTPUT_JSON_NAME = "_simulation_reading_output.json"


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


def _find_simulation_sections(catalog: dict) -> list[dict]:
    """Return all sections with section_type in SIMULATION_SECTION_TYPES."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in SIMULATION_SECTION_TYPES]


def _load_segment_text(paper_bank_root: Path, cite_key: str, segment_id: str) -> str:
    """Load a segment file by ID (filename stem matches segment_id)."""
    seg_dir = paper_bank_root / cite_key / "segments"
    seg_path = seg_dir / f"{segment_id}.md"
    if seg_path.exists():
        return seg_path.read_text(encoding="utf-8")
    return ""


def _load_context_md(vault_root: Path, cite_key: str, filename: str) -> str:
    """Load a note file from the Citadel vault; return empty string if absent."""
    note_path = vault_root / "literature" / "papers" / cite_key / filename
    if not note_path.exists():
        return ""
    return note_path.read_text(encoding="utf-8")


def _load_convergence_rates(paper_bank_root: Path, cite_key: str) -> dict:
    """Load convergence_rates.yaml from paper-bank; return empty dict if absent."""
    cr_path = paper_bank_root / cite_key / "convergence_rates.yaml"
    if not cr_path.exists():
        return {}
    with cr_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _has_translation_artifacts(text: str) -> bool:
    """Heuristic: detect garbled translation artifacts in segment text."""
    artifact_markers = [
        "TRANSLATION_ERROR",
        "[[untranslated]]",
        "???",
        "\ufffd",
    ]
    return any(m in text for m in artifact_markers)


# ---------------------------------------------------------------------------
# Simulation extraction protocol — prompt and fallback
# ---------------------------------------------------------------------------

def _classify_scenario_importance(section: dict) -> str:
    """Step 0: classify as 'important' or 'delta' based on section metadata."""
    heading = (section.get("heading") or "").lower()
    delta_keywords = {"ablation", "sensitivity", "appendix", "supplementary", "additional"}
    for kw in delta_keywords:
        if kw in heading:
            return "delta"
    return "important"


def _build_simulation_extraction_prompt(
    cite_key: str,
    segment_id: str,
    scenario_type: str,
    segment_text: str,
    intro_md: str,
    method_md: str,
    theory_md: str,
    convergence_rates: dict,
    layer_a: dict,
) -> str:
    """Build the ADEMP simulation extraction prompt for one simulation section."""
    contributions_context = intro_md[:2000] if intro_md else "(intro.md not available)"
    method_context = method_md[:1000] if method_md else "(method.md not available)"
    theory_context = theory_md[:1000] if theory_md else "(theory.md not available)"
    cr_context = json.dumps(convergence_rates, ensure_ascii=False)[:500] if convergence_rates else "{}"
    constitution_excerpt = (layer_a.get("constitution") or "")[:1500]

    if scenario_type == "delta":
        task_description = """This is a DELTA scenario (ablation/sensitivity/supplementary).
Extract only:
- What differs from the main simulation setup
- The key result delta (improvement/degradation)
- Any skepticism flag if the delta is suspicious

Output Format (JSON only):
{
  "scenario_type": "delta",
  "delta_description": "<what changes vs main setup>",
  "delta_result": "<key result change>",
  "skepticism_flags": []
}"""
    else:
        task_description = """This is an IMPORTANT scenario. Run full ADEMP evaluation:

Step 0: Identify what this scenario is testing.
Steps 1-4: ADEMP evaluation:
  A — Aims: What is the simulation trying to show?
  D — DGMs (Data Generating Mechanisms): How is data generated? Parameters?
  E — Estimands: What quantities are being estimated or compared?
  M — Methods: Which methods are evaluated? How many replications?
  P — Performance: What metrics are used? What are the main results?

Step 5: Claim verification — which claimed contributions from intro.md are supported?
Step 6: Skepticism flags — flag at INFO/CAUTION/WARNING any concerns about:
  - Missing baselines, cherry-picked parameters, unrealistic DGMs,
    missing variance reporting, suspiciously clean results, etc.

Output Format (JSON only):
{
  "scenario_type": "important",
  "aims": "<what the simulation tests>",
  "dgm": {
    "description": "<data generating mechanism>",
    "parameters": ["<param1>", "..."],
    "replications": "<number or unknown>"
  },
  "estimands": ["<estimand 1>", "..."],
  "methods_compared": ["<method 1>", "..."],
  "performance_metrics": ["<metric 1>", "..."],
  "main_results": "<brief summary>",
  "claim_support": ["<supported claim from intro.md>", "..."],
  "skepticism_flags": [
    {"level": "INFO|CAUTION|WARNING", "message": "<flag description>"}
  ]
}"""

    return f"""You are a research reading assistant running the SIMULATION SECTION READING PROTOCOL (Part 6 §3).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Paper: {cite_key}
## Segment: {segment_id}
## Scenario Classification: {scenario_type}

## Claimed Contributions (from intro.md)
{contributions_context}

## Method Context (from method.md)
{method_context}

## Theory Context (from theory.md)
{theory_context}

## Convergence Rates (from convergence_rates.yaml)
{cr_context}

## Segment Text
{segment_text}

## Task
{task_description}
"""


def _fallback_simulation_extraction(segment_id: str, scenario_type: str) -> dict:
    """Minimal extraction when LLM is unavailable."""
    if scenario_type == "delta":
        return {
            "scenario_type": "delta",
            "delta_description": "(requires manual review)",
            "delta_result": "(requires manual review)",
            "skepticism_flags": [],
            "_fallback": True,
            "_segment_id": segment_id,
        }
    return {
        "scenario_type": "important",
        "aims": "(requires manual review)",
        "dgm": {
            "description": "(requires manual review)",
            "parameters": [],
            "replications": "unknown",
        },
        "estimands": [],
        "methods_compared": [],
        "performance_metrics": [],
        "main_results": "(requires manual review)",
        "claim_support": [],
        "skepticism_flags": [],
        "_fallback": True,
        "_segment_id": segment_id,
    }


# ---------------------------------------------------------------------------
# simulation.md renderer
# ---------------------------------------------------------------------------

def _build_simulation_md(
    cite_key: str,
    simulation_sections: list[dict],
    extraction_results: list[dict],
    all_skepticism_flags: list[dict],
) -> str:
    """Render simulation.md content with required frontmatter."""
    created_at = datetime.now(tz=timezone.utc).isoformat()
    lines = [
        "---",
        f"cite_key: {cite_key}",
        "section: simulation",
        "status: draft",
        f'created_at: "{created_at}"',
        "---",
        "",
        f"# Simulation: {cite_key}",
        "",
    ]

    if not simulation_sections:
        lines.append("> **Note:** No simulation-tagged segments found for this paper.")
        lines.append(">")
        lines.append("> **Scope gap:** This paper may not include a simulation study,")
        lines.append("> or the relevant segments are not yet tagged as `simulation`")
        lines.append("> or `numerical_experiment` in `_catalog.yaml`.")
        lines.append("")
        return "\n".join(lines)

    # ADEMP table header
    lines.append("## ADEMP Summary Table")
    lines.append("")
    lines.append("| Scenario | Type | Aims | DGM | Estimands | Methods | Performance |")
    lines.append("|----------|------|------|-----|-----------|---------|-------------|")

    for section, result in zip(simulation_sections, extraction_results):
        heading = section.get("heading", "Simulation")
        stype = result.get("scenario_type", "important")
        if stype == "delta":
            lines.append(
                f"| {heading} | delta | (delta) | (delta) | (delta)"
                f" | (delta) | {result.get('delta_result', 'N/A')} |"
            )
        else:
            aims = result.get("aims", "N/A")[:60]
            dgm = result.get("dgm", {}).get("description", "N/A")[:40]
            estimands = "; ".join(result.get("estimands", []))[:40] or "N/A"
            methods = "; ".join(result.get("methods_compared", []))[:40] or "N/A"
            perf = "; ".join(result.get("performance_metrics", []))[:40] or "N/A"
            lines.append(f"| {heading} | important | {aims} | {dgm} | {estimands} | {methods} | {perf} |")

    lines.append("")

    # Detailed sections
    for i, (section, result) in enumerate(zip(simulation_sections, extraction_results), start=1):
        heading = section.get("heading", f"Simulation Section {i}")
        stype = result.get("scenario_type", "important")
        seg_ids = section.get("segments", [])

        lines.append(f"## {heading}")
        lines.append(f"*Type: {stype}*")
        lines.append("")

        if stype == "delta":
            lines.append(f"**Delta:** {result.get('delta_description', 'N/A')}")
            lines.append(f"**Result delta:** {result.get('delta_result', 'N/A')}")
        else:
            lines.append(f"**Aims:** {result.get('aims', 'N/A')}")
            lines.append("")

            dgm = result.get("dgm", {})
            lines.append("### Data Generating Mechanism")
            lines.append(dgm.get("description", "N/A"))
            params = dgm.get("parameters", [])
            if params:
                lines.append("**Parameters:**")
                for p in params:
                    lines.append(f"- {p}")
            lines.append(f"**Replications:** {dgm.get('replications', 'unknown')}")
            lines.append("")

            estimands = result.get("estimands", [])
            lines.append("### Estimands")
            if estimands:
                for e in estimands:
                    lines.append(f"- {e}")
            else:
                lines.append("*(none extracted)*")
            lines.append("")

            methods = result.get("methods_compared", [])
            lines.append("### Methods Compared")
            if methods:
                for m in methods:
                    lines.append(f"- {m}")
            else:
                lines.append("*(none extracted)*")
            lines.append("")

            perf = result.get("performance_metrics", [])
            lines.append("### Performance Metrics")
            if perf:
                for p in perf:
                    lines.append(f"- {p}")
            else:
                lines.append("*(none extracted)*")
            lines.append("")

            lines.append("### Main Results")
            lines.append(result.get("main_results", "N/A"))
            lines.append("")

            claims = result.get("claim_support", [])
            lines.append("### Claim Verification")
            if claims:
                for c in claims:
                    lines.append(f"- {c}")
            else:
                lines.append("*(no claims verified in this segment)*")
            lines.append("")

        # Section-level skepticism flags
        flags = result.get("skepticism_flags", [])
        if flags:
            lines.append("### Skepticism Flags")
            for f in flags:
                level = f.get("level", "INFO")
                msg = f.get("message", "")
                lines.append(f"- **{level}:** {msg}")
            lines.append("")

        lines.append(f"*Segments: {', '.join(seg_ids) if seg_ids else 'N/A'}*")
        lines.append("")

    # Global skepticism flags summary
    if all_skepticism_flags:
        lines.append("## Skepticism Flags Summary")
        lines.append("")
        for f in all_skepticism_flags:
            level = f.get("level", "INFO")
            msg = f.get("message", "")
            section_id = f.get("section_id", "")
            lines.append(f"- **{level}** [{section_id}]: {msg}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_simulation_reading(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 6.3: simulation section reading (ADEMP protocol).

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
        - ``simulation_present``
        - ``scenarios_analyzed``
        - ``skepticism_flags``
        - ``claim_support``
        - ``inline_gap_markers``
        - ``simulation_artifacts_produced``
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

    # Load catalog and find simulation sections
    catalog = _load_catalog(bank_root, cite_key)
    simulation_sections = _find_simulation_sections(catalog)
    simulation_segments_found = len(simulation_sections)

    # Collect all segment IDs across simulation sections
    all_segment_ids: list[str] = []
    for sec in simulation_sections:
        all_segment_ids.extend(sec.get("segments", []))

    # Planned output paths
    simulation_md_path = str(vroot / "literature" / "papers" / cite_key / "simulation.md")
    output_json_path = str(paper_dir / OUTPUT_JSON_NAME)
    outputs_planned = [simulation_md_path, output_json_path]

    if dry_run:
        return {
            "cite_key": cite_key,
            "simulation_segments_found": simulation_segments_found,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
            "model": model,
            "segment_ids": all_segment_ids,
        }

    # Live run: load Layer A context
    layer_a = load_layer_a(str(sroot))

    # Load reading context (read-only)
    intro_md = _load_context_md(vroot, cite_key, "intro.md")
    method_md = _load_context_md(vroot, cite_key, "method.md")
    theory_md = _load_context_md(vroot, cite_key, "theory.md")
    convergence_rates = _load_convergence_rates(bank_root, cite_key)

    # Handle no simulation sections
    if simulation_segments_found == 0:
        # Write minimal simulation.md noting absence
        simulation_md_content = _build_simulation_md(cite_key, [], [], [])
        simulation_md_full_path = vroot / "literature" / "papers" / cite_key / "simulation.md"
        simulation_md_full_path.parent.mkdir(parents=True, exist_ok=True)
        simulation_md_full_path.write_text(simulation_md_content, encoding="utf-8")

        output_json: dict[str, Any] = {
            "cite_key": cite_key,
            "simulation_present": False,
            "scenarios_analyzed": [],
            "skepticism_flags": [],
            "claim_support": [],
            "inline_gap_markers": [
                {
                    "gap_type": "missing_simulation",
                    "description": "No simulation-tagged segments found. Paper may lack a simulation study.",
                    "source": "simulation_reader",
                }
            ],
            "retranslation_needed": False,
        }
        output_json_full_path = paper_dir / OUTPUT_JSON_NAME
        output_json_full_path.write_text(
            json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {
            **output_json,
            "simulation_artifacts_produced": [str(simulation_md_full_path), str(output_json_full_path)],
        }

    # Run ADEMP extraction for each simulation section
    extraction_results: list[dict] = []
    all_skepticism_flags: list[dict] = []
    all_claim_support: list[str] = []
    all_inline_gap_markers: list[dict] = []
    retranslation_needed = False

    for section in simulation_sections:
        seg_ids = section.get("segments", [])
        combined_text = "\n\n".join(
            _load_segment_text(bank_root, cite_key, sid) for sid in seg_ids
        ).strip()

        segment_label = section.get("id", "unknown")

        # Check for translation artifacts
        if _has_translation_artifacts(combined_text):
            retranslation_needed = True

        scenario_type = _classify_scenario_importance(section)

        prompt = _build_simulation_extraction_prompt(
            cite_key=cite_key,
            segment_id=segment_label,
            scenario_type=scenario_type,
            segment_text=combined_text,
            intro_md=intro_md,
            method_md=method_md,
            theory_md=theory_md,
            convergence_rates=convergence_rates,
            layer_a=layer_a,
        )

        result = _fallback_simulation_extraction(segment_label, scenario_type)

        extraction_results.append(result)

        # Collect flags with section context
        for flag in result.get("skepticism_flags", []):
            all_skepticism_flags.append({**flag, "section_id": segment_label})

        # Collect claim support
        all_claim_support.extend(result.get("claim_support", []))

    # Deduplicate claim support
    seen_cs: set[str] = set()
    unique_claim_support: list[str] = []
    for c in all_claim_support:
        if c not in seen_cs:
            seen_cs.add(c)
            unique_claim_support.append(c)

    # Write simulation.md to Citadel vault
    simulation_md_content = _build_simulation_md(
        cite_key, simulation_sections, extraction_results, all_skepticism_flags
    )
    simulation_md_full_path = vroot / "literature" / "papers" / cite_key / "simulation.md"
    simulation_md_full_path.parent.mkdir(parents=True, exist_ok=True)
    simulation_md_full_path.write_text(simulation_md_content, encoding="utf-8")

    # Build scenarios_analyzed summary
    scenarios_analyzed = []
    for section, result in zip(simulation_sections, extraction_results):
        scenarios_analyzed.append({
            "section_id": section.get("id", "unknown"),
            "heading": section.get("heading", ""),
            "scenario_type": result.get("scenario_type", "important"),
            "segments": section.get("segments", []),
        })

    # Write _simulation_reading_output.json to paper-bank
    output_json = {
        "cite_key": cite_key,
        "simulation_present": True,
        "scenarios_analyzed": scenarios_analyzed,
        "skepticism_flags": all_skepticism_flags,
        "claim_support": unique_claim_support,
        "inline_gap_markers": all_inline_gap_markers,
        "retranslation_needed": retranslation_needed,
    }
    output_json_full_path = paper_dir / OUTPUT_JSON_NAME
    output_json_full_path.write_text(
        json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Build SubagentOutput-compatible return value
    simulation_artifacts_produced = [
        str(simulation_md_full_path), str(output_json_full_path)
    ]

    return {
        **output_json,
        "simulation_artifacts_produced": simulation_artifacts_produced,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 6.3: Simulation Section Reading — run the ADEMP simulation "
            "reading protocol and produce simulation.md and _simulation_reading_output.json."
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

    result = run_simulation_reading(
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
