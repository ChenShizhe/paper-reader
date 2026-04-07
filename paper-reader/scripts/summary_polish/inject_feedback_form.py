#!/usr/bin/env python3
"""Inject the Part 9 feedback scaffold into a summary note."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_RATING_LABELS = [
    "Faithfulness:",
    "Coverage:",
    "Usefulness:",
    "Overall verdict:",
    "Revision request:",
]

AI_QUALITY_TEMPLATE_LINES = [
    "<!-- [User-filled] Rate the AI's summary on three dimensions. -->",
    "",
    "**Faithfulness:** [ ] Good  [ ] Has errors -> Describe:",
    "**Coverage:** [ ] Complete  [ ] Missing: (list sections)",
    "**Usefulness:** [ ] Useful  [ ] Not useful for my current work -> Why:",
    "",
    "**Overall verdict:** [ ] Approved  [ ] Needs revision",
    "",
    "**Revision request:** (free text - describe what to fix and what to prioritize)",
]

PERSONAL_SYNTHESIS_TEMPLATE_LINES = [
    "<!-- [AI draft - edit, rewrite, or leave blank] -->",
    "",
    "> *AI draft synthesis:*",
    "> [One paragraph: what this paper does, how it fits in the literature, and what you would tell a colleague about it in one minute.]",
    "",
    "_Your synthesis (optional):_",
]


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _find_h2_section(markdown: str, heading: str) -> tuple[int, int, int] | None:
    pattern = re.compile(rf"(?m)^##\s+{re.escape(heading)}\s*$")
    match = pattern.search(markdown)
    if match is None:
        return None
    body_start = match.end()
    next_heading = re.search(r"(?m)^##\s+", markdown[body_start:])
    end = body_start + next_heading.start() if next_heading else len(markdown)
    return match.start(), body_start, end


def _replace_h2_section_body(markdown: str, heading: str, new_body: str) -> tuple[str, bool]:
    section = _find_h2_section(markdown, heading)
    if section is None:
        return markdown, False
    _, body_start, end = section
    normalized_body = new_body
    if normalized_body and not normalized_body.startswith("\n"):
        normalized_body = "\n" + normalized_body.lstrip("\n")
    updated = markdown[:body_start] + normalized_body + markdown[end:]
    return updated, updated != markdown


def _append_h2_section(markdown: str, heading: str, body: str) -> str:
    base = markdown.rstrip()
    if base:
        base += "\n\n"
    return f"{base}## {heading}\n{body}"


def _insert_feedback_after_reading_quality(markdown: str, feedback_body: str) -> str:
    reading_quality = _find_h2_section(markdown, "Reading Quality")
    feedback_section = f"## Feedback\n{feedback_body}"

    if reading_quality is None:
        return _append_h2_section(markdown, "Feedback", feedback_body)

    _, _, reading_quality_end = reading_quality
    insertion = "\n\n" + feedback_section + "\n"
    return markdown[:reading_quality_end].rstrip() + insertion + markdown[reading_quality_end:]


def _line_present(section_body: str, needle: str) -> bool:
    escaped = re.escape(needle)
    pattern = re.compile(rf"(?im)^\s*(?:[-*]\s+)?(?:\*\*)?{escaped}")
    return pattern.search(section_body) is not None


def _ensure_ai_quality_lines(existing_body: str) -> tuple[str, bool]:
    body = existing_body.rstrip()
    changed = False

    if not body:
        body = "\n".join(AI_QUALITY_TEMPLATE_LINES)
        return body + "\n", True

    missing_lines = [line for line in AI_QUALITY_TEMPLATE_LINES if ":" in line and not _line_present(body, line.split(":")[0] + ":")]
    if missing_lines:
        if not body.endswith("\n"):
            body += "\n"
        body += "\n"
        body += "\n".join(missing_lines)
        changed = True

    if not body.endswith("\n"):
        body += "\n"
    return body, changed


def _ensure_personal_synthesis(existing_body: str) -> tuple[str, bool]:
    body = existing_body.rstrip()
    changed = False

    if not body:
        return "\n".join(PERSONAL_SYNTHESIS_TEMPLATE_LINES) + "\n", True

    required_snippets = ["AI draft synthesis:", "_Your synthesis (optional):_"]
    missing = [snippet for snippet in required_snippets if snippet not in body]
    if missing:
        if not body.endswith("\n"):
            body += "\n"
        body += "\n"
        for line in PERSONAL_SYNTHESIS_TEMPLATE_LINES:
            if line and line in body:
                continue
            body += f"{line}\n"
        changed = True

    if not body.endswith("\n"):
        body += "\n"
    return body, changed


def _normalize_subheading(title: str) -> str:
    return _clean_text(title).lower()


def _split_h3_subsections(section_body: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(re.finditer(r"(?m)^###\s+(.+?)\s*$", section_body))
    if not matches:
        return section_body, []

    prefix = section_body[: matches[0].start()]
    subsections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section_body)
        subsections.append((_clean_text(match.group(1)), section_body[start:end]))

    return prefix, subsections


def _build_feedback_body(existing_body: str | None) -> tuple[str, bool]:
    if existing_body is None:
        ai_body = "\n".join(AI_QUALITY_TEMPLATE_LINES) + "\n"
        personal_body = "\n".join(PERSONAL_SYNTHESIS_TEMPLATE_LINES) + "\n"
        combined = (
            "\n"
            "### AI Summary Quality\n"
            f"{ai_body}\n"
            "### Personal Synthesis\n"
            f"{personal_body}"
        )
        return combined, True

    prefix, subsections = _split_h3_subsections(existing_body)
    subsection_map = { _normalize_subheading(title): body for title, body in subsections }

    ai_existing = subsection_map.get("ai summary quality", "")
    personal_existing = subsection_map.get("personal synthesis", "")

    ai_body, ai_changed = _ensure_ai_quality_lines(ai_existing)
    personal_body, personal_changed = _ensure_personal_synthesis(personal_existing)

    preserved_prefix = prefix.rstrip()
    chunks: list[str] = []
    if preserved_prefix:
        chunks.append(preserved_prefix + "\n")
    chunks.append("### AI Summary Quality\n")
    chunks.append(ai_body.rstrip() + "\n\n")
    chunks.append("### Personal Synthesis\n")
    chunks.append(personal_body.rstrip() + "\n")

    rebuilt = "".join(chunks)
    changed = ai_changed or personal_changed or len(subsections) != 2 or sorted(_normalize_subheading(t) for t, _ in subsections) != [
        "ai summary quality",
        "personal synthesis",
    ]
    return rebuilt, changed or rebuilt != existing_body


def _extract_confidence_value(faithfulness_report: Path) -> str:
    fallback = "medium"
    if not faithfulness_report.exists():
        return fallback

    try:
        payload = json.loads(faithfulness_report.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback

    if not isinstance(payload, dict):
        return fallback

    report_confidence = _clean_text(payload.get("confidence")).lower()
    if report_confidence in {"high", "medium", "low"}:
        return report_confidence

    total_claims = payload.get("total_claims")
    flagged_claims = payload.get("flagged_claims")
    if isinstance(total_claims, int) and isinstance(flagged_claims, int) and total_claims > 0:
        ratio = flagged_claims / total_claims
        if ratio <= 0.1:
            return "high"
        if ratio <= 0.35:
            return "medium"
        return "low"
    return fallback


def _ensure_overall_confidence_line(reading_quality_body: str, confidence_value: str) -> tuple[str, bool]:
    target_phrase = "Overall confidence in this summary:"
    if re.search(rf"(?im){re.escape(target_phrase)}", reading_quality_body):
        return reading_quality_body, False

    line = f"- {target_phrase} {confidence_value}."
    trimmed = reading_quality_body.rstrip()
    if trimmed:
        updated = f"{trimmed}\n{line}\n"
    else:
        updated = f"\n{line}\n"
    return updated, True


def _collect_sections_present(markdown: str) -> dict[str, Any]:
    feedback = _find_h2_section(markdown, "Feedback")
    reading = _find_h2_section(markdown, "Reading Quality")

    feedback_body = markdown[feedback[1]:feedback[2]] if feedback else ""
    reading_body = markdown[reading[1]:reading[2]] if reading else ""

    rating_presence = {label: _line_present(feedback_body, label) for label in REQUIRED_RATING_LABELS}

    return {
        "feedback_heading": feedback is not None,
        "ai_summary_quality_heading": "### AI Summary Quality" in feedback_body,
        "personal_synthesis_heading": "### Personal Synthesis" in feedback_body,
        "rating_lines": rating_presence,
        "overall_confidence_line": "Overall confidence in this summary:" in reading_body,
    }


def inject_feedback_form(
    summary_note: str | Path,
    cite_key: str,
    faithfulness_report: str | Path,
    output_report: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ensure the feedback scaffold and confidence line exist in the summary note."""

    summary_note_p = Path(summary_note)
    faithfulness_report_p = Path(faithfulness_report)
    output_report_p = Path(output_report)
    clean_cite_key = _clean_text(cite_key)

    missing_inputs: list[str] = []
    if not summary_note_p.exists():
        missing_inputs.append(f"summary_note:{_to_posix(summary_note_p)}")
    if not faithfulness_report_p.exists():
        missing_inputs.append(f"faithfulness_report:{_to_posix(faithfulness_report_p)}")
    if not clean_cite_key:
        missing_inputs.append("cite_key:<empty>")

    confidence_value = _extract_confidence_value(faithfulness_report_p)

    original_markdown = summary_note_p.read_text(encoding="utf-8") if summary_note_p.exists() else ""
    updated_markdown = original_markdown

    reading_quality = _find_h2_section(updated_markdown, "Reading Quality")
    reading_quality_changed = False
    if reading_quality is None:
        updated_markdown = _append_h2_section(
            updated_markdown,
            "Reading Quality",
            f"\n- Overall confidence in this summary: {confidence_value}.\n",
        )
        reading_quality_changed = True
    else:
        _, body_start, body_end = reading_quality
        body = updated_markdown[body_start:body_end]
        updated_body, changed = _ensure_overall_confidence_line(body, confidence_value)
        if changed:
            updated_markdown = updated_markdown[:body_start] + updated_body + updated_markdown[body_end:]
            reading_quality_changed = True

    feedback_section = _find_h2_section(updated_markdown, "Feedback")
    feedback_body = None if feedback_section is None else updated_markdown[feedback_section[1]:feedback_section[2]]
    rebuilt_feedback_body, feedback_changed = _build_feedback_body(feedback_body)

    if feedback_section is None:
        updated_markdown = _insert_feedback_after_reading_quality(updated_markdown, rebuilt_feedback_body)
        feedback_changed = True
    else:
        updated_markdown, replaced = _replace_h2_section_body(updated_markdown, "Feedback", rebuilt_feedback_body)
        feedback_changed = feedback_changed or replaced

    sections_present = _collect_sections_present(updated_markdown)
    note_changed = updated_markdown != original_markdown

    summary: dict[str, Any] = {
        "inputs_valid": len(missing_inputs) == 0,
        "feedback_inserted": feedback_changed,
        "sections_present": sections_present,
        "confidence_value": confidence_value,
    }
    if missing_inputs:
        summary["missing_inputs"] = missing_inputs

    if dry_run:
        return summary

    if missing_inputs:
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    if note_changed:
        summary_note_p.write_text(updated_markdown, encoding="utf-8")

    report_payload = {
        "schemaVersion": "feedback-form-report.v1",
        "cite_key": clean_cite_key,
        "feedback_inserted": feedback_changed,
        "sections_present": sections_present,
        "confidence_value": confidence_value,
    }
    output_report_p.parent.mkdir(parents=True, exist_ok=True)
    output_report_p.write_text(json.dumps(report_payload, indent=2) + "\n", encoding="utf-8")

    return {
        **summary,
        "schemaVersion": report_payload["schemaVersion"],
        "cite_key": clean_cite_key,
        "summary_note_path": _to_posix(summary_note_p),
        "output_report_path": _to_posix(output_report_p),
        "summary_note_updated": note_changed,
        "reading_quality_updated": reading_quality_changed,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inject or update the Part 9 feedback scaffold in a summary note.",
    )
    parser.add_argument("--summary-note", required=True, help="Path to summary note markdown")
    parser.add_argument("--cite-key", required=True, help="Paper cite key")
    parser.add_argument("--faithfulness-report", required=True, help="Path to _faithfulness_report.json")
    parser.add_argument("--output-report", required=True, help="Path to write _feedback_form_report.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and report changes without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = inject_feedback_form(
        summary_note=args.summary_note,
        cite_key=args.cite_key,
        faithfulness_report=args.faithfulness_report,
        output_report=args.output_report,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
