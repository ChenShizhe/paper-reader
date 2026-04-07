#!/usr/bin/env python3
"""[DEPRECATED - v2] Legacy v1 note renderer.

Retained for backward compatibility but is no longer invoked by the v2 orchestration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


AUTO_BEGIN = "<!-- AUTO-GENERATED:BEGIN -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"
UPDATE_BLOCK_RE = re.compile(
    r"\n?<!-- AUTO-GENERATED:UPDATE:[^>]+ -->\n.*?\n<!-- AUTO-GENERATED:UPDATE:END -->\n?",
    re.DOTALL,
)
AUTO_BLOCK_RE = re.compile(
    rf"{re.escape(AUTO_BEGIN)}\n.*?\n{re.escape(AUTO_END)}",
    re.DOTALL,
)
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)

SECTION_ORDER = [
    ("theorem", "Key Theorems / Results"),
    ("assumption", "Key Assumptions"),
    ("methodology", "Methodology / Key Techniques"),
    ("empirical", "Empirical Findings"),
    ("connection", "Connections To Other Papers"),
    ("availability", "Data & Code Availability"),
    ("limitation", "Limitations"),
]

CLAIM_TYPE_TO_SECTION = {
    "theorem": "theorem",
    "assumption": "assumption",
    "methodology": "methodology",
    "empirical": "empirical",
    "connection": "connection",
    "data-availability": "availability",
    "code-availability": "availability",
    "limitation": "limitation",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "[DEPRECATED - v2] Legacy v1 note renderer retained for backward compatibility; "
            "no longer invoked by the v2 orchestration."
        )
    )
    parser.add_argument("metadata_json", help="Path to JSON file containing note metadata fields")
    parser.add_argument("claims_json", help="Path to claims/<cite_key>.json")
    parser.add_argument("--existing-note", help="Optional path to an existing note to update safely")
    parser.add_argument("--output", "-o", help="Write rendered note to this path instead of stdout")
    parser.add_argument(
        "--update-date",
        default=date.today().isoformat(),
        help="Date used in AUTO-GENERATED:UPDATE markers (default: today)",
    )
    return parser.parse_args()


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def quote_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def yaml_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {quote_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                nested = yaml_lines(item, indent + 2)
                if isinstance(item, dict) and nested:
                    first = nested[0].lstrip()
                    lines.append(f"{prefix}- {first}")
                    lines.extend(nested[1:])
                else:
                    lines.append(f"{prefix}-")
                    lines.extend(nested)
            else:
                lines.append(f"{prefix}- {quote_scalar(item)}")
        return lines
    return [f"{prefix}{quote_scalar(value)}"]


def build_frontmatter(metadata: dict[str, Any], auto_block_hash: str, review_status: str) -> str:
    # Frontmatter is intentionally serialized as single-line scalars only.
    # Metadata values are normalized upstream with `clean_text`, so multiline
    # strings are flattened before serialization.
    ordered: dict[str, Any] = {
        "schema_version": metadata.get("schema_version", "1"),
        "canonical_id": metadata.get("canonical_id"),
        "cite_key": metadata.get("cite_key"),
        "arxiv_id": metadata.get("arxiv_id"),
        "doi": metadata.get("doi"),
        "openalex_id": metadata.get("openalex_id"),
        "title": metadata.get("title"),
        "authors": normalize_list(metadata.get("authors")),
        "year": metadata.get("year"),
        "tags": normalize_list(metadata.get("tags")),
        "date_read": metadata.get("date_read"),
        "last_read_at": metadata.get("last_read_at"),
        "source_type": metadata.get("source_type"),
        "source_path": metadata.get("source_path"),
        "bank_path": metadata.get("bank_path"),
        "source_parse_status": metadata.get("source_parse_status"),
        "bibliography_status": metadata.get("bibliography_status"),
        "content_status": metadata.get("content_status"),
        "extraction_confidence": metadata.get("extraction_confidence"),
        "validation_status": metadata.get("validation_status"),
        "review_status": review_status,
        "auto_block_hash": auto_block_hash,
        "dataset_links": normalize_list(metadata.get("dataset_links")),
        "code_links": normalize_list(metadata.get("code_links")),
        "supplementary_links": normalize_list(metadata.get("supplementary_links")),
    }
    lines = ["---"]
    for key, value in ordered.items():
        if isinstance(value, list):
            if value and all(not isinstance(item, (dict, list)) for item in value):
                if all(clean_text(item) for item in value):
                    lines.append(f"{key}:")
                    lines.extend(yaml_lines(value, 2))
                else:
                    lines.append(f"{key}: []")
            elif value:
                lines.append(f"{key}:")
                lines.extend(yaml_lines(value, 2))
            else:
                lines.append(f"{key}: []")
        elif value is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f"{key}: {quote_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def claim_bullet(claim: dict[str, Any]) -> str:
    anchor = claim.get("source_anchor") or {}
    locator = clean_text(anchor.get("locator")) or "not found"
    confidence = clean_text(anchor.get("confidence")) or "low"
    text = clean_text(claim.get("text")) or "not found"
    if claim.get("type") == "connection":
        linked_paper = clean_text(claim.get("linked_paper")) or "not found"
        linked_status = clean_text(claim.get("linked_paper_status"))
        if linked_status not in {"in-corpus", "out-of-corpus"}:
            raise ValueError("Connection claims must set linked_paper_status to in-corpus or out-of-corpus")
        if linked_status == "out-of-corpus":
            detail_parts = ["out-of-corpus"]
            linked_doi = clean_text(claim.get("linked_doi"))
            linked_canonical_id = clean_text(claim.get("linked_canonical_id"))
            if linked_doi:
                detail_parts.append(f"DOI: {linked_doi}")
            elif linked_canonical_id:
                detail_parts.append(f"canonical_id: {linked_canonical_id}")
            detail_text = "; ".join(detail_parts)
            text = f"[[{linked_paper}]] ({detail_text}): {text}"
        else:
            text = f"[[{linked_paper}]]: {text}"
    elif claim.get("type") == "data-availability":
        text = f"Data: {text}"
    elif claim.get("type") == "code-availability":
        text = f"Code: {text}"
    return f"- {text}. [Source: {locator}, confidence: {confidence}]"


def grouped_claims(claims_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in SECTION_ORDER}
    for claim in normalize_list(claims_payload.get("claims")):
        if not isinstance(claim, dict):
            continue
        section_key = CLAIM_TYPE_TO_SECTION.get(clean_text(claim.get("type")))
        if section_key:
            grouped[section_key].append(claim)
    return grouped


def render_machine_block(metadata: dict[str, Any], claims_payload: dict[str, Any]) -> str:
    grouped = grouped_claims(claims_payload)
    abstract = clean_text(metadata.get("abstract") or metadata.get("abstract_text")) or "not found"
    lines = [
        AUTO_BEGIN,
        "## Abstract",
        abstract,
        "",
    ]
    for section_key, heading in SECTION_ORDER:
        lines.append(f"## {heading}")
        claims = grouped.get(section_key) or []
        if claims:
            for claim in claims:
                lines.append(claim_bullet(claim))
        else:
            if section_key == "availability":
                lines.append("- Data: not found")
                lines.append("- Code: not found")
            else:
                lines.append("- not found")
        lines.append("")
    lines[-1] = AUTO_END
    return "\n".join(lines)


def compute_auto_block_hash(block: str) -> str:
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def frontmatter_value(note_text: str, key: str) -> str | None:
    match = FRONTMATTER_RE.match(note_text)
    if not match:
        return None
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+)$", re.MULTILINE)
    key_match = pattern.search(match.group(1))
    if not key_match:
        return None
    return key_match.group(1).strip().strip('"')


def split_frontmatter(note_text: str) -> tuple[str | None, str]:
    match = FRONTMATTER_RE.match(note_text)
    if not match:
        return None, note_text
    return match.group(0).rstrip("\n"), note_text[match.end():].lstrip("\n")


def current_auto_block(note_body: str) -> str | None:
    match = AUTO_BLOCK_RE.search(note_body)
    if not match:
        return None
    return match.group(0)


def render_update_block(machine_block: str, update_date: str) -> str:
    body = machine_block
    if body.startswith(f"{AUTO_BEGIN}\n"):
        body = body[len(f"{AUTO_BEGIN}\n") :]
    if body.endswith(f"\n{AUTO_END}"):
        body = body[: -len(f"\n{AUTO_END}")]
    return "\n".join(
        [
            f"<!-- AUTO-GENERATED:UPDATE:{update_date} -->",
            body,
            "<!-- AUTO-GENERATED:UPDATE:END -->",
        ]
    )


def replace_update_block(note_body: str, update_block: str) -> str:
    if UPDATE_BLOCK_RE.search(note_body):
        replaced = UPDATE_BLOCK_RE.sub(f"\n{update_block}\n", note_body, count=1)
        return replaced.strip("\n")
    marker = "\n## Reading Notes"
    index = note_body.find(marker)
    if index >= 0:
        return f"{note_body[:index].rstrip()}\n\n{update_block}\n\n{note_body[index:].lstrip()}"
    return f"{note_body.rstrip()}\n\n{update_block}\n"


def render_note(
    metadata: dict[str, Any],
    claims_payload: dict[str, Any],
    existing_note_text: str | None = None,
    update_date: str | None = None,
) -> tuple[str, dict[str, Any]]:
    cite_key = clean_text(metadata.get("cite_key"))
    canonical_id = clean_text(metadata.get("canonical_id"))
    if cite_key != clean_text(claims_payload.get("cite_key")):
        raise ValueError("Metadata cite_key must match claims sidecar cite_key")
    if canonical_id != clean_text(claims_payload.get("canonical_id")):
        raise ValueError("Metadata canonical_id must match claims sidecar canonical_id")

    machine_block = render_machine_block(metadata, claims_payload)
    new_hash = compute_auto_block_hash(machine_block)
    review_status = clean_text(metadata.get("review_status")) or "auto"

    if not existing_note_text:
        frontmatter = build_frontmatter(metadata, new_hash, review_status)
        body = "\n\n".join(
            [
                machine_block,
                "## Reading Notes\n_User-owned section. Never rewrite automatically._",
            ]
        )
        return f"{frontmatter}\n\n{body}\n", {
            "review_status": review_status,
            "auto_block_hash": new_hash,
            "updated_via": "replace",
        }

    _, note_body = split_frontmatter(existing_note_text)
    existing_block = current_auto_block(note_body)
    stored_hash = frontmatter_value(existing_note_text, "auto_block_hash")

    if existing_block and stored_hash and compute_auto_block_hash(existing_block) == stored_hash:
        clean_body = UPDATE_BLOCK_RE.sub("\n", note_body).strip("\n")
        replaced_body = AUTO_BLOCK_RE.sub(machine_block, clean_body, count=1)
        frontmatter = build_frontmatter(metadata, new_hash, review_status)
        return f"{frontmatter}\n\n{replaced_body.strip()}\n", {
            "review_status": review_status,
            "auto_block_hash": new_hash,
            "updated_via": "replace",
        }

    review_status = "user-edited"
    preserved_hash = stored_hash or new_hash
    update_block = render_update_block(machine_block, update_date or date.today().isoformat())
    updated_body = replace_update_block(note_body, update_block)
    frontmatter = build_frontmatter(metadata, preserved_hash, review_status)
    return f"{frontmatter}\n\n{updated_body.strip()}\n", {
        "review_status": review_status,
        "auto_block_hash": preserved_hash,
        "updated_via": "append-update",
    }


def main() -> int:
    try:
        args = parse_args()
        metadata = load_json(args.metadata_json)
        claims_payload = load_json(args.claims_json)
        existing_note_text = None
        if args.existing_note:
            existing_note_text = Path(args.existing_note).read_text(encoding="utf-8")
        rendered, info = render_note(
            metadata,
            claims_payload,
            existing_note_text=existing_note_text,
            update_date=args.update_date,
        )
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        print(json.dumps(info, indent=2), file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
