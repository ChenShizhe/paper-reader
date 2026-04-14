#!/usr/bin/env python3
"""comprehend_intro.py — M5 intro comprehension orchestration driver.

Orchestrates Steps 4.1 → 4.2 → (dummy notes + author notes + xref citations) → 4.3
plus catalog snapshot and catalog v2 enrichment updates.

Importable API
--------------
    from comprehend_intro import run_intro_comprehension
    result = run_intro_comprehension("smith2024neural")

CLI
---
    python3 comprehend_intro.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import re
import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Step module imports (all six required by M5)
# ---------------------------------------------------------------------------
from intro_positioner import run_step41, extract_notation_entries
from intro_reader import run_step42
from dummy_note_writer import create_dummy_notes
from author_note_writer import create_author_notes
from notation_extractor import run_step43
from xref_writer import write_intro_citations

# ---------------------------------------------------------------------------
# Infrastructure imports
# ---------------------------------------------------------------------------
from build_catalog import snapshot_catalog
from context_loader import load_layer_a
from meta_note_query import query_meta_notes
from subagent_contracts import SubagentOutput

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"
DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
_SEGMENT_MANIFEST_REL_PATH = Path("segments/_segment_manifest.json")

STEPS_PLANNED = [
    "step_4.1_positioning",
    "step_4.2_intro_reader",
    "create_dummy_notes",
    "create_author_notes",
    "write_intro_citations",
    "step_4.3_notation",
    "catalog_v2_enrichment",
]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_inputs(paper_dir: Path) -> list[str]:
    """Return a list of error messages for any missing required inputs."""
    errors: list[str] = []

    catalog_path = paper_dir / "_catalog.yaml"
    if not catalog_path.exists():
        errors.append(f"Missing required file: {catalog_path}")

    manifest_path = paper_dir / _SEGMENT_MANIFEST_REL_PATH
    if not manifest_path.exists():
        errors.append(f"Missing required file: {manifest_path}")

    seg_dir = paper_dir / "segments"
    if not seg_dir.exists() or not any(seg_dir.glob("*.md")):
        errors.append(f"Missing segment files in: {seg_dir}")

    return errors


# ---------------------------------------------------------------------------
# Catalog helpers
# ---------------------------------------------------------------------------

def _load_catalog(catalog_path: Path) -> dict:
    with open(catalog_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _extract_paper_meta(catalog: dict) -> dict:
    return catalog.get("paper") or {}


def _get_domain_tags(catalog: dict) -> list[str]:
    paper_meta = _extract_paper_meta(catalog)
    return (
        paper_meta.get("vault_tags")
        or catalog.get("domain_tags")
        or catalog.get("tags")
        or []
    )


# ---------------------------------------------------------------------------
# Metadata extraction pre-step helpers
# Writes paper.title, paper.authors, paper.year, paper.abstract into _catalog.yaml
# so the positioner receives meaningful metadata before Step 4.1 runs.
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r'\b(199\d|200\d|20[12]\d|203\d)\b')
_AUTHOR_LINE_RE = re.compile(
    r'^[A-Z][a-zA-Z\-\'\.]+(\s+[A-Z][a-zA-Z\-\'\.]+)+'
    r'(,\s*[A-Z][a-zA-Z\-\'\.]+(\s+[A-Z][a-zA-Z\-\'\.]+)+)*$'
)


def _strip_frontmatter_text(text: str) -> str:
    """Remove YAML frontmatter from *text*, returning only the body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


def _parse_segment_metadata(text: str) -> dict:
    """Parse title, authors, year, and abstract from a segment file's text.

    Reads title from YAML frontmatter, then scans the body for year (4-digit),
    author names (capitalized comma-separated line), and abstract text.
    """
    lines = text.splitlines()
    fm: dict = {}
    if lines and lines[0].strip() == "---":
        end = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end = i
                break
        if end is not None:
            raw = "\n".join(lines[1:end])
            try:
                fm = yaml.safe_load(raw) or {}
            except Exception:
                fm = {}

    title = str(fm.get("title", "")).strip()
    body = _strip_frontmatter_text(text)

    # Year: first 4-digit year in 1990–2039 range
    year_match = _YEAR_RE.search(body)
    year = int(year_match.group(1)) if year_match else None

    # Authors: first non-heading, non-abstract line in first 20 body lines
    # that matches a capitalized comma-separated name pattern
    authors: list[str] = []
    for bl in [ln.strip() for ln in body.splitlines() if ln.strip()][:20]:
        if bl.startswith("#") or re.search(r'\babstract\b|\bintroduction\b', bl, re.IGNORECASE):
            continue
        if _AUTHOR_LINE_RE.match(bl):
            authors = [a.strip() for a in re.split(r',\s*', bl)]
            break

    # Abstract: text after "Abstract." heading until first numbered section
    abstract = ""
    abs_match = re.search(
        r'(?:^|\n)Abstract\.?\s*\n+(.*?)(?=\n+\d+\s+[A-Z]|\n+#{1,3}\s+|\Z)',
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if abs_match:
        abstract = abs_match.group(1).strip()

    return {"title": title, "authors": authors, "year": year, "abstract": abstract}


def _extract_and_write_metadata(paper_dir: Path) -> dict:
    """Extract paper metadata and write into _catalog.yaml (paper.title, paper.authors, paper.year).

    Reads title, authors, year, and abstract from _translation_manifest.json
    (preferred) or from the first segment file (fallback).  Updates the
    ``paper`` section of _catalog.yaml so the positioner receives real metadata.

    Returns the extracted metadata dict.
    """
    catalog_path = paper_dir / "_catalog.yaml"
    if not catalog_path.exists():
        return {}

    meta: dict = {}

    # Prefer _translation_manifest.json when it contains the relevant fields
    manifest_path = paper_dir / "_translation_manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
            for k in ("title", "authors", "year", "abstract"):
                if manifest.get(k):
                    meta[k] = manifest[k]
        except Exception:
            pass

    # Fallback: read metadata from the first sorted segment file
    if not meta.get("title"):
        seg_dir = paper_dir / "segments"
        if seg_dir.exists():
            for seg_path in sorted(seg_dir.glob("*.md")):
                try:
                    text = seg_path.read_text(encoding="utf-8")
                    seg_meta = _parse_segment_metadata(text)
                    for k in ("title", "authors", "year", "abstract"):
                        if seg_meta.get(k) and not meta.get(k):
                            meta[k] = seg_meta[k]
                except OSError:
                    pass
                if meta.get("title"):
                    break

    if not meta:
        return meta

    # Write extracted metadata into the catalog's paper section
    with open(catalog_path, encoding="utf-8") as fh:
        catalog_data = yaml.safe_load(fh) or {}

    paper_section = catalog_data.setdefault("paper", {})
    for k in ("title", "authors", "year", "abstract"):
        if meta.get(k):
            paper_section[k] = meta[k]

    with open(catalog_path, "w", encoding="utf-8") as fh:
        yaml.dump(catalog_data, fh, default_flow_style=False, allow_unicode=True)

    return meta


# ---------------------------------------------------------------------------
# Skill-root resolution
# ---------------------------------------------------------------------------

def _resolve_skill_root(skill_root_arg: str) -> Path:
    sroot = Path(skill_root_arg)
    if not sroot.is_absolute():
        sroot = Path.cwd() / sroot
    return sroot


# ---------------------------------------------------------------------------
# Dry-run path
# ---------------------------------------------------------------------------

def _subagent_dispatch(args: argparse.Namespace) -> int:
    """Generate a structured dispatch plan and write it to <paper-bank-dir>/<cite_key>/_dispatch_plan.json.

    Does not call the Anthropic SDK.  The dispatch plan describes which segments
    to read and which output files to produce so that Claude Code can act as the
    comprehension engine.
    """
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    cite_key = args.cite_key
    paper_dir = paper_bank_root / cite_key

    vroot = Path(os.path.expanduser(args.vault_root))
    paper_note_dir = vroot / "literature" / "papers" / cite_key

    dispatch_plan: dict[str, Any] = {
        "schema_version": "1.0",
        "cite_key": cite_key,
        "script": "comprehend_intro.py",
        "section_type": "introduction",
        "segment_ids": [
            {
                "section_type": "introduction",
                "description": "All introduction-type segments in segments/_segment_manifest.json",
            }
        ],
        "required_reads": [
            str(paper_dir / "_catalog.yaml"),
            str(paper_dir / "segments" / "_segment_manifest.json"),
            str(paper_dir / "segments"),
        ],
        "output_paths": {
            "intro_md": str(paper_note_dir / "intro.md"),
            "notation_md": str(paper_note_dir / "notation.md"),
            "notation_dict_yaml": str(paper_dir / "notation_dict.yaml"),
            "xref_index_yaml": str(paper_dir / "_xref_index.yaml"),
            "catalog_yaml": str(paper_dir / "_catalog.yaml"),
        },
        "steps_planned": STEPS_PLANNED,
    }

    dispatch_plan_path = paper_dir / "_dispatch_plan.json"
    paper_dir.mkdir(parents=True, exist_ok=True)
    with open(dispatch_plan_path, "w", encoding="utf-8") as fh:
        json.dump(dispatch_plan, fh, indent=2, ensure_ascii=False)

    print(json.dumps(
        {"dispatch_plan_path": str(dispatch_plan_path), "cite_key": cite_key},
        indent=2,
        ensure_ascii=False,
    ))
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    """Validate inputs and print dispatch plan as JSON; exit 0 on success, 1 on missing inputs."""
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    vault_root = os.path.expanduser(args.vault_root)
    cite_key = args.cite_key
    paper_dir = paper_bank_root / cite_key

    # Validate required inputs; exit 1 if any are missing
    errors = _validate_inputs(paper_dir)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    inputs_valid = True
    catalog = _load_catalog(paper_dir / "_catalog.yaml")
    domain_tags = _get_domain_tags(catalog)

    # Try to load Layer A context (non-fatal in dry-run)
    layer_a_loaded = False
    layer_b_notes_found = 0
    try:
        skill_root = _resolve_skill_root(args.skill_root)
        load_layer_a(str(skill_root))
        layer_a_loaded = True

        # Query Layer B meta-notes for the intro section type
        layer_b_notes = query_meta_notes(
            vault_root=vault_root,
            domain_tags=domain_tags,
            section_type="introduction",
        )
        layer_b_notes_found = len(layer_b_notes)
    except Exception:
        pass

    # Build planned output paths
    vroot = Path(os.path.expanduser(args.vault_root))
    paper_note_dir = vroot / "literature" / "papers" / cite_key
    outputs_planned = [
        str(paper_note_dir / "intro.md"),
        str(paper_note_dir / "notation.md"),
        str(paper_dir / "notation_dict.yaml"),
        str(paper_dir / "_xref_index.yaml"),
        str(paper_dir / "_catalog.yaml"),
    ]

    result: dict[str, Any] = {
        "cite_key": cite_key,
        "steps_planned": STEPS_PLANNED,
        "inputs_valid": inputs_valid,
        "layer_a_loaded": layer_a_loaded,
        "layer_b_notes_found": layer_b_notes_found,
        "outputs_planned": outputs_planned,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Live run path
# ---------------------------------------------------------------------------

def _live_run(args: argparse.Namespace) -> int:
    """Execute the full intro comprehension pipeline; return exit code."""
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    vault_root = os.path.expanduser(args.vault_root)
    cite_key = args.cite_key
    model = args.model
    paper_dir = paper_bank_root / cite_key

    # Validate required inputs
    errors = _validate_inputs(paper_dir)
    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    # Load Layer A context
    skill_root = _resolve_skill_root(args.skill_root)
    try:
        layer_a = load_layer_a(str(skill_root))
    except FileNotFoundError as exc:
        print(f"Error loading Layer A context: {exc}", file=sys.stderr)
        return 1

    # Load catalog and query Layer B meta-notes
    catalog = _load_catalog(paper_dir / "_catalog.yaml")
    paper_meta = _extract_paper_meta(catalog)
    domain_tags = _get_domain_tags(catalog)

    layer_b_notes = query_meta_notes(
        vault_root=vault_root,
        domain_tags=domain_tags,
        section_type="introduction",
    )

    artifacts_produced: list[str] = []

    # ------------------------------------------------------------------
    # PRE-STEP: Extract paper.title, paper.authors, paper.year, paper.abstract
    # from the first segment (or _translation_manifest.json) and write into
    # _catalog.yaml so the positioner has meaningful metadata.
    # ------------------------------------------------------------------
    _extract_and_write_metadata(paper_dir)
    # Reload catalog after metadata write so paper_meta reflects new values
    catalog = _load_catalog(paper_dir / "_catalog.yaml")
    paper_meta = _extract_paper_meta(catalog)

    # ------------------------------------------------------------------
    # Step 4.1 — Metadata extraction and vault positioning
    # ------------------------------------------------------------------
    meta = run_step41(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
        model=model,
    )
    if meta.get("intro_md_path"):
        artifacts_produced.append(meta["intro_md_path"])

    # ------------------------------------------------------------------
    # Step 4.2 — Introduction block classification and citation extraction
    # ------------------------------------------------------------------
    result = run_step42(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
        skill_root=str(skill_root),
        model=model,
    )
    citations_extracted: list[dict] = result.get("citations_extracted", [])

    # ------------------------------------------------------------------
    # Task 06 — Write intro citations to _xref_index.yaml
    # (runs before dummy notes so xref entries exist when dummy_note_created is set)
    # ------------------------------------------------------------------
    xref_result = write_intro_citations(
        citations=citations_extracted,
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
    )
    if xref_result.get("xref_path"):
        artifacts_produced.append(xref_result["xref_path"])

    # ------------------------------------------------------------------
    # Task 03 — Create dummy notes for foundation/alternative citations
    # ------------------------------------------------------------------
    create_dummy_notes(
        citations=citations_extracted,
        citing_cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
    )

    # ------------------------------------------------------------------
    # Task 04 — Create author notes
    # ------------------------------------------------------------------
    author_metadata: dict[str, Any] = {
        "cite_key": cite_key,
        "title": paper_meta.get("title", ""),
        "authors": paper_meta.get("authors", []),
        "year": paper_meta.get("year", ""),
        "venue": paper_meta.get("journal") or paper_meta.get("venue", ""),
        "paper_type": meta.get("paper_type") or paper_meta.get("source_format", ""),
    }
    create_author_notes(
        metadata=author_metadata,
        cite_key=cite_key,
    )

    # ------------------------------------------------------------------
    # Step 4.3 — Notation extraction
    # ------------------------------------------------------------------
    notation_result = run_step43(
        cite_key=cite_key,
        paper_bank_root=str(paper_bank_root),
        vault_root=vault_root,
        model=model,
    )
    for path_val in notation_result.get("output_paths", {}).values():
        if path_val:
            artifacts_produced.append(path_val)

    # ------------------------------------------------------------------
    # Notation fallback: if Step 4.3 produced zero entries (e.g. LLM returned
    # empty list), scan intro + model + methods segments with the LaTeX-aware
    # heuristic from intro_positioner and overwrite notation_dict.yaml.
    # ------------------------------------------------------------------
    if notation_result.get("entries_written", 0) == 0:
        fallback_entries: list[dict] = []
        _seg_dir = paper_dir / "segments"
        _FALLBACK_TYPES = {"introduction", "model", "section", "methods", "method_theory", "method"}
        if _seg_dir.exists():
            for _seg_path in sorted(_seg_dir.glob("*.md")):
                try:
                    _seg_text = _seg_path.read_text(encoding="utf-8")
                    _seg_fm_lines = _seg_text.splitlines()
                    _seg_fm: dict = {}
                    if _seg_fm_lines and _seg_fm_lines[0].strip() == "---":
                        _fm_end = None
                        for _fi, _fl in enumerate(_seg_fm_lines[1:], start=1):
                            if _fl.strip() == "---":
                                _fm_end = _fi
                                break
                        if _fm_end is not None:
                            try:
                                _seg_fm = yaml.safe_load("\n".join(_seg_fm_lines[1:_fm_end])) or {}
                            except Exception:
                                pass
                    _stype = _seg_fm.get("section_type", "")
                    if _stype in _FALLBACK_TYPES or not _stype:
                        _body = _strip_frontmatter_text(_seg_text)
                        _label = _seg_fm.get("title", _stype or _seg_path.stem)
                        fallback_entries.extend(extract_notation_entries(_body, _label))
                except OSError:
                    pass

        if fallback_entries:
            # Deduplicate by symbol and write notation_dict.yaml
            _seen_syms: set[str] = set()
            _unique: list[dict] = []
            for _e in fallback_entries:
                if _e["symbol"] not in _seen_syms:
                    _seen_syms.add(_e["symbol"])
                    _unique.append(_e)
            _notation_yaml_path = paper_dir / "notation_dict.yaml"
            _notation_fallback = {
                "cite_key": cite_key,
                "extraction_step": "4.1-fallback",
                "notation_source": "intro positioner heuristic (LaTeX-aware)",
                "notation_types": ["function", "variable", "parameter", "operator", "set", "constant"],
                "entries": _unique,
            }
            with open(_notation_yaml_path, "w", encoding="utf-8") as fh:
                yaml.dump(_notation_fallback, fh, default_flow_style=False, allow_unicode=True)
            if str(_notation_yaml_path) not in artifacts_produced:
                artifacts_produced.append(str(_notation_yaml_path))
            notation_result = dict(notation_result)
            notation_result["entries_written"] = len(_unique)

    # ------------------------------------------------------------------
    # Snapshot catalog BEFORE any catalog modification
    # ------------------------------------------------------------------
    snapshot_path = snapshot_catalog(paper_dir)
    if snapshot_path:
        print(f"Catalog snapshot written: {snapshot_path}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Write catalog v2 enrichment
    # ------------------------------------------------------------------
    catalog_path = paper_dir / "_catalog.yaml"
    with open(catalog_path, encoding="utf-8") as fh:
        catalog_data = yaml.safe_load(fh) or {}

    paper_section = catalog_data.setdefault("paper", {})
    paper_section["catalog_version"] = 2
    paper_section["comprehension_status"] = "intro_complete"
    paper_section["notation_status"] = notation_result.get("notation_segment_found", False)
    paper_section["positioning_tags"] = meta.get("paper_type", "unknown")
    paper_section["paper_type"] = meta.get("paper_type", "unknown")

    # Propagate intro_complete status to introduction sections and their segments
    for _sec in catalog_data.get("sections", []):
        if _sec.get("section_type") == "introduction":
            _sec["comprehension_status"] = "intro_complete"
    for _seg in catalog_data.get("segments", []):
        if _seg.get("section_type") == "introduction":
            _seg["comprehension_status"] = "intro_complete"

    with open(catalog_path, "w", encoding="utf-8") as fh:
        yaml.dump(catalog_data, fh, default_flow_style=False, allow_unicode=True)

    artifacts_produced.append(str(catalog_path))

    # ------------------------------------------------------------------
    # Build SubagentOutput and emit result
    # ------------------------------------------------------------------
    subagent_out = SubagentOutput(
        cite_key=cite_key,
        section_type="introduction",
        status="intro_complete",
        notes_written=artifacts_produced,
        catalog_updates={
            "catalog_version": 2,
            "comprehension_status": "intro_complete",
        },
        flags=[],
        extra={
            "intro_artifacts_produced": artifacts_produced,
            "citations_extracted_count": len(citations_extracted),
            "layer_b_notes_used": len(layer_b_notes),
        },
    )

    print(json.dumps({
        "cite_key": subagent_out.cite_key,
        "status": subagent_out.status,
        "intro_artifacts_produced": subagent_out.extra["intro_artifacts_produced"],
        "catalog_updates": subagent_out.catalog_updates,
    }, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Public importable API
# ---------------------------------------------------------------------------

def run_intro_comprehension(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Orchestrate full intro comprehension for *cite_key*.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    skill_root:
        Path to the paper-reader skill directory; default ``skills/paper-reader``.
    model:
        Anthropic model ID; overridden by ``PAPER_READER_MODEL`` env var.
    dry_run:
        When True, validate inputs and return plan dict without writing files or calling LLM.

    Returns
    -------
    dict with keys matching the CLI JSON output.
    """
    # Build a minimal namespace that mirrors argparse output
    class _Args:
        pass

    ns = _Args()
    ns.cite_key = cite_key
    ns.paper_bank_root = paper_bank_root
    ns.vault_root = vault_root
    ns.skill_root = skill_root
    ns.model = model
    ns.dry_run = dry_run

    import io
    import contextlib

    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        if dry_run:
            code = _dry_run(ns)
        else:
            code = _live_run(ns)

    raw = out_buf.getvalue()
    if raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {"exit_code": code, "raw_output": raw}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M5 intro comprehension orchestrator (Steps 4.1→4.2→4.3)."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print dispatch plan as JSON; no LLM calls, no file writes.",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Root directory of the paper bank (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    parser.add_argument(
        "--vault-root",
        default=DEFAULT_VAULT_ROOT,
        help=f"Root of the Citadel vault (default: {DEFAULT_VAULT_ROOT}).",
    )
    parser.add_argument(
        "--skill-root",
        default=DEFAULT_SKILL_ROOT,
        help=(
            "Path to the paper-reader skill directory "
            f"(default: {DEFAULT_SKILL_ROOT}, resolved from cwd)."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model ID (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--llm-dispatch",
        choices=["inline", "subagent"],
        default="inline",
        dest="llm_dispatch",
        help=(
            "Dispatch mode. 'inline' runs section reading in-process. "
            "'subagent' generates a structured dispatch plan JSON at "
            "<paper-bank-dir>/<cite_key>/_dispatch_plan.json for agent-driven dispatch."
        ),
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.dry_run:
        sys.exit(_dry_run(args))
    elif args.llm_dispatch == "subagent":
        sys.exit(_subagent_dispatch(args))
    else:
        sys.exit(_live_run(args))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
