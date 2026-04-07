#!/usr/bin/env python3
"""Run a faithfulness check against the rendered summary note."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_NOTES = [
    "intro",
    "model",
    "method",
    "theory",
    "simulation",
    "real_data",
    "discussion",
]

CLAIM_EXCLUDED_HEADINGS = {"Reading Quality", "Section Map"}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "with",
}


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_claim(value: str) -> str:
    normalized = _clean_text(value).lower()
    normalized = re.sub(r"[`*_]", "", normalized)
    return normalized.rstrip(" .;:,!?")


def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return markdown
    return markdown[end + 5 :]


def _extract_claims(summary_markdown: str) -> list[str]:
    body = _strip_frontmatter(summary_markdown)
    claims: list[str] = []
    seen: set[str] = set()
    current_heading: str | None = None

    for raw_line in body.splitlines():
        heading_match = re.match(r"^##\s+(.+?)\s*$", raw_line)
        if heading_match:
            current_heading = _clean_text(heading_match.group(1))
            continue

        if current_heading is None or current_heading in CLAIM_EXCLUDED_HEADINGS:
            continue

        line = raw_line.strip()
        if not line or line.startswith("<!--") or line.startswith("|"):
            continue

        claim_match = re.match(r"^[-*]\s+(.+)$", line)
        if claim_match is None:
            claim_match = re.match(r"^\d+\.\s+(.+)$", line)
        if claim_match is None:
            continue

        claim = _clean_text(claim_match.group(1))
        while claim.startswith("- "):
            claim = _clean_text(claim[2:])
        if not claim:
            continue

        normalized = _normalize_claim(claim)
        if normalized and normalized not in seen:
            seen.add(normalized)
            claims.append(claim)

    return claims


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def _claim_supported_by_note(claim: str, note_text: str) -> bool:
    if not note_text:
        return False

    claim_normalized = _normalize_claim(claim)
    note_normalized = _normalize_claim(note_text)
    if claim_normalized and claim_normalized in note_normalized:
        return True

    claim_tokens = _tokenize(claim)
    note_tokens = _tokenize(note_text)
    if not claim_tokens or not note_tokens:
        return False

    overlap = len(claim_tokens & note_tokens)
    required_overlap = max(2, min(4, len(claim_tokens) // 2))
    return overlap >= required_overlap


def _load_section_note_texts(vault_path: Path, cite_key: str) -> tuple[dict[str, str], list[str]]:
    notes_root = vault_path / "literature" / "papers" / cite_key
    note_texts: dict[str, str] = {}
    missing_notes: list[str] = []

    for note_name in SOURCE_NOTES:
        note_path = notes_root / f"{note_name}.md"
        if not note_path.exists():
            missing_notes.append(note_name)
            continue
        note_texts[note_name] = _strip_frontmatter(note_path.read_text(encoding="utf-8"))

    return note_texts, missing_notes


def _normalize_source_note(source_note: str) -> str:
    clean = _clean_text(source_note)
    if not clean:
        return ""
    return clean.lstrip("/")


def _resolve_source_note_path(vault_path: Path, source_note: str) -> Path | None:
    normalized = _normalize_source_note(source_note)
    if not normalized:
        return None
    if normalized.startswith("literature/"):
        return vault_path / normalized
    return vault_path / "literature" / "papers" / normalized


def _append_trace(
    index: dict[str, list[dict[str, str]]],
    claim: str,
    trace: dict[str, str],
) -> None:
    normalized_claim = _normalize_claim(claim)
    if not normalized_claim:
        return

    compact_trace = {key: _clean_text(value) for key, value in trace.items() if _clean_text(value)}
    if not compact_trace:
        return

    slot = index.setdefault(normalized_claim, [])
    if compact_trace not in slot:
        slot.append(compact_trace)


def _load_explicit_trace_index(work_dir: Path, cite_key: str) -> dict[str, list[dict[str, str]]]:
    layers_path = work_dir / "_summary_layers.json"
    if not layers_path.exists():
        return {}

    try:
        loaded = json.loads(layers_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if not isinstance(loaded, dict):
        return {}

    index: dict[str, list[dict[str, str]]] = {}

    l3_rows = loaded.get("l3_summary_points")
    if isinstance(l3_rows, list):
        for row in l3_rows:
            if not isinstance(row, dict):
                continue
            _append_trace(
                index=index,
                claim=_clean_text(row.get("point")),
                trace={
                    "source_note": _normalize_source_note(str(row.get("source_note") or "")),
                    "source_heading": _clean_text(row.get("source_heading")),
                    "section_id": _clean_text(row.get("section_id")),
                },
            )

    l2_rows = loaded.get("l2_section_map")
    if isinstance(l2_rows, list):
        for row in l2_rows:
            if not isinstance(row, dict):
                continue
            mapped_note = _clean_text(row.get("mapped_note"))
            inferred_source = f"literature/papers/{cite_key}/{mapped_note}.md" if mapped_note else ""
            claims = row.get("key_claims")
            if not isinstance(claims, list):
                continue
            for claim in claims:
                _append_trace(
                    index=index,
                    claim=_clean_text(claim),
                    trace={
                        "source_note": inferred_source,
                        "source_heading": _clean_text(row.get("section_heading")),
                        "section_id": _clean_text(row.get("section_id")),
                    },
                )

    return index


def _claim_snippet(claim: str, limit: int = 180) -> str:
    clean = _clean_text(claim)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _compute_confidence(total_claims: int, flagged_claims: int, missing_source_traces: int) -> str:
    if total_claims == 0:
        return "low"

    flagged_ratio = flagged_claims / total_claims
    missing_trace_ratio = missing_source_traces / total_claims

    if flagged_ratio == 0 and missing_trace_ratio <= 0.1:
        return "high"
    if flagged_ratio <= 0.35 and missing_trace_ratio <= 0.5:
        return "medium"
    return "low"


def _upsert_reading_quality_line(markdown: str, line: str) -> tuple[str, bool]:
    heading_match = re.search(r"(?m)^## Reading Quality\s*$", markdown)
    result_line = f"- {line}"

    if heading_match is None:
        body = markdown.rstrip()
        if body:
            body += "\n\n"
        updated = f"{body}## Reading Quality\n\n{result_line}\n"
        return updated, updated != markdown

    section_start = heading_match.end()
    next_heading = re.search(r"(?m)^##\s+", markdown[section_start:])
    section_end = section_start + next_heading.start() if next_heading else len(markdown)
    section_text = markdown[section_start:section_end]

    existing_lines = section_text.splitlines()
    kept_lines = [item for item in existing_lines if "Faithfulness check result:" not in item]

    while kept_lines and not kept_lines[-1].strip():
        kept_lines.pop()

    rebuilt_body = "\n".join(kept_lines).rstrip()
    if rebuilt_body:
        replacement = f"\n\n{rebuilt_body}\n{result_line}\n\n"
    else:
        replacement = f"\n\n{result_line}\n\n"

    updated = markdown[:section_start] + replacement + markdown[section_end:]
    return updated, updated != markdown


def run_faithfulness_check(
    work_dir: str | Path,
    summary_note: str | Path,
    vault_path: str | Path,
    cite_key: str,
    output: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Trace summary claims to section-note evidence and report faithfulness."""

    work_dir_p = Path(work_dir)
    summary_note_p = Path(summary_note)
    vault_path_p = Path(vault_path)
    output_p = Path(output)

    missing_inputs: list[str] = []
    if not work_dir_p.exists():
        missing_inputs.append(f"work_dir:{_to_posix(work_dir_p)}")
    elif not work_dir_p.is_dir():
        missing_inputs.append(f"work_dir_not_directory:{_to_posix(work_dir_p)}")
    if not summary_note_p.exists():
        missing_inputs.append(f"summary_note:{_to_posix(summary_note_p)}")
    if not vault_path_p.exists():
        missing_inputs.append(f"vault_path:{_to_posix(vault_path_p)}")
    elif not vault_path_p.is_dir():
        missing_inputs.append(f"vault_path_not_directory:{_to_posix(vault_path_p)}")
    if not _clean_text(cite_key):
        missing_inputs.append("cite_key:<empty>")

    summary_markdown = summary_note_p.read_text(encoding="utf-8") if summary_note_p.exists() else ""
    claims = _extract_claims(summary_markdown)
    note_texts, missing_notes = _load_section_note_texts(vault_path_p, cite_key)
    trace_index = _load_explicit_trace_index(work_dir_p, cite_key)

    flags: list[dict[str, str]] = []
    traced_claims = 0
    missing_source_traces = 0

    for claim in claims:
        normalized_claim = _normalize_claim(claim)
        explicit_traces = trace_index.get(normalized_claim, [])

        note_matches = [
            note_name for note_name, note_text in note_texts.items() if _claim_supported_by_note(claim, note_text)
        ]

        has_explicit_trace = bool(explicit_traces)
        if note_matches or has_explicit_trace:
            traced_claims += 1
        else:
            flags.append(
                {
                    "claim_snippet": _claim_snippet(claim),
                    "reason": "no_trace_to_section_notes_or_synthesis_pairs",
                }
            )
            continue

        if not note_matches and has_explicit_trace:
            resolved_paths = [
                _resolve_source_note_path(vault_path_p, trace.get("source_note", ""))
                for trace in explicit_traces
            ]
            existing_paths = [path for path in resolved_paths if path is not None and path.exists()]
            if not existing_paths:
                missing_source_traces += 1

    flagged_claims = len(flags)
    total_claims = len(claims)
    confidence = _compute_confidence(
        total_claims=total_claims,
        flagged_claims=flagged_claims,
        missing_source_traces=missing_source_traces,
    )

    dry_run_summary: dict[str, Any] = {
        "inputs_valid": len(missing_inputs) == 0,
        "total_claims": total_claims,
        "flagged_claims": flagged_claims,
        "confidence": confidence,
    }
    if missing_inputs:
        dry_run_summary["missing_inputs"] = missing_inputs

    if dry_run:
        return dry_run_summary

    if missing_inputs:
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    report: dict[str, Any] = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_claims": total_claims,
        "traced_claims": traced_claims,
        "flagged_claims": flagged_claims,
        "flags": flags,
        "confidence": confidence,
    }

    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    result_line = (
        f"Faithfulness check result: traced {traced_claims}/{total_claims} claims; "
        f"flagged {flagged_claims}; confidence {confidence}."
    )
    updated_markdown, note_changed = _upsert_reading_quality_line(summary_markdown, result_line)
    if note_changed:
        summary_note_p.write_text(updated_markdown, encoding="utf-8")

    return {
        **report,
        "inputs_valid": True,
        "missing_notes": missing_notes,
        "summary_note_updated": note_changed,
        "output_path": _to_posix(output_p),
        "summary_note_path": _to_posix(summary_note_p),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trace rendered summary claims to section notes and synthesis traces.",
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--summary-note", required=True, help="Path to summary note markdown")
    parser.add_argument("--vault-path", required=True, help="Path to Citadel vault root")
    parser.add_argument("--cite-key", required=True, help="Paper cite key")
    parser.add_argument("--output", required=True, help="Path to write _faithfulness_report.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and summarize claim tracing without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = run_faithfulness_check(
        work_dir=args.work_dir,
        summary_note=args.summary_note,
        vault_path=args.vault_path,
        cite_key=args.cite_key,
        output=args.output,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
