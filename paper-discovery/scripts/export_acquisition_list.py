#!/usr/bin/env python3
"""Convert paper_manifest.json to acquisition-list.md markdown table."""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_PAPER_BANK = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))


def parse_reference_queue(queue_path: Path) -> tuple[set, set]:
    """Parse reference-queue.md for deduplication and update tracking.

    Returns:
        excluded_keys: cite_keys with status 'pending', 'downloaded', or 'read'
        mentioned_keys: cite_keys with status 'mentioned' (candidates for promotion)
    """
    excluded_statuses = {"pending", "downloaded", "read"}
    excluded_keys: set = set()
    mentioned_keys: set = set()

    if not queue_path.exists():
        return excluded_keys, mentioned_keys

    text = queue_path.read_text(encoding="utf-8")
    table_lines = [l for l in text.splitlines() if l.strip().startswith("|")]

    if len(table_lines) < 2:
        return excluded_keys, mentioned_keys

    headers = [h.strip().lower().replace(" ", "_").replace("-", "_")
               for h in table_lines[0].strip("|").split("|")]

    try:
        cite_key_idx = headers.index("cite_key")
        status_idx = headers.index("status")
    except ValueError:
        return excluded_keys, mentioned_keys

    for line in table_lines[2:]:  # skip header and separator rows
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) <= max(cite_key_idx, status_idx):
            continue
        cite_key = cells[cite_key_idx]
        status = cells[status_idx].lower()
        if not cite_key:
            continue
        if status in excluded_statuses:
            excluded_keys.add(cite_key)
        elif status == "mentioned":
            mentioned_keys.add(cite_key)

    return excluded_keys, mentioned_keys


def update_reference_queue_to_pending(queue_path: Path, cite_keys: set) -> int:
    """Update reference-queue.md entries from status 'mentioned' to 'pending'.

    Returns the number of entries updated.
    """
    if not cite_keys or not queue_path.exists():
        return 0

    text = queue_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_idx = None
    cite_key_idx = None
    status_idx = None

    for i, line in enumerate(lines):
        if line.strip().startswith("|") and "cite_key" in line.lower():
            headers = [h.strip().lower().replace(" ", "_").replace("-", "_")
                       for h in line.strip("|").split("|")]
            try:
                cite_key_idx = headers.index("cite_key")
                status_idx = headers.index("status")
                header_idx = i
            except ValueError:
                pass
            break

    if header_idx is None:
        return 0

    updated = 0
    new_lines = []
    for i, line in enumerate(lines):
        if i <= header_idx + 1 or not line.strip().startswith("|"):
            new_lines.append(line)
            continue
        cells = line.split("|")
        # cells[0] = empty (leading pipe), cells[1:] = column values
        col_cells = cells[1:]
        if len(col_cells) <= max(cite_key_idx, status_idx):
            new_lines.append(line)
            continue
        cite_key = col_cells[cite_key_idx].strip()
        if cite_key in cite_keys and col_cells[status_idx].strip().lower() == "mentioned":
            # Replace only the status value, preserving surrounding whitespace
            old_cell = col_cells[status_idx]
            col_cells[status_idx] = old_cell.replace(old_cell.strip(), "pending")
            new_lines.append("|" + "|".join(col_cells))
            updated += 1
        else:
            new_lines.append(line)

    if updated > 0:
        new_text = "\n".join(new_lines)
        if text.endswith("\n"):
            new_text += "\n"
        queue_path.write_text(new_text, encoding="utf-8")

    return updated


def parse_existing_cite_keys(output_path: Path) -> set:
    """Return all cite_keys already present in an existing acquisition-list.md."""
    cite_keys = set()
    if not output_path.exists():
        return cite_keys
    for line in output_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ") and not line.startswith("| cite_key") and not line.startswith("|---"):
            parts = [p.strip() for p in line.split("|")]
            # parts[0] is empty (leading pipe), parts[1] is cite_key
            if len(parts) >= 2 and parts[1]:
                cite_keys.add(parts[1])
    return cite_keys


def extract_arxiv_id(paper: dict) -> str:
    """Return arxiv_id from paper dict; fall back to parsing url."""
    arxiv_id = (paper.get("arxiv_id") or "").strip()
    if arxiv_id:
        return arxiv_id
    url = paper.get("url", "")
    match = re.search(r"arxiv\.org/abs/([^\s/]+)", url)
    if match:
        return match.group(1)
    return ""


def map_priority(rank: int, total: int) -> str:
    """Map a 1-based rank to high/medium/low using 30/40/30 split."""
    if total == 0:
        return "low"
    percentile = rank / total
    if percentile <= 0.30:
        return "high"
    elif percentile <= 0.70:
        return "medium"
    else:
        return "low"


def first_sentence(text: str, max_chars: int = 80) -> str:
    """Return the first sentence of text, truncated to max_chars."""
    if not text:
        return ""
    # Split on sentence-ending punctuation
    match = re.search(r"[.!?]", text)
    if match:
        sentence = text[: match.end()].strip()
    else:
        sentence = text.strip()
    if len(sentence) > max_chars:
        sentence = sentence[:max_chars].rstrip()
    return sentence


def sanitize_title(title: str) -> str:
    """Replace pipe characters to avoid markdown table breakage."""
    return title.replace("|", "\u2014")


def build_rows(papers: list, paper_bank_dir: Path = None, source: str = "user") -> list:
    """Build list of row dicts from papers list."""
    total = len(papers)
    rows = []
    for rank, paper in enumerate(papers, start=1):
        cite_key = paper.get("cite_key", "").strip()
        arxiv_id = extract_arxiv_id(paper)

        title = sanitize_title(paper.get("title", "").strip())

        topics = paper.get("topics") or paper.get("categories", [])
        keywords = paper.get("keywords") or paper.get("categories", [])
        topic = (topics[0] if topics else (keywords[0] if keywords else "")).strip()

        priority = map_priority(rank, total)

        relevance = paper.get("relevance_summary") or paper.get("abstract", "")
        reason = first_sentence(relevance)

        if arxiv_id:
            if paper_bank_dir is not None and (paper_bank_dir / cite_key).is_dir():
                status = "downloaded"
            else:
                status = "pending"
        else:
            status = "needs-review"

        rows.append(
            {
                "cite_key": cite_key,
                "arxiv_id": arxiv_id,
                "title": title,
                "topic": topic,
                "priority": priority,
                "reason": reason,
                "status": status,
                "source": source,
            }
        )
    return rows


def format_table(rows: list) -> str:
    """Format rows as a markdown table string."""
    header = "| cite_key | arxiv_id | title | topic | priority | reason | status | source |"
    separator = "|---|---|---|---|---|---|---|---|"
    lines = [header, separator]
    for r in rows:
        lines.append(
            f"| {r['cite_key']} | {r['arxiv_id']} | {r['title']} | {r['topic']}"
            f" | {r['priority']} | {r['reason']} | {r['status']} | {r.get('source', 'user')} |"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Export paper_manifest.json to acquisition-list.md markdown table."
    )
    parser.add_argument(
        "--manifest-path",
        required=True,
        help="Path to paper_manifest.json (paper-discovery output).",
    )
    parser.add_argument(
        "--output-path",
        default=os.path.join(_PAPER_BANK, "acquisition-list.md"),
        help="Path to write acquisition-list.md (default: $PAPER_BANK/acquisition-list.md).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="If set, skip cite_keys already present in the existing output file.",
    )
    parser.add_argument(
        "--reference-queue",
        default=None,
        help=(
            "Path to reference-queue.md. Papers whose cite_key appears with status "
            "'pending', 'downloaded', or 'read' will be excluded from export. "
            "After export, newly added papers are promoted from 'mentioned' to 'pending'."
        ),
    )
    parser.add_argument(
        "--paper-bank-dir",
        default=os.path.join(_PAPER_BANK, "raw"),
        help="Path to paper-bank raw directory for checking already-downloaded papers (default: $PAPER_BANK/raw/).",
    )
    parser.add_argument(
        "--source",
        default="user",
        choices=["user", "reference-queue"],
        help="Source tag to write into the 'source' column of every exported row (default: user).",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest_path).expanduser()
    output_path = Path(args.output_path).expanduser()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    papers = manifest.get("papers") or manifest.get("entries", [])

    existing_cite_keys: set = set()
    if args.append:
        existing_cite_keys = parse_existing_cite_keys(output_path)

    # Parse reference queue for deduplication
    queue_path = Path(args.reference_queue).expanduser() if args.reference_queue else None
    rq_excluded: set = set()
    rq_mentioned: set = set()
    if queue_path:
        rq_excluded, rq_mentioned = parse_reference_queue(queue_path)

    paper_bank_dir = Path(args.paper_bank_dir).expanduser()
    rows = build_rows(papers, paper_bank_dir=paper_bank_dir, source=args.source)

    # Deduplicate when --append
    skipped = 0
    if args.append:
        filtered = []
        for row in rows:
            if row["cite_key"] in existing_cite_keys:
                skipped += 1
            else:
                filtered.append(row)
        rows = filtered

    # Exclude papers already in the pipeline via reference queue
    rq_skipped = 0
    if queue_path and rq_excluded:
        rq_filtered = []
        for row in rows:
            if row["cite_key"] in rq_excluded:
                rq_skipped += 1
            else:
                rq_filtered.append(row)
        rows = rq_filtered

    # Build output content
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header_lines = [
        "# Paper Acquisition List",
        f"# Last updated: {now}",
        f"# Generated from: {manifest_path}",
        "",
    ]

    if not papers:
        header_lines.append("# WARNING: manifest contained zero papers — table is empty.")
        content = "\n".join(header_lines) + "\n"
    else:
        table_str = format_table(rows)
        if args.append and output_path.exists():
            # Append rows only (no new header block)
            existing_content = output_path.read_text(encoding="utf-8")
            # Update the "Last updated" line
            updated_content = re.sub(
                r"# Last updated: .*", f"# Last updated: {now}", existing_content
            )
            if rows:
                new_row_lines = "\n".join(
                    f"| {r['cite_key']} | {r['arxiv_id']} | {r['title']} | {r['topic']}"
                    f" | {r['priority']} | {r['reason']} | {r['status']} | {r.get('source', 'user')} |"
                    for r in rows
                )
                updated_content = updated_content.rstrip("\n") + "\n" + new_row_lines + "\n"
            content = updated_content
        else:
            content = "\n".join(header_lines) + table_str + "\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    # After export: promote newly added papers in reference queue from 'mentioned' to 'pending'
    rq_updated = 0
    if queue_path and rows:
        newly_exported = {r["cite_key"] for r in rows if r["cite_key"] in rq_mentioned}
        if newly_exported:
            rq_updated = update_reference_queue_to_pending(queue_path, newly_exported)

    msg = f"Exported {len(rows)} papers to {output_path}. Skipped {skipped} duplicates."
    if queue_path:
        msg += f" Excluded {rq_skipped} already-in-pipeline (reference queue). Updated {rq_updated} reference-queue entries to 'pending'."
    print(msg)


if __name__ == "__main__":
    main()
