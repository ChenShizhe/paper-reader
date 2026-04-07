#!/usr/bin/env python3
"""Stage create/upgrade payloads for cited paper stubs.

This module reads citation signals from paper-bank files and stages markdown
artifacts under ``<work_dir>/stubs/`` for downstream vault ingestion.
It never writes directly to the vault.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SEEN_IN_HEADER = "## Seen In"
ALTERNATE_SEEN_IN_HEADER = "## Seen in Other Papers"
AUTO_OPEN = "<!-- AUTO-GENERATED -->"
AUTO_CLOSE = "<!-- /AUTO-GENERATED -->"
# Keep a literal marker for simple text-based verification checks.
STUB_STATUS_MARKER = "status: stub"


@dataclass
class CitationRef:
    ref_key: str
    source: str
    description: str


def _slugify(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", lowered):
        return lowered
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    if compact:
        return compact
    fallback = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return fallback


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_frontmatter(markdown: str) -> dict[str, Any]:
    if not markdown.startswith("---\n"):
        return {}
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return {}
    raw = markdown[4:end]
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _collect_refs_from_xref(xref_index: dict[str, Any], current_cite_key: str) -> list[CitationRef]:
    refs: list[CitationRef] = []
    seen: set[str] = set()

    for item in xref_index.get("citations", []) or []:
        if not isinstance(item, dict):
            continue
        raw_ref = item.get("cite_key") or item.get("cited_key")
        if not raw_ref and item.get("vault_note"):
            note = str(item.get("vault_note") or "")
            match = re.fullmatch(r"literature/papers/([^/]+)\.md", note)
            if match:
                raw_ref = match.group(1)
        ref_key = _slugify(str(raw_ref or ""))
        if not ref_key or ref_key == current_cite_key or ref_key in seen:
            continue

        desc = str(
            item.get("expected_contribution")
            or item.get("description")
            or item.get("context")
            or "citation context pending"
        ).strip()
        refs.append(CitationRef(ref_key=ref_key, source="xref_index", description=desc))
        seen.add(ref_key)

    return refs


def _collect_refs_from_search_results(
    search_results: dict[str, Any],
    current_cite_key: str,
    existing: set[str],
) -> list[CitationRef]:
    refs: list[CitationRef] = []
    results = search_results.get("results", {}) if isinstance(search_results, dict) else {}

    for item in results.get("papers", []) or []:
        if not isinstance(item, dict):
            continue
        note_path = str(item.get("note_path") or "")
        match = re.fullmatch(r"literature/papers/([^/]+)\.md", note_path)
        if not match:
            continue
        ref_key = _slugify(match.group(1))
        if not ref_key or ref_key == current_cite_key or ref_key in existing:
            continue
        desc = str(item.get("relevance_reason") or "related vault paper match").strip()
        refs.append(CitationRef(ref_key=ref_key, source="vault_search_results", description=desc))
        existing.add(ref_key)

    return refs


def _extract_seen_in_lines(note_text: str) -> list[str]:
    headers = [SEEN_IN_HEADER, ALTERNATE_SEEN_IN_HEADER]
    lowered = note_text.lower()
    section_text = ""

    for header in headers:
        idx = lowered.find(header.lower())
        if idx == -1:
            continue
        after = note_text[idx + len(header):]
        next_heading = re.search(r"\n##\s+", after)
        section_text = after[: next_heading.start()] if next_heading else after
        break

    if not section_text:
        return []

    auto_match = re.search(
        re.escape(AUTO_OPEN) + r"\s*(.*?)\s*" + re.escape(AUTO_CLOSE),
        section_text,
        re.DOTALL,
    )
    if auto_match:
        candidate = auto_match.group(1)
    else:
        candidate = section_text

    lines: list[str] = []
    for line in candidate.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        if stripped.startswith("##"):
            continue
        lines.append(stripped)
    return lines


def _render_seen_in_entry(citing_cite_key: str, description: str) -> list[str]:
    desc = description.strip() or "cited context pending"
    return [
        f"- [[{citing_cite_key}]]: cited this work.",
        f"  Expected contribution: {desc}",
    ]


def _merge_seen_in_lines(existing: list[str], new_entry: list[str], citing_cite_key: str) -> list[str]:
    marker = f"[[{citing_cite_key}]]"
    for line in existing:
        if marker in line:
            return existing

    merged = list(existing)
    if merged and merged[-1].strip():
        merged.append("")
    merged.extend(new_entry)
    return merged


def _build_stub_frontmatter(ref_key: str, today_iso: str) -> str:
    data = {
        "type": "paper",
        "title": ref_key,
        "cite_key": ref_key,
        "canonical_id": "",
        "authors": [],
        "year": "",
        "date": today_iso,
        "tags": ["stub", "paper"],
        "last_updated": today_iso,
        "content_status": "unavailable",
        "review_status": "draft",
        "status": "stub",
        "bank_path": "",
    }
    return "---\n" + yaml.dump(data, default_flow_style=False, allow_unicode=True) + "---\n"


def _build_stub_create_payload(ref_key: str, citing_cite_key: str, description: str, today_iso: str) -> str:
    frontmatter = _build_stub_frontmatter(ref_key=ref_key, today_iso=today_iso)
    entry = "\n".join(_render_seen_in_entry(citing_cite_key, description))
    body = (
        "\n## Summary\n\n"
        "*(stub - not yet read)*\n\n"
        f"{SEEN_IN_HEADER}\n\n"
        f"{AUTO_OPEN}\n"
        f"{entry}\n"
        f"{AUTO_CLOSE}\n\n"
        "## Key Claims\n\n"
        "*(stub - to be populated when read)*\n\n"
        "## Links\n"
        f"- Cited by: [[{citing_cite_key}]]\n"
    )
    return frontmatter + body


def _build_stub_update_payload(
    ref_key: str,
    note_text: str,
    citing_cite_key: str,
    description: str,
) -> tuple[str, bool]:
    existing_seen = _extract_seen_in_lines(note_text)
    merged_seen = _merge_seen_in_lines(
        existing=existing_seen,
        new_entry=_render_seen_in_entry(citing_cite_key, description),
        citing_cite_key=citing_cite_key,
    )

    # no-op when this citing paper already appears
    if merged_seen == existing_seen:
        return "", False

    merged_content = "\n".join(merged_seen).strip()
    patch = {
        "patch_type": "stub_seen_in_update",
        "target_vault_path": f"literature/papers/{ref_key}.md",
        "status": "stub",
    }
    frontmatter = "---\n" + yaml.dump(patch, default_flow_style=False, allow_unicode=True) + "---\n"
    body = (
        f"\n{SEEN_IN_HEADER}\n\n"
        f"{AUTO_OPEN}\n"
        f"{merged_content}\n"
        f"{AUTO_CLOSE}\n"
    )
    return frontmatter + body, True


def prepare_stub_upgrades(
    work_dir: str | Path,
    vault_path: str | Path,
    cite_key: str,
    output_report: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Stage create/upgrade markdown payloads for cited paper stubs.

    In dry-run mode, no files are written and only a JSON summary is returned.
    """
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    output_report = Path(output_report)
    cite_key = _slugify(cite_key)

    xref_path = work_dir / "_xref_index.yaml"
    search_results_path = work_dir / "_vault_search_results.json"

    missing_inputs: list[str] = []
    if not work_dir.exists():
        missing_inputs.append(f"work_dir:{work_dir}")
    if not vault_path.exists():
        missing_inputs.append(f"vault_path:{vault_path}")
    if not cite_key:
        missing_inputs.append("cite_key:<empty>")
    if not xref_path.exists() and not search_results_path.exists():
        missing_inputs.append("citations_source:_xref_index.yaml|_vault_search_results.json")

    if missing_inputs:
        summary = {
            "cite_key": cite_key or work_dir.name,
            "inputs_valid": False,
            "missing_inputs": missing_inputs,
            "create_stub_count": 0,
            "upgrade_stub_count": 0,
            "skipped_count": 0,
        }
        if dry_run:
            return summary
        for msg in missing_inputs:
            print(f"ERROR: missing required input: {msg}", file=sys.stderr)
        return summary

    xref_index = _load_yaml(xref_path)
    search_results = _load_json(search_results_path)

    refs = _collect_refs_from_xref(xref_index=xref_index, current_cite_key=cite_key)
    seen = {r.ref_key for r in refs}
    refs.extend(
        _collect_refs_from_search_results(
            search_results=search_results,
            current_cite_key=cite_key,
            existing=seen,
        )
    )

    stubs_dir = work_dir / "stubs"
    today_iso = datetime.now(tz=timezone.utc).date().isoformat()

    create_actions: list[dict[str, str]] = []
    upgrade_actions: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for ref in refs:
        vault_note_rel = f"literature/papers/{ref.ref_key}.md"
        vault_note_path = vault_path / vault_note_rel

        if vault_note_path.exists():
            note_text = vault_note_path.read_text(encoding="utf-8")
            fm = _parse_frontmatter(note_text)
            status = str(fm.get("status") or "").strip().lower()

            if status == "stub":
                payload, has_change = _build_stub_update_payload(
                    ref_key=ref.ref_key,
                    note_text=note_text,
                    citing_cite_key=cite_key,
                    description=ref.description,
                )
                if not has_change:
                    skipped.append(
                        {
                            "ref_key": ref.ref_key,
                            "reason": "already_contains_seen_in_for_citing_paper",
                            "source": ref.source,
                        }
                    )
                    continue

                staged_path = stubs_dir / f"{ref.ref_key}-seen-in-update.md"
                if not dry_run:
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_path.write_text(payload, encoding="utf-8")

                upgrade_actions.append(
                    {
                        "ref_key": ref.ref_key,
                        "vault_path": vault_note_rel,
                        "staged_path": str(staged_path),
                        "source": ref.source,
                    }
                )
            else:
                skipped.append(
                    {
                        "ref_key": ref.ref_key,
                        "reason": f"vault_note_exists_with_status:{status or 'unknown'}",
                        "source": ref.source,
                    }
                )
            continue

        payload = _build_stub_create_payload(
            ref_key=ref.ref_key,
            citing_cite_key=cite_key,
            description=ref.description,
            today_iso=today_iso,
        )
        staged_path = stubs_dir / f"{ref.ref_key}-stub.md"
        if not dry_run:
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_text(payload, encoding="utf-8")

        create_actions.append(
            {
                "ref_key": ref.ref_key,
                "vault_path": vault_note_rel,
                "staged_path": str(staged_path),
                "source": ref.source,
            }
        )

    summary = {
        "cite_key": cite_key,
        "inputs_valid": True,
        "create_stub_count": len(create_actions),
        "upgrade_stub_count": len(upgrade_actions),
        "skipped_count": len(skipped),
    }

    if dry_run:
        return summary

    report = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "inputs_valid": True,
        "create_stub": create_actions,
        "upgrade_stub": upgrade_actions,
        "skipped": skipped,
        "create_stub_count": len(create_actions),
        "upgrade_stub_count": len(upgrade_actions),
        "skipped_count": len(skipped),
    }
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage create/upgrade payloads for cited stubs while preserving in-place Seen In continuity."
        )
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--vault-path", required=True, help="Path to Citadel vault root")
    parser.add_argument("--cite-key", required=True, help="cite_key of the current paper")
    parser.add_argument(
        "--output-report",
        required=True,
        help="Path to write _stub_upgrade_report.json in live mode",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print summary counts without writing files.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = prepare_stub_upgrades(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        cite_key=args.cite_key,
        output_report=args.output_report,
        dry_run=args.dry_run,
    )

    if not args.dry_run and not result.get("inputs_valid", False):
        print(json.dumps(result, indent=2))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
