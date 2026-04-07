#!/usr/bin/env python3
"""Generate refs.bib from extraction metadata without mutating the source input."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from identity import (
    assign_cite_keys,
    clean_doi,
    clean_text,
    normalize_string_list,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate refs.bib from extraction metadata")
    parser.add_argument(
        "input_path",
        nargs="?",
        default="paper_manifest.json",
        help="Path to schema-v1 paper_manifest.json or a JSON list of per-paper metadata",
    )
    parser.add_argument(
        "--paper-bank-root",
        help="Fallback root containing per-paper directories with _catalog.yaml.",
    )
    parser.add_argument(
        "--catalog",
        action="append",
        default=[],
        help="Explicit _catalog.yaml path for fallback mode. May be repeated.",
    )
    parser.add_argument("--output", "-o", default="refs.bib", help="Output BibTeX path")
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        records = payload.get("entries")
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError("Input must be a manifest object or a list of paper records")

    if not isinstance(records, list):
        raise ValueError("Input does not contain a list of paper records")
    return [dict(record) for record in records if isinstance(record, dict)]


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on runtime env
        raise ValueError("PyYAML is required to load fallback _catalog.yaml files") from exc

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"_catalog.yaml must be a mapping: {path}")
    return payload


def _record_from_catalog(path: Path) -> dict[str, Any]:
    catalog = _load_yaml_mapping(path)
    paper = catalog.get("paper")
    if not isinstance(paper, dict):
        paper = {}

    record: dict[str, Any] = {
        "cite_key": clean_text(paper.get("cite_key") or path.parent.name),
        "title": clean_text(paper.get("title") or path.parent.name),
        "authors": normalize_string_list(paper.get("authors")),
        "year": clean_text(paper.get("year")),
        "journal": clean_text(paper.get("journal")),
        "doi": clean_doi(paper.get("doi")),
        "arxiv_id": clean_text(paper.get("arxiv_id")),
        "url": clean_text(paper.get("url") or paper.get("pdf_url")),
        "entry_type": clean_text(paper.get("entry_type")),
    }
    if not record["cite_key"]:
        raise ValueError(f"catalog does not include a usable cite key: {path}")
    return record


def discover_catalog_paths(
    input_path: Path,
    paper_bank_root: str | None,
    explicit_catalogs: list[str],
) -> list[Path]:
    candidates: list[Path] = []

    for raw in explicit_catalogs:
        path = Path(raw).expanduser()
        if path.exists():
            candidates.append(path)

    if paper_bank_root:
        root = Path(paper_bank_root).expanduser()
        if root.exists():
            candidates.extend(sorted(root.glob("*/_catalog.yaml")))
    else:
        # Minimal deterministic fallback when no manifest is present:
        # check local work-dir, then direct child paper dirs.
        local_catalog = Path.cwd() / "_catalog.yaml"
        if local_catalog.exists():
            candidates.append(local_catalog)
        candidates.extend(sorted(Path.cwd().glob("*/_catalog.yaml")))

    if input_path.parent.exists():
        candidates.extend(sorted(input_path.parent.glob("*/_catalog.yaml")))

    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(candidate)
    return unique_paths


def load_records_without_manifest(
    input_path: Path,
    paper_bank_root: str | None,
    explicit_catalogs: list[str],
) -> list[dict[str, Any]]:
    catalog_paths = discover_catalog_paths(
        input_path=input_path,
        paper_bank_root=paper_bank_root,
        explicit_catalogs=explicit_catalogs,
    )
    if not catalog_paths:
        raise ValueError(
            "paper_manifest.json not found and no _catalog.yaml fallback metadata discovered"
        )

    records: list[dict[str, Any]] = []
    for catalog_path in catalog_paths:
        records.append(_record_from_catalog(catalog_path))
    return records

def escape_bibtex(text: str) -> str:
    text = text.replace("\\", r"\\")
    text = text.replace("&", r"\&")
    text = text.replace("_", r"\_")
    text = text.replace("%", r"\%")
    text = text.replace("#", r"\#")
    return text


def format_authors_bibtex(authors: list[str]) -> str:
    formatted: list[str] = []
    for author in authors:
        cleaned = clean_text(author)
        if not cleaned:
            continue
        if "," in cleaned:
            formatted.append(cleaned)
            continue
        parts = cleaned.split()
        if len(parts) >= 2:
            formatted.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        else:
            formatted.append(cleaned)
    return " and ".join(formatted)


def infer_entry_type(record: dict[str, Any]) -> str:
    if clean_text(record.get("entry_type")):
        return clean_text(record["entry_type"]).lower()
    if clean_text(record.get("booktitle")):
        return "inproceedings"
    if clean_text(record.get("journal")) or clean_text(record.get("doi")) or clean_text(record.get("arxiv_id")):
        return "article"
    return "misc"


def append_field(lines: list[str], key: str, value: str) -> None:
    if clean_text(value):
        lines.append(f"  {key} = {{{value}}},")


def generate_entry(record: dict[str, Any]) -> str:
    cite_key = clean_text(record.get("cite_key"))
    arxiv_id = clean_text(record.get("arxiv_id"))
    doi = clean_doi(record.get("doi"))
    authors = normalize_string_list(record.get("authors"))
    title = clean_text(record.get("title"))
    year = clean_text(record.get("year"))
    categories = normalize_string_list(record.get("categories"))
    url = clean_text(record.get("url") or record.get("pdf_url"))
    abstract = clean_text(record.get("abstract"))

    entry_type = infer_entry_type(record)
    lines = [f"@{entry_type}{{{cite_key},"]

    append_field(lines, "author", format_authors_bibtex(authors))
    if title:
        lines.append(f"  title = {{{{{escape_bibtex(title)}}}}},")
    append_field(lines, "journal", escape_bibtex(clean_text(record.get("journal"))))
    append_field(lines, "booktitle", escape_bibtex(clean_text(record.get("booktitle"))))
    append_field(lines, "year", year)
    append_field(lines, "volume", clean_text(record.get("volume")))
    append_field(lines, "number", clean_text(record.get("number")))
    append_field(lines, "pages", clean_text(record.get("pages")))
    if arxiv_id:
        append_field(lines, "eprint", arxiv_id)
        append_field(lines, "archivePrefix", "arXiv")
        if categories:
            append_field(lines, "primaryClass", clean_text(categories[0]))
    if doi:
        append_field(lines, "doi", doi)
    append_field(lines, "pmid", clean_text(record.get("pmid")))
    if not url:
        if arxiv_id:
            url = f"https://arxiv.org/abs/{arxiv_id}"
        elif doi:
            url = f"https://doi.org/{doi}"
    append_field(lines, "url", escape_bibtex(url))
    if abstract:
        short_abstract = abstract[:500] + ("..." if len(abstract) > 500 else "")
        append_field(lines, "abstract", escape_bibtex(short_abstract))

    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    try:
        args = parse_args()
        input_path = Path(args.input_path)
        source_label = str(input_path)
        if input_path.exists():
            records = load_records(input_path)
        else:
            records = load_records_without_manifest(
                input_path=input_path,
                paper_bank_root=args.paper_bank_root,
                explicit_catalogs=args.catalog,
            )
            source_label = "fallback:_catalog.yaml"

        if not records:
            raise ValueError("No paper records found in input")

        assign_cite_keys(records)
        entries = [generate_entry(record) for record in sorted(records, key=lambda item: item["cite_key"])]

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n".join(
                [
                    f"% Auto-generated BibTeX - {len(entries)} entries",
                    f"% Source: {source_label}",
                    "",
                    "\n\n".join(entries),
                    "",
                ]
            ),
            encoding="utf-8",
        )

        print(f"Generated {len(entries)} BibTeX entries -> {output_path}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
