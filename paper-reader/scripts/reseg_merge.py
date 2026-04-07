#!/usr/bin/env python3
"""Merge Executor — processes all merges entries from _reseg_plan.json.

Concatenates segments in catalog order with merged-from separators, archives
originals, and updates _segment_manifest.json.

Usage:
    python3 reseg_merge.py --cite-key <cite_key> [--dry-run]
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

PAPER_BANK = Path.home() / "Documents" / "paper-bank"
MAX_SEGMENT_BYTES = 20 * 1024  # 20 KB


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_plan(paper_dir: Path, cite_key: str) -> dict:
    plan_path = paper_dir / "_reseg_plan.json"
    if not plan_path.exists():
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
    """Resolve a source segment reference to an existing path."""
    segments_dir = paper_dir / "segments"
    if source_ref.startswith("/"):
        candidate = Path(source_ref)
    else:
        candidate = paper_dir / source_ref
    if candidate.exists():
        return candidate
    # Try bare filename under segments/
    fallback = segments_dir / Path(source_ref).name
    if fallback.exists():
        return fallback
    return None


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

def _build_separator(source_ids: List[str]) -> str:
    """Build the merged-from separator comment."""
    ids_str = ", ".join(source_ids)
    return f"<!-- merged from {ids_str} -->"


def _catalog_ordered_sources(
    source_segments: List[str],
    manifest_segments: List[dict],
) -> List[str]:
    """Return source_segments sorted by their position in the manifest."""
    # Build a position map: segment_id -> index
    pos: Dict[str, int] = {}
    for i, seg in enumerate(manifest_segments):
        if isinstance(seg, dict) and "segment_id" in seg:
            pos[seg["segment_id"]] = i
        if isinstance(seg, dict) and "file" in seg:
            # Also key by bare file stem
            stem = Path(seg["file"]).stem
            if stem not in pos:
                pos[stem] = i

    def sort_key(ref: str) -> int:
        stem = Path(ref).stem
        if ref in pos:
            return pos[ref]
        if stem in pos:
            return pos[stem]
        return 999999

    return sorted(source_segments, key=sort_key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_merges(cite_key: str, dry_run: bool = False) -> dict:
    """Execute all merges from _reseg_plan.json for the given cite_key.

    In dry_run mode returns a summary dict without writing any files.
    In live mode writes merged segment files, archives originals, updates
    _segment_manifest.json, and writes _reseg_merge_output.json.

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
    merges = plan.get("merges", [])

    # ── No merges needed ─────────────────────────────────────────────────────
    if not merges:
        result: dict = {
            "cite_key": cite_key,
            "merges_planned": 0,
            "no_merges_needed": True,
        }
        if not dry_run:
            result.update(
                {
                    "merges_executed": 0,
                    "new_segment_files": [],
                    "archived_files": [],
                    "merge_size_warnings": [],
                    "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                }
            )
            output_path = paper_dir / "_reseg_merge_output.json"
            with open(output_path, "w") as fh:
                json.dump(result, fh, indent=2)
        return result

    manifest = _load_manifest(paper_dir)
    manifest_segments: List[dict] = manifest.get("segments", [])
    segments_dir = paper_dir / "segments"

    # ── Dry-run: analyse without writing ─────────────────────────────────────
    if dry_run:
        dry_merges = []
        for entry in merges:
            proposed_id = entry.get("proposed_segment_id", "")
            label = entry.get("label", "")
            source_segments: List[str] = entry.get("source_segments", [])

            ordered = _catalog_ordered_sources(source_segments, manifest_segments)
            separator = _build_separator(
                [Path(s).stem for s in ordered]
            )

            pieces = []
            total_bytes = 0
            for src_ref in ordered:
                src_path = _resolve_segment_path(paper_dir, src_ref)
                if src_path is None:
                    pieces.append(
                        {"source": src_ref, "error": f"source file not found: {src_ref}"}
                    )
                    continue
                content = src_path.read_text(encoding="utf-8")
                size = len(content.encode("utf-8"))
                total_bytes += size
                pieces.append(
                    {
                        "source": src_ref,
                        "segment_id": src_path.stem,
                        "bytes": size,
                    }
                )

            size_warning = total_bytes > MAX_SEGMENT_BYTES

            dry_merges.append(
                {
                    "proposed_segment_id": proposed_id,
                    "label": label,
                    "source_segments": ordered,
                    "separator_comment": separator,
                    "estimated_merged_bytes": total_bytes,
                    "merge_size_warning": size_warning,
                    "sources": pieces,
                }
            )

        return {
            "cite_key": cite_key,
            "merges_planned": len(merges),
            "no_merges_needed": False,
            "dry_run": True,
            "merges": dry_merges,
        }

    # ── Live run ──────────────────────────────────────────────────────────────
    archive_dir = segments_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Build position index: segment_id -> list index
    seg_index_map: Dict[str, int] = {
        s["segment_id"]: i
        for i, s in enumerate(manifest_segments)
        if isinstance(s, dict) and "segment_id" in s
    }

    new_segment_files: List[str] = []
    archived_files: List[str] = []
    merge_size_warnings: List[dict] = []

    # Process merges in reverse manifest order (by position of first source)
    # to keep indices stable when modifying manifest_segments in-place
    def _first_source_pos(entry: dict) -> int:
        for src_ref in entry.get("source_segments", []):
            stem = Path(src_ref).stem
            if stem in seg_index_map:
                return seg_index_map[stem]
            if src_ref in seg_index_map:
                return seg_index_map[src_ref]
        return 999999

    sorted_merges = sorted(merges, key=_first_source_pos, reverse=True)

    for entry in sorted_merges:
        proposed_id = entry.get("proposed_segment_id", "")
        label = entry.get("label", proposed_id)
        source_segments: List[str] = entry.get("source_segments", [])

        # Order by catalog position
        ordered = _catalog_ordered_sources(source_segments, manifest_segments)

        # Read and concatenate
        parts: List[str] = []
        source_ids: List[str] = []
        for src_ref in ordered:
            src_path = _resolve_segment_path(paper_dir, src_ref)
            if src_path is None:
                print(
                    f"WARNING: source file not found for merge into '{proposed_id}': {src_ref}",
                    file=sys.stderr,
                )
                continue
            content = src_path.read_text(encoding="utf-8")
            parts.append(content)
            source_ids.append(src_path.stem)

        if not parts:
            print(
                f"WARNING: no valid source files found for merge '{proposed_id}'; skipping.",
                file=sys.stderr,
            )
            continue

        separator = _build_separator(source_ids)
        merged_content = f"\n{separator}\n".join(parts)
        merged_bytes = len(merged_content.encode("utf-8"))

        # Size check — informational only
        if merged_bytes > MAX_SEGMENT_BYTES:
            merge_size_warnings.append(
                {
                    "proposed_segment_id": proposed_id,
                    "merged_bytes": merged_bytes,
                    "limit_bytes": MAX_SEGMENT_BYTES,
                }
            )

        # Write merged segment
        merged_filename = f"{proposed_id}.md"
        merged_path = segments_dir / merged_filename
        merged_path.write_text(merged_content, encoding="utf-8")
        rel_merged = f"segments/{merged_filename}"
        new_segment_files.append(rel_merged)

        # Archive source segments and collect their manifest positions
        source_positions = []
        for src_ref in ordered:
            src_path = _resolve_segment_path(paper_dir, src_ref)
            if src_path is None:
                continue
            seg_id = src_path.stem
            archive_path = archive_dir / f"{seg_id}.v1.md"
            shutil.move(str(src_path), str(archive_path))
            archived_files.append(f"segments/archive/{seg_id}.v1.md")

            pos = seg_index_map.get(seg_id)
            if pos is not None:
                source_positions.append(pos)

        # Build new manifest entry
        new_entry = {
            "segment_id": proposed_id,
            "file": rel_merged,
            "label": label,
            "section_type": "unknown",
            "source_format": "markdown",
            "token_estimate": len(merged_content) // 4,
            "comprehension_status": "pending",
            "has_equations": "$" in merged_content,
            "merged_from": source_ids,
        }

        # Update manifest: remove source entries, insert merged at first source position
        source_ids_set = set(source_ids)
        insert_idx = min(source_positions) if source_positions else len(manifest_segments)

        # Remove source entries (highest index first to keep lower indices stable)
        for idx in sorted(source_positions, reverse=True):
            if idx < len(manifest_segments):
                manifest_segments.pop(idx)
                # Adjust insert_idx if we removed something before it
                if idx < insert_idx:
                    insert_idx -= 1

        manifest_segments.insert(insert_idx, new_entry)

        # Rebuild position index
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
        "merges_executed": len(sorted_merges),
        "new_segment_files": new_segment_files,
        "archived_files": archived_files,
        "merge_size_warnings": merge_size_warnings,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    output_path = paper_dir / "_reseg_merge_output.json"
    with open(output_path, "w") as fh:
        json.dump(output, fh, indent=2)

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute merge plans from _reseg_plan.json."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and plan only; do not write any files.",
    )
    args = parser.parse_args()

    result = execute_merges(args.cite_key, dry_run=args.dry_run)

    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        if result.get("no_merges_needed"):
            print(f"[reseg_merge] No merges needed for '{args.cite_key}'.")
        else:
            n = result.get("merges_executed", 0)
            print(f"[reseg_merge] Executed {n} merge(s) for '{args.cite_key}'.")
            if result.get("merge_size_warnings"):
                print(
                    f"  merge_size_warnings: {len(result['merge_size_warnings'])}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
