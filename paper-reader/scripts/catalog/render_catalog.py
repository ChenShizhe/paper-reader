#!/usr/bin/env python3
"""Render catalog.md from _catalog.yaml — idempotent, deterministic output."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ── Status emoji mapping ───────────────────────────────────────────────────────

_STATUS_EMOJI = {
    "pending": "⬜",
    "in_progress": "🟡",
    "complete": "✅",
    "skipped": "⏭️",
}


def _emoji(status: str) -> str:
    return _STATUS_EMOJI.get(status, "⬜")


# ── Renderer ───────────────────────────────────────────────────────────────────

def render_catalog(cite_key: str, work_dir: Path) -> str:
    catalog_path = work_dir / '_catalog.yaml'
    if not catalog_path.exists():
        raise FileNotFoundError(f'Catalog not found: {catalog_path}')

    with open(catalog_path, encoding='utf-8') as fh:
        data = yaml.safe_load(fh)

    paper = data['paper']
    sections = data.get('sections', [])
    segments_list = data.get('segments', [])

    seg_by_id = {s['id']: s for s in segments_list}

    lines: list = []

    # --- YAML frontmatter ---
    lines.append('---')
    lines.append(f'cite_key: {paper["cite_key"]}')
    title_str = paper.get('title') or ''
    lines.append(f'title: "{title_str}"')
    authors = paper.get('authors') or []
    if authors:
        lines.append('authors:')
        for a in authors:
            lines.append(f'  - {a}')
    if paper.get('year'):
        lines.append(f'year: {paper["year"]}')
    journal_str = paper.get('journal') or ''
    if journal_str:
        lines.append(f'journal: "{journal_str}"')
    lines.append(f'catalog_version: {paper.get("catalog_version", 1)}')
    lines.append(f'comprehension_pass: {paper.get("comprehension_pass", 0)}')
    lines.append(f'generated_at: {datetime.now(timezone.utc).isoformat()}')
    lines.append('---')
    lines.append('')

    # --- Title ---
    lines.append(f'# {title_str or cite_key}')
    lines.append('')

    # --- Metadata ---
    lines.append('## Metadata')
    lines.append('')
    if authors:
        lines.append(f'**Authors:** {", ".join(authors)}')
    if paper.get('year'):
        lines.append(f'**Year:** {paper["year"]}')
    if journal_str:
        lines.append(f'**Journal:** {journal_str}')
    lines.append(f'**Comprehension pass:** {paper.get("comprehension_pass", 0)}')
    lines.append('')

    # --- Paper Structure ---
    lines.append('## Paper Structure')
    lines.append('')

    for section in sections:
        sec_status = section.get('comprehension_status', 'pending')
        emoji = _emoji(sec_status)
        heading = section.get('heading') or section.get('id', '')
        lines.append(f'### {emoji} {heading}')
        lines.append('')

        for seg_id in section.get('segments', []):
            seg = seg_by_id.get(seg_id)
            if seg:
                seg_status = seg.get('comprehension_status', 'pending')
                seg_emoji = _emoji(seg_status)
                tokens = seg.get('token_estimate', 0)
                tags = []
                if seg.get('has_equations'):
                    tags.append('eq')
                if seg.get('has_figures'):
                    tags.append('fig')
                if seg.get('has_tables'):
                    tags.append('tbl')
                tag_str = f' `[{", ".join(tags)}]`' if tags else ''
                lines.append(f'- {seg_emoji} `{seg_id}` ({tokens} tok){tag_str}')
        lines.append('')

    # --- Cross References ---
    lines.append('## Cross References')
    lines.append('')
    xref_path = work_dir / '_xref_index.yaml'
    if xref_path.exists():
        with open(xref_path, encoding='utf-8') as fh:
            xref = yaml.safe_load(fh)
        lines.append(f'- Equations: {len(xref.get("equations", []))}')
        lines.append(f'- Theorems: {len(xref.get("theorems", []))}')
        lines.append(f'- Figures: {len(xref.get("figures", []))}')
        lines.append(f'- Citations: {len(xref.get("citations", []))}')
    else:
        lines.append('_No xref index found._')
    lines.append('')

    return '\n'.join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Render catalog.md from _catalog.yaml.'
    )
    parser.add_argument('--cite-key', required=True, help='Paper cite key')
    parser.add_argument('--work-dir', required=True, help='Paper working directory')
    args = parser.parse_args()

    work_dir = Path(args.work_dir).expanduser().resolve()
    content = render_catalog(args.cite_key, work_dir)

    output_path = work_dir / 'catalog.md'

    # Idempotent: only write when content has actually changed.
    if output_path.exists():
        existing = output_path.read_text(encoding='utf-8')
        if existing == content:
            print(f'Unchanged {output_path}')
            return

    output_path.write_text(content, encoding='utf-8')
    print(f'Wrote {output_path}')


if __name__ == '__main__':
    main()
