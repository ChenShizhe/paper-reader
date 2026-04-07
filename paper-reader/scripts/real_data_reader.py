#!/usr/bin/env python3
"""Step 6.4 — Real Data Analysis Reading module.

Runs the Real Data Reading Protocol on all real_data/application-tagged
segments for a paper and produces:
  * ``real_data.md``                    — knowledge artifact in the Citadel vault
  * ``_real_data_reading_output.json``  — structured extraction result in paper-bank

Importable API
--------------
    from real_data_reader import run_real_data_reading
    result = run_real_data_reading("smith2024neural")

CLI
---
    python3 real_data_reader.py --cite-key <key> [--dry-run]
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

REAL_DATA_SECTION_TYPES = {"real_data", "application", "empirical", "case_study", "data_analysis"}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

OUTPUT_JSON_NAME = "_real_data_reading_output.json"


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


def _find_real_data_sections(catalog: dict) -> list[dict]:
    """Return all sections with section_type in REAL_DATA_SECTION_TYPES."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in REAL_DATA_SECTION_TYPES]


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


def _load_simulation_output(paper_bank_root: Path, cite_key: str) -> dict:
    """Load _simulation_reading_output.json from paper-bank; return empty dict if absent."""
    sim_path = paper_bank_root / cite_key / "_simulation_reading_output.json"
    if not sim_path.exists():
        return {}
    try:
        with sim_path.open(encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (json.JSONDecodeError, OSError):
        return {}


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
# LLM helpers
# ---------------------------------------------------------------------------

def _call_llm_json(prompt: str, model: str) -> Optional[dict]:
    """Call Anthropic API expecting a JSON response. Returns parsed dict or None."""
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    response_text = message.content[0].text.strip()

    if response_text.startswith("```"):
        lines = response_text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_text = "\n".join(lines)

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Real data extraction protocol — prompt and fallback
# ---------------------------------------------------------------------------

def _build_real_data_extraction_prompt(
    cite_key: str,
    segment_id: str,
    segment_text: str,
    model_md: str,
    method_md: str,
    simulation_context: dict,
    layer_a: dict,
) -> str:
    """Build the Real Data Reading Protocol prompt for one real data section."""
    model_context = model_md[:1500] if model_md else "(model.md not available)"
    method_context = method_md[:1500] if method_md else "(method.md not available)"
    sim_context_str = json.dumps(simulation_context, ensure_ascii=False)[:800] if simulation_context else "{}"
    constitution_excerpt = (layer_a.get("constitution") or "")[:1500]

    return f"""You are a research reading assistant running the REAL DATA ANALYSIS READING PROTOCOL (Part 6 §4).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Paper: {cite_key}
## Segment: {segment_id}

## Model Context (from model.md)
{model_context}

## Method Context (from method.md)
{method_context}

## Simulation Results Context (from _simulation_reading_output.json)
{sim_context_str}

## Segment Text
{segment_text}

## Task
Run all 5 steps of the Real Data Reading Protocol:

Step 1 — Dataset Provenance: Extract for each dataset:
  - name, source (URL or reference), size (n samples, p features, T time steps as applicable),
    preprocessing steps, availability (public/private/request), domain.
  - Note: absence of any field is noteworthy.

Step 2 — Analysis Design: What does this real data analysis demonstrate?
  What evaluation criteria are used? Is uncertainty quantification present?

Step 3 — Results and Interpretation: What are the main findings?
  Mark domain-dependent assessments with domain_check: "needs_expert".

Step 4 — Dataset Registry Entry: For each dataset, produce a structured entry
  with all provenance fields (mark absent fields as null).

Step 5 — Skepticism Flags: Generate flags at INFO / CAUTION / WARNING levels
  for concerns such as: missing preprocessing details, non-public data,
  cherry-picked datasets, no uncertainty reporting, implausible sample sizes,
  domain-specific interpretation without expert validation, etc.

Output Format (JSON only):
{{
  "datasets": [
    {{
      "name": "<dataset name>",
      "source": "<URL or citation or null>",
      "n_samples": "<integer or description or null>",
      "n_features": "<integer or description or null>",
      "n_time_steps": "<integer or description or null>",
      "preprocessing": "<description or null>",
      "availability": "public|private|request|null",
      "domain": "<domain or null>"
    }}
  ],
  "analysis_design": {{
    "demonstrates": "<what the analysis shows>",
    "evaluation_criteria": ["<criterion 1>", "..."],
    "uncertainty_quantification": "<description or null>"
  }},
  "main_results": "<brief summary of key findings>",
  "domain_checks": ["<item needing expert review>"],
  "claim_support": ["<supported claim from model.md or method.md>"],
  "skepticism_flags": [
    {{"level": "INFO|CAUTION|WARNING", "message": "<flag description>"}}
  ]
}}
"""


def _fallback_real_data_extraction(segment_id: str) -> dict:
    """Minimal extraction when LLM is unavailable."""
    return {
        "datasets": [
            {
                "name": None,
                "source": None,
                "n_samples": None,
                "n_features": None,
                "n_time_steps": None,
                "preprocessing": None,
                "availability": None,
                "domain": None,
            }
        ],
        "analysis_design": {
            "demonstrates": "(requires manual review)",
            "evaluation_criteria": [],
            "uncertainty_quantification": None,
        },
        "main_results": "(requires manual review)",
        "domain_checks": [],
        "claim_support": [],
        "skepticism_flags": [],
        "_fallback": True,
        "_segment_id": segment_id,
    }


# ---------------------------------------------------------------------------
# real_data.md renderer
# ---------------------------------------------------------------------------

def _build_real_data_md(
    cite_key: str,
    real_data_sections: list[dict],
    extraction_results: list[dict],
    all_skepticism_flags: list[dict],
) -> str:
    """Render real_data.md content with required frontmatter."""
    created_at = datetime.now(tz=timezone.utc).isoformat()
    lines = [
        "---",
        f"cite_key: {cite_key}",
        "section: real_data",
        "status: draft",
        f'created_at: "{created_at}"',
        "---",
        "",
        f"# Real Data Analysis: {cite_key}",
        "",
    ]

    if not real_data_sections:
        lines.append("> **Note:** No real_data/application-tagged segments found for this paper.")
        lines.append(">")
        lines.append("> **Scope gap:** This paper may not include a real data analysis,")
        lines.append("> or the relevant segments are not yet tagged as `real_data` or `application`")
        lines.append("> in `_catalog.yaml`.")
        lines.append("")
        return "\n".join(lines)

    # Dataset provenance table
    lines.append("## Dataset Provenance")
    lines.append("")
    lines.append("| Dataset | Domain | Size | Source | Availability |")
    lines.append("|---------|--------|------|--------|--------------|")

    for result in extraction_results:
        for ds in result.get("datasets", []):
            name = ds.get("name") or "N/A"
            domain = ds.get("domain") or "N/A"
            n = ds.get("n_samples") or ds.get("n_time_steps") or "N/A"
            source = ds.get("source") or "N/A"
            avail = ds.get("availability") or "N/A"
            lines.append(f"| {name} | {domain} | {n} | {source} | {avail} |")

    lines.append("")

    # Detailed sections
    for i, (section, result) in enumerate(zip(real_data_sections, extraction_results), start=1):
        heading = section.get("heading", f"Real Data Section {i}")
        seg_ids = section.get("segments", [])

        lines.append(f"## {heading}")
        lines.append("")

        # Dataset details
        datasets = result.get("datasets", [])
        if datasets:
            lines.append("### Datasets")
            for ds in datasets:
                name = ds.get("name") or "Unknown Dataset"
                lines.append(f"#### {name}")
                lines.append(f"- **Source:** {ds.get('source') or 'N/A'}")
                lines.append(f"- **Domain:** {ds.get('domain') or 'N/A'}")
                lines.append(f"- **n samples:** {ds.get('n_samples') or 'N/A'}")
                lines.append(f"- **n features:** {ds.get('n_features') or 'N/A'}")
                lines.append(f"- **Time steps:** {ds.get('n_time_steps') or 'N/A'}")
                lines.append(f"- **Preprocessing:** {ds.get('preprocessing') or 'N/A'}")
                lines.append(f"- **Availability:** {ds.get('availability') or 'N/A'}")
                lines.append("")

        # Analysis design
        design = result.get("analysis_design", {})
        lines.append("### Analysis Design")
        lines.append(f"**Demonstrates:** {design.get('demonstrates', 'N/A')}")
        criteria = design.get("evaluation_criteria", [])
        if criteria:
            lines.append("**Evaluation Criteria:**")
            for c in criteria:
                lines.append(f"- {c}")
        uq = design.get("uncertainty_quantification")
        lines.append(f"**Uncertainty Quantification:** {uq or 'N/A'}")
        lines.append("")

        # Main results
        lines.append("### Key Findings")
        lines.append(result.get("main_results", "N/A"))
        lines.append("")

        # Domain checks
        domain_checks = result.get("domain_checks", [])
        if domain_checks:
            lines.append("### Domain Checks (needs expert review)")
            for dc in domain_checks:
                lines.append(f"- {dc}")
            lines.append("")

        # Claim support
        claims = result.get("claim_support", [])
        if claims:
            lines.append("### Claim Support")
            for c in claims:
                lines.append(f"- {c}")
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

def run_real_data_reading(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 6.4: real data analysis section reading.

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
        - ``real_data_present``
        - ``datasets``
        - ``skepticism_flags``
        - ``claim_support``
        - ``inline_gap_markers``
        - ``real_data_artifacts_produced``
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

    # Load catalog and find real data sections
    catalog = _load_catalog(bank_root, cite_key)
    real_data_sections = _find_real_data_sections(catalog)
    real_data_segments_found = len(real_data_sections)

    # Collect all segment IDs across real data sections
    all_segment_ids: list[str] = []
    for sec in real_data_sections:
        all_segment_ids.extend(sec.get("segments", []))

    # Planned output paths
    real_data_md_path = str(vroot / "literature" / "papers" / cite_key / "real_data.md")
    output_json_path = str(paper_dir / OUTPUT_JSON_NAME)
    outputs_planned = [real_data_md_path, output_json_path]

    if dry_run:
        return {
            "cite_key": cite_key,
            "real_data_segments_found": real_data_segments_found,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
            "model": model,
            "segment_ids": all_segment_ids,
        }

    # Live run: load Layer A context
    layer_a = load_layer_a(str(sroot))

    # Load reading context (read-only)
    model_md = _load_context_md(vroot, cite_key, "model.md")
    method_md = _load_context_md(vroot, cite_key, "method.md")
    simulation_context = _load_simulation_output(bank_root, cite_key)

    # Handle no real data sections
    if real_data_segments_found == 0:
        # Check if paper claims practical applicability (scope gap logic)
        intro_md = _load_context_md(vroot, cite_key, "intro.md")
        applicability_keywords = {"real data", "application", "empirical", "dataset", "stock", "finance"}
        claims_applicability = any(kw in intro_md.lower() for kw in applicability_keywords) if intro_md else False

        real_data_md_content = _build_real_data_md(cite_key, [], [], [])
        real_data_md_full_path = vroot / "literature" / "papers" / cite_key / "real_data.md"
        real_data_md_full_path.parent.mkdir(parents=True, exist_ok=True)
        real_data_md_full_path.write_text(real_data_md_content, encoding="utf-8")

        inline_gap_markers: list[dict] = []
        if claims_applicability:
            inline_gap_markers.append(
                {
                    "gap_type": "missing_real_data_analysis",
                    "description": (
                        "Paper claims practical applicability but no real_data/application "
                        "segments are tagged in _catalog.yaml."
                    ),
                    "source": "real_data_reader",
                }
            )

        output_json: dict[str, Any] = {
            "cite_key": cite_key,
            "real_data_present": False,
            "datasets": [],
            "skepticism_flags": [],
            "claim_support": [],
            "inline_gap_markers": inline_gap_markers,
            "retranslation_needed": False,
        }
        output_json_full_path = paper_dir / OUTPUT_JSON_NAME
        output_json_full_path.write_text(
            json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {
            **output_json,
            "real_data_artifacts_produced": [str(real_data_md_full_path), str(output_json_full_path)],
        }

    # Run Real Data Reading Protocol for each section
    extraction_results: list[dict] = []
    all_skepticism_flags: list[dict] = []
    all_claim_support: list[str] = []
    all_datasets: list[dict] = []
    all_inline_gap_markers: list[dict] = []
    retranslation_needed = False

    for section in real_data_sections:
        seg_ids = section.get("segments", [])
        combined_text = "\n\n".join(
            _load_segment_text(bank_root, cite_key, sid) for sid in seg_ids
        ).strip()

        segment_label = section.get("id", "unknown")

        # Check for translation artifacts
        if _has_translation_artifacts(combined_text):
            retranslation_needed = True

        prompt = _build_real_data_extraction_prompt(
            cite_key=cite_key,
            segment_id=segment_label,
            segment_text=combined_text,
            model_md=model_md,
            method_md=method_md,
            simulation_context=simulation_context,
            layer_a=layer_a,
        )

        result = _call_llm_json(prompt, model)
        if result is None:
            result = _fallback_real_data_extraction(segment_label)

        extraction_results.append(result)

        # Collect datasets
        all_datasets.extend(result.get("datasets", []))

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

    # Write real_data.md to Citadel vault
    real_data_md_content = _build_real_data_md(
        cite_key, real_data_sections, extraction_results, all_skepticism_flags
    )
    real_data_md_full_path = vroot / "literature" / "papers" / cite_key / "real_data.md"
    real_data_md_full_path.parent.mkdir(parents=True, exist_ok=True)
    real_data_md_full_path.write_text(real_data_md_content, encoding="utf-8")

    # Write _real_data_reading_output.json to paper-bank
    output_json = {
        "cite_key": cite_key,
        "real_data_present": True,
        "datasets": all_datasets,
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
    real_data_artifacts_produced = [
        str(real_data_md_full_path), str(output_json_full_path)
    ]

    return {
        **output_json,
        "real_data_artifacts_produced": real_data_artifacts_produced,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 6.4: Real Data Analysis Reading — run the Real Data Reading "
            "Protocol and produce real_data.md and _real_data_reading_output.json."
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

    result = run_real_data_reading(
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
