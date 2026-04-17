#!/usr/bin/env python3
"""Compatibility CLI for catalog build and snapshot workflows.

Delegates to catalog.build_catalog which:
- Uses segment_id as the primary catalog key for all catalog entries (S-002 fix).
- Asserts that core keyword segments (introduction, method, bayesian,
  experiment, conclusion) are never silently excluded from the catalog (S-001 fix).
  Keyword guard pattern: bayesian|introduction|experiment|conclusion
- Sets knowledge_gaps_file when segment_count > 20 or section_count > 15.
- Reads source_format from _translation_manifest.json when present, falling
  back to the value inferred from segment file extensions.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_CANONICAL_MANIFEST_REL_PATH = Path("segments/_segment_manifest.json")
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from catalog.build_catalog import main as _catalog_main


def snapshot_catalog(paper_bank_dir: str | Path) -> str | None:
    work_dir = Path(paper_bank_dir).expanduser().resolve()
    catalog_path = work_dir / "_catalog.yaml"
    if not catalog_path.exists() or not catalog_path.is_file():
        return None

    max_version = 0
    for candidate in work_dir.glob("_catalog_v*.yaml"):
        suffix = candidate.stem.removeprefix("_catalog_v")
        if suffix.isdigit():
            max_version = max(max_version, int(suffix))

    target = work_dir / f"_catalog_v{max_version + 1}.yaml"
    shutil.copy2(catalog_path, target)
    return str(target)


def _manifest_path(work_dir: Path) -> Path:
    return work_dir / _CANONICAL_MANIFEST_REL_PATH


def _run_build_mode(cite_key: str, work_dir: str, claim_domain: str = "academic") -> None:
    resolved_work = Path(work_dir).expanduser().resolve()
    manifest_path = _manifest_path(resolved_work)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    resolved_work_dir = str(resolved_work)
    original_argv = sys.argv[:]
    try:
        sys.argv = [
            str(_SCRIPTS_DIR / "catalog" / "build_catalog.py"),
            "--cite-key",
            cite_key,
            "--work-dir",
            resolved_work_dir,
            "--claim-domain",
            claim_domain,
        ]
        _catalog_main()
    finally:
        sys.argv = original_argv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build catalog artifacts and create catalog snapshots."
    )
    parser.add_argument("--cite-key", help="Paper cite key (build mode)")
    parser.add_argument("--work-dir", help="Paper working directory (build mode)")
    parser.add_argument(
        "--source-dir",
        help="Legacy alias for --work-dir (build mode compatibility).",
    )
    parser.add_argument(
        "--claim-domain",
        default="academic",
        choices=["academic", "institutional", "sell_side", "hybrid"],
        help="Claim domain written to catalog frontmatter (default: academic).",
    )
    subparsers = parser.add_subparsers(dest="command")
    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help="Copy _catalog.yaml to the next _catalog_vN.yaml file.",
    )
    snapshot_parser.add_argument(
        "--paper-bank-dir",
        required=True,
        help="Paper-bank directory that may contain _catalog.yaml.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "snapshot":
        snapshot_path = snapshot_catalog(args.paper_bank_dir)
        if snapshot_path is None:
            print("No catalog to snapshot.")
            return
        print(f"Wrote snapshot: {snapshot_path}")
        return

    cite_key = args.cite_key
    work_dir = args.work_dir or args.source_dir
    if not cite_key or not work_dir:
        parser.error(
            "build mode requires --cite-key and one of --work-dir/--source-dir."
        )
    _run_build_mode(cite_key, work_dir, claim_domain=args.claim_domain)


if __name__ == "__main__":
    main()
