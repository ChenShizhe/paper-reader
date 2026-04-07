#!/usr/bin/env python3
"""Search Zotero via the installed zotero-mcp package and emit normalized JSON."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Zotero library via zotero-mcp")
    parser.add_argument(
        "--query",
        action="append",
        required=True,
        help="Search query text; may be passed multiple times",
    )
    parser.add_argument("--max-results", type=int, default=15, help="Max results per query")
    parser.add_argument(
        "--zotero-mcp-root",
        default=os.environ.get("ZOTERO_MCP_ROOT", str(Path.home() / "Documents" / "MCPs" / "zotero-mcp")),
        help="Path to zotero-mcp repository root",
    )
    parser.add_argument(
        "--mode",
        choices=("keyword", "semantic", "auto"),
        default="auto",
        help="Search mode. auto tries semantic then falls back to keyword search.",
    )
    parser.add_argument("--output", default="zotero_results.json", help="Output JSON path")
    parser.add_argument(
        "--log-output",
        default="",
        help="Optional JSON log path for request metadata",
    )
    return parser.parse_args()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_year(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(19|20)\d{2}", text)
    if not match:
        return None
    return int(match.group(0))


def extract_arxiv_id(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", text)
    if match:
        return match.group(1)
    match = re.search(r"arxiv[:/ ]([A-Za-z\-\.]+/\d{7})(v\d+)?", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def extract_pmid(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"\b(\d{5,12})\b", text)
    if not match:
        return None
    if "pmid" in text.lower() or "pubmed" in text.lower():
        return match.group(1)
    return None


def normalize_cite_key(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    normalized = re.sub(r"[^A-Za-z0-9_]+", "", text).lower()
    return normalized or None


def extract_cite_key(extra: Any) -> str | None:
    text = clean_text(extra)
    if not text:
        return None
    for line in text.splitlines():
        lower = line.lower()
        if "citation key" in lower or "citekey" in lower:
            _, _, tail = line.partition(":")
            return normalize_cite_key(tail or line)
    return None


def normalize_creators(creators: Any) -> list[str]:
    authors: list[str] = []
    if not isinstance(creators, list):
        return authors

    for creator in creators:
        if not isinstance(creator, dict):
            continue
        if creator.get("creatorType") not in (None, "author"):
            continue
        name = clean_text(creator.get("name"))
        if not name:
            first = clean_text(creator.get("firstName"))
            last = clean_text(creator.get("lastName"))
            name = clean_text(f"{first} {last}")
        if name and name not in authors:
            authors.append(name)
    return authors


def normalize_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    values: list[str] = []
    for tag in tags:
        if isinstance(tag, dict):
            label = clean_text(tag.get("tag"))
        else:
            label = clean_text(tag)
        if label and label not in values:
            values.append(label)
    return values


def normalize_doi(value: Any) -> str | None:
    text = clean_text(value).lower()
    if not text:
        return None
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text if re.fullmatch(r"10\.\d{4,9}/\S+", text) else None


def _load_standalone_client_env() -> dict[str, str]:
    config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    client_env = payload.get("client_env")
    if not isinstance(client_env, dict):
        return {}
    return {str(key): str(value) for key, value in client_env.items()}


def _with_zotero_env_defaults(env: dict[str, str]) -> dict[str, str]:
    updated = _load_standalone_client_env()
    updated.update(env)
    has_remote = bool(updated.get("ZOTERO_LIBRARY_ID") and updated.get("ZOTERO_API_KEY"))
    if not updated.get("ZOTERO_LOCAL") and not has_remote:
        updated["ZOTERO_LOCAL"] = "true"
        updated.setdefault("ZOTERO_LIBRARY_ID", "0")
    return updated


def _dedup_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.get("item_key") or f"title:{clean_text(record.get('title')).lower()}:{record.get('year') or ''}"
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = record
            continue

        # Keep the record with richer metadata and merge per-field gaps.
        score_existing = sum(1 for field in ("doi", "arxiv_id", "pmid", "abstract", "pdf_url", "cite_key") if existing.get(field))
        score_new = sum(1 for field in ("doi", "arxiv_id", "pmid", "abstract", "pdf_url", "cite_key") if record.get(field))
        winner = record if score_new > score_existing else existing
        loser = existing if winner is record else record
        for field in ("authors", "categories"):
            merged = list(dict.fromkeys((winner.get(field) or []) + (loser.get(field) or [])))
            winner[field] = merged
        for field in ("doi", "arxiv_id", "pmid", "abstract", "pdf_url", "cite_key", "zotero_url"):
            if not winner.get(field) and loser.get(field):
                winner[field] = loser[field]
        deduped[key] = winner

    return list(deduped.values())


def _load_zotero_mcp(zotero_mcp_root: Path) -> dict[str, Any]:
    src_root = zotero_mcp_root / "src"
    if not src_root.exists():
        raise FileNotFoundError(f"zotero-mcp src path not found: {src_root}")
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    try:
        from zotero_mcp.client import get_zotero_client  # type: ignore
    except Exception:
        venv_python = zotero_mcp_root / ".venv" / "bin" / "python"
        if not venv_python.exists():
            raise RuntimeError(
                "Unable to import zotero_mcp.client and bundled venv python is missing at "
                f"{venv_python}"
            )
        return {
            "mode": "subprocess",
            "venv_python": str(venv_python),
            "src_root": str(src_root),
        }

    return {
        "mode": "python",
        "get_zotero_client": get_zotero_client,
        "src_root": str(src_root),
    }


def _keyword_search(zotero_handle: dict[str, Any], query: str, max_results: int) -> list[dict[str, Any]]:
    if zotero_handle["mode"] == "python":
        os.environ.update(_with_zotero_env_defaults(dict(os.environ)))
        zot = zotero_handle["get_zotero_client"]()
        items = zot.items(q=query, limit=max_results)
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    env = _with_zotero_env_defaults(dict(os.environ))
    env["PYTHONPATH"] = zotero_handle["src_root"] + (
        ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    inline = (
        "import json,sys;"
        "from zotero_mcp.client import get_zotero_client;"
        "q=sys.argv[1];limit=int(sys.argv[2]);"
        "z=get_zotero_client();"
        "rows=z.items(q=q, limit=limit);"
        "print(json.dumps(rows))"
    )
    result = subprocess.run(
        [zotero_handle["venv_python"], "-c", inline, query, str(max_results)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _semantic_search(zotero_mcp_root: Path, query: str, max_results: int) -> list[dict[str, Any]]:
    src_root = zotero_mcp_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    from zotero_mcp.semantic_search import create_semantic_search  # type: ignore

    config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"
    search = create_semantic_search(str(config_path))
    result = search.search(query=query, limit=max_results, filters=None) or {}
    rows: list[dict[str, Any]] = []
    for hit in result.get("results", []):
        if not isinstance(hit, dict):
            continue
        zotero_item = hit.get("zotero_item")
        if isinstance(zotero_item, dict):
            row = dict(zotero_item)
            row["_semantic_similarity"] = hit.get("similarity")
            rows.append(row)
    return rows


def _to_record(raw_item: dict[str, Any], query: str, mode: str) -> dict[str, Any] | None:
    data = raw_item.get("data") if isinstance(raw_item.get("data"), dict) else raw_item

    title = clean_text(data.get("title"))
    if not title:
        return None

    item_key = clean_text(data.get("key") or raw_item.get("item_key") or raw_item.get("id"))
    extra = data.get("extra")
    record = {
        "id": item_key or clean_text(raw_item.get("id")),
        "item_key": item_key or None,
        "title": title,
        "authors": normalize_creators(data.get("creators") or raw_item.get("authors")),
        "year": parse_year(data.get("date") or raw_item.get("year")),
        "abstract": clean_text(data.get("abstractNote") or raw_item.get("abstract")),
        "doi": normalize_doi(data.get("DOI") or raw_item.get("doi")),
        "arxiv_id": extract_arxiv_id(data.get("archiveLocation") or raw_item.get("arxiv_id") or data.get("url")),
        "pmid": extract_pmid(data.get("extra") or raw_item.get("pmid")),
        "pdf_url": clean_text(raw_item.get("pdf_url")) or None,
        "categories": normalize_tags(data.get("tags") or raw_item.get("categories")),
        "zotero_url": clean_text(raw_item.get("zotero_url")) or (f"zotero://select/items/{item_key}" if item_key else ""),
        "cite_key": normalize_cite_key(raw_item.get("cite_key") or raw_item.get("citation_key") or extract_cite_key(extra)),
        "collection_keys": data.get("collections") if isinstance(data.get("collections"), list) else [],
        "source_query": query,
        "search_mode": mode,
        "search_source": "zotero",
        "similarity": raw_item.get("_semantic_similarity"),
    }

    if record["pdf_url"] in ("", "null"):
        record["pdf_url"] = None
    if not record["zotero_url"]:
        record["zotero_url"] = None
    return record


def query_zotero(
    *,
    queries: list[str],
    max_results: int,
    zotero_mcp_root: Path,
    mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    zotero_handle = _load_zotero_mcp(zotero_mcp_root)

    all_records: list[dict[str, Any]] = []
    request_log: list[dict[str, Any]] = []

    for query in queries:
        effective_mode = mode
        raw_items: list[dict[str, Any]] = []
        error: str | None = None

        if mode in ("semantic", "auto"):
            try:
                raw_items = _semantic_search(zotero_mcp_root, query, max_results)
                effective_mode = "semantic"
            except Exception as exc:  # pragma: no cover - depends on local semantic db
                error = f"semantic-search-failed: {exc}"
                raw_items = []

        if not raw_items and mode in ("keyword", "auto"):
            raw_items = _keyword_search(zotero_handle, query, max_results)
            effective_mode = "keyword"

        normalized = [record for item in raw_items if (record := _to_record(item, query, effective_mode))]
        all_records.extend(normalized)

        request_log.append(
            {
                "query": query,
                "mode": effective_mode,
                "raw_item_count": len(raw_items),
                "entry_count": len(normalized),
                "error": error,
            }
        )

    deduped = _dedup_records(all_records)
    run_log = {
        "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query_count": len(queries),
        "max_results": max_results,
        "mode": mode,
        "entry_count": len(deduped),
        "requests": request_log,
    }
    return deduped, run_log


def main() -> int:
    args = parse_args()
    if args.max_results <= 0:
        print("ERROR: --max-results must be positive")
        return 1

    try:
        records, run_log = query_zotero(
            queries=args.query,
            max_results=args.max_results,
            zotero_mcp_root=Path(args.zotero_mcp_root).expanduser(),
            mode=args.mode,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.log_output:
        log_path = Path(args.log_output)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(run_log, indent=2) + "\n", encoding="utf-8")

    print(f"Saved Zotero results: {output_path}")
    print(f"Queries: {len(args.query)}")
    print(f"Entries: {len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
