#!/usr/bin/env python3
"""Rebalance Executor — processes all rebalances entries from _reseg_plan.json.

Moves trailing paragraph blocks from an oversized segment into an adjacent
segment, preserving equation fence integrity in both the donor and recipient.

Usage:
    python3 reseg_rebalance.py --cite-key <cite_key> [--dry-run]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PAPER_BANK = Path.home() / "Documents" / "paper-bank"


# ---------------------------------------------------------------------------
# Equation fence helpers
# ---------------------------------------------------------------------------

def _check_equation_fence(text: str) -> Tuple[bool, str]:
    """Return (balanced, warning_message) for LaTeX equation fence integrity.

    Checks that $$ delimiters occur in even count, then that remaining single
    $ delimiters also occur in even count.  An odd count indicates an unclosed
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


def _split_trailing_paragraphs(body: str, n: int) -> Tuple[str, str, Optional[str]]:
    """Split *body* by moving the last *n* paragraph blocks to a separate string.

    Returns (donor_remainder, moved_text, equation_fence_warning_or_None).

    Paragraph boundaries are identified as one or more blank lines (\\n\\n+).
    If fewer than n boundaries exist the last available boundary is used,
    which may yield an empty remainder.  The equation_fence balance is checked
    on both halves; a warning is recorded but the split proceeds regardless.
    """
    # Collect paragraph boundary positions (start of blank-line gap, end of gap)
    boundaries: List[Tuple[int, int]] = [
        (m.start(), m.end()) for m in re.finditer(r"\n\n+", body)
    ]

    if not boundaries:
        # No paragraph boundaries: move nothing, keep body intact
        return body, "", None

    # We want to move the last `n` paragraphs, meaning we cut after
    # the (len(boundaries) - n)-th boundary from the start.
    cut_idx = max(0, len(boundaries) - n)
    cut_start, cut_end = boundaries[cut_idx]

    donor_remainder = body[:cut_start]
    moved_text = body[cut_end:]

    # Check equation_fence integrity
    equation_fence_warning: Optional[str] = None
    d_ok, d_warn = _check_equation_fence(donor_remainder)
    m_ok, m_warn = _check_equation_fence(moved_text)
    if not d_ok or not m_ok:
        equation_fence_warning = " | ".join(filter(None, [d_warn, m_warn]))

    return donor_remainder, moved_text, equation_fence_warning


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


def _resolve_segment_path(paper_dir: Path, source_ref: str) -> Optional[Path]:
    """Resolve a source segment reference (id or relative path) to an existing path."""
    segments_dir = paper_dir / "segments"
    if source_ref.startswith("/"):
        candidate = Path(source_ref)
    else:
        candidate = paper_dir / source_ref
    if candidate.exists():
        return candidate
    # Try as bare filename under segments/
    fallback = segments_dir / Path(source_ref).name
    if fallback.exists():
        return fallback
    # Try treating source_ref as a segment_id (no extension)
    by_id = segments_dir / f"{source_ref}.md"
    if by_id.exists():
        return by_id
    return None


def _find_segment_file(paper_dir: Path, seg_id: str, manifest_segments: List[dict]) -> Optional[Path]:
    """Locate the file for seg_id via the manifest, then fall back to a glob."""
    # Try manifest first
    for seg in manifest_segments:
        if isinstance(seg, dict) and seg.get("segment_id") == seg_id:
            file_ref = seg.get("file", "")
            if file_ref:
                path = _resolve_segment_path(paper_dir, file_ref)
                if path:
                    return path
    # Fall back to treating seg_id as a direct reference
    return _resolve_segment_path(paper_dir, seg_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_rebalances(cite_key: str, dry_run: bool = False) -> dict:
    """Execute all rebalances from _reseg_plan.json for the given cite_key.

    In dry_run mode returns a summary dict without writing any files.
    In live mode moves paragraph blocks between adjacent segments, updates
    _segment_manifest.json, and writes _reseg_rebalance_output.json.

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
    rebalances = plan.get("rebalances", [])

    # ── No rebalances needed ──────────────────────────────────────────────────
    if not rebalances:
        result: dict = {
            "cite_key": cite_key,
            "rebalances_planned": 0,
            "no_rebalances_needed": True,
        }
        if not dry_run:
            result.update(
                {
                    "rebalances_executed": 0,
                    "updated_segment_files": [],
                    "equation_fence_warnings": [],
                    "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                }
            )
            output_path = paper_dir / "_reseg_rebalance_output.json"
            with open(output_path, "w") as fh:
                json.dump(result, fh, indent=2)
        return result

    manifest = _load_manifest(paper_dir)
    manifest_segments: List[dict] = manifest.get("segments", [])

    # ── Dry-run: analyse without writing ─────────────────────────────────────
    if dry_run:
        dry_rebalances = []
        for entry in rebalances:
            from_seg = entry.get("from_segment", "")
            to_seg = entry.get("to_segment", "")
            n_paragraphs = entry.get("n_paragraphs_to_move", 1)

            from_path = _find_segment_file(paper_dir, from_seg, manifest_segments)
            to_path = _find_segment_file(paper_dir, to_seg, manifest_segments) if to_seg else None

            item: dict = {
                "from_segment": from_seg,
                "to_segment": to_seg,
                "n_paragraphs_to_move": n_paragraphs,
                "trigger_codes": entry.get("trigger_codes", []),
                "justification": entry.get("justification", ""),
            }

            if from_path is None:
                item["error"] = f"from_segment file not found: {from_seg}"
                dry_rebalances.append(item)
                continue

            from_content = from_path.read_text(encoding="utf-8")
            _, from_body = _read_frontmatter(from_content)
            from_remainder, moved_text, fence_warn = _split_trailing_paragraphs(
                from_body, n_paragraphs
            )

            item["from_segment_bytes"] = len(from_content.encode("utf-8"))
            item["estimated_moved_bytes"] = len(moved_text.encode("utf-8"))
            item["estimated_donor_remainder_bytes"] = len(from_remainder.encode("utf-8"))
            item["equation_fence_status"] = "equation_fence_warning" if fence_warn else "ok"
            if fence_warn:
                item["equation_fence_warning"] = fence_warn

            if to_path is None and to_seg:
                item["to_segment_warning"] = f"to_segment file not found: {to_seg}"
            elif to_path:
                to_content = to_path.read_text(encoding="utf-8")
                item["to_segment_bytes"] = len(to_content.encode("utf-8"))
                _, to_body = _read_frontmatter(to_content)
                combined = moved_text + ("\n\n" if moved_text and to_body else "") + to_body
                t_ok, t_warn = _check_equation_fence(combined)
                if not t_ok:
                    item.setdefault("equation_fence_warning", "")
                    item["equation_fence_warning"] = " | ".join(
                        filter(None, [item.get("equation_fence_warning"), t_warn])
                    )
                    item["equation_fence_status"] = "equation_fence_warning"

            dry_rebalances.append(item)

        return {
            "cite_key": cite_key,
            "rebalances_planned": len(rebalances),
            "no_rebalances_needed": False,
            "dry_run": True,
            "rebalances": dry_rebalances,
        }

    # ── Live run ──────────────────────────────────────────────────────────────
    updated_segment_files: List[str] = []
    equation_fence_warnings: List[dict] = []

    for entry in rebalances:
        from_seg = entry.get("from_segment", "")
        to_seg = entry.get("to_segment", "")
        n_paragraphs = entry.get("n_paragraphs_to_move", 1)

        from_path = _find_segment_file(paper_dir, from_seg, manifest_segments)
        if from_path is None:
            print(
                f"WARNING: from_segment file not found for rebalance '{from_seg}'; skipping.",
                file=sys.stderr,
            )
            continue

        if not to_seg:
            print(
                f"WARNING: rebalance entry for '{from_seg}' has no to_segment; skipping.",
                file=sys.stderr,
            )
            continue

        to_path = _find_segment_file(paper_dir, to_seg, manifest_segments)
        if to_path is None:
            print(
                f"WARNING: to_segment file not found for rebalance '{to_seg}'; skipping.",
                file=sys.stderr,
            )
            continue

        from_content = from_path.read_text(encoding="utf-8")
        to_content = to_path.read_text(encoding="utf-8")

        from_fm, from_body = _read_frontmatter(from_content)
        to_fm, to_body = _read_frontmatter(to_content)

        from_remainder, moved_text, fence_warn = _split_trailing_paragraphs(
            from_body, n_paragraphs
        )

        if fence_warn:
            equation_fence_warnings.append(
                {
                    "from_segment": from_seg,
                    "to_segment": to_seg,
                    "equation_fence_warning": fence_warn,
                }
            )

        # Prepend moved text to to_segment body
        separator = "\n\n" if moved_text and to_body else ""
        new_to_body = moved_text + separator + to_body

        # Check equation_fence integrity on merged recipient
        t_ok, t_warn = _check_equation_fence(new_to_body)
        if not t_ok:
            equation_fence_warnings.append(
                {
                    "to_segment": to_seg,
                    "equation_fence_warning": t_warn,
                }
            )

        # Write updated files
        new_from_content = from_fm + from_remainder
        new_to_content = to_fm + new_to_body

        from_path.write_text(new_from_content, encoding="utf-8")
        to_path.write_text(new_to_content, encoding="utf-8")

        # Track relative paths for output report
        try:
            rel_from = from_path.relative_to(paper_dir)
            rel_to = to_path.relative_to(paper_dir)
        except ValueError:
            rel_from = from_path
            rel_to = to_path

        updated_segment_files.append(str(rel_from))
        updated_segment_files.append(str(rel_to))

        # Update manifest token estimates
        for seg in manifest_segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("segment_id") == from_seg:
                seg["token_estimate"] = len(new_from_content) // 4
                seg["has_equations"] = bool(re.search(r"\$", new_from_content))
            elif seg.get("segment_id") == to_seg:
                seg["token_estimate"] = len(new_to_content) // 4
                seg["has_equations"] = bool(re.search(r"\$", new_to_content))

    # Atomic manifest write
    manifest["segments"] = manifest_segments
    _save_manifest(paper_dir, manifest)

    output = {
        "cite_key": cite_key,
        "rebalances_executed": len(rebalances),
        "updated_segment_files": list(dict.fromkeys(updated_segment_files)),
        "equation_fence_warnings": equation_fence_warnings,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    output_path = paper_dir / "_reseg_rebalance_output.json"
    with open(output_path, "w") as fh:
        json.dump(output, fh, indent=2)

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute rebalance plans from _reseg_plan.json."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and plan only; do not write any files.",
    )
    args = parser.parse_args()

    result = execute_rebalances(args.cite_key, dry_run=args.dry_run)

    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        if result.get("no_rebalances_needed"):
            print(f"[reseg_rebalance] No rebalances needed for '{args.cite_key}'.")
        else:
            n = result.get("rebalances_executed", 0)
            print(f"[reseg_rebalance] Executed {n} rebalance(s) for '{args.cite_key}'.")
            if result.get("equation_fence_warnings"):
                print(
                    f"  equation_fence_warnings: {len(result['equation_fence_warnings'])}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
