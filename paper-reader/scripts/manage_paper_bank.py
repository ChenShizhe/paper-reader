#!/usr/bin/env python3
"""Store extraction artifacts in paper-bank and update its machine manifest."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move or copy extracted paper files into paper-bank")
    parser.add_argument("--cite-key", required=True, help="Final cite key used for the paper note")
    parser.add_argument("--canonical-id", default="", help="Canonical paper ID (optional)")
    parser.add_argument("--title", default="", help="Paper title (optional)")
    parser.add_argument(
        "--paper-bank-root",
        "--paper-bank",
        dest="paper_bank",
        default=str(Path.home() / "Documents" / "paper-bank"),
        help="Paper-bank root directory",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize paper-bank scaffold (creates cite-key/raw and cite-key/segments directories)",
    )
    parser.add_argument("--pdf", action="append", default=[], help="Path to a PDF artifact")
    parser.add_argument("--source", action="append", default=[], help="Path to source file or extracted source directory")
    parser.add_argument("--supplementary", action="append", default=[], help="Path to supplementary file or directory")
    parser.add_argument("--metadata-json", default="", help="Optional metadata JSON to patch with bank_path")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of moving them")
    parser.add_argument("--output", default="", help="Optional report output path")
    return parser.parse_args()


def _copy_or_move(src: Path, dst: Path, copy_mode: bool) -> None:
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    if copy_mode:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    else:
        shutil.move(str(src), str(dst))


def _normalize_file_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in name)


def _ingest_group(
    *,
    inputs: list[str],
    destination: Path,
    copy_mode: bool,
    default_name: str | None = None,
) -> list[str]:
    stored: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)

    for idx, raw in enumerate(inputs):
        src = Path(raw).expanduser()
        if not src.exists():
            continue

        if default_name and idx == 0 and src.is_file():
            target_name = _normalize_file_name(default_name)
        else:
            target_name = _normalize_file_name(src.name)

        dst = destination / target_name
        _copy_or_move(src, dst, copy_mode)
        stored.append(str(dst))
    return stored


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1", "papers": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"schema_version": "1", "papers": {}}
    if "papers" not in payload or not isinstance(payload["papers"], dict):
        payload["papers"] = {}
    if "schema_version" not in payload:
        payload["schema_version"] = "1"
    return payload


def _update_metadata_file(path: Path, bank_path: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Metadata JSON must contain an object: {path}")
    payload["bank_path"] = bank_path
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def init_paper_bank(*, cite_key: str, paper_bank_root: Path) -> dict[str, Any]:
    if not cite_key.strip():
        raise ValueError("cite_key must be non-empty")

    bank_root = paper_bank_root.expanduser()
    paper_root = bank_root / cite_key.strip()
    raw_root = paper_root / "raw"
    segments_root = paper_root / "segments"

    bank_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    segments_root.mkdir(parents=True, exist_ok=True)

    return {
        "cite_key": cite_key.strip(),
        "bank_path": str(paper_root),
        "raw_dir": str(raw_root),
        "segments_dir": str(segments_root),
    }


def manage_paper_bank(
    *,
    cite_key: str,
    canonical_id: str,
    title: str,
    paper_bank_root: Path,
    pdf_paths: list[str],
    source_paths: list[str],
    supplementary_paths: list[str],
    metadata_json: str,
    copy_mode: bool,
) -> dict[str, Any]:
    if not cite_key.strip():
        raise ValueError("cite_key must be non-empty")

    paper_root = paper_bank_root.expanduser() / cite_key.strip()
    paper_root.mkdir(parents=True, exist_ok=True)
    (paper_root / "raw").mkdir(parents=True, exist_ok=True)
    (paper_root / "segments").mkdir(parents=True, exist_ok=True)
    source_root = paper_root / "source"
    supplementary_root = paper_root / "supplementary"

    stored_pdfs = _ingest_group(
        inputs=pdf_paths,
        destination=paper_root,
        copy_mode=copy_mode,
        default_name=f"{cite_key}.pdf",
    )
    stored_sources = _ingest_group(
        inputs=source_paths,
        destination=source_root,
        copy_mode=copy_mode,
    )
    stored_supp = _ingest_group(
        inputs=supplementary_paths,
        destination=supplementary_root,
        copy_mode=copy_mode,
    )

    manifest_path = paper_bank_root.expanduser() / "_manifest.json"
    manifest = _load_manifest(manifest_path)
    papers = manifest["papers"]
    papers[cite_key] = {
        "cite_key": cite_key,
        "canonical_id": canonical_id or None,
        "title": title or None,
        "bank_path": str(paper_root),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pdf_files": stored_pdfs,
        "source_files": stored_sources,
        "supplementary_files": stored_supp,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if metadata_json:
        _update_metadata_file(Path(metadata_json).expanduser(), str(paper_root))

    return {
        "cite_key": cite_key,
        "bank_path": str(paper_root),
        "manifest_path": str(manifest_path),
        "pdf_files": stored_pdfs,
        "source_files": stored_sources,
        "supplementary_files": stored_supp,
        "copied": copy_mode,
    }


def main() -> int:
    args = parse_args()
    if args.init:
        report = init_paper_bank(
            cite_key=args.cite_key,
            paper_bank_root=Path(args.paper_bank),
        )
    else:
        report = manage_paper_bank(
            cite_key=args.cite_key,
            canonical_id=args.canonical_id,
            title=args.title,
            paper_bank_root=Path(args.paper_bank),
            pdf_paths=list(args.pdf),
            source_paths=list(args.source),
            supplementary_paths=list(args.supplementary),
            metadata_json=args.metadata_json,
            copy_mode=args.copy,
        )

    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
