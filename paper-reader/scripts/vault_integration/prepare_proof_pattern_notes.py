"""Proof-Pattern Note Preparation module.

Stages proof-pattern notes or update patches in paper-bank/proof-patterns from
theory.md and _vault_search_results.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TECHNIQUE_PATTERNS: dict[str, list[str]] = {
    "Bernstein inequality": [r"\bbernstein(?:'s)?\s+inequalit(?:y|ies)\b"],
    "Martingale argument": [r"\bmartingale\s+(?:argument|method|approach)\b"],
    "Union bound": [r"\bunion\s+bound\b"],
    "Chaining argument": [r"\bchaining(?:\s+argument)?\b"],
    "Concentration inequality": [r"\bconcentration\s+inequalit(?:y|ies)\b"],
    "Azuma-Hoeffding inequality": [
        r"\bazuma(?:-|\s+)hoeffding\s+inequalit(?:y|ies)\b",
        r"\bhoeffding\s+inequalit(?:y|ies)\b",
    ],
    "Doob decomposition": [r"\bdoob(?:-|\s+)meyer\s+decomposition\b", r"\bdoob\s+decomposition\b"],
    "Gronwall inequality": [r"\bgronwall\s+inequalit(?:y|ies)\b"],
    "Coupling argument": [r"\bcoupling\s+argument\b"],
    "Fixed-point argument": [r"\bfixed(?:-|\s+)point\s+argument\b"],
}

GENERIC_TECHNIQUE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z-]*(?:\s+[A-Za-z][A-Za-z-]*){0,5}\s+"
    r"(?:inequality|argument|bound|chaining|decomposition|method|technique|lemma))\b",
    re.IGNORECASE,
)


def _slug(name: str) -> str:
    """Convert a proof-technique name to a filesystem-safe slug."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-+", "-", s)
    return s or "unknown"


def _word_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _usage_excerpt(text: str, start_index: int) -> str:
    """Return a short line-level excerpt around the match location."""
    line_start = text.rfind("\n", 0, start_index)
    line_end = text.find("\n", start_index)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    if line_end == -1:
        line_end = len(text)
    line = re.sub(r"\s+", " ", text[line_start:line_end]).strip(" -*\t")
    if len(line) > 180:
        line = line[:177].rstrip() + "..."
    return line


def _add_candidate(
    candidates_by_slug: dict[str, dict],
    title: str,
    source: str,
    usage_context: str,
) -> None:
    s = _slug(title)
    if s in candidates_by_slug:
        return
    candidates_by_slug[s] = {
        "title": title,
        "slug": s,
        "source": source,
        "usage_context": usage_context,
    }


# ---------------------------------------------------------------------------
# Proof-technique extraction from theory.md
# ---------------------------------------------------------------------------

def _extract_proof_sections(theory_text: str) -> list[tuple[str, str]]:
    """Return heading/body tuples for proof-related sections."""
    headings = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", theory_text, re.MULTILINE))
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(headings):
        title = match.group(2).strip()
        lower = title.lower()
        if "proof" not in lower:
            continue
        body_start = match.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(theory_text)
        body = theory_text[body_start:body_end]
        sections.append((title, body))
    return sections


def _scan_named_techniques(
    text: str,
    source_label: str,
    candidates_by_slug: dict[str, dict],
) -> None:
    """Collect known techniques from text into candidates_by_slug."""
    for title, patterns in TECHNIQUE_PATTERNS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                excerpt = _usage_excerpt(text, match.start())
                usage_context = f"{source_label}: {excerpt}" if excerpt else source_label
                _add_candidate(
                    candidates_by_slug=candidates_by_slug,
                    title=title,
                    source="theory.md",
                    usage_context=usage_context,
                )


def _scan_generic_techniques(
    text: str,
    source_label: str,
    candidates_by_slug: dict[str, dict],
) -> None:
    """Collect coarse generic technique phrases from text."""
    for match in GENERIC_TECHNIQUE_RE.finditer(text):
        phrase = match.group(1).strip().strip(".,;:()[]")
        words = phrase.split()
        if len(words) < 2 or len(words) > 7:
            continue
        lower = phrase.lower()
        if lower.startswith(("the ", "this ", "that ", "our ", "their ", "proof ")):
            continue
        title = " ".join(w.capitalize() for w in words)
        excerpt = _usage_excerpt(text, match.start())
        usage_context = f"{source_label}: {excerpt}" if excerpt else source_label
        _add_candidate(
            candidates_by_slug=candidates_by_slug,
            title=title,
            source="theory.md",
            usage_context=usage_context,
        )


def _extract_proof_patterns_from_theory(theory_text: str) -> list[dict]:
    """Extract coarse proof techniques from theory.md content."""
    candidates_by_slug: dict[str, dict] = {}

    proof_sections = _extract_proof_sections(theory_text)
    for heading, body in proof_sections:
        block = f"{heading}\n{body}"
        section_label = f"section '{heading}'"
        _scan_named_techniques(block, section_label, candidates_by_slug)
        _scan_generic_techniques(block, section_label, candidates_by_slug)

        # Fallback: if the heading includes "Proof Strategy: <Technique>", stage <Technique>.
        heading_technique = re.sub(
            r"(?i)\bproof(?:\s+strategy|\s+sketch|\s+outline|\s+idea|\s+techniques?)\b[:\-\s]*",
            "",
            heading,
        ).strip()
        if heading_technique and heading_technique.lower() != heading.lower():
            _add_candidate(
                candidates_by_slug=candidates_by_slug,
                title=heading_technique,
                source="theory.md",
                usage_context=f"section '{heading}'",
            )

    # Inline scan across the full theory for named techniques.
    _scan_named_techniques(theory_text, "theory.md inline", candidates_by_slug)

    return list(candidates_by_slug.values())


# ---------------------------------------------------------------------------
# Vault proof-pattern lookup
# ---------------------------------------------------------------------------

def _existing_vault_patterns(vault_search_results: dict) -> list[dict]:
    """Return existing proof-pattern records from vault search results."""
    records: list[dict] = []
    for record in (vault_search_results.get("results", {}).get("proof_patterns") or []):
        note_path = str(record.get("note_path") or "")
        if not note_path:
            continue
        stem = Path(note_path).stem.lower()
        tokens = _word_tokens(stem.replace("-", " "))
        for term in record.get("match_terms") or []:
            tokens |= _word_tokens(str(term))
        records.append({
            "slug": stem,
            "note_path": note_path,
            "tokens": tokens,
        })
    return records


def _match_existing_pattern(slug: str, title: str, existing_records: list[dict]) -> dict | None:
    """Match extracted pattern by slug or by title keyword overlap."""
    for record in existing_records:
        if record["slug"] == slug:
            return record

    title_tokens = _word_tokens(title)
    if not title_tokens:
        return None

    for record in existing_records:
        overlap = title_tokens & record["tokens"]
        if len(overlap) >= 2:
            return record
    return None


# ---------------------------------------------------------------------------
# Note content builders
# ---------------------------------------------------------------------------

def _new_pattern_frontmatter(
    cite_key: str,
    title: str,
    usage_context: str,
    today_iso: str,
) -> str:
    data = {
        "type": "proof-pattern",
        "title": title,
        "date": today_iso,
        "tags": ["proof-pattern", "statistics"],
        "status": "active",
        "category": "proof-pattern",
        "seen_in_papers": [cite_key],
        "usage_context": usage_context,
    }
    return "---\n" + yaml.dump(data, default_flow_style=False, allow_unicode=True) + "---\n"


def _new_pattern_body(title: str) -> str:
    return (
        f"\n## Description\n\n<!-- TODO: Add description of {title} -->\n\n"
        "## Standard Form\n\n<!-- TODO: Add standard form -->\n\n"
        "## When to Use\n\n<!-- TODO: Add guidance -->\n\n"
        "## Variants\n\n<!-- TODO: Add variants -->\n\n"
        "## Example Papers\n\n<!-- TODO: Add paper references -->\n\n"
        "## Seen In\n\n<!-- TODO: Add cross-references -->\n\n"
        "## Related Techniques\n\n<!-- TODO: Add related techniques -->\n"
    )


def _update_patch_content(
    cite_key: str,
    title: str,
    usage_context: str,
    vault_path_str: str,
) -> str:
    """Build an update patch file content for an existing vault proof-pattern note."""
    patch_data = {
        "patch_type": "proof_pattern_update",
        "target_vault_path": vault_path_str,
        "append_seen_in_papers": [cite_key],
        "append_usage_context": [
            {
                "paper": cite_key,
                "context": usage_context,
            }
        ],
    }
    header = "---\n" + yaml.dump(patch_data, default_flow_style=False, allow_unicode=True) + "---\n"
    body = (
        f"\n<!-- Update patch for: {title} -->\n"
        f"<!-- Add to seen_in_papers: {cite_key} -->\n"
        f"<!-- Append usage_context: {usage_context} -->\n"
    )
    return header + body


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def prepare_proof_pattern_notes(
    work_dir: str | Path,
    vault_path: str | Path,
    vault_search_results_path: str | Path,
    cite_key: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Prepare staged proof-pattern notes from theory.md and vault search results.

    Reads theory.md from <vault_path>/literature/papers/<cite_key>/theory.md.
    In dry_run mode no files are written; returns a summary dict.
    Raises SystemExit(1) on missing vault_search_results.
    In dry_run mode, missing theory.md returns inputs_valid: False without exiting 1.
    """
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    vault_search_results_path = Path(vault_search_results_path)

    if not vault_search_results_path.exists():
        print(
            f"ERROR: vault search results file not found: {vault_search_results_path}\n"
            "Run Task 01 (search_vault) first to generate this file.",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_search_results = json.loads(vault_search_results_path.read_text(encoding="utf-8"))
    if not cite_key:
        cite_key = vault_search_results.get("cite_key") or work_dir.name

    theory_path = vault_path / "literature" / "papers" / cite_key / "theory.md"
    if not theory_path.exists():
        if dry_run:
            return {
                "cite_key": cite_key,
                "patterns_new_count": 0,
                "patterns_updated_count": 0,
                "inputs_valid": False,
            }
        print(
            f"ERROR: theory.md not found: {theory_path}\n"
            "Run M6 (paper comprehension) first to generate theory.md for this paper.",
            file=sys.stderr,
        )
        sys.exit(1)

    theory_text = theory_path.read_text(encoding="utf-8")
    patterns = _extract_proof_patterns_from_theory(theory_text)

    if not patterns:
        print(
            f"WARNING: No proof techniques found in {theory_path}. Writing empty report.",
            file=sys.stderr,
        )

    existing_patterns = _existing_vault_patterns(vault_search_results)
    today_iso = datetime.now(tz=timezone.utc).date().isoformat()

    new_patterns: list[dict] = []
    updated_patterns: list[dict] = []

    for pattern in patterns:
        title = pattern["title"]
        usage_context = pattern["usage_context"]
        slug = pattern["slug"]
        existing = _match_existing_pattern(slug, title, existing_patterns)

        if existing:
            vault_note_path = existing["note_path"]
            update_filename = f"{slug}-update.md"
            update_path = work_dir / "proof-patterns" / update_filename

            if not dry_run:
                update_path.parent.mkdir(parents=True, exist_ok=True)
                update_path.write_text(
                    _update_patch_content(
                        cite_key=cite_key,
                        title=title,
                        usage_context=usage_context,
                        vault_path_str=vault_note_path,
                    ),
                    encoding="utf-8",
                )

            updated_patterns.append({
                "slug": slug,
                "vault_path": vault_note_path,
                "update_path": str(update_path),
            })
        else:
            staged_filename = f"{slug}.md"
            staged_path = work_dir / "proof-patterns" / staged_filename

            if not dry_run:
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path.write_text(
                    _new_pattern_frontmatter(
                        cite_key=cite_key,
                        title=title,
                        usage_context=usage_context,
                        today_iso=today_iso,
                    )
                    + _new_pattern_body(title),
                    encoding="utf-8",
                )

            new_patterns.append({
                "slug": slug,
                "staged_path": str(staged_path),
            })

    if dry_run:
        return {
            "cite_key": cite_key,
            "patterns_new_count": len(new_patterns),
            "patterns_updated_count": len(updated_patterns),
            "inputs_valid": True,
        }

    report = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "patterns_new": new_patterns,
        "patterns_updated": updated_patterns,
        "total_patterns": len(new_patterns) + len(updated_patterns),
    }
    report_path = work_dir / "_proof_pattern_prep_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Prepare staged proof-pattern notes from theory.md and vault search results."
    )
    p.add_argument("--work-dir", required=True, help="Paper-bank directory for the paper.")
    p.add_argument("--vault-path", required=True, help="Root of the Obsidian vault (citadel/).")
    p.add_argument(
        "--vault-search-results",
        required=True,
        help="Path to _vault_search_results.json produced by Task 01.",
    )
    p.add_argument(
        "--cite-key",
        required=False,
        default=None,
        help="Cite key used to locate theory.md in the vault. Defaults to cite_key from vault_search_results.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and report counts; write no files.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    result = prepare_proof_pattern_notes(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        vault_search_results_path=args.vault_search_results,
        cite_key=args.cite_key,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
