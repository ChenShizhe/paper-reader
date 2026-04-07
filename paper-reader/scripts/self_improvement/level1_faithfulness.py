"""
level1_faithfulness.py — Level 1 faithfulness-to-proposal mapper.

Reads a _faithfulness_report.json and converts flagged claims into
low-confidence constitution improvement candidates for
reading-constitution-proposals.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Reason-code → proposal text mapping
# ---------------------------------------------------------------------------

_REASON_PROPOSALS: dict[str, str] = {
    "no_trace_to_section_notes_or_synthesis_pairs": (
        "Add traceability requirement: every output claim must link to a "
        "section note or synthesis pair so faithfulness checks can verify it."
    ),
}

_DEFAULT_PROPOSAL_TEMPLATE = (
    "Review reading-constitution guidance for claim flagged as '{reason}': "
    "{claim_snippet}"
)


def _flag_to_proposal_text(flag: dict[str, Any]) -> str:
    reason = flag.get("reason", "unknown")
    claim = flag.get("claim_snippet", "")
    if reason in _REASON_PROPOSALS:
        return _REASON_PROPOSALS[reason]
    return _DEFAULT_PROPOSAL_TEMPLATE.format(
        reason=reason,
        claim_snippet=claim[:120],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_candidates(
    faithfulness_report_path: str | Path | None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Parse *faithfulness_report_path* and return ``(candidates, flags_processed)``.

    Returns ``([], 0)`` gracefully when the path is None or the file does not
    exist.  Deduplicates candidates by proposal text.
    """
    if faithfulness_report_path is None:
        return [], 0

    path = Path(faithfulness_report_path)
    if not path.exists():
        return [], 0

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], 0

    flags: list[dict[str, Any]] = report.get("flags", [])
    flags_processed = len(flags)

    seen_proposals: set[str] = set()
    candidates: list[dict[str, Any]] = []

    for flag in flags:
        proposal_text = _flag_to_proposal_text(flag)
        if proposal_text in seen_proposals:
            continue
        seen_proposals.add(proposal_text)
        candidates.append(
            {
                "proposal": proposal_text,
                "source_claim": flag.get("claim_snippet", ""),
                "flag_reason": flag.get("reason", "unknown"),
                "Source": "level1-faithfulness",
                "Confidence": "low",
            }
        )

    return candidates, flags_processed


def append_to_proposals(
    candidates: list[dict[str, Any]],
    proposals_path: str | Path,
    cite_key: str,
) -> None:
    """Append *candidates* as markdown blocks to *proposals_path*."""
    if not candidates:
        return

    proposals_path = Path(proposals_path)
    block_lines: list[str] = [
        f"\n## Level 1 Faithfulness — {cite_key}\n\n",
    ]
    for i, c in enumerate(candidates, 1):
        block_lines.append(f"### Candidate {i}\n\n")
        block_lines.append(f"**Proposal:** {c['proposal']}\n\n")
        block_lines.append(f"- Source: level1-faithfulness\n")
        block_lines.append(f"- Confidence: low\n")
        block_lines.append(f"- Flag reason: `{c['flag_reason']}`\n")
        if c.get("source_claim"):
            block_lines.append(f"- Source claim: {c['source_claim']}\n")
        block_lines.append("\n")

    with proposals_path.open("a", encoding="utf-8") as fh:
        fh.write("".join(block_lines))
