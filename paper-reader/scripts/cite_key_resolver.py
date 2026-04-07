"""Resolve cite keys from paper metadata (title + authors)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from identity import base_cite_key, clean_text, normalize_string_list


def _strip_latex(text: str) -> str:
    value = text or ""
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\[A-Za-z@]+\*?", " ", value)
    value = value.replace("{", " ").replace("}", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _extract_title_from_tex(tex_text: str) -> str | None:
    match = re.search(r"\\title\{([^}]*)\}", tex_text, flags=re.DOTALL)
    if not match:
        return None
    title = _strip_latex(match.group(1))
    return title or None


def _extract_authors_from_tex(tex_text: str) -> list[str]:
    surnames = [_strip_latex(m) for m in re.findall(r"\\snm\{([^}]*)\}", tex_text)]
    surnames = [value for value in surnames if value]
    if surnames:
        return surnames

    values = []
    for author_block in re.findall(r"\\author\{([^}]*)\}", tex_text, flags=re.DOTALL):
        cleaned = _strip_latex(author_block)
        if not cleaned:
            continue
        split_values = [item.strip() for item in re.split(r",| and ", cleaned) if item.strip()]
        values.extend(split_values or [cleaned])
    return values


def _iter_tex_candidates(paper_bank_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for rel in ("raw", "source", ""):
        root = paper_bank_dir / rel if rel else paper_bank_dir
        if not root.is_dir():
            continue
        candidates.extend(sorted(root.rglob("*.tex")))
    return candidates


def extract_metadata_for_cite_key(paper_bank_dir: Path) -> dict[str, Any]:
    for tex_path in _iter_tex_candidates(paper_bank_dir):
        try:
            tex_text = tex_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = _extract_title_from_tex(tex_text)
        authors = _extract_authors_from_tex(tex_text)
        if not title or not authors:
            continue
        return {
            "title": clean_text(title),
            "authors": normalize_string_list(authors),
            "source_path": str(tex_path),
        }
    return {
        "title": "",
        "authors": [],
        "source_path": "",
    }


def derive_cite_key_from_metadata(*, title: str, authors: list[str]) -> str | None:
    normalized_title = clean_text(title)
    normalized_authors = normalize_string_list(authors)
    if not normalized_title or not normalized_authors:
        return None
    return base_cite_key(
        {
            "title": normalized_title,
            "authors": normalized_authors,
            "year": "",
        }
    )


def resolve_cite_key(*, requested_cite_key: str, paper_bank_dir: Path) -> tuple[str, dict[str, Any]]:
    requested = clean_text(requested_cite_key)
    metadata = extract_metadata_for_cite_key(paper_bank_dir)
    derived = derive_cite_key_from_metadata(
        title=metadata.get("title", ""),
        authors=metadata.get("authors", []),
    )
    if derived:
        metadata["derived_cite_key"] = derived
        metadata["requested_cite_key"] = requested
        metadata["key_override_applied"] = bool(requested and requested != derived)
        return derived, metadata

    metadata["derived_cite_key"] = None
    metadata["requested_cite_key"] = requested
    metadata["key_override_applied"] = False
    return (requested or "paper"), metadata


def migrate_alias_dir_to_canonical(
    *,
    requested_dir: Path,
    resolved_cite_key: str,
) -> tuple[Path, dict[str, Any]]:
    requested = requested_dir.expanduser()
    canonical = requested if requested.name == resolved_cite_key else requested.parent / resolved_cite_key

    report: dict[str, Any] = {
        "alias_applied": canonical != requested,
        "requested_dir": str(requested),
        "canonical_dir": str(canonical),
        "moved_items": [],
    }

    if canonical == requested:
        canonical.mkdir(parents=True, exist_ok=True)
        return canonical, report

    canonical.mkdir(parents=True, exist_ok=True)
    if not requested.exists():
        return canonical, report

    candidate_dirs = (
        "raw",
        "source",
        "supplementary",
        "segments",
        "pdf_segments",
    )
    candidate_files = (
        "translated_full.md",
        "translated_full_pdf.md",
        "_translation_manifest.json",
        "_translation_manifest_pdf.json",
        "_translation_warnings.log",
        "_segment_manifest.json",
        "_catalog.yaml",
        "_xref_index.yaml",
        "_theorem_index.json",
    )

    for name in candidate_dirs:
        source = requested / name
        destination = canonical / name
        if not source.exists() or destination.exists():
            continue
        shutil.move(str(source), str(destination))
        report["moved_items"].append(name)

    for name in candidate_files:
        source = requested / name
        destination = canonical / name
        if not source.exists() or destination.exists():
            continue
        shutil.move(str(source), str(destination))
        report["moved_items"].append(name)

    return canonical, report
