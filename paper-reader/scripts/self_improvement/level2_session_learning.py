"""
level2_session_learning.py — Level 2 session-end learning module.

Triggered only when feedback verdict is 'needs_revision'.
Maps feedback dimension scores to section-targeted constitution rule proposals,
appends them to reading-constitution-proposals.md with Source/Verdict markers,
and links proposals to _feedback.yaml via deterministic constitution_proposal IDs.
Preserves idempotence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

VERDICT_TRIGGER = "needs_revision"

# Score at-or-below this value triggers a proposal for that dimension.
_SCORE_THRESHOLD = 3

# Dimension → (section_tag, proposal_text)
_DIM_PROPOSALS: dict[str, tuple[str, str]] = {
    "faithfulness": (
        "R-FAITHFULNESS",
        "When faithfulness score is low, require explicit source-to-claim "
        "traceability: every output statement must cite the originating section "
        "note or synthesis pair so faithfulness checks can verify it.",
    ),
    "coverage": (
        "R-APPENDIX",
        "When coverage score is low, require a checklist pass over appendix and "
        "supplementary sections before finalizing output. Mandate explicit coverage "
        "of all named appendix subsections (e.g., identifiability, proofs).",
    ),
    "usefulness": (
        "R-SYNTHESIS",
        "When usefulness score is low, require synthesis pairs that connect model "
        "findings to practical implications. At least one actionable takeaway must "
        "be recorded per major finding.",
    ),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_proposal_id(cite_key: str, dimension: str, proposal_text: str) -> str:
    """Return a short deterministic proposal ID."""
    content = f"{cite_key}|{dimension}|{proposal_text}"
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:8]
    return f"l2-{cite_key}-{dimension}-{digest}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_candidates(
    cite_key: str,
    feedback: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Generate Level 2 candidates from feedback dimensions.

    Returns an empty list when verdict is not 'needs_revision'.
    Candidates carry deterministic constitution_proposal IDs for idempotent
    linking into _feedback.yaml.
    """
    if feedback.get("verdict") != VERDICT_TRIGGER:
        return []

    candidates: list[dict[str, Any]] = []
    for dimension, (section_tag, proposal_text) in _DIM_PROPOSALS.items():
        score = feedback.get(dimension)
        if not isinstance(score, (int, float)):
            continue
        if score <= _SCORE_THRESHOLD:
            prop_id = _make_proposal_id(cite_key, dimension, proposal_text)
            candidates.append(
                {
                    "constitution_proposal_id": prop_id,
                    "constitution_proposal": prop_id,
                    "dimension": dimension,
                    "score": score,
                    "section_tag": section_tag,
                    "proposal": proposal_text,
                    "Source": "level2-session-feedback",
                    "Verdict": VERDICT_TRIGGER,
                }
            )

    return candidates


def append_to_proposals(
    candidates: list[dict[str, Any]],
    proposals_path: str | Path,
    cite_key: str,
    revision_request: str = "",
) -> None:
    """
    Append *candidates* as markdown blocks to *proposals_path*.

    Idempotent: blocks whose constitution_proposal_id already appear in the
    file are skipped.
    """
    if not candidates:
        return

    proposals_path = Path(proposals_path)
    existing_text = ""
    if proposals_path.exists():
        existing_text = proposals_path.read_text(encoding="utf-8")

    header_written = False
    block_lines: list[str] = []

    for i, c in enumerate(candidates, 1):
        prop_id = c["constitution_proposal_id"]
        if prop_id in existing_text:
            continue

        if not header_written:
            block_lines.append(f"\n## Level 2 Session Feedback — {cite_key}\n\n")
            if revision_request:
                block_lines.append(f"**Revision request:** {revision_request}\n\n")
            header_written = True

        block_lines.append(f"### Candidate {i} — {prop_id}\n\n")
        block_lines.append(f"**Proposal:** {c['proposal']}\n\n")
        block_lines.append(f"- Source: level2-session-feedback\n")
        block_lines.append(f"- Verdict: needs_revision\n")
        block_lines.append(f"- Dimension: {c['dimension']} (score={c['score']})\n")
        block_lines.append(f"- Section: {c['section_tag']}\n")
        block_lines.append(f"- ProposalID: {prop_id}\n")
        block_lines.append("\n")

    if block_lines:
        with proposals_path.open("a", encoding="utf-8") as fh:
            fh.write("".join(block_lines))


def update_feedback_yaml(
    candidates: list[dict[str, Any]],
    work_dir: str | Path,
    cite_key: str,
    feedback: dict[str, Any],
) -> None:
    """
    Write or update *work_dir/_feedback.yaml* with constitution_proposal links.

    Idempotent: proposal entries already present (by ID) are not duplicated.
    """
    if not candidates:
        return

    work_dir = Path(work_dir)
    feedback_path = work_dir / "_feedback.yaml"

    if feedback_path.exists():
        try:
            data = yaml.safe_load(feedback_path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    else:
        data = {}

    existing_entries: list[dict[str, Any]] = data.get("level2_proposals", [])
    existing_ids: set[str] = {
        e.get("constitution_proposal_id", "")
        for e in existing_entries
        if isinstance(e, dict)
    }

    new_entries: list[dict[str, Any]] = []
    for c in candidates:
        prop_id = c["constitution_proposal_id"]
        if prop_id not in existing_ids:
            new_entries.append(
                {
                    "constitution_proposal": prop_id,
                    "constitution_proposal_id": prop_id,
                    "dimension": c["dimension"],
                    "section_tag": c["section_tag"],
                    "verdict": VERDICT_TRIGGER,
                    "source": "level2-session-feedback",
                }
            )

    if not new_entries:
        return

    if "level2_proposals" not in data:
        data["level2_proposals"] = []
    data["level2_proposals"].extend(new_entries)

    data.setdefault("cite_key", cite_key)
    data["last_verdict"] = feedback.get("verdict", "unknown")
    dim_keys = ("faithfulness", "coverage", "usefulness")
    data["last_feedback"] = {k: feedback[k] for k in dim_keys if k in feedback}

    feedback_path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
