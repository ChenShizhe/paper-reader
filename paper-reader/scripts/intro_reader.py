#!/usr/bin/env python3
"""Step 4.2 — Introduction block classification and structured extraction.

Reads all introduction segments for a paper, classifies each paragraph block
using the INTRODUCTION READING PROTOCOL (R-INTRO rules from the reading
constitution), extracts citation metadata, and appends structured output
sections to the paper's intro.md in the Citadel vault.

Block types classified: BACKGROUND, PRIOR_WORK, GAP, CONTRIBUTION, MOTIVATION, ROADMAP

Importable API
--------------
    from intro_reader import run_step42
    result = run_step42("smith2024neural")

CLI
---
    python3 intro_reader.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from subagent_contracts import SubagentInput, SubagentOutput  # noqa: F401

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Six block classification types from §4.1 of the INTRODUCTION READING PROTOCOL
BLOCK_TYPES = ["BACKGROUND", "PRIOR_WORK", "GAP", "CONTRIBUTION", "MOTIVATION", "ROADMAP"]

# Citation role types and their dummy_eligible values
CITATION_ROLES = {
    "foundation": True,   # dummy_eligible
    "alternative": True,  # dummy_eligible
    "background": False,  # not dummy_eligible
}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

# Output section headers appended to intro.md
SECTION_INTRO_NOTES = "## Intro Notes"
SECTION_CLAIMED_CONTRIBUTIONS = "## Claimed Contributions"
SECTION_MOTIVATING_CHALLENGES = "## Motivating Challenges"
SECTION_AUTHOR_KEYWORDS = "## Author Keywords"

PLANNED_SECTIONS = [SECTION_INTRO_NOTES, SECTION_CLAIMED_CONTRIBUTIONS, SECTION_MOTIVATING_CHALLENGES, SECTION_AUTHOR_KEYWORDS]


# ---------------------------------------------------------------------------
# YAML frontmatter helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict:
    """Return YAML frontmatter dict from *text*, or {} if absent/invalid."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    raw = "\n".join(lines[1:end])
    try:
        result = yaml.safe_load(raw)
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def _strip_frontmatter(text: str) -> str:
    """Return *text* with YAML frontmatter removed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


# ---------------------------------------------------------------------------
# Introduction segment loader
# ---------------------------------------------------------------------------

def _find_intro_segments(paper_bank_root: Path, cite_key: str) -> list[dict]:
    """Return all segments with section_type: introduction, sorted by filename.

    Each entry: {path, text, frontmatter}
    """
    seg_dir = paper_bank_root / cite_key / "segments"
    if not seg_dir.exists():
        return []

    segments = []
    for seg_path in sorted(seg_dir.glob("*.md")):
        try:
            text = seg_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if fm.get("section_type") == "introduction":
            segments.append({"path": seg_path, "text": text, "frontmatter": fm})

    return segments


# ---------------------------------------------------------------------------
# Reading constitution loader (R-INTRO rules)
# ---------------------------------------------------------------------------

def _load_r_intro_rules(skill_root: Path) -> str:
    """Load the R-INTRO section from reading-constitution.md.

    Returns the raw text of the R-INTRO block, or a fallback message if the
    file cannot be found.
    """
    constitution_path = skill_root / "reading-constitution.md"
    if not constitution_path.exists():
        return "(reading-constitution.md not found — R-INTRO rules unavailable)"

    text = constitution_path.read_text(encoding="utf-8")

    # Extract from "## Section: Introduction (R-INTRO)" to the next "## Section:" heading
    lines = text.splitlines()
    in_section = False
    collected: list[str] = []
    for line in lines:
        if "R-INTRO" in line and line.startswith("## Section:"):
            in_section = True
            collected.append(line)
            continue
        if in_section:
            if line.startswith("## Section:") and "R-INTRO" not in line:
                break
            collected.append(line)

    if collected:
        return "\n".join(collected)
    return "(R-INTRO section not found in reading-constitution.md)"


# ---------------------------------------------------------------------------
# Paragraph splitter
# ---------------------------------------------------------------------------

def _split_into_paragraphs(text: str) -> list[str]:
    """Split *text* into non-empty paragraphs (double-newline separated)."""
    paragraphs = [p.strip() for p in text.split("\n\n")]
    return [p for p in paragraphs if p]


# ---------------------------------------------------------------------------
# LLM call — block classification and citation extraction
# ---------------------------------------------------------------------------

def _build_block_classification_prompt(
    cite_key: str,
    segment_id: str,
    paragraph_text: str,
    r_intro_rules: str,
) -> str:
    """Build prompt for classifying one paragraph block."""
    block_types_str = ", ".join(BLOCK_TYPES)
    return f"""You are a research reading assistant following the INTRODUCTION READING PROTOCOL.

## R-INTRO Rules (from reading-constitution.md)
{r_intro_rules}

## Task
Classify the following paragraph from the introduction of paper `{cite_key}` (segment: {segment_id}).

Block types: {block_types_str}

For each citation in the paragraph, extract:
- author_year: string like "Chen et al. 2018" or bare "Smith 2020"
- role: one of foundation | alternative | background
- importance: one of high | medium | low
- description: one sentence describing what the cited work contributes

## Paragraph
{paragraph_text}

## Output Format (JSON only, no prose)
{{
  "block_type": "<one of {block_types_str}>",
  "summary": "<one sentence summary of this paragraph's contribution to the intro>",
  "citations": [
    {{
      "author_year": "<string>",
      "role": "<foundation|alternative|background>",
      "importance": "<high|medium|low>",
      "description": "<one sentence>"
    }}
  ]
}}
"""


def _build_synthesis_prompt(
    cite_key: str,
    classified_blocks: list[dict],
    r_intro_rules: str,
) -> str:
    """Build prompt to synthesize contributions and challenges from all classified blocks."""
    blocks_json = json.dumps(classified_blocks, indent=2, ensure_ascii=False)
    return f"""You are a research reading assistant. You have already classified all paragraph blocks
in the introduction of paper `{cite_key}`.

## R-INTRO Rules (from reading-constitution.md)
{r_intro_rules}

## Classified Blocks
{blocks_json}

## Task
Synthesize across all blocks to produce:

1. A numbered list of **Claimed Contributions** (from CONTRIBUTION blocks):
   - Each item is a direct quote or close paraphrase of a contribution claim.
   - Append `→ To verify in: <section>` for each (e.g., "→ To verify in: Theory section (Part 5)").

2. A numbered list of **Motivating Challenges** (from GAP and MOTIVATION blocks):
   - Each item is a stated challenge or gap.
   - Append `→ Challenge type: <empirical|theoretical|computational|other>`.
   - Append `→ Cross-check with: <section or methodology>`.

3. List the paper's **author-provided keywords** verbatim under an `## Author Keywords` heading.
   If the introduction or abstract contains a "Keywords:" or "Key words:" line, reproduce
   those keywords exactly. If no author keywords are found, return an empty list.

## Output Format (JSON only, no prose)
{{
  "contributions": [
    {{
      "claim": "<direct quote or close paraphrase>",
      "verify_in": "<section reference>"
    }}
  ],
  "challenges": [
    {{
      "challenge": "<challenge statement>",
      "challenge_type": "<empirical|theoretical|computational|other>",
      "cross_check_with": "<section or methodology>"
    }}
  ],
  "author_keywords": ["<keyword1>", "<keyword2>"]
}}
"""


def _call_llm_json(prompt: str, model: str) -> Optional[dict]:
    """Call Anthropic API expecting a JSON response. Returns parsed dict or None on failure."""
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    response_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        # Remove first and last lines (``` fences)
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_text = "\n".join(lines)

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Fallback (no-LLM) classifiers
# ---------------------------------------------------------------------------

def _fallback_classify_paragraph(paragraph: str) -> dict:
    """Heuristic block classification used when LLM is unavailable."""
    text_lower = paragraph.lower()

    # Simple keyword heuristics
    if any(kw in text_lower for kw in ["we propose", "we introduce", "our contribution", "in this paper, we"]):
        block_type = "CONTRIBUTION"
    elif any(kw in text_lower for kw in ["however", "limitation", "gap", "missing", "fails to", "lack of"]):
        block_type = "GAP"
    elif any(kw in text_lower for kw in ["previous work", "prior work", "has been studied", "et al."]):
        block_type = "PRIOR_WORK"
    elif any(kw in text_lower for kw in ["motivated by", "challenge", "difficulty", "problem of"]):
        block_type = "MOTIVATION"
    elif any(kw in text_lower for kw in ["the rest of this paper", "section", "organized as follows", "outline"]):
        block_type = "ROADMAP"
    else:
        block_type = "BACKGROUND"

    return {
        "block_type": block_type,
        "summary": paragraph[:120].replace("\n", " ") + ("..." if len(paragraph) > 120 else ""),
        "citations": [],
    }


def _fallback_synthesize(classified_blocks: list[dict]) -> dict:
    """Minimal synthesis when LLM is unavailable."""
    contributions = []
    challenges = []

    for block in classified_blocks:
        bt = block.get("block_type", "")
        summary = block.get("summary", "")
        if bt == "CONTRIBUTION":
            contributions.append({
                "claim": summary,
                "verify_in": "(section TBD — requires manual review)",
            })
        elif bt in ("GAP", "MOTIVATION"):
            challenges.append({
                "challenge": summary,
                "challenge_type": "other",
                "cross_check_with": "(cross-check TBD — requires manual review)",
            })

    return {"contributions": contributions, "challenges": challenges}


# ---------------------------------------------------------------------------
# Intro.md appender
# ---------------------------------------------------------------------------

def _build_intro_notes_text(classified_blocks: list[dict]) -> str:
    """Render the ## Intro Notes section from classified blocks."""
    lines = [SECTION_INTRO_NOTES, ""]
    for i, block in enumerate(classified_blocks, start=1):
        bt = block.get("block_type", "UNKNOWN")
        summary = block.get("summary", "")
        lines.append(f"**Block {i} [{bt}]:** {summary}")
        citations = block.get("citations", [])
        if citations:
            for cit in citations:
                ay = cit.get("author_year", "")
                role = cit.get("role", "")
                importance = cit.get("importance", "")
                desc = cit.get("description", "")
                lines.append(f"  - {ay} ({role}, {importance}): {desc}")
        lines.append("")
    return "\n".join(lines)


def _build_contributions_text(synthesis: dict) -> str:
    """Render the ## Claimed Contributions section."""
    lines = [SECTION_CLAIMED_CONTRIBUTIONS, ""]
    contributions = synthesis.get("contributions", [])
    if not contributions:
        lines.append("*(No explicit contribution claims identified.)*")
        lines.append("")
    else:
        for i, item in enumerate(contributions, start=1):
            claim = item.get("claim", "")
            verify_in = item.get("verify_in", "")
            lines.append(f"{i}. {claim}")
            lines.append(f"   → To verify in: {verify_in}")
            lines.append("")
    return "\n".join(lines)


def _build_challenges_text(synthesis: dict) -> str:
    """Render the ## Motivating Challenges section."""
    lines = [SECTION_MOTIVATING_CHALLENGES, ""]
    challenges = synthesis.get("challenges", [])
    if not challenges:
        lines.append("*(No explicit motivating challenges identified.)*")
        lines.append("")
    else:
        for i, item in enumerate(challenges, start=1):
            challenge = item.get("challenge", "")
            challenge_type = item.get("challenge_type", "other")
            cross_check = item.get("cross_check_with", "")
            lines.append(f"{i}. {challenge}")
            lines.append(f"   → Challenge type: {challenge_type}")
            lines.append(f"   → Cross-check with: {cross_check}")
            lines.append("")
    return "\n".join(lines)


def _build_author_keywords_text(synthesis: dict) -> str:
    """Render the ## Author Keywords section from synthesis output."""
    lines = [SECTION_AUTHOR_KEYWORDS, ""]
    keywords = synthesis.get("author_keywords", [])
    if not keywords:
        lines.append("*(No author-provided keywords found.)*")
        lines.append("")
    else:
        for kw in keywords:
            lines.append(f"- {kw}")
        lines.append("")
    return "\n".join(lines)


def _append_to_intro_md(
    vault_root: Path,
    cite_key: str,
    intro_notes_text: str,
    contributions_text: str,
    challenges_text: str,
    author_keywords_text: str = "",
) -> Path:
    """Append intro sections to intro.md without overwriting §Positioning.

    The parent directory is created if it does not exist.
    Returns the path to the intro.md file.
    """
    note_dir = vault_root / "literature" / "papers" / cite_key
    note_dir.mkdir(parents=True, exist_ok=True)
    intro_path = note_dir / "intro.md"

    parts = [
        "",
        intro_notes_text,
        contributions_text,
        challenges_text,
    ]
    if author_keywords_text:
        parts.append(author_keywords_text)
    append_block = "\n".join(parts)

    if intro_path.exists():
        existing = intro_path.read_text(encoding="utf-8")
        intro_path.write_text(existing.rstrip() + "\n" + append_block, encoding="utf-8")
    else:
        intro_path.write_text(append_block.lstrip(), encoding="utf-8")

    return intro_path


# ---------------------------------------------------------------------------
# Citation list builder
# ---------------------------------------------------------------------------

def _build_citations_extracted(classified_blocks: list[dict]) -> list[dict]:
    """Flatten citations from all blocks into the handoff list for Tasks 03 and 06.

    Sets dummy_eligible: True for foundation/alternative roles, False for background.
    """
    seen: set[str] = set()
    result: list[dict] = []

    for block in classified_blocks:
        for cit in block.get("citations", []):
            author_year = cit.get("author_year", "")
            role = cit.get("role", "background")
            importance = cit.get("importance", "low")
            description = cit.get("description", "")

            # dummy_eligible: true for foundation and alternative roles
            dummy_eligible = CITATION_ROLES.get(role, False)

            key = f"{author_year}:{role}"
            if key not in seen:
                seen.add(key)
                result.append({
                    "author_year": author_year,
                    "role": role,
                    "importance": importance,
                    "description": description,
                    "dummy_eligible": dummy_eligible,
                })

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_step42(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 4.2: classify intro blocks, extract citations, append intro sections.

    Reads all segments with ``section_type: introduction``, classifies each paragraph
    block using the INTRODUCTION READING PROTOCOL (R-INTRO rules), extracts citation
    metadata with dummy_eligible flags, synthesizes ##Claimed Contributions and
    ##Motivating Challenges, and appends to intro.md without overwriting §Positioning.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    skill_root:
        Root of the paper-reader skill (for reading-constitution.md); default
        ``skills/paper-reader`` (relative to cwd).
    model:
        Anthropic model ID; overridden by ``PAPER_READER_MODEL`` env var.
    dry_run:
        When True, skip all file writes and LLM calls; return a dry-run summary dict.

    Returns
    -------
    dict with keys:
        - ``cite_key``              – echo of input
        - ``blocks_classified``     – int: total blocks classified across all segments
        - ``contributions_found``   – int: claimed contributions extracted
        - ``challenges_found``      – int: motivating challenges extracted
        - ``citations_extracted``   – list of dicts: {author_year, role, importance,
                                        description, dummy_eligible}
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))

    # Resolve skill_root (supports both absolute and relative-to-cwd paths)
    sroot = Path(skill_root)
    if not sroot.is_absolute():
        sroot = Path.cwd() / sroot

    # 1. Find all introduction segments
    intro_segments = _find_intro_segments(bank_root, cite_key)
    intro_segments_found = len(intro_segments)

    # Expected intro.md path
    expected_intro_path = str(vroot / "literature" / "papers" / cite_key / "intro.md")

    if dry_run:
        return {
            "cite_key": cite_key,
            "intro_segments_found": intro_segments_found,
            "planned_sections": PLANNED_SECTIONS,
            "output_path": expected_intro_path,
            "model": model,
            "block_types": BLOCK_TYPES,
        }

    # 2. Load R-INTRO rules from reading-constitution.md
    r_intro_rules = _load_r_intro_rules(sroot)

    # 3. Classify every paragraph across all intro segments
    classified_blocks: list[dict] = []

    for seg in intro_segments:
        segment_text = _strip_frontmatter(seg["text"])
        segment_id = seg["path"].name
        paragraphs = _split_into_paragraphs(segment_text)

        for para in paragraphs:
            if not para.strip():
                continue

            prompt = _build_block_classification_prompt(
                cite_key=cite_key,
                segment_id=segment_id,
                paragraph_text=para,
                r_intro_rules=r_intro_rules,
            )
            result = _call_llm_json(prompt, model)
            if result is None:
                result = _fallback_classify_paragraph(para)

            # Ensure block_type is a valid label
            if result.get("block_type") not in BLOCK_TYPES:
                result["block_type"] = "BACKGROUND"

            classified_blocks.append(result)

    # 4. Synthesize contributions and challenges across all blocks
    if classified_blocks:
        synth_prompt = _build_synthesis_prompt(
            cite_key=cite_key,
            classified_blocks=classified_blocks,
            r_intro_rules=r_intro_rules,
        )
        synthesis = _call_llm_json(synth_prompt, model)
        if synthesis is None:
            synthesis = _fallback_synthesize(classified_blocks)
    else:
        synthesis = {"contributions": [], "challenges": []}

    contributions = synthesis.get("contributions", [])
    challenges = synthesis.get("challenges", [])

    # 5. Build citations handoff list (dummy_eligible set per role)
    citations_extracted = _build_citations_extracted(classified_blocks)

    # 6. Render output sections
    intro_notes_text = _build_intro_notes_text(classified_blocks)
    contributions_text = _build_contributions_text(synthesis)
    challenges_text = _build_challenges_text(synthesis)
    author_keywords_text = _build_author_keywords_text(synthesis)

    # 7. Append sections to intro.md (preserves §Positioning written by Task 01)
    _append_to_intro_md(
        vault_root=vroot,
        cite_key=cite_key,
        intro_notes_text=intro_notes_text,
        contributions_text=contributions_text,
        challenges_text=challenges_text,
        author_keywords_text=author_keywords_text,
    )

    return {
        "cite_key": cite_key,
        "blocks_classified": len(classified_blocks),
        "contributions_found": len(contributions),
        "challenges_found": len(challenges),
        "citations_extracted": citations_extracted,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 4.2: Classify introduction paragraph blocks, extract citation metadata, "
            "and append ## Intro Notes / ## Claimed Contributions / ## Motivating Challenges "
            "to intro.md."
        )
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan without writing files or calling the LLM; print JSON to stdout.",
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

    result = run_step42(
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
