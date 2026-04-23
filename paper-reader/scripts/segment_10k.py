#!/usr/bin/env python3
"""segment_10k.py — split a 10-K markdown translation into Item-boundary segments.

Reads <paper-bank>/translated_full.md and produces one segment per Item
(canonical SEC Form 10-K hierarchy: Part I Items 1, 1A, 1B, 1C, 2, 3, 4;
Part II Items 5, 6, 7, 7A, 8, 9, 9A, 9B; Part III Items 10–14; Part IV
Items 15, 16). Missing Items are recorded in the manifest with
`present: false`. Detects incorporation-by-reference language so Part III
items in a typical large-cap 10-K are preserved with the reference pointer.

Output:
  <paper-bank>/segments/item-<N>.md         — one file per present Item
  <paper-bank>/segments/_segment_manifest.json

This segmenter is proposal-07-specific and does not replace `segment_paper.py`;
the latter continues to handle academic-paper segmentation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Canonical Form 10-K Item hierarchy. Items can be present / absent /
# incorporated-by-reference (the typical pattern for Items 10–14).
CANONICAL_ITEMS: list[tuple[str, str]] = [
    ("1",   "Business"),
    ("1A",  "Risk Factors"),
    ("1B",  "Unresolved Staff Comments"),
    ("1C",  "Cybersecurity"),
    ("2",   "Properties"),
    ("3",   "Legal Proceedings"),
    ("4",   "Mine Safety Disclosures"),
    ("5",   "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities"),
    ("6",   "[Reserved]"),
    ("7",   "Management's Discussion and Analysis of Financial Condition and Results of Operations"),
    ("7A",  "Quantitative and Qualitative Disclosures About Market Risk"),
    ("8",   "Financial Statements and Supplementary Data"),
    ("9",   "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure"),
    ("9A",  "Controls and Procedures"),
    ("9B",  "Other Information"),
    ("10",  "Directors, Executive Officers and Corporate Governance"),
    ("11",  "Executive Compensation"),
    ("12",  "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters"),
    ("13",  "Certain Relationships and Related Transactions, and Director Independence"),
    ("14",  "Principal Accountant Fees and Services"),
    ("15",  "Exhibits and Financial Statement Schedules"),
    ("16",  "Form 10-K Summary"),
]

# Matches "Item 1.", "Item 1A.", "ITEM 7.", "Item 7A:" etc. at start of a line,
# optionally preceded by heading markers (#, ##, etc.).
_ITEM_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+)?Item\s+(\d{1,2}[A-Z]?)\s*[.:\-–—]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Incorporation-by-reference detection on a per-Item body.
_IBR_RE = re.compile(
    r"incorporated\s+(?:herein\s+)?by\s+reference\s+(?:from|to)\s+([^.\n]+)",
    re.IGNORECASE,
)


@dataclass
class ItemSegment:
    item_number: str
    item_title: str
    present: str  # "true" | "false" | "incorporated_by_reference"
    start: int = 0
    end: int = 0
    body: str = ""
    incorporation_pointer: str = ""
    word_count: int = 0
    page_range: str = ""  # filled in when PyMuPDF page markers exist


def _canonical_title(number: str) -> str:
    for num, title in CANONICAL_ITEMS:
        if num == number:
            return title
    return ""


def detect_items(text: str) -> list[ItemSegment]:
    """Scan *text* and return ItemSegment records for every detected Item heading.

    Items are ordered by their position in the text, not by canonical index —
    the caller merges with canonical to fill in absent Items.
    """
    matches = list(_ITEM_HEADING_RE.finditer(text))
    if not matches:
        return []

    segments: list[ItemSegment] = []
    for idx, m in enumerate(matches):
        item_number = m.group(1).upper()
        item_title = m.group(2).strip()
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        ibr_match = _IBR_RE.search(body[:2000])  # first 2 KB only — avoid false positives
        presence = "true"
        pointer = ""
        if ibr_match and len(body) < 3000:
            presence = "incorporated_by_reference"
            pointer = ibr_match.group(1).strip().rstrip(",.;")

        segments.append(
            ItemSegment(
                item_number=item_number,
                item_title=item_title or _canonical_title(item_number),
                present=presence,
                start=start,
                end=end,
                body=body,
                incorporation_pointer=pointer,
                word_count=len(re.findall(r"[A-Za-z0-9]+", body)),
            )
        )
    return segments


def _detect_page_markers(body: str) -> str:
    """Return a 'first-last' page range if PyMuPDF page markers exist."""
    pages = [int(m.group(1)) for m in re.finditer(r"<!--\s*page\s+(\d+)\s*-->", body)]
    if not pages:
        return ""
    return f"{min(pages)}-{max(pages)}"


def merge_with_canonical(
    detected: list[ItemSegment],
) -> list[ItemSegment]:
    """Merge detected Items against CANONICAL_ITEMS, filling in absent ones.

    Absent Items get present="false" stubs so the manifest records the
    complete canonical Item list for downstream validators.
    """
    by_number: dict[str, ItemSegment] = {seg.item_number: seg for seg in detected}
    complete: list[ItemSegment] = []
    for number, title in CANONICAL_ITEMS:
        if number in by_number:
            seg = by_number[number]
            if seg.body and "<!--" in seg.body:
                seg.page_range = _detect_page_markers(seg.body)
            complete.append(seg)
        else:
            complete.append(
                ItemSegment(
                    item_number=number,
                    item_title=title,
                    present="false",
                )
            )
    return complete


def write_segments(
    segments: list[ItemSegment],
    *,
    cite_key: str,
    output_dir: Path,
) -> Path:
    """Write per-Item segment files and a 10-K-specific manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict] = []
    for seg in segments:
        if seg.present in ("true", "incorporated_by_reference") and seg.body:
            filename = f"item-{seg.item_number}.md"
            path = output_dir / filename
            header = (
                "---\n"
                f"cite_key: {cite_key}\n"
                f"item: {seg.item_number}\n"
                f"item_title: {seg.item_title}\n"
                f"present: {seg.present}\n"
                f"word_count: {seg.word_count}\n"
                + (f"page_range: {seg.page_range}\n" if seg.page_range else "")
                + (
                    f"incorporation_pointer: {seg.incorporation_pointer}\n"
                    if seg.incorporation_pointer
                    else ""
                )
                + "---\n\n"
            )
            body = seg.body if seg.present == "true" else (
                f"{seg.body}\n\n*Incorporated by reference from: {seg.incorporation_pointer}*\n"
            )
            path.write_text(header + body, encoding="utf-8")
        entry = {
            "item": seg.item_number,
            "item_title": seg.item_title,
            "present": seg.present,
            "word_count": seg.word_count,
        }
        if seg.page_range:
            entry["page_range"] = seg.page_range
        if seg.incorporation_pointer:
            entry["incorporation_pointer"] = seg.incorporation_pointer
        if seg.present == "true" and seg.body:
            entry["file"] = f"segments/item-{seg.item_number}.md"
        manifest_entries.append(entry)

    manifest_path = output_dir / "_segment_manifest.json"
    manifest = {
        "cite_key": cite_key,
        "segmentation_version": "10k_v1",
        "segments": manifest_entries,
        "segment_count": len(manifest_entries),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def segment_10k_file(
    *,
    cite_key: str,
    source_path: Path,
    output_dir: Path,
) -> Path:
    text = source_path.read_text(encoding="utf-8", errors="replace")
    detected = detect_items(text)
    segments = merge_with_canonical(detected)
    return write_segments(segments, cite_key=cite_key, output_dir=output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Segment a 10-K markdown translation into Item segments.")
    parser.add_argument("--cite-key", required=True)
    parser.add_argument("--source-path", required=True, help="Path to translated_full.md")
    parser.add_argument("--output-dir", required=True, help="Output directory for segments.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.source_path).expanduser()
    output = Path(args.output_dir).expanduser()
    if not source.exists():
        print(f"ERROR: source file not found: {source}", file=sys.stderr)
        return 1
    manifest_path = segment_10k_file(
        cite_key=args.cite_key,
        source_path=source,
        output_dir=output,
    )
    print(f"[segment_10k] manifest written: {manifest_path}", file=sys.stderr)
    print(str(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
