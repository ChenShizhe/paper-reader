"""Shared utilities for paper segmentation."""

import re
from pathlib import Path
from typing import Optional, Sequence


def slugify(text: str, max_len: int = 40) -> str:
    """Convert text to a valid slug: lowercase alphanumeric and underscores only."""
    slug = text.lower()
    slug = re.sub(r'[^a-z0-9]+', '_', slug)
    slug = slug.strip('_')
    return slug[:max_len]


def _fmt_field(key: str, value: str) -> str:
    """Format a YAML frontmatter field, quoting values that contain special chars."""
    if re.match(r'^[a-zA-Z0-9_./ -]+$', value):
        return f"{key}: {value}"
    escaped = value.replace('"', '\\"')
    return f'{key}: "{escaped}"'


def build_frontmatter(
    cite_key: str,
    segment_id: str,
    section_type: str,
    title: str,
    source_file: Optional[str] = None,
    split_reason: Optional[str] = None,
    source_format: Optional[str] = None,
    source_pages: Optional[Sequence[int]] = None,
    figure_numbers: Optional[Sequence[int]] = None,
    table_numbers: Optional[Sequence[int]] = None,
) -> str:
    """Build a YAML frontmatter block string."""
    lines = ['---']
    lines.append(_fmt_field('cite_key', cite_key))
    lines.append(_fmt_field('segment_id', segment_id))
    lines.append(_fmt_field('title', title))
    lines.append(_fmt_field('section_type', section_type))
    lines.append('comprehension_status: pending')
    if source_format:
        lines.append(_fmt_field('source_format', source_format))
    if source_file:
        lines.append(_fmt_field('source_file', source_file))
    if split_reason:
        lines.append(_fmt_field('split_reason', split_reason))
    if source_pages:
        lines.append('source_pages:')
        for page_number in source_pages:
            lines.append(f'  - {int(page_number)}')
    fig_list = sorted(set(int(n) for n in figure_numbers)) if figure_numbers else []
    tbl_list = sorted(set(int(n) for n in table_numbers)) if table_numbers else []
    lines.append(f'figure_numbers: [{", ".join(str(n) for n in fig_list)}]')
    lines.append(f'table_numbers: [{", ".join(str(n) for n in tbl_list)}]')
    lines.append('---')
    return '\n'.join(lines)


def write_segment(
    output_dir: Path,
    cite_key: str,
    index: int,
    slug: str,
    section_type: str,
    title: str,
    body: str,
    source_file: Optional[str] = None,
    split_reason: Optional[str] = None,
    source_format: Optional[str] = None,
    source_pages: Optional[Sequence[int]] = None,
    figure_numbers: Optional[Sequence[int]] = None,
    table_numbers: Optional[Sequence[int]] = None,
) -> Path:
    """Write a single segment Markdown file with frontmatter."""
    filename = f"{cite_key}__v01_seg_{index:03d}_{slug}.md"
    segment_id = f"{cite_key}__v01_seg_{index:03d}_{slug}"

    frontmatter = build_frontmatter(
        cite_key=cite_key,
        segment_id=segment_id,
        section_type=section_type,
        title=title,
        source_file=source_file,
        split_reason=split_reason,
        source_format=source_format,
        source_pages=source_pages,
        figure_numbers=figure_numbers,
        table_numbers=table_numbers,
    )

    content = f"{frontmatter}\n\n# {title}\n\n{body}\n"

    fpath = output_dir / filename
    fpath.write_text(content, encoding='utf-8')
    return fpath
