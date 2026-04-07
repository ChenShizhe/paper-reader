#!/usr/bin/env python3
"""Create/update Zotero items from note frontmatter and sync cite keys."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import fastmcp  # noqa: F401
except ImportError:
    print("sync_zotero: fastmcp not available, skipping Zotero sync", file=sys.stderr)
    sys.exit(0)


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create/update Zotero items from paper notes")
    parser.add_argument("note_path", help="Path to papers/<cite_key>.md")
    parser.add_argument(
        "--zotero-mcp-root",
        default=os.environ.get("ZOTERO_MCP_ROOT", str(Path.home() / "Documents" / "MCPs" / "zotero-mcp")),
        help="Path to zotero-mcp repository root",
    )
    parser.add_argument("--collection-key", action="append", default=[], help="Optional Zotero collection key")
    parser.add_argument("--dry-run", action="store_true", help="Do not create/update remote items")
    parser.add_argument("--output", default="", help="Optional output JSON path")
    return parser.parse_args()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_doi(value: Any) -> str | None:
    text = clean_text(value).lower()
    if not text:
        return None
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text if re.fullmatch(r"10\.\d{4,9}/\S+", text) else None


def normalize_cite_key(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    normalized = re.sub(r"[^A-Za-z0-9_]+", "", text).lower()
    return normalized or None


def normalize_title(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value).lower())


def parse_frontmatter(note_text: str) -> dict[str, Any]:
    match = FRONTMATTER_RE.match(note_text)
    if not match:
        raise ValueError("Note is missing YAML frontmatter")
    block = match.group(1)
    payload: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") and current_list_key:
            payload.setdefault(current_list_key, [])
            if isinstance(payload[current_list_key], list):
                payload[current_list_key].append(_yaml_scalar(line[4:]))
            continue
        if ":" not in line or line.startswith(" "):
            current_list_key = None
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value == "":
            payload[key] = []
            current_list_key = key
            continue
        payload[key] = _yaml_scalar(raw_value)
        current_list_key = None

    return payload


def _yaml_scalar(raw: str) -> Any:
    token = raw.strip()
    if token in {"null", "~"}:
        return None
    if token == "[]":
        return []
    if token in {"true", "false"}:
        return token == "true"
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        return token[1:-1]
    if re.fullmatch(r"-?\d+", token):
        try:
            return int(token)
        except ValueError:
            return token
    return token


def extract_abstract(note_text: str) -> str | None:
    if "## Abstract" not in note_text:
        return None
    _, _, tail = note_text.partition("## Abstract")
    lines = tail.splitlines()
    content: list[str] = []
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if line.strip():
            content.append(line.strip())
    abstract = clean_text(" ".join(content))
    return abstract or None


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
        print("WARNING: ZOTERO_API_KEY not set; using local-only Zotero mode.", file=sys.stderr)
        updated["ZOTERO_LOCAL"] = "true"
        updated.setdefault("ZOTERO_LIBRARY_ID", "0")
    return updated


def load_zotero_client(zotero_mcp_root: Path) -> Any:
    src_root = zotero_mcp_root / "src"
    if not src_root.exists():
        raise FileNotFoundError(f"zotero-mcp src path not found: {src_root}")
    if str(src_root) not in os.sys.path:
        os.sys.path.insert(0, str(src_root))
    from zotero_mcp.client import get_zotero_client  # type: ignore

    os.environ.update(_with_zotero_env_defaults(dict(os.environ)))
    return get_zotero_client()


def _creator_from_author(author: str) -> dict[str, str]:
    text = clean_text(author)
    if not text:
        return {"creatorType": "author", "name": "Unknown"}
    if "," in text:
        last, first = [clean_text(part) for part in text.split(",", 1)]
        if first and last:
            return {"creatorType": "author", "firstName": first, "lastName": last}
    parts = text.split()
    if len(parts) == 1:
        return {"creatorType": "author", "name": text}
    return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}


def _extract_citation_key(extra: Any) -> str | None:
    text = clean_text(extra)
    if not text:
        return None
    for line in text.splitlines():
        if "citation key" in line.lower() or "citekey" in line.lower():
            _, _, tail = line.partition(":")
            return normalize_cite_key(tail or line)
    return None


def _merge_extra_field(existing_extra: Any, payload_extra: Any) -> str:
    existing_text = clean_text(existing_extra)
    payload_text = clean_text(payload_extra)
    existing_key = _extract_citation_key(existing_text)

    if not existing_key:
        return payload_text

    merged_lines = [f"Citation Key: {existing_key}"]
    for line in payload_text.splitlines():
        lower = line.lower()
        if "citation key" in lower or "citekey" in lower:
            continue
        cleaned = clean_text(line)
        if cleaned:
            merged_lines.append(cleaned)
    return "\n".join(merged_lines)


def _build_item_payload(frontmatter: dict[str, Any], abstract: str | None, collection_keys: list[str]) -> dict[str, Any]:
    cite_key = clean_text(frontmatter.get("cite_key"))
    canonical_id = clean_text(frontmatter.get("canonical_id"))
    arxiv_id = clean_text(frontmatter.get("arxiv_id"))
    bank_path = clean_text(frontmatter.get("bank_path"))

    creators = []
    authors = frontmatter.get("authors")
    if isinstance(authors, list):
        creators = [_creator_from_author(str(author)) for author in authors if clean_text(author)]
    elif clean_text(authors):
        creators = [_creator_from_author(str(authors))]

    extra_lines = [f"Citation Key: {cite_key}"] if cite_key else []
    if canonical_id:
        extra_lines.append(f"Canonical ID: {canonical_id}")
    if bank_path:
        extra_lines.append(f"Paper Bank Path: {bank_path}")

    payload: dict[str, Any] = {
        "itemType": "journalArticle",
        "title": clean_text(frontmatter.get("title")),
        "creators": creators,
        "date": clean_text(frontmatter.get("year")),
        "DOI": normalize_doi(frontmatter.get("doi")) or "",
        "archive": "arXiv" if arxiv_id else "",
        "archiveLocation": arxiv_id or "",
        "extra": "\n".join(extra_lines),
        "abstractNote": abstract or "",
        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
    }
    if collection_keys:
        payload["collections"] = list(dict.fromkeys(collection_keys))
    return payload


def _iter_items_from_query(zot: Any, query: str) -> list[dict[str, Any]]:
    if not clean_text(query):
        return []
    rows = zot.items(q=query, limit=25)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _item_match_score(item: dict[str, Any], frontmatter: dict[str, Any]) -> int:
    data = item.get("data") if isinstance(item.get("data"), dict) else item
    score = 0

    note_doi = normalize_doi(frontmatter.get("doi"))
    item_doi = normalize_doi(data.get("DOI") or data.get("doi"))
    if note_doi and item_doi:
        if note_doi == item_doi:
            score += 100
        else:
            return 0

    note_arxiv = clean_text(frontmatter.get("arxiv_id"))
    item_arxiv = clean_text(data.get("archiveLocation") or data.get("arxiv_id"))
    if note_arxiv and item_arxiv:
        if note_arxiv == item_arxiv:
            score += 70
        else:
            return 0

    note_title = normalize_title(frontmatter.get("title"))
    item_title = normalize_title(data.get("title"))
    if note_title and item_title:
        if note_title == item_title:
            score += 30
        elif note_title in item_title or item_title in note_title:
            score += 10

    note_year = clean_text(frontmatter.get("year"))
    item_year = clean_text(data.get("date"))[:4]
    if note_year and item_year and note_year == item_year:
        score += 5

    return score


def _find_best_existing_item(zot: Any, frontmatter: dict[str, Any]) -> dict[str, Any] | None:
    queries = [
        normalize_doi(frontmatter.get("doi")) or "",
        clean_text(frontmatter.get("arxiv_id")),
        clean_text(frontmatter.get("title")),
    ]

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        for item in _iter_items_from_query(zot, query):
            data = item.get("data") if isinstance(item.get("data"), dict) else item
            item_key = clean_text(data.get("key") or item.get("key"))
            if item_key and item_key in seen:
                continue
            if item_key:
                seen.add(item_key)
            candidates.append(item)

    best_item: dict[str, Any] | None = None
    best_score = 0
    for candidate in candidates:
        score = _item_match_score(candidate, frontmatter)
        if score > best_score:
            best_item = candidate
            best_score = score
    return best_item


def _merge_payload(existing_item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    item = json.loads(json.dumps(existing_item))
    data = item.get("data") if isinstance(item.get("data"), dict) else item
    for field in ("title", "date", "DOI", "archive", "archiveLocation", "abstractNote", "url"):
        incoming = payload.get(field)
        if clean_text(incoming):
            data[field] = incoming
    if clean_text(payload.get("extra")):
        data["extra"] = _merge_extra_field(data.get("extra"), payload.get("extra"))
    if payload.get("creators"):
        data["creators"] = payload["creators"]
    if payload.get("collections"):
        merged = list(dict.fromkeys((data.get("collections") or []) + payload["collections"]))
        data["collections"] = merged
    if "data" in item and isinstance(item["data"], dict):
        item["data"] = data
    return item


def _extract_final_cite_key(item: dict[str, Any], fallback_key: str) -> str:
    data = item.get("data") if isinstance(item.get("data"), dict) else item
    explicit = normalize_cite_key(data.get("citationKey")) or _extract_citation_key(data.get("extra"))
    return explicit or fallback_key


def update_note_cite_key(note_text: str, new_cite_key: str) -> str:
    match = FRONTMATTER_RE.match(note_text)
    if not match:
        return note_text
    block = match.group(1)
    if re.search(r"^cite_key:\s*.+$", block, flags=re.MULTILINE):
        updated = re.sub(
            r"^cite_key:\s*.+$",
            f'cite_key: "{new_cite_key}"',
            block,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        updated = block + f'\ncite_key: "{new_cite_key}"'
    return f"---\n{updated}\n---\n{note_text[match.end():]}"


def sync_note_with_zotero(
    *,
    note_path: Path,
    zot: Any,
    collection_keys: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    note_text = note_path.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(note_text)
    abstract = extract_abstract(note_text)
    note_cite_key = normalize_cite_key(frontmatter.get("cite_key")) or ""
    payload = _build_item_payload(frontmatter, abstract, collection_keys)

    existing = _find_best_existing_item(zot, frontmatter)
    action = "create" if existing is None else "update"

    if existing is not None:
        merged = _merge_payload(existing, payload)
        if not dry_run:
            zot.update_item(merged)
        final_item = merged
    else:
        final_item = {"data": payload}
        if not dry_run:
            result = zot.create_items([payload])
            success = result.get("success") if isinstance(result, dict) else None
            if not isinstance(success, dict) or not success:
                raise RuntimeError(f"Zotero create_items did not return success payload: {result}")
            item_key = str(next(iter(success.keys())))
            final_item = zot.item(item_key)

    final_cite_key = _extract_final_cite_key(final_item, note_cite_key or normalize_cite_key(payload.get("title")) or "")
    note_updated = False
    if final_cite_key and note_cite_key and final_cite_key != note_cite_key:
        updated_note_text = update_note_cite_key(note_text, final_cite_key)
        note_path.write_text(updated_note_text, encoding="utf-8")
        note_updated = True

    data = final_item.get("data") if isinstance(final_item.get("data"), dict) else final_item
    item_key = clean_text(data.get("key") or final_item.get("key"))

    return {
        "note_path": str(note_path),
        "action": action,
        "dry_run": dry_run,
        "item_key": item_key or None,
        "old_cite_key": note_cite_key or None,
        "final_cite_key": final_cite_key or None,
        "note_updated": note_updated,
    }


def main() -> int:
    args = parse_args()
    note_path = Path(args.note_path).expanduser()
    try:
        zot = load_zotero_client(Path(args.zotero_mcp_root).expanduser())
        report = sync_note_with_zotero(
            note_path=note_path,
            zot=zot,
            collection_keys=list(args.collection_key),
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
