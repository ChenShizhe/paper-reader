"""Shared paper identity and cite-key helpers for literature-review skills."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "toward",
    "towards",
    "with",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_doi(value: Any) -> str | None:
    if not value:
        return None
    text = clean_text(value).lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    text = text.strip()
    return text if re.fullmatch(r"10\.\d{4,9}/\S+", text) else None


def clean_openalex_id(value: Any) -> str | None:
    if not value:
        return None
    text = clean_text(value)
    match = re.search(r"(W\d+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def clean_pmid(value: Any) -> str | None:
    if not value:
        return None
    text = clean_text(value)
    text = re.sub(r"^(pmid|pubmed)\s*:\s*", "", text, flags=re.IGNORECASE)
    if not (re.fullmatch(r"\d{5,12}", text) or "pmid" in text.lower() or "pubmed" in text.lower()):
        return None
    match = re.search(r"(\d{5,12})", text)
    return match.group(1) if match else None


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str) and clean_text(value):
        return [clean_text(value)]
    return []


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^A-Za-z0-9]+", "", ascii_text).lower()
    return ascii_text or "unknown"


def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title or "").lower()
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def canonical_id_for_entry(entry: dict[str, Any]) -> str:
    arxiv_id = clean_text(entry.get("arxiv_id"))
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    doi = clean_doi(entry.get("doi"))
    if doi:
        return f"doi:{doi}"
    openalex_id = clean_openalex_id(entry.get("openalex_id"))
    if openalex_id:
        return f"openalex:{openalex_id}"
    normalized = normalize_title(clean_text(entry.get("title")))
    year = clean_text(entry.get("year")) or "0"
    digest = hashlib.sha256(f"{normalized}{year}".encode("utf-8")).hexdigest()[:8]
    return f"manual:{digest}"


def first_author_surname(authors: list[str]) -> str:
    normalized_authors = normalize_string_list(authors)
    if not normalized_authors:
        return "unknown"
    first = clean_text(normalized_authors[0])
    if "," in first:
        surname = first.split(",", 1)[0]
    else:
        surname = first.split()[-1]
    return slugify(surname)


def first_title_content_word(title: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", title or "").lower())
    for token in tokens:
        if token not in STOPWORDS:
            return slugify(token)
    return "paper"


def title_content_signature(title: str, *, max_words: int = 3) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", title or "").lower())
    selected: list[str] = []
    for token in tokens:
        if token in STOPWORDS:
            continue
        cleaned = slugify(token)
        if not cleaned or cleaned == "unknown":
            continue
        selected.append(cleaned)
        if len(selected) >= max_words:
            break
    if not selected:
        return "paper"
    return "".join(selected)


def collision_suffix(canonical_id: str) -> str:
    if canonical_id.startswith("arxiv:"):
        fragment = re.sub(r"[^0-9]", "", canonical_id.split(":", 1)[1])[:4] or "0000"
        return f"a{fragment}"
    if canonical_id.startswith("doi:"):
        doi = canonical_id.split(":", 1)[1]
        pieces = [slugify(piece) for piece in doi.split("/") if piece]
        fragment = (pieces[1][-4:] if len(pieces) > 1 else pieces[0][-4:]) if pieces else "0000"
        return f"d{fragment or '0000'}"
    if canonical_id.startswith("openalex:"):
        fragment = re.sub(r"[^A-Za-z0-9]", "", canonical_id.split(":", 1)[1])[-4:] or "0000"
        return f"o{fragment.lower()}"
    if canonical_id.startswith("manual:"):
        return f"m{canonical_id.split(':', 1)[1][:4].lower()}"
    return f"x{hashlib.sha1(canonical_id.encode('utf-8')).hexdigest()[:4]}"


def base_cite_key(entry: dict[str, Any]) -> str:
    author_part = first_author_surname(normalize_string_list(entry.get("authors")))
    year = clean_text(entry.get("year"))
    year_part = year if year and re.fullmatch(r"\d{4}", year) else ""
    title_part = title_content_signature(clean_text(entry.get("title")))
    return f"{author_part}{year_part}{title_part}"


def assign_cite_keys(entries: list[dict[str, Any]]) -> None:
    seen_keys: set[str] = set()
    pending: dict[str, list[dict[str, Any]]] = {}

    for entry in entries:
        entry["canonical_id"] = canonical_id_for_entry(entry)
        explicit = clean_text(entry.get("cite_key"))
        if explicit:
            if explicit in seen_keys:
                raise ValueError(f"Duplicate cite_key in input metadata: {explicit}")
            entry["cite_key"] = explicit
            seen_keys.add(explicit)
            continue
        pending.setdefault(base_cite_key(entry), []).append(entry)

    for base, bucket in pending.items():
        if len(bucket) == 1 and base not in seen_keys:
            bucket[0]["cite_key"] = base
            seen_keys.add(base)
            continue

        for entry in sorted(bucket, key=lambda item: item["canonical_id"]):
            candidate = f"{base}_{collision_suffix(entry['canonical_id'])}"
            if candidate in seen_keys:
                raise ValueError(f"Unable to assign unique cite_key for {entry['canonical_id']}")
            entry["cite_key"] = candidate
            seen_keys.add(candidate)


__all__ = [
    "STOPWORDS",
    "assign_cite_keys",
    "base_cite_key",
    "canonical_id_for_entry",
    "clean_doi",
    "clean_openalex_id",
    "clean_pmid",
    "clean_text",
    "collision_suffix",
    "first_author_surname",
    "first_title_content_word",
    "normalize_string_list",
    "normalize_title",
    "slugify",
    "title_content_signature",
]
