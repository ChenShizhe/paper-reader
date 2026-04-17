"""Parse and validate chapter plan files (YAML frontmatter + GFM table)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError("PyYAML is required: pip install pyyaml") from exc

_REQUIRED_FM = {"cite_key", "source_pdf", "claim_domain"}
_REQUIRED_COLS = {"slug", "page_range", "role", "depth", "include_in_synthesis", "domain_lens"}
_VALID_DEPTHS = {"deep", "summary", "skip"}
_PAGE_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


@dataclass
class ChapterRow:
    slug: str
    page_range: str
    page_start: int
    page_end: int
    role: str
    depth: str
    include_in_synthesis: bool
    domain_lens: str
    subagent_prompt: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def __iter__(self) -> Iterator["ChapterRow"]:
        yield self


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split '---\\n...\\n---\\nrest' into (yaml_block, rest)."""
    if not text.startswith("---"):
        raise ValueError("Chapter plan file must begin with a YAML frontmatter block (---)")
    # find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("Frontmatter block is not closed (missing closing ---)")
    yaml_block = text[3:end].strip()
    rest = text[end + 4:].lstrip("\n")
    return yaml_block, rest


def _parse_frontmatter(yaml_block: str) -> dict[str, Any]:
    data = yaml.safe_load(yaml_block) or {}
    missing = _REQUIRED_FM - data.keys()
    if missing:
        raise ValueError(f"Missing required frontmatter fields: {sorted(missing)}")
    data.setdefault("page_offset", 0)
    data.setdefault("synthesis_target_words", 5000)
    return data


def _parse_table(text: str) -> list[ChapterRow]:
    """Parse a GFM pipe table from markdown text."""
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    if not lines:
        raise ValueError("No GFM table found in chapter plan body")

    def _cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    headers = [h.lower().replace(" ", "_") for h in _cells(lines[0])]

    missing_cols = _REQUIRED_COLS - set(headers)
    if missing_cols:
        raise ValueError(f"Table is missing required columns: {sorted(missing_cols)}")

    # skip separator row
    data_lines = [l for l in lines[1:] if not re.match(r"^\|[-| :]+\|?$", l.strip())]

    rows: list[ChapterRow] = []
    for lineno, line in enumerate(data_lines, start=1):
        cells = _cells(line)
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        row_dict = dict(zip(headers, cells))

        slug = row_dict["slug"]
        if not slug:
            raise ValueError(f"Row {lineno}: slug is empty")

        page_range = row_dict["page_range"]
        m = _PAGE_RANGE_RE.match(page_range)
        if not m:
            raise ValueError(
                f"Row {lineno} (slug={slug!r}): malformed page_range {page_range!r}; expected 'N-M'"
            )
        page_start, page_end = int(m.group(1)), int(m.group(2))
        if page_start > page_end:
            raise ValueError(
                f"Row {lineno} (slug={slug!r}): page_range start {page_start} > end {page_end}"
            )

        depth = row_dict["depth"].strip().lower()
        if depth not in _VALID_DEPTHS:
            raise ValueError(
                f"Row {lineno} (slug={slug!r}): depth {depth!r} must be one of {sorted(_VALID_DEPTHS)}"
            )

        raw_include = row_dict["include_in_synthesis"].strip().lower()
        if raw_include in {"true", "yes", "1"}:
            include_in_synthesis = True
        elif raw_include in {"false", "no", "0"}:
            include_in_synthesis = False
        else:
            raise ValueError(
                f"Row {lineno} (slug={slug!r}): include_in_synthesis {raw_include!r} must be a boolean"
            )

        known = _REQUIRED_COLS | {"subagent_prompt"}
        extra = {k: v for k, v in row_dict.items() if k not in known}

        rows.append(ChapterRow(
            slug=slug,
            page_range=page_range,
            page_start=page_start,
            page_end=page_end,
            role=row_dict["role"],
            depth=depth,
            include_in_synthesis=include_in_synthesis,
            domain_lens=row_dict["domain_lens"],
            subagent_prompt=row_dict.get("subagent_prompt", ""),
            extra=extra,
        ))

    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_chapter_plan(path: str | Path) -> tuple[dict[str, Any], list[ChapterRow]]:
    """Parse a chapter plan file and return (frontmatter_dict, list[ChapterRow])."""
    text = Path(path).read_text(encoding="utf-8")
    yaml_block, body = _split_frontmatter(text)
    frontmatter = _parse_frontmatter(yaml_block)
    rows = _parse_table(body)
    return frontmatter, rows


def validate_chapter_plan(rows: list[ChapterRow], pdf_page_count: int) -> None:
    """Validate parsed rows against the PDF page count.

    Raises ValueError on: duplicate slugs, malformed page_range, page_range
    outside PDF, or mixed domain_lens labels across included rows.
    """
    seen_slugs: dict[str, int] = {}
    lens_labels: set[str] = set()

    for i, row in enumerate(rows, start=1):
        # duplicate slug check
        if row.slug in seen_slugs:
            raise ValueError(
                f"Duplicate slug {row.slug!r} found at row {i} "
                f"(first seen at row {seen_slugs[row.slug]})"
            )
        seen_slugs[row.slug] = i

        # page_range format — already validated during parse, but guard in case rows
        # were constructed manually
        m = _PAGE_RANGE_RE.match(row.page_range)
        if not m:
            raise ValueError(
                f"Row {i} (slug={row.slug!r}): malformed page_range {row.page_range!r}"
            )

        # page_range within PDF bounds
        if row.page_start < 1 or row.page_end > pdf_page_count:
            raise ValueError(
                f"Row {i} (slug={row.slug!r}): page_range {row.page_range!r} "
                f"is outside PDF bounds 1-{pdf_page_count}"
            )

        if row.include_in_synthesis and row.domain_lens:
            lens_labels.add(row.domain_lens)

    if len(lens_labels) > 1:
        raise ValueError(
            f"Mixed domain_lens labels across synthesis rows: {sorted(lens_labels)}. "
            "All included rows must share a single lens label."
        )


def iter_rows(rows: list[ChapterRow], depth_filter: str | None = None) -> Iterator[ChapterRow]:
    """Yield rows, optionally filtered by depth."""
    for row in rows:
        if depth_filter is None or row.depth == depth_filter:
            yield row
