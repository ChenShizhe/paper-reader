#!/usr/bin/env python3
"""Generate a SummQ-style summary coverage quiz and coverage report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORE_TARGETS: list[tuple[str, str]] = [
    ("problem", "Problem & Motivation"),
    ("method", "Proposed Method"),
    ("theory", "Theoretical Guarantees"),
    ("empirics", "Empirical Evidence"),
    ("connections", "Connections to Prior Work"),
]

QUESTION_TEMPLATES: dict[str, str] = {
    "problem": (
        "What concrete gap in prior work motivates this paper, and what would fail if that gap were ignored?"
    ),
    "method": (
        "Describe the proposed method as a sequence of main steps, and explain why each step is needed."
    ),
    "theory": (
        "Which assumptions and guarantees are claimed, and how do those guarantees bound model behavior?"
    ),
    "empirics": (
        "What empirical evidence is presented, and how does it validate the key claims rather than only reporting metrics?"
    ),
    "connections": (
        "How does the paper connect to prior work, and which specific differences matter for interpretation?"
    ),
}

MISSING_SECTION_TEMPLATES: dict[str, str] = {
    "problem": (
        "This section appears missing. What problem statement and failure mode should be added to make the motivation testable?"
    ),
    "method": (
        "This section appears missing. What method steps and design rationale should be added so the approach is reproducible?"
    ),
    "theory": (
        "This section appears missing. What assumptions and formal guarantees should be added to support the claims?"
    ),
    "empirics": (
        "This section appears missing. What experiments would be required to validate the paper's claims convincingly?"
    ),
    "connections": (
        "This section appears missing. Which prior methods should be compared and what contrasts should be explicit?"
    ),
}


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return markdown
    return markdown[end + 5 :]


def _extract_sections(summary_markdown: str) -> dict[str, list[str]]:
    body = _strip_frontmatter(summary_markdown)
    sections: dict[str, list[str]] = {}
    current_heading: str | None = None

    for raw_line in body.splitlines():
        heading_match = re.match(r"^##\s+(.+?)\s*$", raw_line)
        if heading_match:
            current_heading = _clean_text(heading_match.group(1))
            sections.setdefault(current_heading, [])
            continue

        if current_heading is None:
            continue
        sections[current_heading].append(raw_line.rstrip())

    return sections


def _has_substantive_content(lines: list[str]) -> bool:
    for line in lines:
        text = _clean_text(line)
        if not text:
            continue
        if text.startswith("<!--") or text.startswith("|"):
            continue
        if re.match(r"^[-*]\s+", text) or re.match(r"^\d+\.\s+", text):
            return True
        if re.search(r"[A-Za-z]", text):
            return True
    return False


def _build_questions(section_map: dict[str, list[str]]) -> tuple[list[dict[str, str]], list[str], list[str]]:
    available: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []

    for target_key, heading in CORE_TARGETS:
        lines = section_map.get(heading)
        if lines is not None and _has_substantive_content(lines):
            available.append((target_key, heading))
        else:
            missing.append((target_key, heading))

    questions: list[dict[str, str]] = []
    for target_key, heading in available:
        questions.append(
            {
                "prompt": QUESTION_TEMPLATES[target_key],
                "expected_section": heading,
            }
        )

    if len(questions) < 3:
        for target_key, heading in missing:
            questions.append(
                {
                    "prompt": MISSING_SECTION_TEMPLATES[target_key],
                    "expected_section": heading,
                }
            )
            if len(questions) >= 3:
                break

    questions = questions[:5]
    sections_covered = [heading for _, heading in available if any(q["expected_section"] == heading for q in questions)]
    missing_sections = [heading for _, heading in CORE_TARGETS if heading not in sections_covered]
    return questions, sections_covered, missing_sections


def _render_quiz_markdown(cite_key: str, questions: list[dict[str, str]]) -> str:
    lines = [
        f"# Summary Coverage Quiz: {cite_key}",
        "",
        "Answer each question using only the summary note.",
        "",
    ]

    for idx, question in enumerate(questions, start=1):
        lines.extend(
            [
                f"### Question {idx}",
                question["prompt"],
                f"Expected source section: {question['expected_section']}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def generate_summary_quiz(
    work_dir: str | Path,
    summary_note: str | Path,
    output_quiz: str | Path,
    output_report: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate 3-5 deterministic coverage questions and a JSON coverage report."""

    work_dir_p = Path(work_dir)
    summary_note_p = Path(summary_note)
    output_quiz_p = Path(output_quiz)
    output_report_p = Path(output_report)
    cite_key = _clean_text(work_dir_p.name) or _clean_text(summary_note_p.stem) or "unknown"

    missing_inputs: list[str] = []
    if not work_dir_p.exists():
        missing_inputs.append(f"work_dir:{_to_posix(work_dir_p)}")
    elif not work_dir_p.is_dir():
        missing_inputs.append(f"work_dir_not_directory:{_to_posix(work_dir_p)}")
    if not summary_note_p.exists():
        missing_inputs.append(f"summary_note:{_to_posix(summary_note_p)}")

    section_map: dict[str, list[str]] = {}
    if summary_note_p.exists():
        summary_markdown = summary_note_p.read_text(encoding="utf-8")
        section_map = _extract_sections(summary_markdown)

    questions, sections_covered, missing_sections = _build_questions(section_map)
    report_payload: dict[str, Any] = {
        "schemaVersion": "quiz-coverage-report.v1",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "question_count": len(questions),
        "sections_covered": sections_covered,
        "missing_sections": missing_sections,
    }

    summary: dict[str, Any] = {
        "inputs_valid": len(missing_inputs) == 0,
        "question_count": len(questions),
        "sections_covered": sections_covered,
        "missing_sections": missing_sections,
        "work_dir": _to_posix(work_dir_p),
        "summary_note": _to_posix(summary_note_p),
        "output_quiz": _to_posix(output_quiz_p),
        "output_report": _to_posix(output_report_p),
        "dry_run": dry_run,
    }
    if missing_inputs:
        summary["missing_inputs"] = missing_inputs

    if dry_run:
        return summary

    if missing_inputs:
        for item in missing_inputs:
            print(f"ERROR: missing required input: {item}", file=sys.stderr)
        sys.exit(1)

    output_quiz_p.parent.mkdir(parents=True, exist_ok=True)
    output_report_p.parent.mkdir(parents=True, exist_ok=True)

    quiz_markdown = _render_quiz_markdown(cite_key=cite_key, questions=questions)
    output_quiz_p.write_text(quiz_markdown, encoding="utf-8")
    output_report_p.write_text(json.dumps(report_payload, indent=2) + "\n", encoding="utf-8")

    summary["report"] = report_payload
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a SummQ-style quiz and coverage report from a summary note.",
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/ directory.")
    parser.add_argument("--summary-note", required=True, help="Path to summary note markdown file.")
    parser.add_argument("--output-quiz", required=True, help="Path to write quiz markdown.")
    parser.add_argument("--output-report", required=True, help="Path to write JSON coverage report.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and print JSON summary without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = generate_summary_quiz(
        work_dir=args.work_dir,
        summary_note=args.summary_note,
        output_quiz=args.output_quiz,
        output_report=args.output_report,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
