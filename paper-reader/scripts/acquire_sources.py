#!/usr/bin/env python3
"""Copy a local LaTeX source tree into a paper-bank raw/ directory.

This script is intentionally local-only: it performs no network downloads.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CopyReport:
    source_dir: Path
    raw_dir: Path
    files_copied: int
    dirs_created: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy a local manuscript tree into paper-bank/<cite-key>/raw while preserving subdirectories."
    )
    parser.add_argument("--cite-key", required=True, help="Cite key used for the target paper-bank folder")
    parser.add_argument("--source-dir", required=True, help="Local source directory to copy (e.g., extracted LaTeX tree)")
    parser.add_argument(
        "--paper-bank-dir",
        required=True,
        help="Target paper directory under paper-bank (e.g., $PAPER_BANK/<cite-key>)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing raw/ by deleting it before copying",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the plan without writing files",
    )
    return parser.parse_args()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _raw_is_non_empty(raw_dir: Path) -> bool:
    if not raw_dir.exists():
        return False
    for _ in raw_dir.iterdir():
        return True
    return False


def _copy_tree(*, source_dir: Path, raw_dir: Path, dry_run: bool) -> CopyReport:
    files_copied = 0
    dirs_created = 0

    for root, dirnames, filenames in os.walk(source_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(source_dir)
        dst_root = raw_dir / rel_root

        if not dry_run and not dst_root.exists():
            dst_root.mkdir(parents=True, exist_ok=True)
            dirs_created += 1

        # Ensure directory traversal order doesn't affect determinism.
        dirnames.sort()
        filenames.sort()

        for filename in filenames:
            src = root_path / filename
            dst = dst_root / filename
            files_copied += 1
            if dry_run:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                # raw_dir is expected to be empty unless forced; keep this as a guardrail.
                raise FileExistsError(f"Refusing to overwrite existing file: {dst}")
            shutil.copy2(src, dst, follow_symlinks=False)

    return CopyReport(
        source_dir=source_dir,
        raw_dir=raw_dir,
        files_copied=files_copied,
        dirs_created=dirs_created,
    )


def acquire_sources(*, cite_key: str, source_dir: Path, paper_bank_dir: Path, force: bool, dry_run: bool) -> CopyReport:
    if not cite_key.strip():
        raise ValueError("cite_key must be non-empty")

    source_dir = source_dir.expanduser()
    paper_bank_dir = paper_bank_dir.expanduser()
    raw_dir = paper_bank_dir / "raw"

    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"--source-dir must be an existing directory: {source_dir}")

    if _is_within(raw_dir, source_dir):
        raise ValueError(f"Refusing to copy into a destination inside the source tree: {raw_dir}")

    if _raw_is_non_empty(raw_dir):
        if not force:
            print(
                f"raw/ already exists and is non-empty: {raw_dir} (skipping; use --force to overwrite)",
                file=sys.stderr,
            )
            return CopyReport(source_dir=source_dir, raw_dir=raw_dir, files_copied=0, dirs_created=0)
        if not dry_run:
            shutil.rmtree(raw_dir)

    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)

    return _copy_tree(source_dir=source_dir, raw_dir=raw_dir, dry_run=dry_run)


def main() -> int:
    args = parse_args()
    try:
        report = acquire_sources(
            cite_key=args.cite_key,
            source_dir=Path(args.source_dir),
            paper_bank_dir=Path(args.paper_bank_dir),
            force=args.force,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, FileExistsError, ValueError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover
        print(f"error: unexpected failure: {exc}", file=sys.stderr)
        return 1

    print(
        f"copied {report.files_copied} files into {report.raw_dir} (created {report.dirs_created} dirs)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
