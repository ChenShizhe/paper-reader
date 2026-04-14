#!/usr/bin/env python3
"""Step 6 §5.1 (Step 4) — Final Claim Verification module.

Evaluates final claim support against all collected evidence and annotates
``intro.md`` with a ``## Claim Verification Summary`` table and per-claim
status labels.

Importable API
--------------
    from claim_verifier import run_claim_verification
    result = run_claim_verification("smith2024neural")

CLI
---
    python3 claim_verifier.py --cite-key <key> [--dry-run]
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

# Output file name written to paper-bank/<cite_key>/
CLAIM_VERIFICATION_OUTPUT_JSON_NAME = "_claim_verification_output.json"

# Evidence source files read from paper-bank/<cite_key>/
EVIDENCE_FILES = [
    "_simulation_reading_output.json",
    "_real_data_reading_output.json",
    "_model_reading_output.json",
    "_method_reading_output.json",
    "_theory_reading_output.json",
    "_discussion_reading_output.json",
]

# Four canonical verification status labels
STATUS_SUPPORTED = "Supported"
STATUS_PARTIALLY_SUPPORTED = "Partially Supported"
STATUS_UNSUPPORTED = "Unsupported"
STATUS_UNCERTAIN = "Uncertain"

ALL_STATUSES = [STATUS_SUPPORTED, STATUS_PARTIALLY_SUPPORTED, STATUS_UNSUPPORTED, STATUS_UNCERTAIN]

# Section header for the verification table appended to intro.md
VERIFICATION_SECTION_HEADER = "## Claim Verification Summary"

# Regex to detect an existing status annotation on a claim line
_EXISTING_STATUS_RE = re.compile(
    r"\s*\*\*(?:Supported|Partially Supported|Unsupported|Uncertain)\*\*$"
)

# Regex to find numbered claim items in the ## Claimed Contributions section
_CLAIM_LINE_RE = re.compile(r"^(\s*\d+[\.\)]\s+.+)$")


# ---------------------------------------------------------------------------
# Helpers: evidence loading
# ---------------------------------------------------------------------------


def _load_evidence_sources(paper_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """Load all available evidence JSONs from *paper_dir*.

    Returns
    -------
    (found_files, evidence_data)
        found_files: list of filenames that existed and were loaded
        evidence_data: mapping of filename → parsed JSON dict
    """
    found_files: list[str] = []
    evidence_data: dict[str, Any] = {}
    for fname in EVIDENCE_FILES:
        fpath = paper_dir / fname
        if not fpath.exists():
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            found_files.append(fname)
            evidence_data[fname] = data
        except (json.JSONDecodeError, OSError):
            pass
    return found_files, evidence_data


def _load_high_severity_gaps(paper_dir: Path) -> list[dict]:
    """Load high-severity gaps from _knowledge_gaps.yaml or _catalog.yaml."""
    gaps: list[dict] = []
    gaps_yaml = paper_dir / "_knowledge_gaps.yaml"
    catalog_yaml = paper_dir / "_catalog.yaml"

    try:
        import yaml
    except ImportError:
        return gaps

    if gaps_yaml.exists():
        try:
            data = yaml.safe_load(gaps_yaml.read_text(encoding="utf-8")) or {}
            for g in data.get("gaps", []):
                if isinstance(g, dict) and g.get("severity") == "high":
                    gaps.append(g)
        except Exception:
            pass
    elif catalog_yaml.exists():
        try:
            data = yaml.safe_load(catalog_yaml.read_text(encoding="utf-8")) or {}
            paper = data.get("paper") or {}
            for g in paper.get("knowledge_gaps", []):
                if isinstance(g, dict) and g.get("severity") == "high":
                    gaps.append(g)
        except Exception:
            pass

    return gaps


# ---------------------------------------------------------------------------
# Helpers: intro.md parsing
# ---------------------------------------------------------------------------


def _parse_claims_from_intro(intro_path: Path) -> list[str]:
    """Extract numbered claim lines from the ``## Claimed Contributions`` section."""
    if not intro_path.exists():
        return []
    text = intro_path.read_text(encoding="utf-8")
    claims: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Claimed Contributions"):
            in_section = True
            continue
        if in_section:
            # Stop at next h2 or h1
            if re.match(r"^#{1,2}\s", stripped) and not stripped.startswith("## Claimed"):
                break
            if _CLAIM_LINE_RE.match(line):
                # Strip any existing status annotation for clean counting
                clean = _EXISTING_STATUS_RE.sub("", line).rstrip()
                claims.append(clean)
    return claims


# ---------------------------------------------------------------------------
# Helpers: heuristic claim status determination
# ---------------------------------------------------------------------------


def _extract_claim_support_signals(evidence_data: dict[str, Any]) -> dict[str, list[str]]:
    """Pull claim_support and contributions_verified fields from evidence JSONs.

    Returns a dict mapping signal type to list of string signals.
    """
    signals: dict[str, list[str]] = {
        "supporting": [],
        "limiting": [],
        "contradicting": [],
    }

    for fname, data in evidence_data.items():
        if not isinstance(data, dict):
            continue

        # _simulation_reading_output.json and _real_data_reading_output.json
        claim_support = data.get("claim_support")
        if isinstance(claim_support, list):
            for item in claim_support:
                if isinstance(item, str):
                    signals["supporting"].append(item)
                elif isinstance(item, dict):
                    verdict = str(item.get("verdict", "")).lower()
                    desc = item.get("description") or item.get("claim") or str(item)
                    if any(k in verdict for k in ("support", "confirm", "verified")):
                        signals["supporting"].append(desc)
                    elif any(k in verdict for k in ("partial", "limited", "mixed")):
                        signals["limiting"].append(desc)
                    elif any(k in verdict for k in ("unsupport", "refute", "contradict", "fail")):
                        signals["contradicting"].append(desc)
                    else:
                        signals["limiting"].append(desc)
        elif isinstance(claim_support, dict):
            for k, v in claim_support.items():
                signals["supporting"].append(f"{k}: {v}")

        # _model_reading_output.json
        contributions_verified = data.get("contributions_verified")
        if isinstance(contributions_verified, list):
            for item in contributions_verified:
                if isinstance(item, str):
                    signals["supporting"].append(item)
                elif isinstance(item, dict):
                    verified = item.get("verified")
                    if verified is True:
                        signals["supporting"].append(str(item.get("contribution", item)))
                    elif verified is False:
                        signals["contradicting"].append(str(item.get("contribution", item)))
                    else:
                        signals["limiting"].append(str(item.get("contribution", item)))

        # _discussion_reading_output.json: open questions and acknowledged limitations
        for key in ("open_questions", "limitations_acknowledged", "limitations"):
            items = data.get(key, [])
            if isinstance(items, list):
                for item in items:
                    signals["limiting"].append(str(item))

    return signals


def _determine_status_heuristic(
    claim_text: str,
    signals: dict[str, list[str]],
    high_severity_gaps: list[dict],
    evidence_found: bool,
) -> str:
    """Apply heuristic rules to assign a status label to a single claim.

    Rules (in priority order):
    1. No evidence found at all → Uncertain
    2. High-severity gaps linked to this claim → Uncertain
    3. Contradicting signals present with no supporting → Unsupported
    4. Supporting signals present, limiting signals also present → Partially Supported
    5. Supporting signals present, no limiting/contradicting → Supported
    6. Only limiting signals → Partially Supported
    7. Nothing → Uncertain
    """
    if not evidence_found:
        return STATUS_UNCERTAIN

    # Check for high-severity gaps referencing this claim
    claim_lower = claim_text.lower()
    for gap in high_severity_gaps:
        linked = gap.get("linked_claims", [])
        if isinstance(linked, list):
            for lc in linked:
                if str(lc).lower() in claim_lower or claim_lower in str(lc).lower():
                    return STATUS_UNCERTAIN
        if any(k in claim_lower for k in ("all", "every")):
            # Broad claims are downgraded when high-severity gaps exist
            return STATUS_UNCERTAIN

    n_supporting = len(signals["supporting"])
    n_limiting = len(signals["limiting"])
    n_contradicting = len(signals["contradicting"])
    total = n_supporting + n_limiting + n_contradicting

    if total == 0:
        return STATUS_UNCERTAIN
    if n_contradicting > 0 and n_supporting == 0:
        return STATUS_UNSUPPORTED
    if n_supporting > 0 and n_limiting == 0 and n_contradicting == 0:
        return STATUS_SUPPORTED
    if n_supporting > 0:
        return STATUS_PARTIALLY_SUPPORTED
    if n_limiting > 0:
        return STATUS_PARTIALLY_SUPPORTED
    return STATUS_UNCERTAIN




def _build_claim_verification_prompt(
    cite_key: str,
    claims: list[str],
    signals: dict[str, list[str]],
    high_severity_gaps: list[dict],
) -> str:
    claims_text = "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(claims))
    supporting_sample = "\n".join(f"  - {s}" for s in signals["supporting"][:10])
    limiting_sample = "\n".join(f"  - {s}" for s in signals["limiting"][:10])
    contradicting_sample = "\n".join(f"  - {s}" for s in signals["contradicting"][:5])
    gaps_sample = json.dumps(high_severity_gaps[:5], indent=2, ensure_ascii=False)

    return f"""You are a research reading assistant performing the FINAL CLAIM VERIFICATION PASS (Part 6 §5.1 Step 4).

Paper: {cite_key}

## Claimed Contributions
{claims_text}

## Supporting Evidence Signals (sample)
{supporting_sample or "  (none)"}

## Limiting / Mixed Evidence Signals (sample)
{limiting_sample or "  (none)"}

## Contradicting Evidence Signals (sample)
{contradicting_sample or "  (none)"}

## High-Severity Gaps
{gaps_sample}

## Task
For each numbered claim, assign exactly one of these status labels:
  - Supported — evidence across all sections consistently supports the claim
  - Partially Supported — some evidence supports the claim but with notable limitations
  - Unsupported — evidence contradicts or is absent for the claim
  - Uncertain — insufficient evidence to make a determination

Output JSON only:
{{
  "verifications": [
    {{
      "claim_index": 1,
      "status": "Supported|Partially Supported|Unsupported|Uncertain",
      "evidence_sources": ["list of relevant source file names"],
      "notes": "brief reasoning"
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Helpers: intro.md annotation
# ---------------------------------------------------------------------------


def _annotate_intro_md(
    intro_path: Path,
    claims: list[str],
    verification_table: list[dict],
    cite_key: str,
) -> None:
    """Read intro.md, annotate claimed contributions, and append/replace the
    ``## Claim Verification Summary`` section. Writes modified content back."""
    text = intro_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Build lookup: stripped claim → status
    status_by_claim: dict[str, str] = {}
    for entry in verification_table:
        idx = entry.get("claim_index", 0) - 1
        if 0 <= idx < len(claims):
            key = _EXISTING_STATUS_RE.sub("", claims[idx]).strip()
            status_by_claim[key] = entry.get("status", STATUS_UNCERTAIN)

    # Annotate existing claim lines in ## Claimed Contributions
    in_section = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Claimed Contributions"):
            in_section = True
            new_lines.append(line)
            continue
        if in_section and re.match(r"^#{1,2}\s", stripped) and not stripped.startswith("## Claimed"):
            in_section = False

        if in_section and _CLAIM_LINE_RE.match(line.rstrip("\n\r")):
            # Strip any existing label, then append new one
            clean = _EXISTING_STATUS_RE.sub("", line.rstrip("\n\r"))
            key = clean.strip()
            status = status_by_claim.get(key)
            if status:
                line = f"{clean} **{status}**\n"
        new_lines.append(line)

    annotated = "".join(new_lines)

    # Remove existing ## Claim Verification Summary section if present
    summary_pattern = re.compile(
        r"\n" + re.escape(VERIFICATION_SECTION_HEADER) + r".*?(?=\n## |\Z)",
        re.DOTALL,
    )
    annotated = summary_pattern.sub("", annotated).rstrip()

    # Build the verification summary table
    table_lines = [
        "",
        "",
        VERIFICATION_SECTION_HEADER,
        "",
        f"*Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')} — cite_key: {cite_key}*",
        "",
        "| # | Claim | Status | Evidence Sources | Notes |",
        "|---|-------|--------|-----------------|-------|",
    ]
    for entry in verification_table:
        idx = entry.get("claim_index", "?")
        claim_text = entry.get("claim_text", "")[:80].replace("|", "\\|")
        status = entry.get("status", STATUS_UNCERTAIN)
        sources = ", ".join(entry.get("evidence_sources", []))
        notes = (entry.get("notes") or "").replace("|", "\\|")
        table_lines.append(f"| {idx} | {claim_text} | **{status}** | {sources} | {notes} |")

    annotated += "\n".join(table_lines)
    intro_path.write_text(annotated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Core live verification logic
# ---------------------------------------------------------------------------


def _run_verification_live(
    cite_key: str,
    paper_dir: Path,
    vault_root: Path,
    model: str,
) -> tuple[list[dict], list[str]]:
    """Run full claim verification. Returns (verification_table, notes_written)."""
    intro_path = vault_root / "literature" / "papers" / cite_key / "intro.md"
    claims = _parse_claims_from_intro(intro_path)

    found_files, evidence_data = _load_evidence_sources(paper_dir)
    high_severity_gaps = _load_high_severity_gaps(paper_dir)
    signals = _extract_claim_support_signals(evidence_data)
    evidence_found = bool(found_files)

    # LLM-assisted verification skipped — requires subagent dispatch
    llm_verifications: dict[int, dict] = {}

    # Build verification table: merge LLM result with heuristic fallback
    verification_table: list[dict] = []
    for i, claim_text in enumerate(claims):
        claim_idx = i + 1
        llm_v = llm_verifications.get(claim_idx)
        if llm_v and llm_v.get("status") in ALL_STATUSES:
            status = llm_v["status"]
            sources = llm_v.get("evidence_sources", found_files)
            notes = llm_v.get("notes", "")
        else:
            status = _determine_status_heuristic(
                claim_text, signals, high_severity_gaps, evidence_found
            )
            sources = found_files
            notes = "heuristic determination"
        verification_table.append({
            "claim_index": claim_idx,
            "claim_text": claim_text.strip(),
            "status": status,
            "evidence_sources": sources,
            "notes": notes,
        })

    # Annotate intro.md (read-write; other Citadel files untouched)
    notes_written: list[str] = []
    if intro_path.exists():
        _annotate_intro_md(intro_path, claims, verification_table, cite_key)
        notes_written.append(str(intro_path))

    # Write _claim_verification_output.json to paper-bank
    claims_by_status = {s: 0 for s in ALL_STATUSES}
    for entry in verification_table:
        st = entry.get("status", STATUS_UNCERTAIN)
        if st in claims_by_status:
            claims_by_status[st] += 1

    claim_verification_output = {
        "cite_key": cite_key,
        "claims_total": len(claims),
        "claims_by_status": claims_by_status,
        "verification_table": verification_table,
    }
    output_path = paper_dir / CLAIM_VERIFICATION_OUTPUT_JSON_NAME
    output_path.write_text(
        json.dumps(claim_verification_output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    notes_written.append(str(output_path))

    return verification_table, notes_written


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_claim_verification(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 6 §5.1 (Step 4): final claim verification pass.

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
        When True, validate inputs and return dispatch plan JSON; no file
        writes or LLM calls.

    Returns
    -------
    dict compatible with SubagentOutput contract.
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))
    paper_dir = bank_root / cite_key
    catalog_path = paper_dir / "_catalog.yaml"
    intro_path = vroot / "literature" / "papers" / cite_key / "intro.md"

    # Validate that the paper-bank entry exists
    if not paper_dir.exists() or not catalog_path.exists():
        print(
            f"Error: paper-bank entry not found for cite_key '{cite_key}'. "
            f"Expected directory: {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Collect available evidence sources for dry-run reporting
    found_files, _evidence_data = _load_evidence_sources(paper_dir)
    claims = _parse_claims_from_intro(intro_path)

    # Determine planned outputs
    output_json_path = str(paper_dir / CLAIM_VERIFICATION_OUTPUT_JSON_NAME)
    outputs_planned = [output_json_path, str(intro_path)]

    inputs_valid = (
        paper_dir.exists()
        and catalog_path.exists()
        and bool(found_files or intro_path.exists())
    )

    if dry_run:
        return {
            "cite_key": cite_key,
            "claims_found": len(claims),
            "evidence_sources_found": found_files,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
        }

    # -----------------------------------------------------------------------
    # Live run
    # -----------------------------------------------------------------------
    verification_table, notes_written = _run_verification_live(
        cite_key=cite_key,
        paper_dir=paper_dir,
        vault_root=vroot,
        model=model,
    )

    claims_by_status = {s: 0 for s in ALL_STATUSES}
    for entry in verification_table:
        st = entry.get("status", STATUS_UNCERTAIN)
        if st in claims_by_status:
            claims_by_status[st] += 1

    subagent_out = SubagentOutput(
        cite_key=cite_key,
        section_type="claim_verification",
        status="completed",
        notes_written=notes_written,
        catalog_updates={},
        flags=[],
        extra={
            "claims_total": len(verification_table),
            "claims_by_status": claims_by_status,
        },
    )

    return {
        "cite_key": subagent_out.cite_key,
        "claims_total": len(verification_table),
        "claims_by_status": claims_by_status,
        "verification_table": verification_table,
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
            "Step 6 §5.1 (Step 4): Final Claim Verification — evaluate claim support "
            "across all evidence sources and annotate intro.md with status labels."
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

    result = run_claim_verification(
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
