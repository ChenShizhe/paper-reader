"""
enforce_constitution_cap.py — Constitution size-cap enforcement for paper-reader.

Standalone CLI + importable module. Measures approximate token count of
reading-constitution.md, archives oldest non-high-confidence rules to
reading-constitution-archive.md when > 5000 tokens. Never archives
high-confidence rules. Emits a JSON report with token_count_before,
token_count_after, within_cap, archived_rule_ids. Supports --dry-run
(no mutations).

Hook integration: call enforce_cap() from self_improve.py after proposals
are written to keep the constitution within its size budget.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

TOKEN_CAP = 5000
CHARS_PER_TOKEN = 4

_RULE_ID_RE = re.compile(r'(?m)^\s*###\s+(R-[\w-]+)')
_CONFIDENCE_RE = re.compile(r'\*\*Confidence:\*\*\s*(\w+)', re.IGNORECASE)
_SEP_RE = re.compile(r'(?m)^---$')


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Approximate token count: 1 token ≈ 4 characters."""
    return max(0, len(text) // CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Constitution parsing / rebuilding
# ---------------------------------------------------------------------------

def _split_constitution(text: str) -> list[str]:
    """Split constitution text on bare --- separator lines."""
    return _SEP_RE.split(text)


def _join_constitution(segments: list[str]) -> str:
    """Rejoin segments with --- separator (inverse of _split_constitution)."""
    return '---'.join(segments)


def _parse_rules(segments: list[str]) -> list[dict[str, Any]]:
    """
    Parse rules from constitution segments in document order.

    Each returned dict contains:
        seg_idx     – index into the segments list
        rule_id     – e.g. "R-INTRO-03"
        confidence  – lowercase string: "high", "medium", "low", or "unknown"
        header_part – text in the segment before the ### heading (may contain
                      a ## Section: header for the first rule of each section)
        rule_part   – text from the ### heading to end of segment
    """
    rules: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        rule_match = _RULE_ID_RE.search(seg)
        if not rule_match:
            continue
        rule_id = rule_match.group(1)
        conf_match = _CONFIDENCE_RE.search(seg)
        confidence = conf_match.group(1).lower() if conf_match else 'unknown'
        rule_start = rule_match.start()
        rules.append({
            'seg_idx': idx,
            'rule_id': rule_id,
            'confidence': confidence,
            'header_part': seg[:rule_start],
            'rule_part': seg[rule_start:],
        })
    return rules


def _rebuild(
    segments: list[str],
    archived_ids: set[str],
    rules: list[dict[str, Any]],
) -> str:
    """
    Rebuild constitution text with archived rules removed.

    If a segment contains a section header before the rule heading
    (i.e. the first rule in a section), the section header is kept even
    when the rule itself is archived.
    """
    rule_map: dict[int, dict[str, Any]] = {r['seg_idx']: r for r in rules}
    new_segs: list[str] = []
    for idx, seg in enumerate(segments):
        if idx in rule_map and rule_map[idx]['rule_id'] in archived_ids:
            r = rule_map[idx]
            # Preserve any section header that precedes the rule heading
            if r['header_part'].strip():
                new_segs.append(r['header_part'])
            # Otherwise drop the segment entirely (rule-only segment)
        else:
            new_segs.append(seg)
    return _join_constitution(new_segs)


# ---------------------------------------------------------------------------
# Core enforcement
# ---------------------------------------------------------------------------

def enforce_cap(
    constitution_path: Path,
    archive_path: Path,
    output_path: Path,
    cap: int = TOKEN_CAP,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Enforce the token cap on reading-constitution.md.

    Reads the constitution, measures its approximate token count, and—if over
    *cap*—archives the oldest non-high-confidence rules to *archive_path* until
    the constitution is within budget.  High-confidence rules are never archived.

    Parameters
    ----------
    constitution_path:
        Path to reading-constitution.md.
    archive_path:
        Path to reading-constitution-archive.md.
    output_path:
        Path to write the JSON cap report (written even in dry-run).
    cap:
        Token cap threshold (default: TOKEN_CAP = 5000).
    dry_run:
        If True, measure and report only; never write constitution or archive.

    Returns
    -------
    dict with keys: token_count_before, token_count_after, within_cap,
    archived_rule_ids, cap, dry_run.
    """
    text = constitution_path.read_text(encoding='utf-8')
    token_count_before = _estimate_tokens(text)

    segments = _split_constitution(text)
    rules = _parse_rules(segments)

    # Only non-high-confidence rules are eligible; order = document order (oldest first)
    candidates = [r for r in rules if r['confidence'] != 'high']

    archived: list[dict[str, Any]] = []
    archived_ids: set[str] = set()

    if token_count_before > cap:
        for candidate in candidates:
            archived.append(candidate)
            archived_ids.add(candidate['rule_id'])
            projected = _rebuild(segments, archived_ids, rules)
            if _estimate_tokens(projected) <= cap:
                break  # constitution is now within budget

    rebuilt_text = _rebuild(segments, archived_ids, rules)
    token_count_after = _estimate_tokens(rebuilt_text)
    archived_rule_ids = [r['rule_id'] for r in archived]
    within_cap = token_count_after <= cap

    report: dict[str, Any] = {
        'token_count_before': token_count_before,
        'token_count_after': token_count_after,
        'within_cap': within_cap,
        'archived_rule_ids': archived_rule_ids,
        'cap': cap,
        'dry_run': dry_run,
    }

    if not dry_run and archived:
        # Write updated constitution (rules removed)
        constitution_path.write_text(rebuilt_text, encoding='utf-8')

        # Append archived rules to archive file
        existing = archive_path.read_text(encoding='utf-8') if archive_path.exists() else ''
        archive_blocks = '\n\n'.join(r['rule_part'].strip() for r in archived)
        archive_section = '## Archived Rules\n\n' + archive_blocks + '\n'
        new_archive = (
            existing.rstrip() + '\n\n' + archive_section
            if existing.strip()
            else archive_section
        )
        archive_path.write_text(new_archive, encoding='utf-8')

    # Always write the report (even in dry-run)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding='utf-8')

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Enforce token size-cap on reading-constitution.md.',
    )
    p.add_argument(
        '--constitution-path',
        required=True,
        help='Path to reading-constitution.md.',
    )
    p.add_argument(
        '--archive-path',
        required=True,
        help='Path to reading-constitution-archive.md.',
    )
    p.add_argument(
        '--output',
        required=True,
        help='Path to write the cap report JSON.',
    )
    p.add_argument(
        '--cap',
        type=int,
        default=TOKEN_CAP,
        help=f'Token cap threshold (default: {TOKEN_CAP}).',
    )
    p.add_argument(
        '--dry-run',
        action='store_true',
        default=False,
        help='Measure and report only; do not mutate any files.',
    )
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    constitution_path = Path(args.constitution_path)
    if not constitution_path.exists():
        print(
            f'ERROR: constitution file not found: {constitution_path}',
            file=sys.stderr,
        )
        sys.exit(1)

    report = enforce_cap(
        constitution_path=constitution_path,
        archive_path=Path(args.archive_path),
        output_path=Path(args.output),
        cap=args.cap,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
