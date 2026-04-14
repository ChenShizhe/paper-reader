#!/usr/bin/env python3
"""run_pipeline.py – v2 end-to-end pipeline orchestrator for paper-reader.

Accepts --source-format, --source-path, and --cite-key as the primary inputs
and executes all pipeline steps in deterministic order. Step order is
format-aware:
  - PDF path: segment -> translate
  - Non-PDF path: translate -> segment

Pipeline steps:
  1. preflight      – preflight_extraction.py
  2. translate/segment (format-aware ordering)
  3. comprehend     – comprehend_paper.py
  4. vault          – vault_integration/run_vault_ingestion.py
  5. summarize      – summarize_paper.py
  6. validate       – validate_extraction.py
  7. bibtex         – generate_bibtex.py (soft; produces refs.bib)
  8. post           – run report summary

Partial failures are recorded in the run report. Soft steps (preflight,
comprehend, summarize, validate) log warnings and continue; hard steps
(translate, segment, vault) abort the pipeline on failure.

A JSON run report is written to --run-report-path (default: run_report.json)
for audit reproducibility.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from cite_key_utils import generate_cite_key as _generate_cite_key
    _CITE_KEY_UTILS_AVAILABLE = True
except ImportError:
    _CITE_KEY_UTILS_AVAILABLE = False


SCRIPTS_DIR = Path(__file__).resolve().parent
CHECKPOINT_SCHEMA_VERSION = "v1"
RESUME_COMPLETE_STATUSES = {"passed", "soft_failure", "skipped", "skipped_checkpoint"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_step(name: str, cmd: list[str], *, soft: bool = False) -> dict:
    """Run one pipeline step; return a result dict."""
    result: dict = {
        "step": name,
        "cmd": cmd,
        "started_at": _ts(),
        "status": "pending",
        "returncode": None,
        "stderr_tail": "",
    }
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        result["returncode"] = proc.returncode
        result["finished_at"] = _ts()
        stderr_lines = (proc.stderr or "").splitlines()
        result["stderr_tail"] = "\n".join(stderr_lines[-20:])

        if proc.returncode == 0:
            result["status"] = "passed"
        elif soft:
            result["status"] = "soft_failure"
            print(
                f"[run_pipeline] WARNING: step '{name}' exited {proc.returncode} (soft, continuing)",
                file=sys.stderr,
            )
        else:
            result["status"] = "failed"
            print(
                f"[run_pipeline] ERROR: step '{name}' exited {proc.returncode}",
                file=sys.stderr,
            )
            if proc.stderr:
                print(proc.stderr[-2000:], file=sys.stderr)

    except FileNotFoundError as exc:
        result["finished_at"] = _ts()
        result["returncode"] = -1
        result["status"] = "soft_failure" if soft else "failed"
        result["stderr_tail"] = str(exc)
        label = "WARNING" if soft else "ERROR"
        print(f"[run_pipeline] {label}: step '{name}' script not found – {exc}", file=sys.stderr)

    return result


def _load_checkpoint(checkpoint_path: Path) -> dict | None:
    if not checkpoint_path.exists():
        return None
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"[run_pipeline] WARNING: failed to read checkpoint at {checkpoint_path}: {exc}",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print(
            f"[run_pipeline] WARNING: checkpoint at {checkpoint_path} is not a JSON object; ignoring.",
            file=sys.stderr,
        )
        return None
    return data


def _checkpoint_completed_steps(checkpoint: dict) -> set[str]:
    step_results = checkpoint.get("step_results")
    if not isinstance(step_results, list):
        return set()

    completed: set[str] = set()
    for entry in step_results:
        if not isinstance(entry, dict):
            continue
        step_name = entry.get("step")
        status = entry.get("status")
        if isinstance(step_name, str) and status in RESUME_COMPLETE_STATUSES:
            completed.add(step_name)
    return completed


def _write_checkpoint(
    *,
    checkpoint_path: Path,
    cite_key: str,
    source_format: str,
    source_path: str,
    paper_bank: Path,
    run_report_path: Path,
    vault_requests_path: Path,
    step_order: list[str],
    step_results: list[dict],
    pipeline_status: str,
) -> None:
    completed_steps: list[str] = []
    seen: set[str] = set()
    for entry in step_results:
        step_name = entry.get("step")
        status = entry.get("status")
        if (
            isinstance(step_name, str)
            and status in RESUME_COMPLETE_STATUSES
            and step_name not in seen
        ):
            completed_steps.append(step_name)
            seen.add(step_name)

    checkpoint_payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "generated_at": _ts(),
        "pipeline_status": pipeline_status,
        "cite_key": cite_key,
        "source_format": source_format,
        "source_path": source_path,
        "last_completed_step": completed_steps[-1] if completed_steps else None,
        "completed_steps": completed_steps,
        "step_order": step_order,
        "key_output_paths": {
            "run_report_path": str(run_report_path.resolve()),
            "paper_bank_dir": str(paper_bank),
            "vault_requests_path": str(vault_requests_path),
            "preflight_report_path": str((run_report_path.parent / "preflight_report.json").resolve()),
        },
        "step_results": step_results,
    }

    try:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(
            json.dumps(checkpoint_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        print(
            f"[run_pipeline] WARNING: failed to write checkpoint at {checkpoint_path}: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "v2 pipeline orchestrator: run all paper-reader steps end-to-end "
            "for a single paper source."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Pipeline steps (in order):\n"
            "  1. preflight   – preflight_extraction.py\n"
            "  2. segment/translate – source-format aware ordering\n"
            "     * pdf: segment before translate\n"
            "     * non-pdf: translate before segment\n"
            "  3. comprehend  – comprehend_paper.py\n"
            "  4. vault       – vault_integration/run_vault_ingestion.py\n"
            "                  (knowledge-maester handoff enforced)\n"
            "  5. summarize   – summarize_paper.py\n"
            "  6. validate    – validate_extraction.py\n"
            "  7. bibtex      – generate_bibtex.py (soft; produces refs.bib)\n"
            "  8. post        – run report summary\n"
            "\n"
            "Exit codes:\n"
            "  0 – pipeline passed (soft failures are allowed)\n"
            "  1 – hard step failure or argument error\n"
        ),
    )
    p.add_argument(
        "--cite-key",
        required=True,
        help=(
            "Cite key for the paper (e.g. smith2023paper). "
            "Convention: {firstauthor_lastname}{year}{shortword}, all lowercase "
            "(e.g. smith2024neural, doe1991optimal)."
        ),
    )
    p.add_argument(
        "--source-format",
        default="auto",
        choices=["auto", "latex", "pdf", "html", "markdown"],
        help="Format of the source input (default: auto).",
    )
    p.add_argument(
        "--source-path",
        required=True,
        help="Path to the source directory or file for this paper.",
    )
    p.add_argument(
        "--paper-bank-dir",
        default="",
        help="Paper-bank directory for this cite key (default: ~/Documents/paper-bank/<cite-key>).",
    )
    p.add_argument(
        "--vault-root",
        default="",
        help="Citadel vault root directory (default: ~/Documents/Citadel).",
    )
    p.add_argument(
        "--run-report-path",
        default="run_report.json",
        help="Path to write the JSON run report (default: run_report.json).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the pipeline plan as JSON without executing any steps.",
    )
    p.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip the preflight step (useful when the environment is known-good).",
    )
    p.add_argument(
        "--skip-steps",
        default="",
        help="Comma-separated step names to skip (e.g. 'vault,summarize').",
    )
    p.add_argument(
        "--resume-from",
        default="",
        help=(
            "Skip all steps before the named step (e.g. '--resume-from vault_prepare'). "
            "Useful after manually completing the comprehension step."
        ),
    )
    p.add_argument(
        "--checkpoint-path",
        default="checkpoint.json",
        help=(
            "Path to checkpoint JSON used for resume. Relative paths are resolved "
            "from the run-report directory (default: checkpoint.json)."
        ),
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers for resolving paths
# ---------------------------------------------------------------------------

def _resolve_paper_bank(cite_key: str, override: str) -> Path:
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))) / cite_key


def _resolve_vault(override: str) -> Path:
    if override:
        return Path(override).expanduser()
    return Path("~/Documents/Citadel").expanduser()


def _resolve_segment_source_dir(source_path: str) -> Path:
    """Return a directory path suitable for segment_paper.py --source-dir."""
    resolved = Path(source_path).expanduser()
    return resolved if resolved.is_dir() else resolved.parent


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return collapsed or "paper"


def _resolve_memory_card_dir() -> Path:
    # scripts/ -> paper-reader/ -> skills/ -> workspace root
    try:
        workspace_root = SCRIPTS_DIR.parents[2]
    except IndexError:
        workspace_root = Path.cwd()
    return workspace_root / "experiences" / "paper-reader-runs"


def _load_catalog_meta(paper_bank: Path) -> dict:
    """Load _catalog.yaml from the paper-bank dir; return {} on any failure."""
    catalog_path = paper_bank / "_catalog.yaml"
    if not catalog_path.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _check_cite_key_convention(cite_key: str, paper_bank: Path) -> None:
    """Warn (advisory only) when cite_key does not match the firstauthor+year+keyword convention."""
    if not _CITE_KEY_UTILS_AVAILABLE:
        return
    catalog = _load_catalog_meta(paper_bank)
    authors: list[str] = catalog.get("authors") or []
    first_author = authors[0].strip() if authors else ""
    # Use the last whitespace-separated token as the lastname
    lastname = first_author.rsplit(None, 1)[-1] if first_author else ""
    if not lastname:
        # Fall back to directory basename (no-op: suggested == cite_key)
        lastname = cite_key
    year = catalog.get("year")
    title: str = catalog.get("title") or ""
    suggested = _generate_cite_key(lastname, year, title)
    if suggested and suggested != cite_key:
        print(
            f"[run_pipeline] WARNING: cite-key '{cite_key}' does not match the "
            f"convention {{lastname}}{{year}}{{keyword}}; suggested key: '{suggested}'",
            file=sys.stderr,
        )


def _persist_run_report(
    *,
    report: dict,
    run_report_path: Path,
    paper_bank: Path,
    cite_key: str,
    source_path: str,
    vault_root: Path,
) -> tuple[Path | None, Path | None]:
    persistent_report_path: Path | None = None
    memory_card_path: Path | None = None

    try:
        paper_bank.mkdir(parents=True, exist_ok=True)
        persistent_report_path = paper_bank / "_run_report.json"
        persistent_report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"[run_pipeline] Persistent run report copy written to {persistent_report_path}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[run_pipeline] WARNING: failed to persist run report copy: {exc}", file=sys.stderr)

    try:
        memory_dir = _resolve_memory_card_dir()
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_card_path = memory_dir / f"{_slugify(cite_key)}-run-report.md"
        card_lines = [
            "---",
            "kind: paper-reader-run-report",
            f"cite_key: {cite_key}",
            f"created_at: {_ts()}",
            "---",
            "",
            "# Paper Reader Run Report",
            "",
            f"- cite_key: `{cite_key}`",
            f"- source_path: `{source_path}`",
            f"- run_report_path: `{run_report_path.resolve()}`",
            f"- persistent_run_report_path: `{persistent_report_path}`" if persistent_report_path else "- persistent_run_report_path: unavailable",
            f"- vault_root: `{vault_root}`",
            f"- pipeline_status: `{report.get('pipeline_status', 'unknown')}`",
            "",
            "This card is a stable pointer so future sessions can locate run_report.json.",
            "",
        ]
        memory_card_path.write_text("\n".join(card_lines), encoding="utf-8")
        print(f"[run_pipeline] Memory card written to {memory_card_path}", file=sys.stderr)
    except Exception as exc:
        print(f"[run_pipeline] WARNING: failed to write run-report memory card: {exc}", file=sys.stderr)

    return persistent_report_path, memory_card_path


# ---------------------------------------------------------------------------
# Supplement source detection (F-001)
# ---------------------------------------------------------------------------
#
# Three-tier classification logic:
#   Tier 1 — Filename patterns (supplement / appendix in the stem) or
#             first-page content keywords ('Supplementary', 'Appendix') →
#             classify the matching file directly as the supplement.
#   Tier 2 — No supplement markers on one file but the other is already
#             identified as main → classify the unmarked file as the
#             supplement (covers the most common "main + supp.pdf" layout).
#   Tier 3 — Both files are unmarked (extremely unlikely) → use file-size
#             heuristic (larger = main) and flag the pair for a QC pass.
#
# For multiple LaTeX files: the root file is identified by \begin{document}
# or a .latexmain marker; all other .tex files are treated as supplementary.

_SUPP_FILENAME_RE = re.compile(r"supplement|appendix", re.IGNORECASE)
_SUPP_CONTENT_RE = re.compile(r"[Ss]upplementary|[Aa]ppendix")


def _first_page_text(filepath: Path, max_bytes: int = 8192) -> str:
    """Return the first *max_bytes* of a file as text, ignoring decode errors."""
    try:
        return filepath.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except Exception:
        return ""


def _has_supplement_markers(filepath: Path) -> bool:
    """Return True when a filename pattern or first-page content marks a supplement."""
    if _SUPP_FILENAME_RE.search(filepath.stem):
        return True
    return bool(_SUPP_CONTENT_RE.search(_first_page_text(filepath)))


def _find_latex_root(tex_files: list[Path]) -> Path | None:
    """Identify the root .tex file by a .latexmain marker or \\begin{document}."""
    for f in tex_files:
        if f.with_suffix(".latexmain").exists():
            return f
    for f in tex_files:
        try:
            if r"\begin{document}" in f.read_text(encoding="utf-8", errors="replace"):
                return f
        except Exception:
            pass
    return None


def _classify_sources(source_path: str) -> tuple[Path, Path | None, str]:
    """Scan *source_path* for multiple PDFs / .tex files and classify them.

    Returns ``(main_path, supplement_path_or_None, classification_note)``.
    *supplement_path_or_None* is ``None`` when only one source file is found.
    """
    src = Path(source_path).expanduser()
    scan_dir = src if src.is_dir() else src.parent

    pdfs = sorted(scan_dir.glob("*.pdf"))
    # Exclude non-document .tex files from supplement candidates:
    # 1. Known macro/utility filenames (no \begin{document})
    # 2. Known non-supplement types (posters, slides, cover letters)
    # 3. Files without \begin{document} (macro-only or fragments)
    _EXCLUDED_NAMES = {
        "math_commands", "macros", "preamble", "defs", "commands",
        "shortcuts", "notation", "header", "packages", "setup",
        "poster", "slides", "beamer", "cover_letter", "response",
    }

    def _is_tex_document(f: Path) -> bool:
        if f.stem.lower() in _EXCLUDED_NAMES:
            return False
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")[:8192]
            return r"\begin{document}" in content
        except OSError:
            return False

    tex_files = sorted(f for f in scan_dir.glob("*.tex") if _is_tex_document(f))
    all_candidates = pdfs + tex_files

    if len(all_candidates) <= 1:
        return src, None, "single_source"

    # Multiple PDFs
    if len(pdfs) >= 2:
        marked = [f for f in pdfs if _has_supplement_markers(f)]
        unmarked = [f for f in pdfs if not _has_supplement_markers(f)]
        if marked and unmarked:
            # Tier 1 / Tier 2: clear roles
            return unmarked[0], marked[0], "tier1_filename_or_content"
        # Tier 3: both unmarked → size heuristic
        by_size = sorted(pdfs, key=lambda f: f.stat().st_size, reverse=True)
        return by_size[0], by_size[1], "tier3_size_heuristic"

    # Multiple .tex files
    if len(tex_files) >= 2:
        root = _find_latex_root(tex_files)
        if root:
            others = [f for f in tex_files if f != root]
            return root, others[0], "tier2_latex_root"
        marked = [f for f in tex_files if _has_supplement_markers(f)]
        unmarked = [f for f in tex_files if not _has_supplement_markers(f)]
        if marked and unmarked:
            return unmarked[0], marked[0], "tier1_filename_or_content"
        by_size = sorted(tex_files, key=lambda f: f.stat().st_size, reverse=True)
        return by_size[0], by_size[1], "tier3_size_heuristic"

    # Mixed PDF + .tex
    if pdfs and tex_files:
        pdf_marked = [f for f in pdfs if _has_supplement_markers(f)]
        tex_marked = [f for f in tex_files if _has_supplement_markers(f)]
        if pdf_marked and not tex_marked:
            return tex_files[0], pdf_marked[0], "tier1_mixed_type"
        if tex_marked and not pdf_marked:
            return pdfs[0], tex_marked[0], "tier1_mixed_type"
        return pdfs[0], tex_files[0], "tier2_mixed_type_default"

    return src, None, "single_source"


def _patch_segment_frontmatter(seg_path: Path, new_segment_id: str) -> None:
    """Update the segment_id field in YAML frontmatter of a segment .md file."""
    try:
        content = seg_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return
        end = content.find("\n---", 3)
        if end == -1:
            return
        fm = content[3:end]
        if "segment_id:" in fm:
            fm = re.sub(
                r"^segment_id:.*$",
                f"segment_id: {new_segment_id}",
                fm,
                flags=re.MULTILINE,
            )
        else:
            fm = fm + f"\nsegment_id: {new_segment_id}"
        seg_path.write_text("---" + fm + content[end:], encoding="utf-8")
    except Exception:
        pass  # Non-critical; the manifest carries the authoritative segment_id


def _postprocess_supplement_segments(paper_bank: Path, cite_key: str) -> list[str]:
    """Rename supplement segments with a supp_ prefix, copy them into the main
    segments directory, and merge their manifest entries (tagged source: supplement)
    into the main _segment_manifest.json.

    Returns the list of merged supplement segment_ids.
    """
    supp_bank = paper_bank / "supplement"
    main_seg_dir = paper_bank / "segments"
    supp_manifest_path = supp_bank / "segments" / "_segment_manifest.json"
    main_manifest_path = main_seg_dir / "_segment_manifest.json"

    if not supp_manifest_path.exists():
        print(
            f"[run_pipeline] WARNING: supplement manifest not found at {supp_manifest_path}",
            file=sys.stderr,
        )
        return []

    try:
        supp_manifest = json.loads(supp_manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[run_pipeline] WARNING: failed to read supplement manifest: {exc}", file=sys.stderr)
        return []

    main_seg_dir.mkdir(parents=True, exist_ok=True)
    supp_entries: list[dict] = []
    supp_seg_ids: list[str] = []

    for entry in supp_manifest.get("segments", []):
        orig_id = entry.get("segment_id", "")
        orig_file_rel = entry.get("file", "")  # relative to supp_bank
        new_id = f"supp_{orig_id}" if not orig_id.startswith("supp_") else orig_id

        # Locate and copy the actual segment file
        orig_file_path = supp_bank / orig_file_rel if orig_file_rel else None
        if orig_file_path and orig_file_path.exists():
            orig_name = orig_file_path.name
            new_name = f"supp_{orig_name}" if not orig_name.startswith("supp_") else orig_name
            dest = main_seg_dir / new_name
            shutil.copy2(str(orig_file_path), str(dest))
            _patch_segment_frontmatter(dest, new_id)
        else:
            new_name = (
                f"supp_{Path(orig_file_rel).name}"
                if orig_file_rel
                else f"{new_id}.md"
            )

        new_entry = dict(entry)
        new_entry["segment_id"] = new_id
        new_entry["file"] = f"segments/{new_name}"
        new_entry["source"] = "supplement"
        supp_entries.append(new_entry)
        supp_seg_ids.append(new_id)

    # Merge into main manifest
    if main_manifest_path.exists():
        try:
            main_manifest = json.loads(main_manifest_path.read_text(encoding="utf-8"))
        except Exception:
            main_manifest = {"cite_key": cite_key, "segmentation_version": 1, "segments": []}
    else:
        main_manifest = {"cite_key": cite_key, "segmentation_version": 1, "segments": []}

    merged = list(main_manifest.get("segments", [])) + supp_entries
    main_manifest["segments"] = merged
    main_manifest["segment_count"] = len(merged)
    main_manifest["has_supplement"] = True
    main_manifest_path.write_text(
        json.dumps(main_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"[run_pipeline] Merged {len(supp_entries)} supplement segment(s) "
        f"into {main_manifest_path}",
        file=sys.stderr,
    )

    # Clean up supplement bank temp dir
    try:
        shutil.rmtree(str(supp_bank))
    except Exception as exc:
        print(
            f"[run_pipeline] WARNING: failed to remove supplement temp dir: {exc}",
            file=sys.stderr,
        )

    return supp_seg_ids


def _append_supplementary_vault_section(
    vault_paper_note_path: Path, supp_segment_ids: list[str]
) -> None:
    """Append a ## Supplementary Material section to the vault paper note."""
    if not vault_paper_note_path.exists():
        print(
            f"[run_pipeline] WARNING: vault note not found at {vault_paper_note_path}; "
            "skipping ## Supplementary Material section.",
            file=sys.stderr,
        )
        return
    try:
        content = vault_paper_note_path.read_text(encoding="utf-8")
        if "## Supplementary Material" in content:
            return
        lines = [
            "",
            "## Supplementary Material",
            "",
            "The following supplement segments were extracted from the supplementary source:",
            "",
        ]
        for seg_id in supp_segment_ids:
            lines.append(f"- `{seg_id}`")
        lines.append("")
        vault_paper_note_path.write_text(
            content.rstrip("\n") + "\n" + "\n".join(lines), encoding="utf-8"
        )
        print(
            f"[run_pipeline] ## Supplementary Material section added to {vault_paper_note_path}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"[run_pipeline] WARNING: failed to append supplementary vault section: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    cite_key: str = args.cite_key
    source_format: str = args.source_format
    source_path: str = args.source_path
    paper_bank: Path = _resolve_paper_bank(cite_key, args.paper_bank_dir)
    vault_root: Path = _resolve_vault(args.vault_root)
    segment_source_dir: Path = _resolve_segment_source_dir(source_path)
    run_report_path: Path = Path(args.run_report_path)
    checkpoint_path: Path = Path(args.checkpoint_path)
    if not checkpoint_path.is_absolute():
        checkpoint_path = run_report_path.parent / checkpoint_path
    skip_steps: set[str] = {s.strip() for s in args.skip_steps.split(",") if s.strip()}
    resume_from: str = args.resume_from.strip() if args.resume_from else ""

    # Supplement source detection (F-001) — scan source directory for multiple
    # PDFs / .tex files and classify them before any pipeline step runs.
    _main_source, supplement_source, classify_note = _classify_sources(source_path)
    if supplement_source:
        print(
            f"[run_pipeline] Supplement source detected: {supplement_source} "
            f"(classification: {classify_note})",
            file=sys.stderr,
        )
    supp_paper_bank: Path | None = paper_bank / "supplement" if supplement_source else None

    # Inline step registry — maps step names to callable Python functions for
    # steps that do not need a subprocess (e.g. supplement postprocessing).
    _inline_fns: dict = {}
    supp_segment_ids: list[str] = []

    # Advisory cite-key convention check (uses _catalog.yaml if present)
    _check_cite_key_convention(cite_key, paper_bank)

    py = sys.executable
    scripts = SCRIPTS_DIR
    skill_root = scripts.parent

    # Map 'auto' source format to a concrete segmenter format
    seg_format = source_format if source_format not in ("auto",) else "latex"
    # I-001: translate_paper.py only accepts {auto, pandoc, pdf, html}; map latex/markdown -> pandoc
    translate_format = source_format if source_format in ('auto', 'pandoc', 'pdf', 'html') else 'pandoc'
    translate_cmd = [
        py,
        str(scripts / "translate_paper.py"),
        "--cite-key", cite_key,
        "--paper-bank-dir", str(paper_bank),
        "--format", translate_format,
    ]
    # PDF segmentation creates translated_full_pdf.md, so translate can be a no-op
    # while still preserving the explicit translate step contract.
    if source_format == "pdf":
        translate_cmd.append("--dry-run")

    segment_cmd = [
        py,
        str(scripts / "segment_paper.py"),
        "--cite-key", cite_key,
        "--source-dir", str(segment_source_dir),
        "--output-dir", str(paper_bank / "segments"),
        "--format", seg_format,
    ]
    comprehend_cmd = [
        py,
        str(scripts / "comprehend_paper.py"),
        "--cite-key", cite_key,
        "--paper-bank-root", str(paper_bank.parent),
        "--skill-root", str(skill_root),
        "--constitution-path", str(skill_root / "reading-constitution.md"),
        "--vault-root", str(vault_root),
    ]
    vault_requests_path = paper_bank / "_vault-write-requests.json"
    vault_cmd = [
        py,
        str(scripts / "vault_integration" / "run_vault_ingestion.py"),
        "--work-dir", str(paper_bank),
        "--vault-path", str(vault_root),
        "--requests", str(vault_requests_path),
        "--dry-run", "false",
    ]
    validate_cmd = [
        py,
        str(scripts / "validate_extraction.py"),
        "--vault-root", str(vault_root),
        "--paper-bank", str(paper_bank.parent),
    ]
    vault_search_results_path = paper_bank / "_vault_search_results.json"
    vault_search_cmd = [
        py,
        str(scripts / "vault_integration" / "search_vault.py"),
        "--work-dir", str(paper_bank),
        "--vault-path", str(vault_root),
        "--output", str(vault_search_results_path),
    ]
    vault_paper_note_path = paper_bank / "_vault_paper_note.md"
    vault_prepare_cmd = [
        py,
        str(scripts / "vault_integration" / "prepare_paper_note.py"),
        "--work-dir", str(paper_bank),
        "--cite-key", cite_key,
        "--vault-path", str(vault_root),
        "--output", str(vault_paper_note_path),
    ]
    vault_write_requests_cmd = [
        py,
        str(scripts / "vault_integration" / "build_vault_write_requests.py"),
        "--work-dir", str(paper_bank),
        "--vault-path", str(vault_root),
        "--output", str(vault_requests_path),
    ]
    bibtex_output = paper_bank / "refs.bib"
    bibtex_cmd = [
        py,
        str(scripts / "generate_bibtex.py"),
        "--catalog", str(paper_bank / "_catalog.yaml"),
        "--output", str(bibtex_output),
    ]
    summary_layers_output = paper_bank / "_summary_layers.json"
    summary_layers_cmd = [
        py,
        str(scripts / "build_summary_layers.py"),
        "--work-dir", str(paper_bank),
        "--cite-key", cite_key,
        "--vault-path", str(vault_root),
        "--output", str(summary_layers_output),
    ]

    # ------------------------------------------------------------------
    # Supplement step commands (F-001) — built only when a supplement is found
    # ------------------------------------------------------------------
    supp_translate_cmd: list[str] = []
    supp_segment_cmd: list[str] = []
    if supplement_source is not None and supp_paper_bank is not None:
        _supp_bank = supp_paper_bank  # local alias for use in closures below

        def _supp_setup() -> None:
            """Stage the supplement file into supplement/source/ for translate."""
            supp_src_dir = _supp_bank / "source"
            supp_src_dir.mkdir(parents=True, exist_ok=True)
            dest = supp_src_dir / supplement_source.name  # type: ignore[union-attr]
            if not dest.exists():
                shutil.copy2(str(supplement_source), str(dest))
            print(
                f"[run_pipeline] Supplement source staged at {dest}",
                file=sys.stderr,
            )

        _inline_fns["supp_setup"] = _supp_setup

        supp_translate_cmd = [
            py,
            str(scripts / "translate_paper.py"),
            "--cite-key", cite_key,
            "--paper-bank-dir", str(supp_paper_bank),
            "--format", translate_format,
        ]
        # Never pass --dry-run for supplement PDFs: we need actual translation output

        supp_segment_cmd = [
            py,
            str(scripts / "segment_paper.py"),
            "--cite-key", cite_key,
            "--source-dir", str(supp_paper_bank),
            "--output-dir", str(supp_paper_bank / "segments"),
            "--format", seg_format,
        ]

        def _supp_postprocess() -> None:
            nonlocal supp_segment_ids
            supp_segment_ids = _postprocess_supplement_segments(paper_bank, cite_key)

        _inline_fns["supp_postprocess"] = _supp_postprocess

        def _supp_vault_note() -> None:
            _append_supplementary_vault_section(vault_paper_note_path, supp_segment_ids)

        _inline_fns["supp_vault_note"] = _supp_vault_note

    # ------------------------------------------------------------------
    # Ordered pipeline step definitions
    # Each entry: (name, cmd, soft)
    # cmd=None means an inline Python step (see _inline_fns registry above).
    # ------------------------------------------------------------------
    format_steps: list[tuple[str, list[str] | None, bool]]
    if source_format == "pdf":
        format_steps = [
            ("segment", segment_cmd, False),
            ("translate", translate_cmd, False),
        ]
    else:
        format_steps = [
            ("translate", translate_cmd, False),
            ("segment", segment_cmd, False),
        ]

    steps: list[tuple[str, list[str] | None, bool]] = [
        (
            "preflight",
            [
                py,
                str(scripts / "preflight_extraction.py"),
                "--output",
                str(run_report_path.parent / "preflight_report.json"),
            ],
            True,   # soft: informational; never aborts pipeline
        ),
    ]
    steps.extend(format_steps)

    # Supplement translate + segment steps run immediately after the main
    # source steps so the full segment manifest is ready for comprehend.
    if supplement_source:
        steps.extend([
            ("supp_setup", None, True),        # inline: stage supplement file
            ("supp_translate", supp_translate_cmd, True),   # soft: supplement translate
            ("supp_segment", supp_segment_cmd, True),       # soft: supplement segment
            ("supp_postprocess", None, True),  # inline: rename + merge segments
        ])

    steps.extend([
        (
            "catalog",
            [py, str(scripts / "build_catalog.py"),
             "--cite-key", cite_key,
             "--work-dir", str(paper_bank)],
            False,  # hard: _catalog.yaml must exist before comprehend
        ),
        (
            "comprehend",
            comprehend_cmd,
            True,   # soft: may need live vault context
        ),
        (
            "summary_layers",
            summary_layers_cmd,
            True,   # soft: enhancement; does not block vault ingestion on failure
        ),
        (
            "vault_search",
            vault_search_cmd,
            False,  # hard: prerequisite for prepare_paper_note
        ),
        (
            "vault_prepare",
            vault_prepare_cmd,
            False,  # hard: prerequisite for build_vault_write_requests
        ),
    ])
    if supplement_source:
        # Append ## Supplementary Material to vault note before write-requests
        # are built so the section is included in the vault ingestion payload.
        steps.append(("supp_vault_note", None, True))  # inline: soft
    steps.extend([
        (
            "vault_write_requests",
            vault_write_requests_cmd,
            False,  # hard: produces _vault-write-requests.json required by vault ingestion
        ),
        (
            "vault",
            vault_cmd,
            False,  # hard: enforce knowledge-maester handoff through vault ingestion
        ),
        (
            "summarize",
            [
                py,
                str(scripts / "summarize_paper.py"),
                "--cite-key", cite_key,
                "--paper-bank-dir", str(paper_bank),
                "--vault-path", str(vault_root),
            ],
            True,   # soft: summarize_paper.py is a future implementation target
        ),
        (
            "validate",
            validate_cmd,
            True,   # soft: audit-only; pipeline output is independent
        ),
        (
            "bibtex",
            bibtex_cmd,
            True,   # soft: missing or empty refs.bib is non-fatal
        ),
    ])

    # Apply --skip-preflight convenience flag
    if args.skip_preflight:
        skip_steps.add("preflight")

    step_order: list[str] = [name for name, _, _ in steps]

    # Apply --resume-from: skip all steps before the named step.
    if resume_from:
        if resume_from not in step_order:
            print(
                f"[run_pipeline] ERROR: --resume-from step {resume_from!r} not found. "
                f"Available steps: {', '.join(step_order)}",
                file=sys.stderr,
            )
            return 1
        for name in step_order:
            if name == resume_from:
                break
            skip_steps.add(name)
    checkpoint = _load_checkpoint(checkpoint_path)
    checkpoint_skips: set[str] = set()
    if checkpoint:
        checkpoint_cite_key = checkpoint.get("cite_key")
        checkpoint_source_path = checkpoint.get("source_path")
        if checkpoint_cite_key not in (None, "", cite_key):
            print(
                (
                    "[run_pipeline] WARNING: checkpoint cite key mismatch "
                    f"({checkpoint_cite_key} != {cite_key}); ignoring checkpoint."
                ),
                file=sys.stderr,
            )
        elif checkpoint_source_path not in (None, "", source_path):
            print(
                (
                    "[run_pipeline] WARNING: checkpoint source path mismatch "
                    f"({checkpoint_source_path} != {source_path}); ignoring checkpoint."
                ),
                file=sys.stderr,
            )
        else:
            checkpoint_skips = _checkpoint_completed_steps(checkpoint)
            if checkpoint_skips:
                resumed = ", ".join(sorted(checkpoint_skips))
                print(
                    f"[run_pipeline] Resuming from checkpoint at {checkpoint_path}; completed steps: {resumed}",
                    file=sys.stderr,
                )

    # ------------------------------------------------------------------
    # Dry-run: print plan and exit
    # ------------------------------------------------------------------
    if args.dry_run:
        plan = [
            {"step": name, "cmd": cmd, "soft": soft}
            for name, cmd, soft in steps
            if name not in skip_steps
        ]
        print(json.dumps(
            {
                "dry_run": True,
                "cite_key": cite_key,
                "source_format": source_format,
                "source_path": source_path,
                "paper_bank_dir": str(paper_bank),
                "vault_root": str(vault_root),
                "checkpoint_path": str(checkpoint_path),
                "plan": plan,
            },
            indent=2,
        ))
        return 0

    # ------------------------------------------------------------------
    # Execute pipeline steps
    # ------------------------------------------------------------------
    results: list[dict] = []
    pipeline_ok = True
    print(
        (
            "[run_pipeline] Enforcing knowledge-maester handoff via "
            f"{vault_requests_path}"
        ),
        file=sys.stderr,
    )

    for name, cmd, soft in steps:
        if name in skip_steps:
            results.append({"step": name, "status": "skipped"})
            print(f"[run_pipeline] ↷ {name} (skipped)", file=sys.stderr)
            _write_checkpoint(
                checkpoint_path=checkpoint_path,
                cite_key=cite_key,
                source_format=source_format,
                source_path=source_path,
                paper_bank=paper_bank,
                run_report_path=run_report_path,
                vault_requests_path=vault_requests_path,
                step_order=step_order,
                step_results=results,
                pipeline_status="running",
            )
            continue

        if name in checkpoint_skips:
            results.append(
                {
                    "step": name,
                    "status": "skipped_checkpoint",
                    "checkpoint_path": str(checkpoint_path),
                }
            )
            print(f"[run_pipeline] ↷ {name} (already completed in checkpoint)", file=sys.stderr)
            _write_checkpoint(
                checkpoint_path=checkpoint_path,
                cite_key=cite_key,
                source_format=source_format,
                source_path=source_path,
                paper_bank=paper_bank,
                run_report_path=run_report_path,
                vault_requests_path=vault_requests_path,
                step_order=step_order,
                step_results=results,
                pipeline_status="running",
            )
            continue

        print(f"[run_pipeline] → {name} ...", file=sys.stderr)
        if cmd is None:
            # Inline Python step — run a registered callable instead of a subprocess
            fn = _inline_fns.get(name)
            result: dict = {
                "step": name,
                "cmd": None,
                "started_at": _ts(),
                "status": "pending",
                "returncode": None,
                "stderr_tail": "",
            }
            if fn is None:
                result["status"] = "soft_failure" if soft else "failed"
                result["stderr_tail"] = f"no inline function registered for step '{name}'"
            else:
                try:
                    fn()
                    result["returncode"] = 0
                    result["status"] = "passed"
                except Exception as exc:
                    result["returncode"] = -1
                    result["status"] = "soft_failure" if soft else "failed"
                    result["stderr_tail"] = str(exc)
                    label = "WARNING" if soft else "ERROR"
                    print(
                        f"[run_pipeline] {label}: inline step '{name}' failed: {exc}",
                        file=sys.stderr,
                    )
            result["finished_at"] = _ts()
        else:
            result = _run_step(name, cmd, soft=soft)
        results.append(result)

        if result["status"] == "failed":
            pipeline_ok = False
            _write_checkpoint(
                checkpoint_path=checkpoint_path,
                cite_key=cite_key,
                source_format=source_format,
                source_path=source_path,
                paper_bank=paper_bank,
                run_report_path=run_report_path,
                vault_requests_path=vault_requests_path,
                step_order=step_order,
                step_results=results,
                pipeline_status="failed",
            )
            print(
                f"[run_pipeline] Pipeline aborted at step '{name}' (hard failure).",
                file=sys.stderr,
            )
            # Append skipped markers for remaining steps
            remaining_names = [n for n, _, _ in steps]
            idx = remaining_names.index(name) + 1
            for n, _, _ in steps[idx:]:
                if n not in skip_steps:
                    results.append({"step": n, "status": "skipped_due_to_failure"})
                    _write_checkpoint(
                        checkpoint_path=checkpoint_path,
                        cite_key=cite_key,
                        source_format=source_format,
                        source_path=source_path,
                        paper_bank=paper_bank,
                        run_report_path=run_report_path,
                        vault_requests_path=vault_requests_path,
                        step_order=step_order,
                        step_results=results,
                        pipeline_status="failed",
                    )
            break
        _write_checkpoint(
            checkpoint_path=checkpoint_path,
            cite_key=cite_key,
            source_format=source_format,
            source_path=source_path,
            paper_bank=paper_bank,
            run_report_path=run_report_path,
            vault_requests_path=vault_requests_path,
            step_order=step_order,
            step_results=results,
            pipeline_status="running",
        )

    # ------------------------------------------------------------------
    # Emit run report
    # ------------------------------------------------------------------
    report = {
        "schema_version": "v2",
        "cite_key": cite_key,
        "source_format": source_format,
        "source_path": source_path,
        "paper_bank_dir": str(paper_bank),
        "vault_root": str(vault_root),
        "pipeline_status": "passed" if pipeline_ok else "failed",
        "generated_at": _ts(),
        "steps": results,
    }

    run_report_path.parent.mkdir(parents=True, exist_ok=True)
    run_report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[run_pipeline] Run report written to {run_report_path}", file=sys.stderr)
    _persist_run_report(
        report=report,
        run_report_path=run_report_path,
        paper_bank=paper_bank,
        cite_key=cite_key,
        source_path=source_path,
        vault_root=vault_root,
    )
    _write_checkpoint(
        checkpoint_path=checkpoint_path,
        cite_key=cite_key,
        source_format=source_format,
        source_path=source_path,
        paper_bank=paper_bank,
        run_report_path=run_report_path,
        vault_requests_path=vault_requests_path,
        step_order=step_order,
        step_results=results,
        pipeline_status=report["pipeline_status"],
    )

    if not pipeline_ok:
        failed = [r for r in results if r.get("status") == "failed"]
        print("[run_pipeline] FAILED STEPS:", file=sys.stderr)
        for r in failed:
            print(f"  - {r['step']}: returncode={r.get('returncode')}", file=sys.stderr)
            tail = r.get("stderr_tail", "")
            if tail:
                print(f"    {tail[:500]}", file=sys.stderr)
        return 1

    soft_failures = [r for r in results if r.get("status") == "soft_failure"]
    if soft_failures:
        names = ", ".join(r["step"] for r in soft_failures)
        print(
            f"[run_pipeline] Pipeline completed with {len(soft_failures)} soft failure(s): {names}",
            file=sys.stderr,
        )
    else:
        print("[run_pipeline] Pipeline completed successfully.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
