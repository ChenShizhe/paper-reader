#!/usr/bin/env python3
"""Merge sub-minimum segments into adjacent segments.

Usage:
    python3 merge_small_segments.py --segments-dir PATH [--min-size N] [--max-size N]
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional


_DEFAULT_MIN_SIZE = 1024
_DEFAULT_MAX_SIZE = 16384


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='Merge segments smaller than --min-size into adjacent segments.'
    )
    p.add_argument('--segments-dir', required=True, type=Path,
                   help='Directory containing segment .md files.')
    p.add_argument('--min-size', type=int, default=_DEFAULT_MIN_SIZE,
                   help=f'Minimum segment size in bytes (default: {_DEFAULT_MIN_SIZE}).')
    p.add_argument('--max-size', type=int, default=_DEFAULT_MAX_SIZE,
                   help=f'Maximum segment size in bytes (default: {_DEFAULT_MAX_SIZE}).')
    return p.parse_args()


def _get_segments(segments_dir: Path) -> List[Path]:
    return sorted(segments_dir.glob('*.md'))


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter block (---...---) from the start of content."""
    if not content.startswith('---'):
        return content
    end = content.find('\n---', 3)
    if end < 0:
        return content
    # Skip past the closing ---\n
    after = content[end + 4:]
    if after.startswith('\n'):
        after = after[1:]
    return after


def _merge_into(
    keep: Path,
    discard: Path,
    keep_comes_first: bool,
) -> str:
    """
    Build merged content string by combining keep and discard files.
    keep_comes_first=True  → keep content + discard body
    keep_comes_first=False → discard body + keep content
    """
    keep_content = keep.read_text(encoding='utf-8')
    discard_content = discard.read_text(encoding='utf-8')
    discard_body = _strip_frontmatter(discard_content).strip()

    if keep_comes_first:
        return keep_content.rstrip() + '\n\n' + discard_body + '\n'
    else:
        # Discard precedes keep in document order; inject discard body after keep's header
        # Structure: keep_frontmatter + discard_body + keep_body_after_frontmatter
        keep_body = _strip_frontmatter(keep_content)
        keep_fm_end = len(keep_content) - len(keep_body)
        keep_frontmatter = keep_content[:keep_fm_end].rstrip()
        return keep_frontmatter + '\n\n' + discard_body + '\n\n' + keep_body.strip() + '\n'


def main() -> None:
    args = parse_args()
    segments_dir = args.segments_dir
    min_size = args.min_size
    max_size = args.max_size

    if not segments_dir.is_dir():
        print(f"Error: {segments_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    total_merged = 0
    # Loop until no more segments are below min_size
    while True:
        segments = _get_segments(segments_dir)
        if not segments:
            break

        small = [(i, s) for i, s in enumerate(segments) if s.stat().st_size < min_size]
        if not small:
            break

        idx, seg = small[0]
        merged = False

        # Candidates: next neighbor (preferred), then previous
        candidates = []
        if idx + 1 < len(segments):
            candidates.append((idx + 1, True))   # keep=neighbor, keep_comes_first=True means seg first
        if idx > 0:
            candidates.append((idx - 1, False))  # keep=neighbor, seg comes after

        for neighbor_idx, seg_is_first in candidates:
            neighbor = segments[neighbor_idx]
            # Check merged size would not exceed max_size
            if seg_is_first:
                # seg comes before neighbor; merge into neighbor, prepend seg body
                new_content = _merge_into(keep=neighbor, discard=seg, keep_comes_first=False)
            else:
                # neighbor comes before seg; merge into neighbor, append seg body
                new_content = _merge_into(keep=neighbor, discard=seg, keep_comes_first=True)

            if len(new_content.encode('utf-8')) <= max_size:
                neighbor.write_text(new_content, encoding='utf-8')
                seg.unlink()
                total_merged += 1
                merged = True
                break

        if not merged:
            # Cannot merge this segment without violating max_size; stop
            print(
                f"Warning: cannot merge {seg.name} ({seg.stat().st_size}B) "
                f"into any neighbor without exceeding {max_size}B.",
                file=sys.stderr,
            )
            break

    if total_merged:
        print(f"merge_small_segments: merged {total_merged} small segment(s).")
    else:
        print("merge_small_segments: no segments below min_size; nothing to do.")


if __name__ == '__main__':
    main()
