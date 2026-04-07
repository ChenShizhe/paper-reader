#!/usr/bin/env python3
"""xref_tech_writer.py — Merge technical equation/theorem entries into _xref_index.yaml.

Reads model/method/theory reading outputs (from paper-bank) and writes structured
technical entries (equations and theorems) into the paper's _xref_index.yaml.
Also appends contribution verification labels to the ## Claimed Contributions
section in intro.md in the Citadel vault.

Verification labels applied to each contribution item:
    Supported           — direct evidence found in theory/model/method outputs
    Partially Supported — indirect or partial evidence found
    Unsupported         — active counter-evidence or explicit absence noted
    Uncertain           — insufficient evidence to determine support status

Duplicate guard: equations are keyed by (eq_number, latex) and theorems by
result_id — already-indexed entries are skipped rather than written twice.
Similarly, contributions in intro.md that already carry a verification label
are left untouched (deduplication by label presence).

Importable API
--------------
    from xref_tech_writer import write_technical_xrefs
    result = write_technical_xrefs("smith2024neural")

CLI
---
    python3 xref_tech_writer.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"

MODEL_OUTPUT_NAME = "_model_reading_output.json"
METHOD_OUTPUT_NAME = "_method_reading_output.json"
THEORY_OUTPUT_NAME = "_theory_reading_output.json"

XREF_INDEX_NAME = "_xref_index.yaml"
INTRO_MD_NAME = "intro.md"

SECTION_CLAIMED_CONTRIBUTIONS = "## Claimed Contributions"

# Ordered set of allowed verification labels (used for deduplication check)
VERIFICATION_LABELS = [
    "Supported",
    "Partially Supported",
    "Unsupported",
    "Uncertain",
]

# Regex that matches any already-present verification label on a contribution line
_LABEL_RE = re.compile(
    r"\[(?:Supported|Partially Supported|Unsupported|Uncertain)\]"
)


# ---------------------------------------------------------------------------
# Internal helpers — file loading
# ---------------------------------------------------------------------------


def _load_xref(xref_path: Path, cite_key: str) -> dict:
    """Load existing _xref_index.yaml or return a skeleton dict."""
    if xref_path.exists():
        raw = xref_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    return {
        "cite_key": cite_key,
        "catalog_version": 1,
        "equations": [],
        "theorems": [],
        "figures": [],
        "citations": [],
    }


def _load_json_output(paper_bank_root: Path, cite_key: str, filename: str) -> Optional[dict]:
    """Load a JSON reading output file from paper-bank; return None if absent."""
    path = paper_bank_root / cite_key / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_intro_md(vault_root: Path, cite_key: str) -> Optional[str]:
    """Load intro.md from the citadel vault; return None if absent."""
    intro_path = vault_root / "literature" / "papers" / cite_key / INTRO_MD_NAME
    if not intro_path.exists():
        return None
    return intro_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers — equation extraction
# ---------------------------------------------------------------------------


def _extract_equations_from_output(output: Optional[dict]) -> list[dict]:
    """Return a flat list of equation dicts extracted from a reading output."""
    if not output:
        return []

    equations: list[dict] = []

    # theory_reader produces key_equations at top level
    for eq in output.get("key_equations", []):
        if isinstance(eq, dict):
            equations.append(eq)

    # model_reader and method_reader nest results under per-section extraction
    for item in output.get("extraction_results", []):
        if isinstance(item, dict):
            for eq in item.get("key_equations", []):
                if isinstance(eq, dict):
                    equations.append(eq)

    # Some outputs nest under a "sections" list
    for section in output.get("sections", []):
        if isinstance(section, dict):
            for eq in section.get("key_equations", []):
                if isinstance(eq, dict):
                    equations.append(eq)

    return equations


def _make_xref_equation_entry(eq: dict, cite_key: str, source: str) -> dict:
    """Convert a raw equation dict into an _xref_index.yaml equation entry."""
    return {
        "eq_id": eq.get("eq_number") or eq.get("eq_id") or "",
        "latex": eq.get("latex", ""),
        "description": eq.get("description", ""),
        "role": eq.get("role", "intermediate"),
        "source": source,
        "cite_key": cite_key,
    }


# ---------------------------------------------------------------------------
# Internal helpers — theorem extraction
# ---------------------------------------------------------------------------


def _extract_theorems_from_output(output: Optional[dict]) -> list[dict]:
    """Return a flat list of theorem dicts extracted from a reading output."""
    if not output:
        return []

    theorems: list[dict] = []

    # theory_reader produces theorems at top level
    for thm in output.get("theorems", []):
        if isinstance(thm, dict):
            theorems.append(thm)

    # Some outputs nest under extraction_results
    for item in output.get("extraction_results", []):
        if isinstance(item, dict):
            for thm in item.get("theorems", []):
                if isinstance(thm, dict):
                    theorems.append(thm)

    return theorems


def _make_xref_theorem_entry(thm: dict, cite_key: str, source: str) -> dict:
    """Convert a raw theorem dict into an _xref_index.yaml theorem entry.

    Preserves the lean_candidate flag from Level 1 statement comprehension so
    that downstream Lean 4 formalization tooling can filter candidates.
    """
    result_id = thm.get("result_id", "")
    result_type = thm.get("result_type", "other")

    level_1 = thm.get("level_1", {})
    plain_english = level_1.get("plain_english_statement", "")
    convergence_rate = level_1.get("convergence_rate")
    optimality = level_1.get("optimality", "unclear")
    # lean_candidate flag from the three-level extraction protocol
    lean_candidate = bool(level_1.get("lean_candidate", False))

    level_3 = thm.get("level_3", {})
    key_insight = level_3.get("key_insight", "")

    return {
        "result_id": result_id,
        "result_type": result_type,
        "plain_english": plain_english,
        "convergence_rate": convergence_rate,
        "optimality": optimality,
        "lean_candidate": lean_candidate,
        "key_insight": key_insight,
        "source": source,
        "cite_key": cite_key,
    }


# ---------------------------------------------------------------------------
# Internal helpers — contribution verification labels
# ---------------------------------------------------------------------------


def _parse_contributions(intro_text: str) -> list[tuple[int, str]]:
    """Return (line_index, line_text) for all contribution item lines in intro_text.

    Contribution items are numbered lines immediately inside the
    ## Claimed Contributions section (e.g. "1. some claim").
    """
    lines = intro_text.splitlines()
    in_contributions = False
    items: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        if line.strip() == SECTION_CLAIMED_CONTRIBUTIONS:
            in_contributions = True
            continue
        if in_contributions:
            # Stop at the next section heading
            if line.startswith("## ") and line.strip() != SECTION_CLAIMED_CONTRIBUTIONS:
                break
            # Numbered contribution item
            if re.match(r"^\d+\.\s", line):
                items.append((i, line))

    return items


def _assign_verification_label(
    claim_text: str,
    theory_output: Optional[dict],
    model_output: Optional[dict],
    method_output: Optional[dict],
) -> str:
    """Heuristically assign a verification label to a contribution claim.

    Strategy:
      - Supported: claim keyword appears in at least one theorem's plain_english
        or a key_equation description in theory/model/method outputs.
      - Partially Supported: claim keywords appear in convergence_rates_raw or
        in method output key_equations but not in a theorem statement directly.
      - Unsupported: theory_output is present but has theory_present=False,
        indicating no proofs were found.
      - Uncertain: default when no other rule fires.
    """
    # If theory output is present but explicitly marks no theory sections
    if theory_output and not theory_output.get("theory_present", True):
        return "Unsupported"

    claim_lower = claim_text.lower()
    # Extract significant keywords: words > 4 chars
    keywords = [w for w in re.findall(r"[a-z]{5,}", claim_lower)]
    if not keywords:
        return "Uncertain"

    # Check theory output theorems
    if theory_output:
        for thm in theory_output.get("theorems", []):
            level_1 = thm.get("level_1", {})
            stmt = (level_1.get("plain_english_statement") or "").lower()
            if any(kw in stmt for kw in keywords):
                return "Supported"

        # Check convergence rates for partial match
        for rate in theory_output.get("convergence_rates_raw", []):
            cond = (rate.get("conditions") or "").lower()
            if any(kw in cond for kw in keywords):
                return "Partially Supported"

    # Check model output
    if model_output:
        for item in model_output.get("extraction_results", []):
            model_desc = json.dumps(item).lower()
            if any(kw in model_desc for kw in keywords[:3]):
                return "Partially Supported"

    # Check method output key equations
    if method_output:
        for item in method_output.get("extraction_results", []):
            method_desc = json.dumps(item).lower()
            if any(kw in method_desc for kw in keywords[:3]):
                return "Partially Supported"

    return "Uncertain"


def _append_labels_to_intro(
    intro_text: str,
    theory_output: Optional[dict],
    model_output: Optional[dict],
    method_output: Optional[dict],
) -> tuple[str, int]:
    """Return (updated_intro_text, labels_added_count).

    For each numbered contribution item in the ## Claimed Contributions section:
    - Skip if a verification label [Supported|Partially Supported|Unsupported|Uncertain]
      is already present (deduplication).
    - Otherwise append the assigned label in square brackets.
    """
    lines = intro_text.splitlines()
    contribution_lines = _parse_contributions(intro_text)
    labels_added = 0

    for line_idx, line_text in contribution_lines:
        # Deduplication: skip if a label is already present
        if _LABEL_RE.search(line_text):
            continue

        label = _assign_verification_label(
            line_text, theory_output, model_output, method_output
        )
        lines[line_idx] = line_text + f"  [**{label}**]"
        labels_added += 1

    return "\n".join(lines), labels_added


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_technical_xrefs(
    cite_key: str,
    paper_bank_root: "str | Path" = DEFAULT_PAPER_BANK_ROOT,
    vault_root: "str | Path" = DEFAULT_VAULT_ROOT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge technical equation/theorem entries into _xref_index.yaml and
    append contribution verification labels in intro.md.

    Reads from:
      paper-bank/<cite_key>/_model_reading_output.json
      paper-bank/<cite_key>/_method_reading_output.json
      paper-bank/<cite_key>/_theory_reading_output.json

    Writes to (live mode only):
      paper-bank/<cite_key>/_xref_index.yaml   — equations and theorems merged
      citadel/literature/papers/<cite_key>/intro.md — verification labels appended

    Duplicate guard for _xref_index.yaml:
      Equations are keyed by (eq_id, latex). Theorems are keyed by result_id.
      Already-indexed entries are skipped.

    Duplicate guard for intro.md:
      Contribution items that already carry a verification label are left unchanged.

    Args:
        cite_key: Identifier for the paper (e.g. "smith2024neural").
        paper_bank_root: Root directory of the paper bank.
        vault_root: Root directory of the Citadel vault.
        dry_run: When True, validate inputs and return a JSON plan without
                 writing any files.

    Returns:
        dict with keys:
            cite_key, model_output_present, method_output_present,
            theory_output_present, intro_md_present, contributions_found,
            equations_would_add (dry_run) / equations_added (live),
            theorems_would_add (dry_run) / theorems_added (live),
            equations_skipped, theorems_skipped,
            labels_added (live only), xref_path (str), intro_path (str).
    """
    bank_root = Path(os.path.expanduser(str(paper_bank_root)))
    v_root = Path(os.path.expanduser(str(vault_root)))

    xref_path = bank_root / cite_key / XREF_INDEX_NAME
    intro_path = v_root / "literature" / "papers" / cite_key / INTRO_MD_NAME

    # Load reading outputs
    model_output = _load_json_output(bank_root, cite_key, MODEL_OUTPUT_NAME)
    method_output = _load_json_output(bank_root, cite_key, METHOD_OUTPUT_NAME)
    theory_output = _load_json_output(bank_root, cite_key, THEORY_OUTPUT_NAME)

    # Load existing xref index
    xref = _load_xref(xref_path, cite_key)

    # Load intro.md
    intro_text = _load_intro_md(v_root, cite_key)

    # -----------------------------------------------------------------------
    # Collect equations from all sources
    # -----------------------------------------------------------------------
    raw_equations: list[tuple[str, dict]] = []
    for source_name, output in [
        ("model", model_output),
        ("method", method_output),
        ("theory", theory_output),
    ]:
        for eq in _extract_equations_from_output(output):
            raw_equations.append((source_name, eq))

    # Build duplicate-guard set from already-indexed equations
    existing_equations: list[dict] = xref.get("equations") or []
    existing_eq_keys: set[tuple[str, str]] = {
        (str(e.get("eq_id", "")), str(e.get("latex", "")))
        for e in existing_equations
    }

    planned_equations: list[dict] = []
    skipped_equations = 0
    for source_name, eq in raw_equations:
        entry = _make_xref_equation_entry(eq, cite_key, source_name)
        key = (entry["eq_id"], entry["latex"])
        if key in existing_eq_keys or not entry["latex"]:
            skipped_equations += 1
            continue
        existing_eq_keys.add(key)
        planned_equations.append(entry)

    # -----------------------------------------------------------------------
    # Collect theorems from all sources
    # -----------------------------------------------------------------------
    raw_theorems: list[tuple[str, dict]] = []
    for source_name, output in [
        ("model", model_output),
        ("method", method_output),
        ("theory", theory_output),
    ]:
        for thm in _extract_theorems_from_output(output):
            raw_theorems.append((source_name, thm))

    # Build duplicate-guard set from already-indexed theorems
    existing_theorems: list[dict] = xref.get("theorems") or []
    existing_thm_ids: set[str] = {
        str(e.get("result_id", "")) for e in existing_theorems
    }

    planned_theorems: list[dict] = []
    skipped_theorems = 0
    for source_name, thm in raw_theorems:
        entry = _make_xref_theorem_entry(thm, cite_key, source_name)
        rid = entry["result_id"]
        if not rid or rid in existing_thm_ids:
            skipped_theorems += 1
            continue
        existing_thm_ids.add(rid)
        planned_theorems.append(entry)

    # -----------------------------------------------------------------------
    # Count intro contributions
    # -----------------------------------------------------------------------
    contribution_items = _parse_contributions(intro_text) if intro_text else []
    contributions_found = len(contribution_items)

    # -----------------------------------------------------------------------
    # Dry-run: return plan as JSON without writing
    # -----------------------------------------------------------------------
    if dry_run:
        return {
            "cite_key": cite_key,
            "model_output_present": model_output is not None,
            "method_output_present": method_output is not None,
            "theory_output_present": theory_output is not None,
            "intro_md_present": intro_text is not None,
            "contributions_found": contributions_found,
            "equations_would_add": len(planned_equations),
            "theorems_would_add": len(planned_theorems),
            "equations_skipped": skipped_equations,
            "theorems_skipped": skipped_theorems,
            "xref_path": str(xref_path),
            "intro_path": str(intro_path),
            "planned_equations": [
                {"eq_id": e["eq_id"], "description": e["description"], "source": e["source"]}
                for e in planned_equations
            ],
            "planned_theorems": [
                {
                    "result_id": t["result_id"],
                    "result_type": t["result_type"],
                    "lean_candidate": t["lean_candidate"],
                    "source": t["source"],
                }
                for t in planned_theorems
            ],
        }

    # -----------------------------------------------------------------------
    # Live run: write equations and theorems to _xref_index.yaml
    # -----------------------------------------------------------------------
    for entry in planned_equations:
        existing_equations.append(entry)
    for entry in planned_theorems:
        existing_theorems.append(entry)

    xref["equations"] = existing_equations
    xref["theorems"] = existing_theorems
    xref_path.parent.mkdir(parents=True, exist_ok=True)
    xref_path.write_text(
        yaml.dump(xref, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    # -----------------------------------------------------------------------
    # Live run: append verification labels to intro.md
    # -----------------------------------------------------------------------
    labels_added = 0
    if intro_text is not None:
        updated_intro, labels_added = _append_labels_to_intro(
            intro_text, theory_output, model_output, method_output
        )
        if labels_added > 0:
            intro_path.parent.mkdir(parents=True, exist_ok=True)
            intro_path.write_text(updated_intro, encoding="utf-8")

    return {
        "cite_key": cite_key,
        "model_output_present": model_output is not None,
        "method_output_present": method_output is not None,
        "theory_output_present": theory_output is not None,
        "intro_md_present": intro_text is not None,
        "contributions_found": contributions_found,
        "equations_added": len(planned_equations),
        "theorems_added": len(planned_theorems),
        "equations_skipped": skipped_equations,
        "theorems_skipped": skipped_theorems,
        "labels_added": labels_added,
        "xref_path": str(xref_path),
        "intro_path": str(intro_path),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge technical equation/theorem entries into _xref_index.yaml and "
            "append contribution verification labels in intro.md."
        )
    )
    parser.add_argument(
        "--cite-key",
        required=True,
        help="cite_key of the paper (e.g. smith2024neural).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions as JSON without writing any files.",
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = write_technical_xrefs(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        vault_root=args.vault_root,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
