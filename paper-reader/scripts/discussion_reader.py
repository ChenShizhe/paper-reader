#!/usr/bin/env python3
"""Step 6.5 — Discussion/Conclusion and Appendix Reading module.

Runs the discussion/conclusion reading protocol (Part 6 §5) plus appendix
proof handling on all discussion-, conclusion-, and appendix-tagged segments
for a paper and produces:
  * ``discussion.md``                    — knowledge artifact in the Citadel vault
  * ``_discussion_reading_output.json``  — structured extraction result in paper-bank

Importable API
--------------
    from discussion_reader import run_discussion_reading
    result = run_discussion_reading("smith2024neural")

CLI
---
    python3 discussion_reader.py --cite-key <key> [--dry-run]
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

DISCUSSION_SECTION_TYPES = {"discussion", "conclusion", "conclusions", "discussion_conclusion"}
APPENDIX_SECTION_TYPES = {"appendix", "supplementary", "supplementary_material", "appendix_proof"}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

OUTPUT_JSON_NAME = "_discussion_reading_output.json"


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


def _find_discussion_sections(catalog: dict) -> list[dict]:
    """Return sections tagged as discussion or conclusion."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in DISCUSSION_SECTION_TYPES]


def _find_appendix_sections(catalog: dict) -> list[dict]:
    """Return sections tagged as appendix or supplementary."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in APPENDIX_SECTION_TYPES]


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


def _load_json_if_present(paper_bank_root: Path, cite_key: str, filename: str) -> dict:
    """Load a JSON file from paper-bank; return empty dict if absent."""
    json_path = paper_bank_root / cite_key / filename
    if not json_path.exists():
        return {}
    try:
        with json_path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


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
# Discussion extraction protocol — prompt and fallback
# ---------------------------------------------------------------------------

def _build_discussion_extraction_prompt(
    cite_key: str,
    segment_id: str,
    segment_text: str,
    intro_md: str,
    model_md: str,
    method_md: str,
    theory_md: str,
    simulation_output: dict,
    real_data_output: dict,
    layer_a: dict,
) -> str:
    """Build the discussion/conclusion extraction prompt for one segment."""
    contributions_context = intro_md[:2000] if intro_md else "(intro.md not available)"
    model_context = model_md[:800] if model_md else "(model.md not available)"
    method_context = method_md[:800] if method_md else "(method.md not available)"
    theory_context = theory_md[:800] if theory_md else "(theory.md not available)"
    sim_context = json.dumps(simulation_output, ensure_ascii=False)[:600] if simulation_output else "{}"
    rdata_context = json.dumps(real_data_output, ensure_ascii=False)[:600] if real_data_output else "{}"
    constitution_excerpt = (layer_a.get("constitution") or "")[:1500]

    return f"""You are a research reading assistant running the DISCUSSION/CONCLUSION READING PROTOCOL (Part 6 §5).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Paper: {cite_key}
## Segment: {segment_id}

## Claimed Contributions (from intro.md)
{contributions_context}

## Model Context (from model.md)
{model_context}

## Method Context (from method.md)
{method_context}

## Theory Context (from theory.md)
{theory_context}

## Simulation Reading Output (summary)
{sim_context}

## Real Data Reading Output (summary)
{rdata_context}

## Segment Text
{segment_text}

## Task
Run the discussion/conclusion reading protocol:

Step 1 — Extract acknowledged limitations:
  - Note each limitation explicitly stated by the authors.
  - Flag whether each limitation was already noted in prior reading steps (intro/theory/simulation).

Step 2 — Extract future work directions:
  - Note each future work direction.
  - Note any vault topic connections or reading suggestions implied.

Step 3 — Extract open questions the authors raise:
  - These become inline gap markers.

Step 4 — Unusual structure flags:
  - If the discussion contains new results not previewed in earlier sections, set unusual_structure to ["discussion_contains_new_results"].
  - Otherwise set to [].

Output Format (JSON only):
{{
  "limitations": [
    {{"text": "<limitation>", "already_flagged": true|false}}
  ],
  "future_work": [
    {{"text": "<direction>", "vault_connection": "<topic or null>"}}
  ],
  "open_questions": ["<question 1>", "..."],
  "unusual_structure": []
}}
"""


def _fallback_discussion_extraction(segment_id: str) -> dict:
    """Minimal extraction when LLM is unavailable."""
    return {
        "limitations": [{"text": "(requires manual review)", "already_flagged": False}],
        "future_work": [{"text": "(requires manual review)", "vault_connection": None}],
        "open_questions": ["(requires manual review)"],
        "unusual_structure": [],
        "_fallback": True,
        "_segment_id": segment_id,
    }


def _build_appendix_proof_prompt(
    cite_key: str,
    segment_id: str,
    segment_text: str,
    theory_md: str,
    layer_a: dict,
) -> str:
    """Build the appendix proof reading prompt (Part 5 three-level protocol)."""
    theory_context = theory_md[:1500] if theory_md else "(theory.md not available)"
    proof_patterns_excerpt = (layer_a.get("proof_patterns") or "")[:1000]
    constitution_excerpt = (layer_a.get("constitution") or "")[:800]

    return f"""You are a research reading assistant running the APPENDIX PROOF READING PROTOCOL (Part 5, three-level).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Proof Patterns (excerpt)
{proof_patterns_excerpt}

## Paper: {cite_key}
## Appendix Segment: {segment_id}

## Existing Theory Context (from theory.md)
{theory_context}

## Appendix Segment Text
{segment_text}

## Task
Apply the three-level proof reading protocol to this appendix segment:

Level 1 — Proof overview: Identify what is being proved and the proof strategy.
Level 2 — Key steps: Note the critical logical steps or lemmas invoked.
Level 3 — Skepticism: Flag any gaps, handwaving, or dependence on unlisted assumptions.

Output Format (JSON only):
{{
  "theorem_or_lemma": "<what is proved>",
  "proof_strategy": "<high-level strategy>",
  "key_steps": ["<step 1>", "..."],
  "assumptions_used": ["<assumption 1>", "..."],
  "skepticism_flags": [
    {{"level": "INFO|CAUTION|WARNING", "message": "<flag>"}}
  ],
  "proof_note_md": "<short markdown note to append to theory.md>"
}}
"""


def _fallback_appendix_extraction(segment_id: str) -> dict:
    """Minimal appendix extraction when LLM is unavailable."""
    return {
        "theorem_or_lemma": "(requires manual review)",
        "proof_strategy": "(requires manual review)",
        "key_steps": [],
        "assumptions_used": [],
        "skepticism_flags": [],
        "proof_note_md": f"\n## Appendix Proof: {segment_id}\n\n*(requires manual review — LLM unavailable)*\n",
        "_fallback": True,
        "_segment_id": segment_id,
    }


# ---------------------------------------------------------------------------
# discussion.md renderer
# ---------------------------------------------------------------------------

def _build_discussion_md(
    cite_key: str,
    discussion_sections: list[dict],
    extraction_results: list[dict],
) -> str:
    """Render discussion.md content with required frontmatter."""
    created_at = datetime.now(tz=timezone.utc).isoformat()
    lines = [
        "---",
        f"cite_key: {cite_key}",
        "section: discussion",
        "status: draft",
        f'created_at: "{created_at}"',
        "---",
        "",
        f"# Discussion: {cite_key}",
        "",
    ]

    if not discussion_sections:
        lines.append("> **Note:** No discussion- or conclusion-tagged segments found for this paper.")
        lines.append(">")
        lines.append("> The relevant segments may not yet be tagged as `discussion` or `conclusion`")
        lines.append("> in `_catalog.yaml`.")
        lines.append("")
        return "\n".join(lines)

    # Aggregate across all sections
    all_limitations: list[dict] = []
    all_future_work: list[dict] = []
    all_open_questions: list[str] = []
    all_unusual_structure: list[str] = []

    for section, result in zip(discussion_sections, extraction_results):
        heading = section.get("heading", "Discussion")
        lines.append(f"## {heading}")
        lines.append("")

        limitations = result.get("limitations", [])
        future_work = result.get("future_work", [])
        open_questions = result.get("open_questions", [])
        unusual = result.get("unusual_structure", [])

        all_limitations.extend(limitations)
        all_future_work.extend(future_work)
        all_open_questions.extend(open_questions)
        all_unusual_structure.extend(unusual)

        lines.append("### Limitations")
        if limitations:
            for lim in limitations:
                flag = " *(previously flagged)*" if lim.get("already_flagged") else ""
                lines.append(f"- {lim.get('text', 'N/A')}{flag}")
        else:
            lines.append("*(none extracted)*")
        lines.append("")

        lines.append("### Future Work")
        if future_work:
            for fw in future_work:
                vc = fw.get("vault_connection")
                suffix = f" → `{vc}`" if vc else ""
                lines.append(f"- {fw.get('text', 'N/A')}{suffix}")
        else:
            lines.append("*(none extracted)*")
        lines.append("")

        lines.append("### Open Questions")
        if open_questions:
            for q in open_questions:
                lines.append(f"- {q}")
        else:
            lines.append("*(none extracted)*")
        lines.append("")

        if unusual:
            lines.append("### Unusual Structure Flags")
            for u in unusual:
                lines.append(f"- `{u}`")
            lines.append("")

        seg_ids = section.get("segments", [])
        lines.append(f"*Segments: {', '.join(seg_ids) if seg_ids else 'N/A'}*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_discussion_reading(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 6.5: discussion/conclusion and appendix reading protocol.

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
        - ``limitations_count``
        - ``future_work_count``
        - ``open_questions_count``
        - ``unusual_structure_flags``
        - ``inline_gap_markers``
        - ``appendix_proofs_appended``
        - ``discussion_artifacts_produced``
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

    # Load catalog and find sections
    catalog = _load_catalog(bank_root, cite_key)
    discussion_sections = _find_discussion_sections(catalog)
    appendix_sections = _find_appendix_sections(catalog)

    discussion_segments_found = sum(len(s.get("segments", [])) for s in discussion_sections)
    appendix_segments_found = sum(len(s.get("segments", [])) for s in appendix_sections)

    # Planned output paths
    discussion_md_path = str(vroot / "literature" / "papers" / cite_key / "discussion.md")
    output_json_path = str(paper_dir / OUTPUT_JSON_NAME)
    outputs_planned = [discussion_md_path, output_json_path]

    if dry_run:
        return {
            "cite_key": cite_key,
            "discussion_segments_found": discussion_segments_found,
            "appendix_segments_found": appendix_segments_found,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
            "model": model,
        }

    # Live run: load Layer A context
    layer_a = load_layer_a(str(sroot))

    # Load reading context (read-only)
    intro_md = _load_context_md(vroot, cite_key, "intro.md")
    model_md = _load_context_md(vroot, cite_key, "model.md")
    method_md = _load_context_md(vroot, cite_key, "method.md")
    theory_md = _load_context_md(vroot, cite_key, "theory.md")

    # Load optional prior-step outputs
    simulation_output = _load_json_if_present(bank_root, cite_key, "_simulation_reading_output.json")
    real_data_output = _load_json_if_present(bank_root, cite_key, "_real_data_reading_output.json")

    # ----- Discussion/Conclusion extraction -----
    extraction_results: list[dict] = []
    all_limitations: list[dict] = []
    all_future_work: list[dict] = []
    all_open_questions: list[str] = []
    all_unusual_structure_flags: list[str] = []
    inline_gap_markers: list[dict] = []

    for section in discussion_sections:
        seg_ids = section.get("segments", [])
        combined_text = "\n\n".join(
            _load_segment_text(bank_root, cite_key, sid) for sid in seg_ids
        ).strip()
        segment_label = section.get("id", "unknown")

        prompt = _build_discussion_extraction_prompt(
            cite_key=cite_key,
            segment_id=segment_label,
            segment_text=combined_text,
            intro_md=intro_md,
            model_md=model_md,
            method_md=method_md,
            theory_md=theory_md,
            simulation_output=simulation_output,
            real_data_output=real_data_output,
            layer_a=layer_a,
        )

        result = _call_llm_json(prompt, model)
        if result is None:
            result = _fallback_discussion_extraction(segment_label)

        extraction_results.append(result)
        all_limitations.extend(result.get("limitations", []))
        all_future_work.extend(result.get("future_work", []))
        all_open_questions.extend(result.get("open_questions", []))
        all_unusual_structure_flags.extend(result.get("unusual_structure", []))

    # Convert open questions to inline gap markers
    for q in all_open_questions:
        inline_gap_markers.append({
            "gap_type": "open_question",
            "description": q,
            "source": "discussion_reader",
        })

    # ----- Appendix proof extraction -----
    appendix_proof_notes: list[str] = []
    appendix_proofs_appended = 0

    for section in appendix_sections:
        seg_ids = section.get("segments", [])
        combined_text = "\n\n".join(
            _load_segment_text(bank_root, cite_key, sid) for sid in seg_ids
        ).strip()
        segment_label = section.get("id", "unknown")

        prompt = _build_appendix_proof_prompt(
            cite_key=cite_key,
            segment_id=segment_label,
            segment_text=combined_text,
            theory_md=theory_md,
            layer_a=layer_a,
        )

        result = _call_llm_json(prompt, model)
        if result is None:
            result = _fallback_appendix_extraction(segment_label)

        proof_note = result.get("proof_note_md", "")
        if proof_note:
            appendix_proof_notes.append(proof_note)
            appendix_proofs_appended += 1

    # Write discussion.md to Citadel vault
    discussion_md_content = _build_discussion_md(cite_key, discussion_sections, extraction_results)
    discussion_md_full_path = vroot / "literature" / "papers" / cite_key / "discussion.md"
    discussion_md_full_path.parent.mkdir(parents=True, exist_ok=True)
    discussion_md_full_path.write_text(discussion_md_content, encoding="utf-8")

    # Append appendix proof notes to theory.md (append-only)
    if appendix_proof_notes:
        theory_md_full_path = vroot / "literature" / "papers" / cite_key / "theory.md"
        existing_theory = theory_md_full_path.read_text(encoding="utf-8") if theory_md_full_path.exists() else ""
        appended_content = existing_theory + "\n\n## Appendix Proofs\n" + "\n".join(appendix_proof_notes)
        theory_md_full_path.parent.mkdir(parents=True, exist_ok=True)
        theory_md_full_path.write_text(appended_content, encoding="utf-8")

    # Build _discussion_reading_output.json
    output_json: dict[str, Any] = {
        "cite_key": cite_key,
        "limitations_count": len(all_limitations),
        "future_work_count": len(all_future_work),
        "open_questions_count": len(all_open_questions),
        "unusual_structure_flags": list(set(all_unusual_structure_flags)),
        "inline_gap_markers": inline_gap_markers,
        "appendix_proofs_appended": appendix_proofs_appended,
    }
    output_json_full_path = paper_dir / OUTPUT_JSON_NAME
    output_json_full_path.write_text(
        json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Build SubagentOutput-compatible return value
    discussion_artifacts_produced = [
        str(discussion_md_full_path), str(output_json_full_path)
    ]

    return {
        **output_json,
        "discussion_artifacts_produced": discussion_artifacts_produced,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 6.5: Discussion/Conclusion and Appendix Reading — run the discussion "
            "reading protocol and produce discussion.md and _discussion_reading_output.json."
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

    result = run_discussion_reading(
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
