#!/usr/bin/env python3
"""Step 5.3 — Theory Section Reading module.

Applies the three-level proof reading protocol to all theory-tagged segments
for a paper and produces:
  * ``theory.md``                    — knowledge artifact in the Citadel vault
  * ``_theory_reading_output.json``  — structured extraction result in paper-bank

Three-Level Protocol
--------------------
  Level 1 — STATEMENT COMPREHENSION:
      Plain-English restatement, convergence rate or bound, comparison to prior
      results, optimality assessment, lean_candidate flag.
  Level 2 — ASSUMPTION ANALYSIS:
      Every assumption invoked: name, formal statement, plain-English meaning,
      assumption category, strength rating, testability, proof usage location.
  Level 3 — PROOF STRATEGY:
      Top-level strategy (direct/contradiction/induction/reduction), key
      techniques, key insight, 3-5 sentence proof sketch, known-paper match.

Importable API
--------------
    from theory_reader import run_theory_reading
    result = run_theory_reading("smith2024neural")

CLI
---
    python3 theory_reader.py --cite-key <key> [--dry-run]
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

THEORY_SECTION_TYPES = {"theory", "proof", "theory_method", "theory_model", "method_theory"}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

OUTPUT_JSON_NAME = "_theory_reading_output.json"

# Three-level protocol level markers (referenced in string literals below)
_LEVEL_MARKERS = ["Level 1", "Level 2", "Level 3", "STATEMENT", "ASSUMPTION", "PROOF STRATEGY"]

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


def _find_theory_sections(catalog: dict) -> list[dict]:
    """Return all sections with section_type in THEORY_SECTION_TYPES."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in THEORY_SECTION_TYPES]


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


def _load_context_note(vault_root: Path, cite_key: str, note_name: str) -> str:
    """Load a named .md note from Citadel; return empty string if absent."""
    note_path = vault_root / "literature" / "papers" / cite_key / note_name
    if not note_path.exists():
        return ""
    return note_path.read_text(encoding="utf-8")


def _load_proof_patterns(skill_root: Path) -> str:
    """Load proof-patterns.md from skill root; return empty string if absent."""
    pp_path = skill_root / "proof-patterns.md"
    if not pp_path.exists():
        return ""
    return pp_path.read_text(encoding="utf-8")


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
# LLM helpers
# ---------------------------------------------------------------------------


def _call_llm_json(prompt: str, model: str) -> Optional[dict]:
    """Call Anthropic API expecting a JSON response. Returns parsed dict or None."""
    import os
    if os.path.exists("mock_theory_reader.json"):
        with open("mock_theory_reader.json", "r") as f:
            return json.load(f)

    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=8192,
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
# Three-level extraction protocol — prompt and fallback
# ---------------------------------------------------------------------------


def _build_theory_extraction_prompt(
    cite_key: str,
    segment_id: str,
    segment_text: str,
    model_md: str,
    method_md: str,
    notation_entries: list[dict],
    proof_patterns: str,
    layer_a: dict,
) -> str:
    """Build the three-level proof reading prompt for one theory section."""
    notation_summary = json.dumps(notation_entries[:20], ensure_ascii=False) if notation_entries else "[]"
    model_context = model_md[:1500] if model_md else "(model.md not available)"
    method_context = method_md[:1500] if method_md else "(method.md not available)"
    constitution_excerpt = (layer_a.get("constitution") or "")[:1200]
    proof_patterns_excerpt = proof_patterns[:1200] if proof_patterns else "(proof-patterns.md not available)"

    return f"""You are a research reading assistant running the THEORY SECTION READING PROTOCOL (Part 5 §5.3).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Proof Patterns (Layer A excerpt)
{proof_patterns_excerpt}

## Paper: {cite_key}
## Segment: {segment_id}

## Model Context (from model.md)
{model_context}

## Method Context (from method.md)
{method_context}

## Existing Notation Entries (first 20)
{notation_summary}

## Segment Text
{segment_text}

## Task
Apply the THREE-LEVEL PROOF READING PROTOCOL to every theorem, proposition, lemma, and corollary in this segment.

### Level 1 — STATEMENT COMPREHENSION
For each result:
- Plain-English restatement of the formal statement
- Convergence rate or bound (exact rate expression if present, else null)
- Comparison to prior results or minimax rate (reference model.md context)
- Optimality assessment: optimal / near-optimal / suboptimal / unclear
- lean_candidate: true if the result has a clean enough statement for Lean 4 formalization, false otherwise

### Level 2 — ASSUMPTION ANALYSIS
List every assumption invoked in the proof of each result:
- name: short identifier
- formal_statement: the formal condition as written
- plain_english: one-sentence plain description
- category: one of regularity / moment / mixing / identifiability / smoothness / sparsity / other
- strength: one of standard / moderate / strong
- testable: true / false
- proof_usage: where in the proof this assumption is used (short phrase)

### Level 3 — PROOF STRATEGY
For each result:
- strategy: one of direct_construction / contradiction / induction / reduction / coupling / other
- key_techniques: list of technique names (e.g. chaining, peeling, symmetrization, Bernstein)
- key_insight: one sentence capturing the core idea
- proof_sketch: 3-5 sentences summarizing the proof arc
- pattern_match: name of a known paper or result this proof resembles, or null

## Output Format (JSON only, no prose)
{{
  "theorems": [
    {{
      "result_id": "<theorem/lemma/proposition/corollary label>",
      "result_type": "<theorem|lemma|proposition|corollary|other>",
      "level_1": {{
        "plain_english_statement": "<restatement>",
        "convergence_rate": "<rate expression or null>",
        "prior_comparison": "<comparison or 'N/A'>",
        "optimality": "<optimal|near-optimal|suboptimal|unclear>",
        "lean_candidate": true
      }},
      "level_2_assumptions": [
        {{
          "name": "<short name>",
          "formal_statement": "<formal condition>",
          "plain_english": "<one sentence>",
          "category": "<regularity|moment|mixing|identifiability|smoothness|sparsity|other>",
          "strength": "<standard|moderate|strong>",
          "testable": true,
          "proof_usage": "<where used>"
        }}
      ],
      "level_3": {{
        "strategy": "<direct_construction|contradiction|induction|reduction|coupling|other>",
        "key_techniques": ["<technique>"],
        "key_insight": "<one sentence>",
        "proof_sketch": "<3-5 sentences>",
        "pattern_match": null
      }}
    }}
  ],
  "key_equations": [
    {{
      "eq_number": "<label or null>",
      "latex": "<LaTeX>",
      "description": "<what it expresses>",
      "role": "<definition|result|intermediate>"
    }}
  ],
  "assumptions_extracted": [
    {{
      "name": "<assumption name>",
      "formal_statement": "<formal condition>",
      "plain_english": "<description>",
      "category": "<category>",
      "strength": "<standard|moderate|strong>",
      "testable": true,
      "theorems_using": ["<result_id>"]
    }}
  ],
  "convergence_rates_raw": [
    {{
      "result_id": "<label>",
      "rate_expression": "<rate>",
      "rate_type": "<upper_bound|lower_bound|exact|minimax>",
      "conditions": "<conditions under which the rate holds>"
    }}
  ],
  "notation_entries_new": [
    {{
      "symbol": "<LaTeX or plain>",
      "type": "<function|variable|parameter|operator|set|constant>",
      "description": "<definition>",
      "first_defined_in": "<segment_id>"
    }}
  ],
  "theory_present": true
}}
"""


def _fallback_theory_extraction(segment_id: str) -> dict:
    """Minimal extraction when LLM is unavailable."""
    return {
        "theorems": [
            {
                "result_id": f"{segment_id}_unknown",
                "result_type": "other",
                "level_1": {
                    "plain_english_statement": "(requires manual review)",
                    "convergence_rate": None,
                    "prior_comparison": "N/A",
                    "optimality": "unclear",
                    "lean_candidate": False,
                },
                "level_2_assumptions": [],
                "level_3": {
                    "strategy": "other",
                    "key_techniques": [],
                    "key_insight": "(requires manual review)",
                    "proof_sketch": "(requires manual review)",
                    "pattern_match": None,
                },
            }
        ],
        "key_equations": [],
        "assumptions_extracted": [],
        "convergence_rates_raw": [],
        "notation_entries_new": [],
        "theory_present": True,
        "_fallback": True,
        "_segment_id": segment_id,
    }


# ---------------------------------------------------------------------------
# theory.md renderer
# ---------------------------------------------------------------------------


def _build_theory_md(
    cite_key: str,
    theory_sections: list[dict],
    extraction_results: list[dict],
    theory_present: bool,
) -> str:
    """Render theory.md content with required frontmatter."""
    created_at = datetime.now(tz=timezone.utc).isoformat()
    lines = [
        "---",
        f"cite_key: {cite_key}",
        "section: theory",
        "status: draft",
        f'created_at: "{created_at}"',
        "---",
        "",
        f"# Theory: {cite_key}",
        "",
    ]

    if not theory_present:
        lines.append("*No theory/proof segments found for this paper.*")
        lines.append("")
        lines.append(
            "This note documents the absence of theory sections. "
            "Tasks 04 (convergence rates) and 05 (assumption writer) "
            "should be skipped."
        )
        return "\n".join(lines)

    for i, (section, result) in enumerate(zip(theory_sections, extraction_results), start=1):
        heading = section.get("heading", f"Theory Section {i}")
        seg_ids = section.get("segments", [])
        lines.append(f"## {heading}")
        lines.append("")

        theorems = result.get("theorems", [])
        if not theorems:
            lines.append("*(no theorem-level results extracted from this segment)*")
            lines.append("")
        else:
            for thm in theorems:
                rid = thm.get("result_id", "unknown")
                rtype = thm.get("result_type", "other").capitalize()
                lines.append(f"### {rtype} — {rid}")
                lines.append("")

                # Level 1 — STATEMENT COMPREHENSION
                l1 = thm.get("level_1", {})
                lines.append("#### Level 1 — STATEMENT COMPREHENSION")
                lines.append(f"**Statement:** {l1.get('plain_english_statement', 'N/A')}")
                cr = l1.get("convergence_rate")
                if cr:
                    lines.append(f"**Convergence Rate:** `{cr}`")
                lines.append(f"**Prior Comparison:** {l1.get('prior_comparison', 'N/A')}")
                lines.append(f"**Optimality:** {l1.get('optimality', 'unclear')}")
                lc = l1.get("lean_candidate", False)
                lines.append(f"**lean_candidate:** {str(lc).lower()}")
                lines.append("")

                # Level 2 — ASSUMPTION ANALYSIS
                assumptions = thm.get("level_2_assumptions", [])
                lines.append("#### Level 2 — ASSUMPTION ANALYSIS")
                if assumptions:
                    for a in assumptions:
                        lines.append(f"- **{a.get('name', '?')}** ({a.get('category', '?')}, {a.get('strength', '?')})")
                        lines.append(f"  - Formal: {a.get('formal_statement', 'N/A')}")
                        lines.append(f"  - Plain: {a.get('plain_english', 'N/A')}")
                        lines.append(f"  - Testable: {a.get('testable', '?')}")
                        lines.append(f"  - Used in proof: {a.get('proof_usage', 'N/A')}")
                else:
                    lines.append("*(no assumptions extracted)*")
                lines.append("")

                # Level 3 — PROOF STRATEGY
                l3 = thm.get("level_3", {})
                lines.append("#### Level 3 — PROOF STRATEGY")
                lines.append(f"**Strategy:** {l3.get('strategy', 'other')}")
                kts = l3.get("key_techniques", [])
                if kts:
                    lines.append(f"**Key Techniques:** {', '.join(kts)}")
                lines.append(f"**Key Insight:** {l3.get('key_insight', 'N/A')}")
                lines.append(f"**Proof Sketch:** {l3.get('proof_sketch', 'N/A')}")
                pm = l3.get("pattern_match")
                if pm:
                    lines.append(f"**Pattern Match:** {pm}")
                lines.append("")

        # Key equations for this section
        key_eqs = result.get("key_equations", [])
        if key_eqs:
            lines.append("#### Key Equations")
            for eq in key_eqs:
                label = eq.get("eq_number") or "unlabeled"
                lines.append(f"- [{label}] `{eq.get('latex', '')}` — {eq.get('description', '')} ({eq.get('role', '')})")
            lines.append("")

        lines.append(f"*Segments: {', '.join(seg_ids) if seg_ids else 'N/A'}*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_theory_reading(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 5.3: three-level theory section extraction protocol.

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
        - ``theorems``
        - ``key_equations``
        - ``assumptions_extracted``
        - ``convergence_rates_raw``
        - ``notation_entries_new``
        - ``theory_present``
        - ``theory_artifacts_produced``
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

    # Load catalog and find theory sections
    catalog = _load_catalog(bank_root, cite_key)
    theory_sections = _find_theory_sections(catalog)
    theory_segments_found = len(theory_sections)

    # Collect all segment IDs across theory sections
    all_segment_ids: list[str] = []
    for sec in theory_sections:
        all_segment_ids.extend(sec.get("segments", []))

    # Check for model.md and method.md in Citadel (read-only context)
    model_md = _load_context_note(vroot, cite_key, "model.md")
    method_md = _load_context_note(vroot, cite_key, "method.md")
    model_note_present = bool(model_md)
    method_note_present = bool(method_md)

    # Check proof-patterns.md
    proof_patterns_text = _load_proof_patterns(sroot)
    proof_patterns_loaded = bool(proof_patterns_text)

    # Planned output paths
    theory_md_path = str(vroot / "literature" / "papers" / cite_key / "theory.md")
    output_json_path = str(paper_dir / OUTPUT_JSON_NAME)
    outputs_planned = [theory_md_path, output_json_path]

    if dry_run:
        return {
            "cite_key": cite_key,
            "theory_segments_found": theory_segments_found,
            "model_note_present": model_note_present,
            "method_note_present": method_note_present,
            "proof_patterns_loaded": proof_patterns_loaded,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
            "model": model,
            "segment_ids": all_segment_ids,
        }

    # Live run: handle theory_present = False gracefully
    theory_present = theory_segments_found > 0

    # Load Layer A context (only when theory may be present)
    layer_a = load_layer_a(str(sroot)) if theory_present else {}

    # Load notation dict for symbol binding
    notation_entries = _load_notation_dict(bank_root, cite_key)

    # Run extraction for each theory section
    extraction_results: list[dict] = []
    all_theorems: list[dict] = []
    all_key_equations: list[dict] = []
    all_assumptions_extracted: list[dict] = []
    all_convergence_rates_raw: list[dict] = []
    all_notation_new: list[dict] = []

    for section in theory_sections:
        seg_ids = section.get("segments", [])
        combined_text = "\n\n".join(
            _load_segment_text(bank_root, cite_key, sid) for sid in seg_ids
        ).strip()

        segment_label = section.get("id", "unknown")

        prompt = _build_theory_extraction_prompt(
            cite_key=cite_key,
            segment_id=segment_label,
            segment_text=combined_text,
            model_md=model_md,
            method_md=method_md,
            notation_entries=notation_entries,
            proof_patterns=proof_patterns_text,
            layer_a=layer_a,
        )

        result = _call_llm_json(prompt, model)
        if result is None:
            result = _fallback_theory_extraction(segment_label)

        extraction_results.append(result)

        all_theorems.extend(result.get("theorems", []))
        all_key_equations.extend(result.get("key_equations", []))
        all_assumptions_extracted.extend(result.get("assumptions_extracted", []))
        all_convergence_rates_raw.extend(result.get("convergence_rates_raw", []))
        all_notation_new.extend(result.get("notation_entries_new", []))

    # Write theory.md to Citadel vault (even when theory_present is False)
    theory_md_content = _build_theory_md(cite_key, theory_sections, extraction_results, theory_present)
    theory_md_full_path = vroot / "literature" / "papers" / cite_key / "theory.md"
    theory_md_full_path.parent.mkdir(parents=True, exist_ok=True)
    theory_md_full_path.write_text(theory_md_content, encoding="utf-8")

    # Write _theory_reading_output.json to paper-bank
    output_json: dict[str, Any] = {
        "cite_key": cite_key,
        "theory_present": theory_present,
        "theorems": all_theorems,
        "key_equations": all_key_equations,
        "assumptions_extracted": all_assumptions_extracted,
        "convergence_rates_raw": all_convergence_rates_raw,
        "notation_entries_new": all_notation_new,
        "theory_segments_used": all_segment_ids,
    }
    output_json_full_path = paper_dir / OUTPUT_JSON_NAME
    output_json_full_path.write_text(
        json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Write convergence_rates.yaml to paper-bank
    cr_path = paper_dir / "convergence_rates.yaml"
    cr_path.write_text(
        yaml.dump(
            {"cite_key": cite_key, "convergence_rates": all_convergence_rates_raw},
            default_flow_style=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    # Update _xref_index.yaml with extracted theory key equations
    xref_equations: list[dict] = []
    for i, eq in enumerate(all_key_equations):
        eq_num = eq.get("eq_number") or f"eq{i}"
        xref_equations.append({
            "eq_id": f"{cite_key}_theory_{eq_num}",
            "latex": eq.get("latex", ""),
            "description": eq.get("description", ""),
            "role": eq.get("role", ""),
            "source": "theory_reader",
            "cite_key": cite_key,
        })
    _update_xref_index(bank_root, cite_key, xref_equations)

    # Build SubagentOutput-compatible return value
    theory_artifacts_produced = [str(theory_md_full_path), str(output_json_full_path)]

    return {
        "cite_key": cite_key,
        "theory_present": theory_present,
        "theorems": all_theorems,
        "key_equations": all_key_equations,
        "assumptions_extracted": all_assumptions_extracted,
        "convergence_rates_raw": all_convergence_rates_raw,
        "notation_entries_new": all_notation_new,
        "theory_segments_used": all_segment_ids,
        "theory_artifacts_produced": theory_artifacts_produced,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 5.3: Theory Section Reading — apply the three-level proof protocol "
            "and produce theory.md and _theory_reading_output.json."
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

    result = run_theory_reading(
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
