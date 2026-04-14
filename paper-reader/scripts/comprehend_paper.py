#!/usr/bin/env python3
"""Comprehension orchestrator skeleton for the paper-reader skill (M4).

Dry-run mode: plans subagent dispatch without writing any files; prints JSON.
Live mode (M4 stub): snapshots catalog, then exits with a not-implemented message.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from build_catalog import snapshot_catalog
from context_loader import load_layer_a
from meta_note_query import query_meta_notes

DispatchMode = Literal["auto", "inline", "subagent"]


def _normalize_dispatch_mode(value: str | None) -> DispatchMode | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in ("auto", "inline", "subagent"):
        return normalized  # type: ignore[return-value]
    return None


def _resolve_llm_dispatch(cli_mode: str) -> tuple[DispatchMode, str]:
    """Resolve LLM dispatch mode from CLI/env.

    Precedence:
      1) explicit CLI flag (`--llm-dispatch inline|subagent`)
      2) PAPER_READER_LLM_DISPATCH
      3) PAPER_READER_USE_SUBAGENT (boolean)
      4) inline default
    """
    if cli_mode != "auto":
        return cli_mode, "cli"

    env_mode = _normalize_dispatch_mode(os.environ.get("PAPER_READER_LLM_DISPATCH"))
    if env_mode and env_mode != "auto":
        return env_mode, "env:PAPER_READER_LLM_DISPATCH"

    env_bool = os.environ.get("PAPER_READER_USE_SUBAGENT", "").strip().lower()
    if env_bool in {"1", "true", "yes", "on"}:
        return "subagent", "env:PAPER_READER_USE_SUBAGENT"
    if env_bool in {"0", "false", "no", "off"}:
        return "inline", "env:PAPER_READER_USE_SUBAGENT"

    return "inline", "default"


def _load_catalog(catalog_path: Path) -> dict:
    """Load and return _catalog.yaml as a plain dict."""
    with open(catalog_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve_vault_papers_dir(vault_root: str) -> str:
    """Mirror validate_extraction.resolve_papers_dir so subagents get the correct output path."""
    v2 = Path(vault_root) / "literature" / "papers"
    if v2.exists():
        return str(v2)
    return str(Path(vault_root) / "papers")


def _build_dispatch_plan(
    cite_key: str,
    catalog: dict,
    layer_a: dict,
    vault_root: str,
    llm_dispatch: DispatchMode,
    constitution_path: str,
) -> tuple[list[dict], dict]:
    """Build the dispatch plan list and layer_b_query_results summary.

    Returns
    -------
    (dispatch_plan, layer_b_query_results)
        dispatch_plan  – list of per-section dispatch entries
        layer_b_query_results – dict mapping section_type -> meta-note count
    """
    layer_a_chars = sum(len(v) for v in layer_a.values() if v is not None)

    sections = catalog.get("sections", [])
    paper_meta = catalog.get("paper", {})
    domain_tags: list[str] = (
        paper_meta.get("vault_tags")
        or catalog.get("domain_tags")
        or catalog.get("tags")
        or []
    )

    # Cache Layer B queries by section_type to avoid redundant vault scans.
    meta_notes_cache: dict[str, list[str]] = {}
    layer_b_query_results: dict[str, int] = {}
    dispatch_plan: list[dict] = []

    for idx, section in enumerate(sections):
        status = section.get("comprehension_status")
        if status not in (None, "pending"):
            continue

        section_type = section.get("section_type", "unknown")
        segment_ids: list[str] = section.get("segments", [])
        sec_id = section.get("id", f"sec_{idx:03d}")

        if section_type not in meta_notes_cache:
            meta_notes = query_meta_notes(
                vault_root=vault_root,
                domain_tags=domain_tags,
                section_type=section_type,
            )
            meta_notes_cache[section_type] = meta_notes
            layer_b_query_results[section_type] = len(meta_notes)

        meta_notes = meta_notes_cache[section_type]

        entry = {
            "dispatch_mode": llm_dispatch,
            "dispatch_target": (
                f"subagent_{cite_key}_{sec_id}"
                if llm_dispatch == "subagent"
                else "inline-main-agent"
            ),
            "constitution_path": constitution_path,
            "section_type": section_type,
            "segment_ids": segment_ids,
            "meta_notes_to_load": meta_notes,
            "layer_a_chars": layer_a_chars,
            "cite_key": cite_key,
            "vault_papers_dir": _resolve_vault_papers_dir(vault_root),
        }
        dispatch_plan.append(entry)

    return dispatch_plan, layer_b_query_results


def _dry_run(args: argparse.Namespace) -> int:
    """Execute the dry-run orchestration path; return exit code."""
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    skill_root = Path(os.path.expanduser(args.skill_root))
    vault_root = os.path.expanduser(args.vault_root)
    if args.constitution_path:
        constitution_path = Path(os.path.expanduser(args.constitution_path))
    else:
        constitution_path = skill_root / "reading-constitution.md"
    cite_key = args.cite_key
    llm_dispatch, dispatch_source = _resolve_llm_dispatch(args.llm_dispatch)

    catalog_path = paper_bank_root / cite_key / "_catalog.yaml"
    if not catalog_path.exists():
        print(
            f"Error: catalog not found at {catalog_path}",
            file=sys.stderr,
        )
        return 1

    catalog = _load_catalog(catalog_path)

    try:
        layer_a = load_layer_a(str(skill_root))
    except FileNotFoundError as exc:
        print(f"Error loading Layer A context: {exc}", file=sys.stderr)
        return 1
    if not constitution_path.exists():
        print(f"Error loading Layer A context: constitution not found at {constitution_path}", file=sys.stderr)
        return 1
    layer_a["constitution"] = constitution_path.read_text(encoding="utf-8")

    dispatch_plan, layer_b_query_results = _build_dispatch_plan(
        cite_key=cite_key,
        catalog=catalog,
        layer_a=layer_a,
        vault_root=vault_root,
        llm_dispatch=llm_dispatch,
        constitution_path=str(constitution_path),
    )

    layer_a_context_loaded = {
        "skill_md_chars": len(layer_a.get("skill_md") or ""),
        "constitution_chars": len(layer_a.get("constitution") or ""),
        "proof_patterns_chars": len(layer_a.get("proof_patterns") or ""),
        "constitution_path": str(constitution_path),
    }

    output = {
        "cite_key": cite_key,
        "llm_dispatch": llm_dispatch,
        "llm_dispatch_source": dispatch_source,
        "dispatch_plan": dispatch_plan,
        "layer_a_context_loaded": layer_a_context_loaded,
        "layer_b_query_results": layer_b_query_results,
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def _live_run(args: argparse.Namespace) -> int:
    """Execute the live orchestration path — call section readers inline."""
    paper_bank_root = Path(os.path.expanduser(args.paper_bank_root))
    cite_key = args.cite_key
    paper_dir = paper_bank_root / cite_key
    skill_root = Path(os.path.expanduser(args.skill_root))
    vault_root = os.path.expanduser(args.vault_root)
    llm_dispatch, dispatch_source = _resolve_llm_dispatch(args.llm_dispatch)

    snapshot_path = snapshot_catalog(paper_dir)
    if snapshot_path:
        print(f"Catalog snapshot written: {snapshot_path}", file=sys.stderr)
    else:
        print(
            f"No catalog found at {paper_dir} — skipping snapshot.",
            file=sys.stderr,
        )

    catalog_path = paper_dir / "_catalog.yaml"
    if not catalog_path.exists():
        print(f"Error: catalog not found at {catalog_path}", file=sys.stderr)
        return 1

    catalog = _load_catalog(catalog_path)
    sections = catalog.get("sections", [])

    # Determine which section types have pending comprehension work.
    section_types_present: set[str] = set()
    for section in sections:
        status = section.get("comprehension_status")
        if status not in (None, "pending"):
            continue
        section_types_present.add(section.get("section_type", "unknown"))

    # Map section types to reader scripts.  Covers all known section_type
    # values from the catalog schema plus common aliases.
    _READER_MAP: dict[str, str] = {
        # Positioning / intro readers
        "introduction": "intro_reader.py",
        "abstract": "intro_reader.py",
        "background": "intro_reader.py",
        "related_work": "intro_reader.py",
        "literature_review": "intro_reader.py",
        "preliminaries": "intro_reader.py",
        # Model readers
        "model": "model_reader.py",
        "model_method": "model_reader.py",
        "model_theory": "model_reader.py",
        "formulation": "model_reader.py",
        # Method readers
        "method": "method_reader.py",
        "methods": "method_reader.py",
        "method_theory": "method_reader.py",
        "algorithm": "method_reader.py",
        "implementation": "method_reader.py",
        # Theory readers
        "theory": "theory_reader.py",
        "proof": "theory_reader.py",
        "analysis": "theory_reader.py",
        "appendix": "theory_reader.py",
    }
    _EMPIRICAL_TYPES = {
        "simulation", "real_data", "discussion", "conclusion",
        "experiments", "results", "evaluation",
    }
    # Generic "section" type: skip silently — these are typically structural
    # headings whose content is covered by more specific child sections.

    readers_to_run: list[str] = []
    seen: set[str] = set()
    for st in sorted(section_types_present):
        if st in _READER_MAP:
            script = _READER_MAP[st]
        elif st in _EMPIRICAL_TYPES:
            script = "comprehend_empirical.py"
        else:
            continue
        if script not in seen:
            readers_to_run.append(script)
            seen.add(script)

    if not readers_to_run:
        print("No pending sections found for comprehension.", file=sys.stderr)
        return 0

    if llm_dispatch == "subagent":
        # Subagent mode: produce the dispatch plan JSON and exit.  The calling
        # agent is expected to spawn subagents for each entry in the plan.
        print(
            f"Dispatch mode: subagent ({dispatch_source}). "
            "Producing dispatch plan — the calling agent must spawn readers.",
            file=sys.stderr,
        )
        return _dry_run(args)

    # Inline mode: call each reader as a subprocess.
    print(
        f"Comprehension: running {len(readers_to_run)} reader(s) inline for {cite_key}",
        file=sys.stderr,
    )

    results: dict[str, str] = {}
    failures: list[str] = []
    for reader_script in readers_to_run:
        script_path = _SCRIPTS_DIR / reader_script
        if not script_path.exists():
            print(f"  WARNING: reader script not found: {reader_script}", file=sys.stderr)
            failures.append(reader_script)
            continue

        cmd = [
            sys.executable, str(script_path),
            "--cite-key", cite_key,
            "--paper-bank-root", str(paper_bank_root),
            "--vault-root", vault_root,
            "--skill-root", str(skill_root),
        ]

        print(f"  Running {reader_script}...", file=sys.stderr)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                print(
                    f"  WARNING: {reader_script} exited with code {result.returncode}",
                    file=sys.stderr,
                )
                if result.stderr:
                    for line in result.stderr.strip().splitlines()[:5]:
                        print(f"    {line}", file=sys.stderr)
                failures.append(reader_script)
            else:
                print(f"  OK: {reader_script}", file=sys.stderr)
                results[reader_script] = "passed"
        except subprocess.TimeoutExpired:
            print(f"  WARNING: {reader_script} timed out after 300s", file=sys.stderr)
            failures.append(reader_script)
        except Exception as exc:
            print(f"  WARNING: {reader_script} failed: {exc}", file=sys.stderr)
            failures.append(reader_script)

    if failures:
        print(
            f"Comprehension completed with {len(failures)} failure(s): {failures}. "
            "Pipeline will continue — downstream steps may produce partial results.",
            file=sys.stderr,
        )
    else:
        print(
            f"Comprehension completed successfully: {len(results)} reader(s) ran.",
            file=sys.stderr,
        )

    # Always return 0 — comprehension failures are soft.  Downstream steps
    # (prepare_paper_note) degrade gracefully when section notes are missing.
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Paper comprehension orchestrator (M4 skeleton)."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan dispatch without writing files; print JSON to stdout.",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank")),
        help="Root directory of the paper bank (default: $PAPER_BANK or ~/Documents/paper-bank).",
    )
    parser.add_argument(
        "--skill-root",
        default="skills/paper-reader",
        help=(
            "Path to the paper-reader skill directory "
            "(default: skills/paper-reader, resolved from the working directory)."
        ),
    )
    parser.add_argument(
        "--vault-root",
        default="~/Documents/citadel",
        help="Path to the Citadel vault root (default: ~/Documents/citadel).",
    )
    parser.add_argument(
        "--constitution-path",
        default="",
        help=(
            "Explicit path to reading-constitution.md passed to each comprehension "
            "subagent plan entry. Defaults to <skill-root>/reading-constitution.md."
        ),
    )
    parser.add_argument(
        "--llm-dispatch",
        choices=["auto", "inline", "subagent"],
        default="auto",
        help=(
            "LLM execution mode: inline (main agent) or subagent fan-out. "
            "Default auto resolves from PAPER_READER_LLM_DISPATCH / PAPER_READER_USE_SUBAGENT."
        ),
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.dry_run:
        sys.exit(_dry_run(args))
    else:
        sys.exit(_live_run(args))


if __name__ == "__main__":
    main()
