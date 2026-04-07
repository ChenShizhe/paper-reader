#!/usr/bin/env python3
"""Read reference-queue.md and output a seed list JSON for paper-discovery."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_markdown_table(text: str) -> list[dict]:
    """Parse a markdown table into a list of dicts keyed by header."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    table_lines = [l for l in lines if l.startswith("|")]
    if len(table_lines) < 2:
        return []

    # First line = headers, second = separator, rest = rows
    headers = [h.strip() for h in table_lines[0].strip("|").split("|")]
    rows = []
    for line in table_lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        rows.append(dict(zip(headers, cells)))
    return rows


def safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read reference-queue.md and output a seed list JSON."
    )
    parser.add_argument(
        "--reference-queue",
        required=True,
        help="Path to reference-queue.md",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Number of top entries to return (default: 20)",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=1,
        help="Minimum importance_score to include (default: 1)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write seed list JSON (default: stdout)",
    )
    args = parser.parse_args()

    queue_path = Path(args.reference_queue)

    # Edge case: file not found — return gracefully, no exception
    if not queue_path.exists():
        result = {"seeds": [], "error": "reference-queue not found"}
        output_json(result, args.output)
        return

    try:
        text = queue_path.read_text(encoding="utf-8")
    except OSError:
        result = {"seeds": [], "error": "reference-queue not found"}
        output_json(result, args.output)
        return

    rows = parse_markdown_table(text)

    # Normalise header names: lowercase, replace spaces/hyphens with underscores
    def normalise(row: dict) -> dict:
        return {
            re.sub(r"[\s\-]+", "_", k).lower().strip("_"): v
            for k, v in row.items()
        }

    rows = [normalise(r) for r in rows]

    # Filter: status must be 'mentioned'
    mentioned = [r for r in rows if r.get("status", "").lower() == "mentioned"]

    # Sort: importance_score desc, sessions_cited desc
    mentioned.sort(
        key=lambda r: (
            safe_int(r.get("importance_score", "0")),
            safe_int(r.get("sessions_cited", "0")),
        ),
        reverse=True,
    )

    # Apply min-score filter and top-n cap
    filtered = [
        r for r in mentioned
        if safe_int(r.get("importance_score", "0")) >= args.min_score
    ][: args.top_n]

    seeds = []
    for r in filtered:
        arxiv_id = r.get("arxiv_id", "").strip()
        cite_key = r.get("cite_key", "").strip()
        seeds.append(
            {
                "arxiv_id": arxiv_id,  # may be blank; caller uses cite_key as fallback
                "cite_key": cite_key,
                "title": r.get("title", "").strip(),
                "importance_score": safe_int(r.get("importance_score", "0")),
                "sessions_cited": safe_int(r.get("sessions_cited", "0")),
            }
        )

    result = {
        "seeds": seeds,
        "source": "reference-queue",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_json(result, args.output)


def output_json(data: dict, output_path: str | None) -> None:
    text = json.dumps(data, indent=2)
    if output_path:
        Path(output_path).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
