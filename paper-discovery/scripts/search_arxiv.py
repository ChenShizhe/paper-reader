#!/usr/bin/env python3
"""Query the official arXiv API and save normalized JSON discovery artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
NS = {
    "atom": ATOM_NS,
    "arxiv": ARXIV_NS,
}

# Retry wait intervals (seconds) for HTTP 429 responses; length == max retry count.
_RETRY_WAITS = [60, 120, 240, 300]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search arXiv via the official API")
    parser.add_argument(
        "--query",
        action="append",
        required=True,
        help="Raw arXiv API search_query value; may be passed multiple times",
    )
    parser.add_argument("--start", type=int, default=0, help="Result offset per query")
    parser.add_argument("--max-results", type=int, default=30, help="Max results per query")
    parser.add_argument(
        "--sort-by",
        choices=("relevance", "lastUpdatedDate", "submittedDate"),
        default="relevance",
        help="arXiv API sortBy value",
    )
    parser.add_argument(
        "--sort-order",
        choices=("ascending", "descending"),
        default="descending",
        help="arXiv API sortOrder value",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=3.0,
        help="Delay between distinct queries to respect arXiv courtesy guidance",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--user-agent",
        default="paper-discovery/1.0",
        help="User-Agent header for API requests",
    )
    parser.add_argument(
        "--base-url",
        default=ARXIV_API_URL,
        help="Override the arXiv API endpoint if needed",
    )
    parser.add_argument("--output", default="arxiv_results.json", help="Output JSON path")
    parser.add_argument(
        "--log-output",
        default="",
        help="Optional JSON log path for request metadata",
    )
    return parser.parse_args()


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def build_request_url(
    *,
    base_url: str,
    query: str,
    start: int,
    max_results: int,
    sort_by: str,
    sort_order: str,
) -> str:
    params = {
        "search_query": query,
        "start": str(start),
        "max_results": str(max_results),
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def fetch_url(url: str, *, user_agent: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/atom+xml",
            "User-Agent": user_agent,
        },
    )
    for attempt in range(len(_RETRY_WAITS) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == len(_RETRY_WAITS):
                raise
            wait = _RETRY_WAITS[attempt]
            print(
                f"Rate limited (429). Retry {attempt + 1}/{len(_RETRY_WAITS)} after {wait}s ...",
                flush=True,
            )
            time.sleep(wait)
    raise RuntimeError("unreachable")


def entry_pdf_url(entry: ET.Element) -> str | None:
    for link in entry.findall("atom:link", NS):
        href = normalize_text(link.get("href"))
        if not href:
            continue
        title = normalize_text(link.get("title")).lower()
        mime_type = normalize_text(link.get("type")).lower()
        if title == "pdf" or mime_type == "application/pdf" or "/pdf/" in href:
            return href
    return None


def parse_feed(feed_bytes: bytes, *, source_query: str) -> list[dict[str, Any]]:
    root = ET.fromstring(feed_bytes)
    records: list[dict[str, Any]] = []

    for entry in root.findall("atom:entry", NS):
        authors = [
            normalize_text(author.findtext("atom:name", default="", namespaces=NS))
            for author in entry.findall("atom:author", NS)
        ]
        categories = []
        for category in entry.findall("atom:category", NS):
            term = normalize_text(category.get("term"))
            if term and term not in categories:
                categories.append(term)

        primary_category = entry.find("arxiv:primary_category", NS)
        primary_term = normalize_text(primary_category.get("term")) if primary_category is not None else ""
        if primary_term and primary_term not in categories:
            categories.insert(0, primary_term)

        record = {
            "id": normalize_text(entry.findtext("atom:id", default="", namespaces=NS)),
            "title": normalize_text(entry.findtext("atom:title", default="", namespaces=NS)),
            "summary": normalize_text(entry.findtext("atom:summary", default="", namespaces=NS)),
            "published": normalize_text(entry.findtext("atom:published", default="", namespaces=NS)),
            "updated": normalize_text(entry.findtext("atom:updated", default="", namespaces=NS)),
            "authors": [author for author in authors if author],
            "categories": categories,
            "primary_category": primary_term or None,
            "doi": normalize_text(entry.findtext("arxiv:doi", default="", namespaces=NS)) or None,
            "pdf_url": entry_pdf_url(entry),
            "source_query": source_query,
        }
        records.append(record)

    return records


def query_arxiv(
    *,
    queries: list[str],
    start: int,
    max_results: int,
    sort_by: str,
    sort_order: str,
    delay_seconds: float,
    timeout: float,
    user_agent: str,
    base_url: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    request_log: list[dict[str, Any]] = []

    for index, query in enumerate(queries):
        if index > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)

        request_url = build_request_url(
            base_url=base_url,
            query=query,
            start=start,
            max_results=max_results,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        feed_bytes = fetch_url(request_url, user_agent=user_agent, timeout=timeout)
        query_records = parse_feed(feed_bytes, source_query=query)
        records.extend(query_records)
        request_log.append(
            {
                "query": query,
                "request_url": request_url,
                "entry_count": len(query_records),
            }
        )

    run_log = {
        "api_url": base_url,
        "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query_count": len(queries),
        "start": start,
        "max_results": max_results,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "delay_seconds": delay_seconds,
        "requests": request_log,
        "entry_count": len(records),
    }
    return records, run_log


def main() -> int:
    args = parse_args()
    if args.start < 0:
        print("ERROR: --start must be non-negative")
        return 1
    if args.max_results <= 0:
        print("ERROR: --max-results must be positive")
        return 1
    if args.delay_seconds < 0:
        print("ERROR: --delay-seconds must be non-negative")
        return 1
    if args.timeout <= 0:
        print("ERROR: --timeout must be positive")
        return 1

    try:
        records, run_log = query_arxiv(
            queries=args.query,
            start=args.start,
            max_results=args.max_results,
            sort_by=args.sort_by,
            sort_order=args.sort_order,
            delay_seconds=args.delay_seconds,
            timeout=args.timeout,
            user_agent=args.user_agent,
            base_url=args.base_url,
        )
    except (ET.ParseError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.log_output:
        log_path = Path(args.log_output)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(run_log, indent=2) + "\n", encoding="utf-8")

    print(f"Saved arXiv results: {output_path}")
    print(f"Queries: {len(args.query)}")
    print(f"Entries: {len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
