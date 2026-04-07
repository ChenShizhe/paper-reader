#!/usr/bin/env python3
"""Build a schema-v1 paper manifest from discovery source results.

The script accepts saved raw JSON from discovery searches, normalizes records from
Zotero, arXiv, OpenAlex, web fallback, and PubMed, deduplicates on strong
identifiers, assigns canonical paper identities, computes provisional cite keys,
ranks entries, and emits `paper_manifest.json`.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from identity import (
    assign_cite_keys,
    canonical_id_for_entry,
    clean_doi,
    clean_openalex_id,
    clean_pmid,
    clean_text,
    normalize_title,
)

SOURCE_PRIORITY = {
    "zotero": 5,
    "arxiv": 4,
    "openalex": 3,
    "pubmed": 2,
    "web": 1,
}

SEARCH_SOURCE_ORDER = ("zotero", "arxiv", "openalex", "web", "pubmed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build schema-v1 paper manifest")
    parser.add_argument("--topic", required=True, help="Topic label stored in the manifest")
    parser.add_argument(
        "--zotero-results",
        action="append",
        default=[],
        help="Path to saved Zotero JSON results; may be passed multiple times",
    )
    parser.add_argument(
        "--arxiv-results",
        action="append",
        default=[],
        help="Path to saved arXiv JSON results; may be passed multiple times",
    )
    parser.add_argument(
        "--openalex-results",
        action="append",
        default=[],
        help="Path to saved OpenAlex JSON results; may be passed multiple times",
    )
    parser.add_argument(
        "--web-results",
        action="append",
        default=[],
        help="Path to saved fallback web JSON results; may be passed multiple times",
    )
    parser.add_argument(
        "--pubmed-results",
        action="append",
        default=[],
        help="Path to saved PubMed JSON results; may be passed multiple times",
    )
    parser.add_argument(
        "--seed-papers",
        default="",
        help="Comma-separated seed identifiers (arXiv ID, DOI, OpenAlex ID, or PMID)",
    )
    parser.add_argument("--keywords", default="", help="Comma-separated keywords")
    parser.add_argument("--date-start", type=int, default=2010, help="Earliest year in scope")
    parser.add_argument(
        "--date-end",
        type=int,
        default=datetime.now(timezone.utc).year,
        help="Latest year in scope",
    )
    parser.add_argument("--max-papers", type=int, default=30, help="Maximum manifest entries")
    parser.add_argument("--output", default="paper_manifest.json", help="Output JSON path")
    return parser.parse_args()


def load_json_records(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []

    for key in ("entries", "results", "works", "items", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    if all(not isinstance(value, list) for value in data.values()):
        return [data]
    return []


def ensure_input_paths(args: argparse.Namespace) -> None:
    input_paths = (
        args.zotero_results
        + args.arxiv_results
        + args.openalex_results
        + args.web_results
        + args.pubmed_results
    )
    if not input_paths:
        raise ValueError("At least one source result file is required")

    for raw_path in input_paths:
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        if not path.is_file():
            raise ValueError(f"Input path is not a file: {path}")


def extract_arxiv_id(value: Any) -> str | None:
    if not value:
        return None
    text = clean_text(value)
    if clean_doi(text) and "arxiv" not in text.lower():
        return None
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", text)
    if match:
        return match.group(1)
    match = re.search(r"arxiv[:/ ]([A-Za-z\-\.]+/\d{7})(v\d+)?", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = clean_text(value)
    match = re.search(r"-?\d+", text)
    if match:
        return int(match.group(0))
    return None


def parse_year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = clean_text(value)
    match = re.search(r"(19|20)\d{2}", text)
    if match:
        return int(match.group(0))
    return None


def normalize_authors(value: Any) -> list[str]:
    authors: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                name = clean_text(item)
            elif isinstance(item, dict):
                name = clean_text(
                    item.get("name")
                    or item.get("display_name")
                    or item.get("full_name")
                    or (
                        f"{clean_text(item.get('firstName'))} {clean_text(item.get('lastName'))}".strip()
                        if item.get("firstName") or item.get("lastName")
                        else ""
                    )
                    or item.get("lastName")
                    or item.get("author", {}).get("display_name")
                )
            else:
                name = ""
            if name:
                authors.append(name)
    elif isinstance(value, str):
        authors = [part.strip() for part in value.split(",") if part.strip()]

    deduped: list[str] = []
    seen: set[str] = set()
    for author in authors:
        if author not in seen:
            deduped.append(author)
            seen.add(author)
    return deduped


def normalize_categories(value: Any) -> list[str]:
    categories: list[str] = []
    if isinstance(value, str):
        categories = [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                label = clean_text(item)
            elif isinstance(item, dict):
                label = clean_text(
                    item.get("display_name")
                    or item.get("name")
                    or item.get("id")
                    or item.get("term")
                )
            else:
                label = ""
            if label:
                categories.append(label)
    elif isinstance(value, dict):
        for key in ("primary", "all"):
            if key in value:
                categories.extend(normalize_categories(value[key]))

    deduped: list[str] = []
    seen: set[str] = set()
    for category in categories:
        if category not in seen:
            deduped.append(category)
            seen.add(category)
    return deduped


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for token, indexes in inverted_index.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int):
                positions[index] = token
    return " ".join(token for _, token in sorted(positions.items()))


def arxiv_pdf_url(arxiv_id: str | None) -> str | None:
    if not arxiv_id:
        return None
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def normalize_identifier_aliases(value: Any) -> set[str]:
    aliases: set[str] = set()
    text = clean_text(value)
    if not text:
        return aliases

    arxiv_id = extract_arxiv_id(text)
    if arxiv_id:
        aliases.add(arxiv_id)
        aliases.add(f"arxiv:{arxiv_id}")

    doi = clean_doi(text)
    if doi:
        aliases.add(doi)
        aliases.add(f"doi:{doi}")

    openalex_id = clean_openalex_id(text)
    if openalex_id:
        aliases.add(openalex_id)
        aliases.add(f"openalex:{openalex_id}")

    pmid = clean_pmid(text)
    if pmid:
        aliases.add(pmid)
        aliases.add(f"pmid:{pmid}")

    aliases.add(text)
    return aliases


def record_aliases(
    arxiv_id: str | None,
    doi: str | None,
    openalex_id: str | None,
    pmid: str | None,
    extras: list[Any] | None = None,
) -> set[str]:
    aliases: set[str] = set()
    for value in (arxiv_id, doi, openalex_id, pmid):
        aliases.update(normalize_identifier_aliases(value))
    for value in extras or []:
        aliases.update(normalize_identifier_aliases(value))
    return aliases


def extract_neighbor_tokens(raw: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    tokens: set[str] = set()
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    tokens.update(
                        normalize_identifier_aliases(
                            item.get("id")
                            or item.get("doi")
                            or item.get("openalex")
                            or item.get("pmid")
                            or item.get("paperId")
                        )
                    )
                else:
                    tokens.update(normalize_identifier_aliases(item))
        elif isinstance(value, dict):
            tokens.update(normalize_identifier_aliases(value.get("id")))
        else:
            tokens.update(normalize_identifier_aliases(value))
    return tokens


def extract_citation_key(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None

    for line in text.splitlines():
        lower_line = line.lower()
        if "citation key" in lower_line or "citekey" in lower_line:
            _, _, candidate = line.partition(":")
            cleaned = clean_text(candidate or line)
            if cleaned:
                normalized = re.sub(r"[^A-Za-z0-9_]+", "", cleaned).lower()
                return normalized or None
    return None


def normalize_zotero_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    title = clean_text(data.get("title") or raw.get("title"))
    if not title:
        return None

    item_key = clean_text(data.get("key") or raw.get("item_key") or raw.get("id"))
    doi = clean_doi(data.get("DOI") or raw.get("doi"))
    arxiv_id = extract_arxiv_id(
        data.get("archiveLocation")
        or raw.get("arxiv_id")
        or raw.get("id")
        or data.get("url")
    )
    pmid = clean_pmid(data.get("PMID") or raw.get("pmid"))
    openalex_id = clean_openalex_id(raw.get("openalex_id"))
    cite_key = clean_text(raw.get("cite_key") or raw.get("citation_key")) or extract_citation_key(
        data.get("extra") or raw.get("extra")
    )
    if cite_key:
        cite_key = re.sub(r"[^A-Za-z0-9_]+", "", cite_key).lower() or None

    tags = data.get("tags")
    normalized_tags: list[str] = []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                normalized_tags.append(clean_text(tag.get("tag")))
            else:
                normalized_tags.append(clean_text(tag))

    url = clean_text(raw.get("zotero_url") or data.get("url"))
    aliases = record_aliases(
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=openalex_id,
        pmid=pmid,
        extras=[item_key, f"zotero:{item_key}" if item_key else "", url],
    )
    if item_key:
        aliases.add(item_key)
        aliases.add(f"zotero:{item_key}")

    return {
        "source": "zotero",
        "title": title,
        "authors": normalize_authors(data.get("creators") or raw.get("authors")),
        "year": parse_year(data.get("date") or raw.get("year")),
        "abstract": clean_text(data.get("abstractNote") or raw.get("abstract")),
        "arxiv_id": arxiv_id,
        "doi": doi,
        "openalex_id": openalex_id,
        "pmid": pmid,
        "pdf_url": clean_text(raw.get("pdf_url")) or None,
        "categories": normalize_categories(normalized_tags or raw.get("categories")),
        "citation_count": parse_int(raw.get("citation_count")),
        "search_source": "zotero",
        "cite_key": cite_key,
        "aliases": aliases,
        "neighbor_tokens": extract_neighbor_tokens(raw, ("references", "citations")),
    }


def normalize_arxiv_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    arxiv_id = extract_arxiv_id(raw.get("arxiv_id") or raw.get("id") or raw.get("entry_id"))
    title = clean_text(raw.get("title"))
    if not title:
        return None

    doi = clean_doi(raw.get("doi"))
    year = parse_year(raw.get("published") or raw.get("year") or raw.get("updated"))
    pdf_url = clean_text(raw.get("pdf_url")) or arxiv_pdf_url(arxiv_id)
    categories = normalize_categories(raw.get("categories") or raw.get("tags"))
    if raw.get("primary_category"):
        categories = normalize_categories([raw["primary_category"], *categories])

    aliases = record_aliases(
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=clean_openalex_id(raw.get("openalex_id")),
        pmid=clean_pmid(raw.get("pmid")),
        extras=[raw.get("id"), raw.get("entry_id"), pdf_url],
    )
    return {
        "source": "arxiv",
        "title": title,
        "authors": normalize_authors(raw.get("authors")),
        "year": year,
        "abstract": clean_text(raw.get("abstract") or raw.get("summary")),
        "arxiv_id": arxiv_id,
        "doi": doi,
        "openalex_id": clean_openalex_id(raw.get("openalex_id")),
        "pmid": clean_pmid(raw.get("pmid")),
        "pdf_url": pdf_url or None,
        "categories": categories,
        "citation_count": parse_int(raw.get("citation_count")),
        "search_source": "arxiv",
        "aliases": aliases,
        "neighbor_tokens": extract_neighbor_tokens(
            raw,
            ("reference_ids", "references", "citation_ids", "citations"),
        ),
    }


def normalize_openalex_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    ids = raw.get("ids") if isinstance(raw.get("ids"), dict) else {}
    openalex_id = clean_openalex_id(raw.get("id") or ids.get("openalex"))
    doi = clean_doi(raw.get("doi") or ids.get("doi"))
    pmid = clean_pmid(ids.get("pmid") or raw.get("pmid"))
    arxiv_id = extract_arxiv_id(ids.get("arxiv") or raw.get("arxiv_id"))
    title = clean_text(raw.get("title") or raw.get("display_name"))
    if not title:
        return None

    pdf_url = None
    for location_key in ("best_oa_location", "primary_location"):
        location = raw.get(location_key)
        if isinstance(location, dict):
            pdf_url = clean_text(location.get("pdf_url"))
            if pdf_url:
                break
    if not pdf_url and arxiv_id:
        pdf_url = arxiv_pdf_url(arxiv_id)

    categories = normalize_categories(raw.get("concepts"))
    primary_topic = raw.get("primary_topic")
    if isinstance(primary_topic, dict):
        categories.extend(
            normalize_categories(
                [
                    primary_topic.get("display_name"),
                    primary_topic.get("subfield", {}),
                    primary_topic.get("field", {}),
                    primary_topic.get("domain", {}),
                ]
            )
        )

    aliases = record_aliases(
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=openalex_id,
        pmid=pmid,
        extras=[raw.get("id"), ids.get("openalex"), ids.get("doi"), ids.get("pmid")],
    )
    return {
        "source": "openalex",
        "title": title,
        "authors": normalize_authors(raw.get("authorships") or raw.get("authors")),
        "year": parse_year(raw.get("publication_year") or raw.get("year")),
        "abstract": clean_text(raw.get("abstract"))
        or reconstruct_abstract(raw.get("abstract_inverted_index")),
        "arxiv_id": arxiv_id,
        "doi": doi,
        "openalex_id": openalex_id,
        "pmid": pmid,
        "pdf_url": pdf_url or None,
        "categories": normalize_categories(categories),
        "citation_count": parse_int(raw.get("cited_by_count") or raw.get("citation_count")),
        "search_source": "openalex",
        "aliases": aliases,
        "neighbor_tokens": extract_neighbor_tokens(
            raw,
            ("referenced_works", "related_works", "citations", "references"),
        ),
    }


def normalize_web_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = clean_text(raw.get("title"))
    if not title:
        return None
    arxiv_id = extract_arxiv_id(raw.get("arxiv_id") or raw.get("url") or raw.get("id"))
    doi = clean_doi(raw.get("doi") or raw.get("url"))
    openalex_id = clean_openalex_id(raw.get("openalex_id") or raw.get("url"))
    pmid = clean_pmid(raw.get("pmid"))
    aliases = record_aliases(
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=openalex_id,
        pmid=pmid,
        extras=[raw.get("url"), raw.get("id")],
    )
    return {
        "source": "web",
        "title": title,
        "authors": normalize_authors(raw.get("authors")),
        "year": parse_year(raw.get("year") or raw.get("published")),
        "abstract": clean_text(raw.get("abstract") or raw.get("snippet")),
        "arxiv_id": arxiv_id,
        "doi": doi,
        "openalex_id": openalex_id,
        "pmid": pmid,
        "pdf_url": clean_text(raw.get("pdf_url")) or arxiv_pdf_url(arxiv_id),
        "categories": normalize_categories(raw.get("categories") or raw.get("tags")),
        "citation_count": parse_int(raw.get("citation_count")),
        "search_source": "web",
        "aliases": aliases,
        "neighbor_tokens": extract_neighbor_tokens(raw, ("references", "citations")),
    }


def normalize_pubmed_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = clean_text(raw.get("title"))
    if not title:
        return None
    doi = clean_doi(raw.get("doi"))
    pmid = clean_pmid(raw.get("pmid") or raw.get("uid"))
    openalex_id = clean_openalex_id(raw.get("openalex_id"))
    arxiv_id = extract_arxiv_id(raw.get("arxiv_id"))
    aliases = record_aliases(
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=openalex_id,
        pmid=pmid,
        extras=[raw.get("id"), raw.get("uid")],
    )
    return {
        "source": "pubmed",
        "title": title,
        "authors": normalize_authors(raw.get("authors")),
        "year": parse_year(raw.get("year") or raw.get("pubdate")),
        "abstract": clean_text(raw.get("abstract")),
        "arxiv_id": arxiv_id,
        "doi": doi,
        "openalex_id": openalex_id,
        "pmid": pmid,
        "pdf_url": clean_text(raw.get("pdf_url")) or None,
        "categories": normalize_categories(raw.get("mesh_terms") or raw.get("categories")),
        "citation_count": parse_int(raw.get("citation_count")),
        "search_source": "pubmed",
        "aliases": aliases,
        "neighbor_tokens": extract_neighbor_tokens(raw, ("references", "citations")),
    }


def normalize_source_records(paths: list[str], source: str) -> list[dict[str, Any]]:
    normalizer_map = {
        "zotero": normalize_zotero_record,
        "arxiv": normalize_arxiv_record,
        "openalex": normalize_openalex_record,
        "web": normalize_web_record,
        "pubmed": normalize_pubmed_record,
    }
    normalizer = normalizer_map[source]
    normalized: list[dict[str, Any]] = []
    for path in paths:
        for raw in load_json_records(path):
            record = normalizer(raw)
            if record:
                normalized.append(record)
    return normalized


def richness(record: dict[str, Any]) -> tuple[int, int]:
    filled = 0
    for key in ("title", "abstract", "arxiv_id", "doi", "openalex_id", "pmid", "pdf_url", "year", "cite_key"):
        value = record.get(key)
        if value not in (None, "", []):
            filled += 1
    filled += len(record.get("authors", []))
    filled += len(record.get("categories", []))
    citation_count = record.get("citation_count")
    if isinstance(citation_count, int):
        filled += 1
    return filled, SOURCE_PRIORITY.get(record.get("search_source", ""), 0)


def choose_scalar(values: list[Any]) -> Any:
    candidates = [value for value in values if value not in (None, "", [])]
    if not candidates:
        return None
    return candidates[0]


def merge_record_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_group = sorted(group, key=richness, reverse=True)

    title = choose_scalar([record.get("title") for record in ordered_group]) or ""
    authors = choose_scalar([record.get("authors") for record in ordered_group]) or []
    year = choose_scalar([record.get("year") for record in ordered_group])
    abstract = choose_scalar([record.get("abstract") for record in ordered_group]) or ""
    arxiv_id = choose_scalar([record.get("arxiv_id") for record in ordered_group])
    doi = choose_scalar([record.get("doi") for record in ordered_group])
    openalex_id = choose_scalar([record.get("openalex_id") for record in ordered_group])
    pmid = choose_scalar([record.get("pmid") for record in ordered_group])
    pdf_url = choose_scalar([record.get("pdf_url") for record in ordered_group])
    citation_count = choose_scalar([record.get("citation_count") for record in ordered_group])
    cite_key = choose_scalar([record.get("cite_key") for record in ordered_group])

    categories: list[str] = []
    seen_categories: set[str] = set()
    for record in ordered_group:
        for category in record.get("categories", []):
            if category not in seen_categories:
                categories.append(category)
                seen_categories.add(category)

    aliases: set[str] = set()
    neighbor_tokens: set[str] = set()
    source_candidates = set()
    for record in ordered_group:
        aliases.update(record.get("aliases", set()))
        neighbor_tokens.update(record.get("neighbor_tokens", set()))
        source_candidates.add(record.get("search_source"))

    if arxiv_id and not pdf_url:
        pdf_url = arxiv_pdf_url(arxiv_id)

    search_source = max(
        source_candidates,
        key=lambda source: SOURCE_PRIORITY.get(source or "", 0),
        default="web",
    )

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "arxiv_id": arxiv_id,
        "doi": doi,
        "openalex_id": openalex_id,
        "pmid": pmid,
        "pdf_url": pdf_url,
        "categories": categories,
        "citation_count": citation_count,
        "search_source": search_source,
        "cite_key": cite_key,
        "aliases": aliases,
        "neighbor_tokens": neighbor_tokens,
    }


def deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    owners: dict[tuple[str, str], int] = {}
    for index, record in enumerate(records):
        for key in ("arxiv_id", "doi", "openalex_id", "pmid"):
            value = record.get(key)
            if not value:
                continue
            owner = owners.get((key, value))
            if owner is None:
                owners[(key, value)] = index
            else:
                union(owner, index)

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, record in enumerate(records):
        groups[find(index)].append(record)
    return [merge_record_group(group) for group in groups.values()]

def keyword_score(entry: dict[str, Any], keywords: list[str]) -> float:
    if not keywords:
        return 0.5
    haystack = normalize_title(f"{entry.get('title', '')} {entry.get('abstract', '')}")
    hits = sum(1 for keyword in keywords if normalize_title(keyword) in haystack)
    return hits / len(keywords)


def citation_score(citation_count: int | None) -> float:
    if citation_count is None or citation_count <= 0:
        return 0.0
    return min(1.0, math.log10(citation_count + 1) / 4.0)


def recency_score(year: int | None, date_start: int, date_end: int) -> float:
    if year is None:
        return 0.25
    lower = min(date_start, date_end)
    upper = max(date_start, date_end)
    clipped = max(lower, min(upper, year))
    return (clipped - lower) / max(1, upper - lower)


def seed_proximity_score(distance: int | None) -> float:
    if distance is None:
        return 0.0
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.7
    if distance == 2:
        return 0.4
    return 0.1


def seed_aliases(seed_value: str) -> set[str]:
    text = clean_text(seed_value)
    if not text:
        return set()
    aliases = normalize_identifier_aliases(text)
    arxiv_id = extract_arxiv_id(text)
    doi = clean_doi(text)
    openalex_id = clean_openalex_id(text)
    if arxiv_id:
        aliases.add(f"arxiv:{arxiv_id}")
    if doi:
        aliases.add(f"doi:{doi}")
    if openalex_id:
        aliases.add(f"openalex:{openalex_id}")
    return aliases


def compute_seed_distances(entries: list[dict[str, Any]], seeds: list[str]) -> dict[str, int | None]:
    alias_to_canonical: dict[str, str] = {}
    for entry in entries:
        alias_to_canonical[entry["canonical_id"]] = entry["canonical_id"]
        for alias in entry.get("aliases", set()):
            alias_to_canonical[alias] = entry["canonical_id"]
        if entry.get("cite_key"):
            alias_to_canonical[entry["cite_key"]] = entry["canonical_id"]

    graph: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        source_id = entry["canonical_id"]
        for token in entry.get("neighbor_tokens", set()):
            target_id = alias_to_canonical.get(token)
            if not target_id or target_id == source_id:
                continue
            graph[source_id].add(target_id)
            graph[target_id].add(source_id)

    queue: deque[tuple[str, int]] = deque()
    distances: dict[str, int] = {}
    for seed in seeds:
        for alias in seed_aliases(seed):
            canonical_id = alias_to_canonical.get(alias)
            if canonical_id and canonical_id not in distances:
                distances[canonical_id] = 0
                queue.append((canonical_id, 0))

    while queue:
        current, distance = queue.popleft()
        for neighbor in graph.get(current, set()):
            if neighbor not in distances:
                distances[neighbor] = distance + 1
                queue.append((neighbor, distance + 1))

    result: dict[str, int | None] = {}
    for entry in entries:
        result[entry["canonical_id"]] = distances.get(entry["canonical_id"])
    return result


def compute_relevance(
    entry: dict[str, Any],
    keywords: list[str],
    distance: int | None,
    date_start: int,
    date_end: int,
) -> float:
    keyword_component = keyword_score(entry, keywords)
    seed_component = seed_proximity_score(distance)
    recency_component = recency_score(entry.get("year"), date_start, date_end)
    citation_component = citation_score(entry.get("citation_count"))
    score = (
        0.3 * keyword_component
        + 0.3 * seed_component
        + 0.2 * recency_component
        + 0.2 * citation_component
    )
    return round(score, 4)


def sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda entry: (
            -entry["relevance_score"],
            entry["seed_distance"] if entry["seed_distance"] is not None else 99,
            -(entry["citation_count"] if entry["citation_count"] is not None else -1),
            -(entry["year"] if entry["year"] is not None else 0),
            entry["canonical_id"],
        ),
    )


def build_entries(args: argparse.Namespace) -> list[dict[str, Any]]:
    ensure_input_paths(args)

    raw_records: list[dict[str, Any]] = []
    raw_records.extend(normalize_source_records(args.zotero_results, "zotero"))
    raw_records.extend(normalize_source_records(args.arxiv_results, "arxiv"))
    raw_records.extend(normalize_source_records(args.openalex_results, "openalex"))
    raw_records.extend(normalize_source_records(args.web_results, "web"))
    raw_records.extend(normalize_source_records(args.pubmed_results, "pubmed"))

    deduped = deduplicate_records(raw_records)
    for entry in deduped:
        entry["canonical_id"] = canonical_id_for_entry(entry)

    assign_cite_keys(deduped)

    seeds = [value.strip() for value in args.seed_papers.split(",") if value.strip()]
    keywords = [value.strip() for value in args.keywords.split(",") if value.strip()]
    seed_distances = compute_seed_distances(deduped, seeds)

    manifest_entries: list[dict[str, Any]] = []
    for entry in deduped:
        canonical_id = entry["canonical_id"]
        seed_distance = seed_distances.get(canonical_id)
        manifest_entry = {
            "canonical_id": canonical_id,
            "cite_key": entry["cite_key"],
            "arxiv_id": entry.get("arxiv_id"),
            "openalex_id": entry.get("openalex_id"),
            "doi": entry.get("doi"),
            "pmid": entry.get("pmid"),
            "title": entry.get("title", ""),
            "authors": entry.get("authors", []),
            "year": entry.get("year"),
            "abstract": entry.get("abstract", ""),
            "pdf_url": entry.get("pdf_url"),
            "categories": entry.get("categories", []),
            "relevance_score": compute_relevance(
                entry,
                keywords=keywords,
                distance=seed_distance,
                date_start=args.date_start,
                date_end=args.date_end,
            ),
            "seed_distance": seed_distance,
            "citation_count": entry.get("citation_count"),
            "search_source": entry.get("search_source", "web"),
        }
        manifest_entries.append(manifest_entry)

    # Post-BFS check: warn for any seed identifier that could not be resolved
    alias_map: dict[str, str] = {}
    for _entry in deduped:
        alias_map[_entry["canonical_id"]] = _entry["canonical_id"]
        for _alias in _entry.get("aliases", set()):
            alias_map[_alias] = _entry["canonical_id"]
        if _entry.get("cite_key"):
            alias_map[_entry["cite_key"]] = _entry["canonical_id"]
    for _seed in seeds:
        if not any(alias_map.get(_alias) for _alias in seed_aliases(_seed)):
            print(
                f'WARNING: seed identifier "{_seed}" could not be resolved to any record.'
                f' If this seed is stored in Zotero by DOI only, pass the DOI as the seed instead:'
                f' --seed-papers "doi:<doi>"',
                file=sys.stderr,
            )

    return sort_entries(manifest_entries)[: args.max_papers]


def detect_search_sources(args: argparse.Namespace) -> list[str]:
    present = []
    for source in SEARCH_SOURCE_ORDER:
        paths = getattr(args, f"{source}_results")
        if paths:
            present.append(source)
    return present


def main() -> int:
    args = parse_args()
    try:
        entries = build_entries(args)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    manifest = {
        "schema_version": "1",
        "topic": args.topic,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "search_sources": detect_search_sources(args),
        "entries": entries,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Manifest written: {output_path}")
    print(f"Entries: {len(entries)}")
    if entries:
        top_entry = entries[0]
        print(
            "Top entry: "
            f"{top_entry['title']} [{top_entry['canonical_id']}] "
            f"score={top_entry['relevance_score']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
