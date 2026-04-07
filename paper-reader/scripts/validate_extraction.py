#!/usr/bin/env python3
"""Validate extraction outputs using the plan's Level 1 offline checks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
AUTO_BLOCK_RE = re.compile(
    rf"{re.escape('<!-- AUTO-GENERATED:BEGIN -->')}\n.*?\n{re.escape('<!-- AUTO-GENERATED:END -->')}",
    re.DOTALL,
)
REQUIRED_FRONTMATTER_KEYS = {
    "schema_version",
    "canonical_id",
    "cite_key",
    "title",
    "authors",
    "year",
    "source_type",
    "source_path",
    "bank_path",
    "source_parse_status",
    "bibliography_status",
    "content_status",
    "extraction_confidence",
    "validation_status",
    "review_status",
    "auto_block_hash",
}
REQUIRED_CLAIMS_KEYS = {
    "schema_version",
    "cite_key",
    "canonical_id",
    "content_status",
    "extraction_confidence",
    "claims",
}
CLAIM_TYPES = {
    "theorem",
    "assumption",
    "methodology",
    "empirical",
    "connection",
    "limitation",
    "data-availability",
    "code-availability",
}
ALLOWED_CONTENT_STATUS = {"full", "partial", "metadata-only"}
BIB_ENTRY_RE = re.compile(r"@\w+\{([^,\s]+),")

# Sidecar file suffixes written by summarize_paper.py.  Files matching any of
# these suffixes are companion files, not main paper notes, and must be excluded
# from schema-v2 validation.
SIDECAR_SUFFIXES = ("-notation.md", "-glossary.md")


def _is_sidecar(note_path: Path) -> bool:
    """Return True if the file is a known sidecar companion, not a main paper note.

    Sidecars are written by summarize_paper.py alongside the main literature note.
    Patterns: *-notation.md, *-glossary.md (see SIDECAR_SUFFIXES).
    """
    return any(note_path.name.endswith(suffix) for suffix in SIDECAR_SUFFIXES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate extraction outputs under a vault root")
    parser.add_argument(
        "vault_root",
        nargs="?",
        help="Vault root containing literature/papers and refs.bib (legacy positional form).",
    )
    parser.add_argument(
        "--vault-root",
        dest="vault_root_flag",
        help="Vault root containing literature/papers and refs.bib.",
    )
    parser.add_argument("--refs", help="Optional explicit refs.bib path")
    parser.add_argument(
        "--paper-bank",
        help=(
            "Root paper-bank directory for v2 artifact checks "
            "(e.g., $PAPER_BANK). "
            "Must be the root directory, not a per-paper subdirectory "
            "(e.g., NOT $PAPER_BANK/some_paper). "
            "Enables checks for: _catalog.yaml, "
            "segments/_segment_manifest.json, _summary_layers.json, per-section "
            "note readiness (intro.md), and summary note presence in Citadel."
        ),
    )
    args = parser.parse_args()
    resolved_vault_root = args.vault_root_flag or args.vault_root
    if not resolved_vault_root:
        parser.error("one of positional vault_root or --vault-root is required")
    args.vault_root = resolved_vault_root
    return args


def frontmatter_lines(note_text: str) -> tuple[str, list[str]]:
    match = FRONTMATTER_RE.match(note_text)
    if not match:
        raise ValueError("Note is missing YAML frontmatter")
    return match.group(1), match.group(1).splitlines()


def frontmatter_value(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", frontmatter, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip('"')


def parse_frontmatter_keys(frontmatter: str) -> set[str]:
    keys: set[str] = set()
    for line in frontmatter.splitlines():
        if line.startswith(" ") or line.startswith("-") or ":" not in line:
            continue
        key, _ = line.split(":", 1)
        keys.add(key.strip())
    return keys


def auto_block_hash(note_text: str) -> str | None:
    match = AUTO_BLOCK_RE.search(note_text)
    if not match:
        return None
    import hashlib

    return hashlib.sha256(match.group(0).encode("utf-8")).hexdigest()


def parse_bibtex_keys(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"refs.bib not found: {path}")
    return BIB_ENTRY_RE.findall(path.read_text(encoding="utf-8"))


def validate_claim_record(path: Path, expected_cite_key: str, expected_canonical_id: str) -> list[str]:
    errors: list[str] = []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return [f"{path.name}: claims sidecar must be a JSON object"]

    missing = sorted(REQUIRED_CLAIMS_KEYS - set(payload.keys()))
    if missing:
        errors.append(f"{path.name}: missing claims keys {', '.join(missing)}")

    if payload.get("cite_key") != expected_cite_key:
        errors.append(f"{path.name}: cite_key mismatch")
    if payload.get("canonical_id") != expected_canonical_id:
        errors.append(f"{path.name}: canonical_id mismatch")
    if payload.get("content_status") not in ALLOWED_CONTENT_STATUS:
        errors.append(f"{path.name}: invalid content_status")

    claims = payload.get("claims")
    if not isinstance(claims, list):
        errors.append(f"{path.name}: claims must be a list")
        return errors

    if payload.get("content_status") == "metadata-only" and claims:
        errors.append(f"{path.name}: metadata-only sidecars must have empty claims")

    for index, claim in enumerate(claims):
        prefix = f"{path.name}: claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if claim.get("type") not in CLAIM_TYPES:
            errors.append(f"{prefix} has invalid type")
        anchor = claim.get("source_anchor")
        if not isinstance(anchor, dict):
            errors.append(f"{prefix} missing source_anchor")
            continue
        if not anchor.get("locator"):
            errors.append(f"{prefix} missing source_anchor.locator")
        if anchor.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"{prefix} has invalid source_anchor.confidence")
        if claim.get("type") == "connection" and claim.get("linked_paper_status") not in {"in-corpus", "out-of-corpus"}:
            errors.append(f"{prefix} missing linked_paper_status")
    return errors


def validate_note(path: Path, claims_path: Path | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    note_text = path.read_text(encoding="utf-8")
    frontmatter, _ = frontmatter_lines(note_text)
    keys = parse_frontmatter_keys(frontmatter)
    missing = sorted(REQUIRED_FRONTMATTER_KEYS - keys)
    if missing:
        errors.append(f"{path.name}: missing frontmatter keys {', '.join(missing)}")

    cite_key = frontmatter_value(frontmatter, "cite_key")
    canonical_id = frontmatter_value(frontmatter, "canonical_id")
    bank_path = frontmatter_value(frontmatter, "bank_path")
    stored_hash = frontmatter_value(frontmatter, "auto_block_hash")
    review_status = frontmatter_value(frontmatter, "review_status")
    actual_hash = auto_block_hash(note_text)

    if not AUTO_BLOCK_RE.search(note_text):
        errors.append(f"{path.name}: missing AUTO-GENERATED block")
    if not bank_path or bank_path in {"null", '""', "''"}:
        errors.append(f"{path.name}: missing bank_path value")
    if stored_hash != actual_hash:
        if review_status == "user-edited":
            warnings.append(f"{path.name}: auto_block_hash mismatch is expected for user-edited notes")
        else:
            errors.append(f"{path.name}: auto_block_hash mismatch")

    if claims_path is None:
        return errors, warnings

    if not claims_path.exists():
        warnings.append(f"{path.name}: claims sidecar missing (allowed in v2 mode): {claims_path.name}")
        return errors, warnings

    errors.extend(validate_claim_record(claims_path, cite_key or "", canonical_id or ""))
    return errors, warnings


def validate_v2_catalog(catalog_path: Path, cite_key: str) -> tuple[list[str], list[str]]:
    """Validate _catalog.yaml existence and constitution_version field presence."""
    errors: list[str] = []
    warnings: list[str] = []
    if not catalog_path.exists():
        errors.append(f"{cite_key}: _catalog.yaml not found at {catalog_path}")
        return errors, warnings
    try:
        import yaml  # noqa: PLC0415

        catalog_data = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except ImportError:
        warnings.append(f"{cite_key}: PyYAML not available; skipped _catalog.yaml content checks")
        return errors, warnings
    except Exception as exc:
        errors.append(f"{cite_key}: failed to parse _catalog.yaml: {exc}")
        return errors, warnings
    if not isinstance(catalog_data, dict):
        errors.append(f"{cite_key}: _catalog.yaml must be a YAML mapping")
        return errors, warnings
    paper_meta = catalog_data.get("paper") or {}
    if "constitution_version" not in paper_meta:
        warnings.append(
            f"{cite_key}: _catalog.yaml paper section missing constitution_version"
            " (comprehension init may not have run)"
        )
    return errors, warnings


def validate_v2_segment_manifest(segment_manifest_path: Path, cite_key: str) -> tuple[list[str], list[str]]:
    """Validate segments/_segment_manifest.json for each paper."""
    errors: list[str] = []
    warnings: list[str] = []
    if not segment_manifest_path.exists():
        errors.append(f"{cite_key}: segment manifest not found at {segment_manifest_path}")
        return errors, warnings

    try:
        payload = json.loads(segment_manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{cite_key}: failed to parse _segment_manifest.json: {exc}")
        return errors, warnings

    if isinstance(payload, dict):
        segments = payload.get("segments")
        if isinstance(segments, list):
            if not segments:
                warnings.append(f"{cite_key}: _segment_manifest.json has no segments")
        else:
            errors.append(f"{cite_key}: _segment_manifest.json missing segments list")
    elif isinstance(payload, list):
        if not payload:
            warnings.append(f"{cite_key}: _segment_manifest.json list is empty")
    else:
        errors.append(f"{cite_key}: _segment_manifest.json must be an object or list")

    return errors, warnings


def validate_v2_summary_layers(summary_layers_path: Path, cite_key: str) -> tuple[list[str], list[str]]:
    """Validate _summary_layers.json presence and parseability."""
    errors: list[str] = []
    warnings: list[str] = []
    if not summary_layers_path.exists():
        errors.append(f"{cite_key}: _summary_layers.json not found at {summary_layers_path}")
        return errors, warnings

    try:
        payload = json.loads(summary_layers_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{cite_key}: failed to parse _summary_layers.json: {exc}")
        return errors, warnings

    if not isinstance(payload, dict):
        errors.append(f"{cite_key}: _summary_layers.json must be a JSON object")
        return errors, warnings

    l3_points = payload.get("l3_summary_points")
    if isinstance(l3_points, list) and not l3_points:
        warnings.append(f"{cite_key}: _summary_layers.json has empty l3_summary_points")

    return errors, warnings


def validate_v2_section_notes(papers_dir: Path, cite_key: str) -> tuple[list[str], list[str]]:
    """Validate per-section note readiness in Citadel; intro.md is the representative check."""
    errors: list[str] = []
    warnings: list[str] = []
    section_dir = papers_dir / cite_key
    intro_path = section_dir / "intro.md"
    if not section_dir.exists():
        warnings.append(
            f"{cite_key}: section notes directory not present"
            " — v2 comprehension pass not yet run"
        )
        return errors, warnings
    if not intro_path.exists():
        errors.append(f"{cite_key}: intro.md missing from section notes directory")
    return errors, warnings


def validate_v2_summary(papers_dir: Path, cite_key: str) -> tuple[list[str], list[str]]:
    """Validate summary artifact presence in Citadel (papers/<cite_key>.md)."""
    errors: list[str] = []
    warnings: list[str] = []
    summary_path = papers_dir / f"{cite_key}.md"
    if not summary_path.exists():
        errors.append(f"{cite_key}: summary note not found ({cite_key}.md)")
    return errors, warnings


def _is_stub(paper_dir: Path) -> bool:
    """Return True if the paper has no complete pipeline run.

    A paper is a stub if _catalog.yaml is absent, or if the catalog field
    pipeline_status is not 'complete'.
    """
    catalog_path = paper_dir / "_catalog.yaml"
    if not catalog_path.exists():
        return True
    try:
        import yaml  # noqa: PLC0415

        catalog_data = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(catalog_data, dict):
        return True
    return catalog_data.get("pipeline_status") != "complete"


def validate_v2_artifacts(
    papers_dir: Path,
    paper_bank: Path,
    cite_keys: set[str],
) -> tuple[list[str], list[str]]:
    """Run v2 checks: catalog, segment manifest, summary layers, notes, and summary note.

    Papers without _catalog.yaml or with pipeline_status != 'complete' are treated
    as stubs: their structural failures are downgraded to warnings so they do not
    cause exit 1. The primary paper (pipeline_status == 'complete') is checked at
    full severity.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for cite_key in sorted(cite_keys):
        paper_dir = paper_bank / cite_key
        catalog_path = paper_bank / cite_key / "_catalog.yaml"

        # Stub detection: absent _catalog.yaml or pipeline_status != 'complete'.
        stub = _is_stub(paper_dir)

        cat_errors, cat_warnings = validate_v2_catalog(catalog_path, cite_key)
        segment_manifest_path = paper_dir / "segments" / "_segment_manifest.json"
        seg_errors, seg_warnings = validate_v2_segment_manifest(segment_manifest_path, cite_key)
        summary_layers_path = paper_dir / "_summary_layers.json"
        layers_errors, layers_warnings = validate_v2_summary_layers(summary_layers_path, cite_key)
        sec_errors, sec_warnings = validate_v2_section_notes(papers_dir, cite_key)
        sum_errors, sum_warnings = validate_v2_summary(papers_dir, cite_key)

        paper_errors = cat_errors + seg_errors + layers_errors + sec_errors + sum_errors
        paper_warnings = (
            cat_warnings + seg_warnings + layers_warnings + sec_warnings + sum_warnings
        )

        if stub:
            # Downgrade structural errors to warnings for stub papers.
            warnings.extend(f"[stub] {e}" for e in paper_errors)
        else:
            errors.extend(paper_errors)
        warnings.extend(paper_warnings)

    return errors, warnings


def resolve_papers_dir(vault_root: Path) -> Path:
    v2_papers = vault_root / "literature" / "papers"
    if v2_papers.exists():
        return v2_papers
    return vault_root / "papers"


def resolve_claims_dir(vault_root: Path) -> Path:
    v2_claims = vault_root / "literature" / "claims"
    if v2_claims.exists():
        return v2_claims
    return vault_root / "claims"


def validate_vault_root(
    vault_root: Path,
    refs_path: Path,
    paper_bank: Path | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    papers_dir = resolve_papers_dir(vault_root)
    claims_dir = resolve_claims_dir(vault_root)

    if not papers_dir.exists():
        return [f"papers directory not found: {papers_dir}"], warnings

    claims_enabled = claims_dir.exists()
    if not claims_enabled:
        warnings.append("claims directory not found (allowed in v2 mode)")

    note_paths = sorted(papers_dir.glob("*.md"))
    note_keys: set[str] = set()
    stub_keys: set[str] = set()
    for note_path in note_paths:
        # Skip sidecar companion files written by summarize_paper.py.
        if _is_sidecar(note_path):
            warnings.append(f"sidecar — skipped: {note_path.name}")
            continue

        claims_path = (claims_dir / f"{note_path.stem}.json") if claims_enabled else None
        note_errors, note_warnings = validate_note(note_path, claims_path)

        # Stub propagation: if paper-bank is available and the paper is a stub,
        # downgrade note-level validation errors to warnings so the validator
        # does not exit 1 for incomplete pipeline entries.
        #
        # Self-lookup guard: if --paper-bank was called with a per-paper directory
        # (e.g., $PAPER_BANK/some_paper) instead of the root ($PAPER_BANK),
        # paper_bank.name matches this note's stem.  In that case, do NOT run the
        # stub check for this note — it is the primary paper being validated.
        if paper_bank is not None and note_path.stem == paper_bank.name:
            # per-paper path detected: skip stub check for the primary paper
            is_stub_note = False
        else:
            is_stub_note = paper_bank is not None and _is_stub(paper_bank / note_path.stem)
        if is_stub_note:
            warnings.extend(f"[stub] {e}" for e in note_errors)
            stub_keys.add(note_path.stem)
        else:
            errors.extend(note_errors)
        warnings.extend(note_warnings)
        note_keys.add(note_path.stem)

    bib_keys = parse_bibtex_keys(refs_path)
    bib_key_set = set(bib_keys)
    if len(bib_keys) != len(bib_key_set):
        errors.append("refs.bib has duplicate cite keys")

    for key in sorted(note_keys):
        if key not in bib_key_set:
            if key in stub_keys:
                warnings.append(f"[stub] refs.bib missing cite_key for note {key}")
            else:
                errors.append(f"refs.bib missing cite_key for note {key}")

    if claims_enabled:
        for claims_path in sorted(claims_dir.glob("*.json")):
            if claims_path.stem not in note_keys:
                errors.append(f"orphan claims sidecar without note: {claims_path.name}")

    for key in sorted(bib_key_set - note_keys):
        warnings.append(f"orphan refs.bib entry without note: {key}")

    if paper_bank is not None:
        if note_keys:
            v2_keys = set(note_keys)
        elif bib_key_set:
            v2_keys = set(bib_key_set)
        else:
            v2_keys = {path.name for path in sorted(paper_bank.glob("*")) if path.is_dir()}

        if not v2_keys:
            warnings.append(f"no cite keys found for v2 checks under {paper_bank}")
        else:
            v2_errors, v2_warnings = validate_v2_artifacts(papers_dir, paper_bank, v2_keys)
            errors.extend(v2_errors)
            warnings.extend(v2_warnings)

    return errors, warnings


def main() -> int:
    try:
        args = parse_args()
        vault_root = Path(args.vault_root)
        refs_path = Path(args.refs) if args.refs else vault_root / "refs.bib"
        paper_bank = Path(args.paper_bank) if args.paper_bank else None
        errors, warnings = validate_vault_root(vault_root, refs_path, paper_bank=paper_bank)
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        if errors:
            return 1
        papers_dir = resolve_papers_dir(vault_root)
        print(f"Extraction outputs valid: {vault_root}")
        print(f"Notes: {len(list(papers_dir.glob('*.md')))}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
