#!/usr/bin/env python3
"""Step 6.6 — Knowledge Gap Consolidation module.

Combines inline gap markers from all section reading output JSONs and per-section
Citadel notes, deduplicates and classifies gaps by type (C/X/E/S), generates
cross-referencing gaps, and emits _knowledge_gaps.yaml and
_gap_consolidation_output.json artifacts.

Importable API
--------------
    from gap_consolidator import run_gap_consolidation
    result = run_gap_consolidation("smith2024neural")

CLI
---
    python3 gap_consolidator.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from subagent_contracts import SubagentOutput  # noqa: F401

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

READING_OUTPUT_FILES = [
    "_intro_reading_output.json",
    "_model_reading_output.json",
    "_method_reading_output.json",
    "_theory_reading_output.json",
    "_simulation_reading_output.json",
    "_real_data_reading_output.json",
    "_discussion_reading_output.json",
]

SECTION_NOTE_FILES = [
    "intro.md",
    "model.md",
    "method.md",
    "theory.md",
    "simulation.md",
    "real_data.md",
    "discussion.md",
]

KNOWLEDGE_GAPS_YAML_NAME = "_knowledge_gaps.yaml"
GAP_CONSOLIDATION_OUTPUT_JSON_NAME = "_gap_consolidation_output.json"

# Gap type codes (Part 6 §6):
#   C = Conceptual   — missing/unclear theoretical concept
#   X = Cross-reference — gap only visible across sections
#   E = Empirical    — missing experimental/data evidence
#   S = Scope        — out-of-scope or boundary gap
GAP_TYPE_CONCEPTUAL = "C"
GAP_TYPE_XREF = "X"
GAP_TYPE_EMPIRICAL = "E"
GAP_TYPE_SCOPE = "S"

VALID_GAP_TYPES = [GAP_TYPE_CONCEPTUAL, GAP_TYPE_XREF, GAP_TYPE_EMPIRICAL, GAP_TYPE_SCOPE]

SOFT_MINIMUM_GAP_COUNT = 3

# ---------------------------------------------------------------------------
# Helpers: loading inputs
# ---------------------------------------------------------------------------


def _load_catalog(paper_bank_root: Path, cite_key: str) -> dict:
    """Load _catalog.yaml for *cite_key*; returns empty dict if absent."""
    catalog_path = paper_bank_root / cite_key / "_catalog.yaml"
    if not catalog_path.exists():
        return {}
    with catalog_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _collect_inline_markers_from_json(paper_dir: Path) -> tuple[list[str], list[dict]]:
    """Load all reading output JSONs and collect their inline_gap_markers lists.

    Returns
    -------
    (found_files, all_markers)
        found_files: list of filenames that existed and were loaded
        all_markers: combined list of raw gap marker dicts
    """
    found_files: list[str] = []
    all_markers: list[dict] = []
    for fname in READING_OUTPUT_FILES:
        fpath = paper_dir / fname
        if not fpath.exists():
            continue
        found_files.append(fname)
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            markers = data.get("inline_gap_markers", [])
            if isinstance(markers, list):
                for m in markers:
                    if isinstance(m, dict):
                        m.setdefault("_source_file", fname)
                        all_markers.append(m)
        except (json.JSONDecodeError, OSError):
            pass
    return found_files, all_markers


_GAP_INLINE_RE = re.compile(r"GAP:\s*(.+)", re.IGNORECASE)


def _scan_notes_for_gap_markers(vault_root: Path, cite_key: str) -> list[dict]:
    """Scan per-section note files in the Citadel vault for inline ``GAP:`` markers."""
    markers: list[dict] = []
    notes_dir = vault_root / "literature" / "papers" / cite_key
    for fname in SECTION_NOTE_FILES:
        fpath = notes_dir / fname
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8")
        section = fname.replace(".md", "")
        for match in _GAP_INLINE_RE.finditer(text):
            markers.append({
                "description": match.group(1).strip(),
                "_source_file": fname,
                "_source": "inline_note",
                "section": section,
            })
    return markers


# ---------------------------------------------------------------------------
# Helpers: gap normalisation
# ---------------------------------------------------------------------------


def _normalize_gap_type(raw_type: Any) -> str:
    """Map a raw type value to one of C/X/E/S."""
    if isinstance(raw_type, str) and raw_type.upper() in VALID_GAP_TYPES:
        return raw_type.upper()
    # Heuristic keyword fallbacks
    lowered = str(raw_type).lower()
    if any(k in lowered for k in ("concept", "theoretical", "definition", "notation")):
        return GAP_TYPE_CONCEPTUAL
    if any(k in lowered for k in ("cross", "xref", "reference", "link")):
        return GAP_TYPE_XREF
    if any(k in lowered for k in ("empirical", "data", "experiment", "evidence", "real_data")):
        return GAP_TYPE_EMPIRICAL
    return GAP_TYPE_SCOPE


def _normalize_severity(raw: Any) -> str:
    if isinstance(raw, str) and raw.lower() in {"low", "medium", "high"}:
        return raw.lower()
    return "medium"


def _normalize_resolution_action(raw: Any) -> str:
    valid = {"read", "reread", "ask", "note", "verify_lean"}
    if isinstance(raw, str) and raw.lower() in valid:
        return raw.lower()
    return "note"


def _deduplicate_gaps(gaps: list[dict]) -> list[dict]:
    """Remove near-duplicate gaps by normalised description prefix."""
    seen: set[str] = set()
    result: list[dict] = []
    for g in gaps:
        key = re.sub(r"\s+", " ", g.get("description", "")).strip().lower()[:120]
        if key and key not in seen:
            seen.add(key)
            result.append(g)
    return result


def _build_gap_entry(idx: int, raw: dict, cite_key: str) -> dict:
    """Build a normalised gap entry from a raw marker or LLM-returned dict."""
    description = (
        raw.get("description")
        or raw.get("gap_description")
        or "(no description)"
    )
    gap_type = _normalize_gap_type(
        raw.get("gap_type") or raw.get("type") or GAP_TYPE_SCOPE
    )
    severity = _normalize_severity(raw.get("severity"))
    resolution_action = _normalize_resolution_action(raw.get("resolution_action"))
    # Infer section from source metadata if not explicitly set
    section = raw.get("section") or (
        raw.get("_source_file", "unknown")
        .replace(".json", "")
        .replace("_reading_output", "")
        .lstrip("_")
    )
    entry: dict[str, Any] = {
        "id": f"gap_{idx:03d}",
        "type": gap_type,
        "section": section,
        "description": description,
        "severity": severity,
        "resolution_action": resolution_action,
    }
    # Optional schema fields
    if "resolution_target" in raw:
        entry["resolution_target"] = raw["resolution_target"]
    if "linked_claims" in raw:
        entry["linked_claims"] = raw["linked_claims"]
    if "confidence_if_resolved" in raw:
        entry["confidence_if_resolved"] = raw["confidence_if_resolved"]
    return entry


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


def _call_llm_json(prompt: str, model: str) -> Optional[dict]:
    """Call the Anthropic API expecting a JSON response. Returns parsed dict or None."""
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
        lines = response_text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_text = "\n".join(lines)
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return None


def _build_xref_gap_prompt(
    cite_key: str,
    collected_gaps: list[dict],
    notes_context: str,
) -> str:
    gaps_json = json.dumps(collected_gaps[:30], indent=2, ensure_ascii=False)
    return f"""You are a research reading assistant performing the KNOWLEDGE GAP CONSOLIDATION PASS (Part 6 §6).

Paper: {cite_key}

## Already-collected gaps (sample)
{gaps_json}

## Per-section notes context (excerpt)
{notes_context[:3000]}

## Task
Identify NEW cross-referencing gaps that can only be seen from a full-paper perspective.
Examples:
- "Simulation tests Claim 3 but provides no evidence for Claim 2"
- "Assumption from theory section is never checked in real data analysis"
- "Method section introduces a parameter that disappears in experiments"

Output JSON only:
{{
  "xref_gaps": [
    {{
      "description": "<cross-referencing gap description>",
      "gap_type": "X",
      "severity": "low|medium|high",
      "resolution_action": "read|reread|ask|note|verify_lean",
      "linked_sections": ["<section1>", "<section2>"]
    }}
  ]
}}

If no cross-referencing gaps are found, return {{"xref_gaps": []}}."""


def _build_targeted_scan_prompt(cite_key: str, notes_context: str) -> str:
    return f"""You are a research reading assistant performing a TARGETED GAP SCAN (soft minimum check).

Paper: {cite_key}

## Notes context (excerpt)
{notes_context[:3000]}

## Task
Fewer than {SOFT_MINIMUM_GAP_COUNT} gaps were found in the first pass.
Scan the claims list, simulation notes, and theory notes carefully for any overlooked gaps.

Output JSON only:
{{
  "additional_gaps": [
    {{
      "description": "<gap description>",
      "gap_type": "C|X|E|S",
      "severity": "low|medium|high",
      "resolution_action": "read|reread|ask|note|verify_lean",
      "section": "<section name>"
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Core live consolidation logic
# ---------------------------------------------------------------------------


def _consolidate_gaps_live(
    cite_key: str,
    paper_dir: Path,
    vault_root: Path,
    model: str,
) -> tuple[list[dict], bool]:
    """Run full gap consolidation. Returns (gap_entries, soft_minimum_triggered)."""
    # Step 1: collect inline markers from reading output JSONs
    _, raw_markers = _collect_inline_markers_from_json(paper_dir)

    # Step 2: scan per-section Citadel notes for inline GAP: markers
    note_markers = _scan_notes_for_gap_markers(vault_root, cite_key)
    raw_markers.extend(note_markers)

    # Step 3: deduplicate
    raw_markers = _deduplicate_gaps(raw_markers)

    # Step 4: normalise into structured gap entries
    gap_entries = [_build_gap_entry(i + 1, m, cite_key) for i, m in enumerate(raw_markers)]

    # Load notes context for LLM calls (read-only)
    notes_dir = vault_root / "literature" / "papers" / cite_key
    notes_context_parts: list[str] = []
    for fname in SECTION_NOTE_FILES:
        fpath = notes_dir / fname
        if fpath.exists():
            notes_context_parts.append(fpath.read_text(encoding="utf-8")[:1000])
    notes_context = "\n\n".join(notes_context_parts)

    # Step 5: generate cross-referencing gaps via LLM
    xref_prompt = _build_xref_gap_prompt(cite_key, gap_entries, notes_context)
    xref_result = _call_llm_json(xref_prompt, model)
    if xref_result and isinstance(xref_result.get("xref_gaps"), list):
        for raw_xref in xref_result["xref_gaps"]:
            raw_xref.setdefault("gap_type", GAP_TYPE_XREF)
            gap_entries.append(_build_gap_entry(len(gap_entries) + 1, raw_xref, cite_key))
        gap_entries = _deduplicate_gaps(gap_entries)

    # Step 6: soft minimum check — if <3 gaps, do one additional targeted scan
    soft_minimum_triggered = False
    if len(gap_entries) < SOFT_MINIMUM_GAP_COUNT:
        soft_minimum_triggered = True
        scan_prompt = _build_targeted_scan_prompt(cite_key, notes_context)
        scan_result = _call_llm_json(scan_prompt, model)
        if scan_result and isinstance(scan_result.get("additional_gaps"), list):
            for raw_add in scan_result["additional_gaps"]:
                gap_entries.append(_build_gap_entry(len(gap_entries) + 1, raw_add, cite_key))
            gap_entries = _deduplicate_gaps(gap_entries)
        # If still <3 on second pass, that is acceptable (genuinely simple paper)

    return gap_entries, soft_minimum_triggered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_gap_consolidation(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 6.6: knowledge gap consolidation pass.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    skill_root:
        Root of the paper-reader skill directory; default ``skills/paper-reader``.
    model:
        Anthropic model ID; overridden by ``PAPER_READER_MODEL`` env var.
    dry_run:
        When True, validate inputs and return dispatch plan JSON; no file writes
        or LLM calls.

    Returns
    -------
    dict compatible with SubagentOutput contract, with keys:
        ``cite_key``, ``gaps_total``, ``gaps_by_type``, ``gaps_by_severity``,
        ``knowledge_gaps_file_used``, ``soft_minimum_check_triggered``,
        ``notes_written``, ``status``, ``flags``.
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))
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

    # Collect reading outputs and inline markers (for dry-run reporting too)
    found_files, raw_markers_preview = _collect_inline_markers_from_json(paper_dir)
    note_markers_preview = _scan_notes_for_gap_markers(vroot, cite_key)
    inline_markers_found = len(raw_markers_preview) + len(note_markers_preview)

    # Determine planned output paths from catalog pointer
    catalog = _load_catalog(bank_root, cite_key)
    knowledge_gaps_file_ptr: Optional[str] = (catalog.get("paper") or {}).get("knowledge_gaps_file")

    if knowledge_gaps_file_ptr:
        gaps_yaml_path = str(paper_dir / knowledge_gaps_file_ptr)
    else:
        # Pointer is null — will write _knowledge_gaps.yaml
        gaps_yaml_path = str(paper_dir / KNOWLEDGE_GAPS_YAML_NAME)

    consolidation_output_path = str(paper_dir / GAP_CONSOLIDATION_OUTPUT_JSON_NAME)
    outputs_planned = [gaps_yaml_path, consolidation_output_path]

    if dry_run:
        return {
            "cite_key": cite_key,
            "reading_outputs_found": found_files,
            "inline_markers_found": inline_markers_found,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
        }

    # -----------------------------------------------------------------------
    # Live run
    # -----------------------------------------------------------------------
    gap_entries, soft_minimum_triggered = _consolidate_gaps_live(
        cite_key=cite_key,
        paper_dir=paper_dir,
        vault_root=vroot,
        model=model,
    )

    # Determine the write path for the knowledge gaps YAML
    if knowledge_gaps_file_ptr:
        knowledge_gaps_out_path = paper_dir / knowledge_gaps_file_ptr
    else:
        knowledge_gaps_out_path = paper_dir / KNOWLEDGE_GAPS_YAML_NAME

    # Build _knowledge_gaps.yaml content (schema: cite_key, last_updated, gaps)
    gaps_yaml_content = {
        "cite_key": cite_key,
        "last_updated": datetime.now(tz=timezone.utc).isoformat(),
        "gaps": gap_entries,
    }

    knowledge_gaps_out_path.parent.mkdir(parents=True, exist_ok=True)
    with knowledge_gaps_out_path.open("w", encoding="utf-8") as fh:
        yaml.dump(gaps_yaml_content, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    knowledge_gaps_file_used = str(knowledge_gaps_out_path)

    # If catalog pointer was null, embed gaps in _catalog.yaml under paper.knowledge_gaps
    if not knowledge_gaps_file_ptr:
        catalog.setdefault("paper", {})["knowledge_gaps"] = gap_entries
        with catalog_path.open("w", encoding="utf-8") as fh:
            yaml.dump(catalog, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # Build summary statistics
    gaps_by_type = {t: 0 for t in VALID_GAP_TYPES}
    gaps_by_severity = {"low": 0, "medium": 0, "high": 0}
    for g in gap_entries:
        gaps_by_type[g["type"]] = gaps_by_type.get(g["type"], 0) + 1
        gaps_by_severity[g["severity"]] = gaps_by_severity.get(g["severity"], 0) + 1

    # Write _gap_consolidation_output.json
    consolidation_output: dict[str, Any] = {
        "cite_key": cite_key,
        "gaps_total": len(gap_entries),
        "gaps_by_type": gaps_by_type,
        "gaps_by_severity": gaps_by_severity,
        "knowledge_gaps_file_used": knowledge_gaps_file_used,
        "soft_minimum_check_triggered": soft_minimum_triggered,
    }
    consolidation_out_path = paper_dir / GAP_CONSOLIDATION_OUTPUT_JSON_NAME
    consolidation_out_path.write_text(
        json.dumps(consolidation_output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Build SubagentOutput-compatible return value
    notes_written = [str(knowledge_gaps_out_path), str(consolidation_out_path)]
    subagent_out = SubagentOutput(
        cite_key=cite_key,
        section_type="gap_consolidation",
        status="completed",
        notes_written=notes_written,
        catalog_updates={"knowledge_gaps_embedded": not bool(knowledge_gaps_file_ptr)},
        flags=["soft_minimum_check_triggered"] if soft_minimum_triggered else [],
        extra=consolidation_output,
    )

    return {
        **consolidation_output,
        "notes_written": subagent_out.notes_written,
        "status": subagent_out.status,
        "flags": subagent_out.flags,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 6.6: Knowledge Gap Consolidation — combine inline gap markers, "
            "classify by type (C/X/E/S), and emit _knowledge_gaps.yaml and "
            "_gap_consolidation_output.json."
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

    result = run_gap_consolidation(
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
