#!/usr/bin/env python3
"""Split Executor — processes all splits entries from _reseg_plan.json.

Enforces paragraph-boundary splitting, equation fence integrity, archives
originals, and updates _segment_manifest.json.

Usage:
    python3 reseg_split.py --cite-key <cite_key> [--dry-run]
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PAPER_BANK = Path.home() / "Documents" / "paper-bank"
MAX_SEGMENT_BYTES = 20 * 1024  # 20 KB
MAX_RECURSIVE_SPLITS = 5


# ---------------------------------------------------------------------------
# Equation fence helpers
# ---------------------------------------------------------------------------

def _check_equation_fence(text: str) -> Tuple[bool, str]:
    """Return (balanced, warning_message) for LaTeX equation fence integrity.

    Checks that $$ delimiters occur in even count, then that remaining single
    $ delimiters also occur in even count.  An odd count means an unclosed
    equation_fence, which must never appear in a finished sub-segment without
    recording a warning.
    """
    double_dollar_count = text.count("$$")
    if double_dollar_count % 2 != 0:
        return False, f"Unbalanced equation_fence: odd $$ count ({double_dollar_count})"

    # Strip $$ pairs before counting single $
    stripped = text.replace("$$", "")
    single_dollar_count = stripped.count("$")
    if single_dollar_count % 2 != 0:
        return False, (
            f"Unbalanced equation_fence: odd $ count ({single_dollar_count}) "
            "after removing $$ tokens"
        )
    return True, ""


def _find_balanced_split(
    body: str,
    approx_line: Optional[int],
) -> Tuple[str, str, Optional[str]]:
    """Split *body* at a paragraph boundary nearest to approx_line.

    Tries each paragraph boundary ordered by distance from the target offset;
    returns the first split where both halves have a balanced equation_fence.
    Falls back to the nearest boundary and records a warning when no balanced
    split is found.

    Returns (part_a, part_b, equation_fence_warning_or_None).
    """
    boundary_positions: List[Tuple[int, int]] = [
        (m.start(), m.end()) for m in re.finditer(r"\n\n+", body)
    ]

    if not boundary_positions:
        mid = len(body) // 2
        return body[:mid], body[mid:], None

    # Determine target offset
    if approx_line is None:
        target = len(body) // 2
    else:
        lines = body.splitlines(keepends=True)
        target = sum(len(ln) for ln in lines[:approx_line])

    sorted_boundaries = sorted(boundary_positions, key=lambda b: abs(b[0] - target))

    equation_fence_warning: Optional[str] = None
    for start, end in sorted_boundaries:
        part_a = body[:start]
        part_b = body[end:]
        a_ok, _ = _check_equation_fence(part_a)
        b_ok, _ = _check_equation_fence(part_b)
        if a_ok and b_ok:
            return part_a, part_b, None

    # No balanced split found — use nearest boundary and record warning
    best_start, best_end = sorted_boundaries[0]
    part_a = body[:best_start]
    part_b = body[best_end:]
    _, warn_a = _check_equation_fence(part_a)
    _, warn_b = _check_equation_fence(part_b)
    equation_fence_warning = " | ".join(filter(None, [warn_a, warn_b]))
    return part_a, part_b, equation_fence_warning


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

def _read_frontmatter(content: str) -> Tuple[str, str]:
    """Return (frontmatter_with_delimiters, body).  frontmatter is '' if absent."""
    if not content.startswith("---"):
        return "", content
    end = content.find("\n---", 3)
    if end < 0:
        return "", content
    fm_end = end + 4  # past the closing ---
    if fm_end < len(content) and content[fm_end] == "\n":
        fm_end += 1
    return content[:fm_end], content[fm_end:]


def _make_child_frontmatter(parent_fm: str, new_id: str, cite_key: str) -> str:
    """Clone parent frontmatter with an updated segment_id."""
    if not parent_fm:
        return f"---\ncite_key: {cite_key}\nsegment_id: {new_id}\n---\n\n"
    fm = re.sub(r"(^|\n)segment_id:[^\n]*", rf"\1segment_id: {new_id}", parent_fm)
    return fm


# ---------------------------------------------------------------------------
# Recursive split
# ---------------------------------------------------------------------------

def _recursive_split(
    content: str,
    cite_key: str,
    base_id: str,
    approx_line: Optional[int],
    depth: int = 0,
    max_depth: int = MAX_RECURSIVE_SPLITS,
) -> Tuple[List[Dict], bool]:
    """Recursively split content until all pieces are <= MAX_SEGMENT_BYTES.

    Returns (list_of_piece_dicts, oversize_allowed).
    Each piece dict has keys: id, content, equation_fence_warning.
    oversize_allowed is True when the recursion cap was hit on any piece.
    """
    is_oversize = len(content.encode("utf-8")) > MAX_SEGMENT_BYTES
    if not is_oversize or depth >= max_depth:
        return (
            [{"id": base_id, "content": content, "equation_fence_warning": None}],
            is_oversize and depth >= max_depth,
        )

    fm, body = _read_frontmatter(content)
    part_a_body, part_b_body, fence_warn = _find_balanced_split(body, approx_line)

    suffix_a = "part_a" if depth == 0 else "a"
    suffix_b = "part_b" if depth == 0 else "b"
    id_a = f"{base_id}_{suffix_a}"
    id_b = f"{base_id}_{suffix_b}"

    fm_a = _make_child_frontmatter(fm, id_a, cite_key)
    fm_b = _make_child_frontmatter(fm, id_b, cite_key)
    content_a = fm_a + part_a_body
    content_b = fm_b + part_b_body

    results: List[Dict] = []
    any_oversize = False

    for child_id, child_content in [(id_a, content_a), (id_b, content_b)]:
        if len(child_content.encode("utf-8")) > MAX_SEGMENT_BYTES:
            sub, sub_over = _recursive_split(
                child_content, cite_key, child_id, None, depth + 1, max_depth
            )
            results.extend(sub)
            any_oversize = any_oversize or sub_over
        else:
            results.append(
                {"id": child_id, "content": child_content, "equation_fence_warning": fence_warn}
            )

    return results, any_oversize


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_plan(paper_dir: Path, cite_key: str) -> dict:
    plan_path = paper_dir / "_reseg_plan.json"
    if not plan_path.exists():
        # No plan means the trigger scanner found nothing to do; treat as empty.
        return {"splits": [], "merges": [], "rebalances": []}
    try:
        with open(plan_path) as fh:
            return json.load(fh)
    except Exception as exc:
        print(
            f"ERROR: Failed to parse _reseg_plan.json for '{cite_key}': {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def _load_manifest(paper_dir: Path) -> dict:
    manifest_path = paper_dir / "_segment_manifest.json"
    if not manifest_path.exists():
        return {"segments": []}
    with open(manifest_path) as fh:
        return json.load(fh)


def _save_manifest(paper_dir: Path, manifest: dict) -> None:
    """Write manifest atomically via a temp file."""
    manifest_path = paper_dir / "_segment_manifest.json"
    tmp_path = manifest_path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    tmp_path.replace(manifest_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_splits(cite_key: str, dry_run: bool = False) -> dict:
    """Execute all splits from _reseg_plan.json for the given cite_key.

    In dry_run mode returns a summary dict without writing any files.
    In live mode writes new segment files, archives originals, updates
    _segment_manifest.json, and writes _reseg_split_output.json.

    Raises SystemExit(1) with a stderr message when the paper directory is
    not found.
    """
    paper_dir = PAPER_BANK / cite_key

    if not paper_dir.exists():
        print(
            f"ERROR: Paper directory not found for cite_key '{cite_key}': {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    plan = _load_plan(paper_dir, cite_key)
    splits = plan.get("splits", [])

    # ── No splits needed ─────────────────────────────────────────────────────
    if not splits:
        result: dict = {
            "cite_key": cite_key,
            "splits_planned": 0,
            "no_splits_needed": True,
        }
        if not dry_run:
            result.update(
                {
                    "splits_executed": 0,
                    "new_segment_files": [],
                    "archived_files": [],
                    "equation_fence_warnings": [],
                    "oversize_allowed": [],
                    "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                }
            )
            output_path = paper_dir / "_reseg_split_output.json"
            with open(output_path, "w") as fh:
                json.dump(result, fh, indent=2)
        return result

    segments_dir = paper_dir / "segments"

    # ── Dry-run: analyse without writing ─────────────────────────────────────
    if dry_run:
        dry_splits = []
        for entry in splits:
            seg_id = entry.get("segment_id", "")
            source_file = entry.get("source_file", "")
            approx_line = entry.get("approx_split_line")

            src_path = (
                Path(source_file) if source_file.startswith("/") else paper_dir / source_file
            )
            if not src_path.exists():
                src_path = segments_dir / Path(source_file).name

            if not src_path.exists():
                dry_splits.append(
                    {
                        "segment_id": seg_id,
                        "source_file": source_file,
                        "error": f"source file not found: {source_file}",
                    }
                )
                continue

            content = src_path.read_text(encoding="utf-8")
            fm, body = _read_frontmatter(content)

            boundary_positions = [
                (m.start(), m.end()) for m in re.finditer(r"\n\n+", body)
            ]
            if approx_line is None:
                target = len(body) // 2
            else:
                body_lines = body.splitlines(keepends=True)
                target = sum(len(ln) for ln in body_lines[:approx_line])

            if boundary_positions:
                best_start, best_end = min(
                    boundary_positions, key=lambda b: abs(b[0] - target)
                )
                part_a_body = body[:best_start]
                part_b_body = body[best_end:]
                size_a = len((fm + part_a_body).encode("utf-8"))
                size_b = len((fm + part_b_body).encode("utf-8"))
                a_ok, _ = _check_equation_fence(part_a_body)
                b_ok, _ = _check_equation_fence(part_b_body)
                eq_fence_status = "ok" if (a_ok and b_ok) else "equation_fence_warning"
            else:
                total = len(content.encode("utf-8"))
                size_a = total // 2
                size_b = total - size_a
                eq_fence_status = "no_paragraph_boundary"

            src_ok, src_warn = _check_equation_fence(body)
            dry_splits.append(
                {
                    "segment_id": seg_id,
                    "source_file": source_file,
                    "trigger_codes": entry.get("trigger_codes", []),
                    "proposed_children": entry.get("proposed_children", []),
                    "approx_split_line": approx_line,
                    "estimated_part_a_bytes": size_a,
                    "estimated_part_b_bytes": size_b,
                    "equation_fence_status": eq_fence_status,
                    "source_fence_balanced": src_ok,
                }
            )

        return {
            "cite_key": cite_key,
            "splits_planned": len(splits),
            "no_splits_needed": False,
            "dry_run": True,
            "splits": dry_splits,
        }

    # ── Live run ──────────────────────────────────────────────────────────────
    archive_dir = segments_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(paper_dir)
    manifest_segments: List[dict] = manifest.get("segments", [])

    # Build position index: segment_id -> list index
    seg_index_map: Dict[str, int] = {
        s["segment_id"]: i
        for i, s in enumerate(manifest_segments)
        if isinstance(s, dict) and "segment_id" in s
    }

    new_segment_files: List[str] = []
    archived_files: List[str] = []
    equation_fence_warnings: List[dict] = []
    oversize_allowed_list: List[str] = []

    # Process splits in reverse manifest order to keep indices stable
    sorted_splits = sorted(
        splits,
        key=lambda s: seg_index_map.get(s.get("segment_id", ""), float("inf")),
        reverse=True,
    )

    for entry in sorted_splits:
        seg_id = entry.get("segment_id", "")
        source_file = entry.get("source_file", "")
        approx_line = entry.get("approx_split_line")
        trigger_codes = entry.get("trigger_codes", [])

        src_path = (
            Path(source_file) if source_file.startswith("/") else paper_dir / source_file
        )
        if not src_path.exists():
            src_path = segments_dir / Path(source_file).name

        if not src_path.exists():
            print(
                f"WARNING: source file not found for '{seg_id}': {source_file}",
                file=sys.stderr,
            )
            continue

        content = src_path.read_text(encoding="utf-8")
        child_pieces, is_oversize = _recursive_split(
            content, cite_key, seg_id, approx_line
        )

        if is_oversize:
            oversize_allowed_list.append(seg_id)

        new_manifest_entries: List[dict] = []
        for piece in child_pieces:
            piece_id: str = piece["id"]
            piece_content: str = piece["content"]
            fence_warn: Optional[str] = piece["equation_fence_warning"]

            piece_filename = f"{piece_id}.md"
            piece_path = segments_dir / piece_filename
            piece_path.write_text(piece_content, encoding="utf-8")

            rel_file = f"segments/{piece_filename}"
            new_segment_files.append(rel_file)

            if fence_warn:
                equation_fence_warnings.append(
                    {"segment_id": piece_id, "equation_fence_warning": fence_warn}
                )

            new_manifest_entries.append(
                {
                    "segment_id": piece_id,
                    "file": rel_file,
                    "section_type": "unknown",
                    "source_format": "markdown",
                    "token_estimate": len(piece_content) // 4,
                    "comprehension_status": "pending",
                    "has_equations": bool(re.search(r"\$", piece_content)),
                    "derived_from": seg_id,
                    "split_trigger_codes": trigger_codes,
                }
            )

        # Archive original
        archive_path = archive_dir / f"{seg_id}.v1.md"
        shutil.move(str(src_path), str(archive_path))
        archived_files.append(f"segments/archive/{seg_id}.v1.md")

        # Update manifest in memory: replace original entry with children
        insert_idx = seg_index_map.get(seg_id)
        if insert_idx is not None:
            manifest_segments = (
                manifest_segments[:insert_idx]
                + new_manifest_entries
                + manifest_segments[insert_idx + 1 :]
            )
        else:
            manifest_segments.extend(new_manifest_entries)

        # Rebuild position index after modification
        seg_index_map = {
            s["segment_id"]: i
            for i, s in enumerate(manifest_segments)
            if isinstance(s, dict) and "segment_id" in s
        }

    # Atomic manifest write
    manifest["segments"] = manifest_segments
    manifest["segment_count"] = len(manifest_segments)
    _save_manifest(paper_dir, manifest)

    output = {
        "cite_key": cite_key,
        "splits_executed": len(sorted_splits),
        "new_segment_files": new_segment_files,
        "archived_files": archived_files,
        "equation_fence_warnings": equation_fence_warnings,
        "oversize_allowed": oversize_allowed_list,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    output_path = paper_dir / "_reseg_split_output.json"
    with open(output_path, "w") as fh:
        json.dump(output, fh, indent=2)

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute split plans from _reseg_plan.json."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and plan only; do not write any files.",
    )
    args = parser.parse_args()

    result = execute_splits(args.cite_key, dry_run=args.dry_run)

    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        if result.get("no_splits_needed"):
            print(f"[reseg_split] No splits needed for '{args.cite_key}'.")
        else:
            n = result.get("splits_executed", 0)
            print(f"[reseg_split] Executed {n} split(s) for '{args.cite_key}'.")
            if result.get("equation_fence_warnings"):
                print(
                    f"  equation_fence_warnings: {len(result['equation_fence_warnings'])}",
                    file=sys.stderr,
                )
            if result.get("oversize_allowed"):
                print(
                    f"  oversize_allowed: {result['oversize_allowed']}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
