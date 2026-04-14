#!/usr/bin/env python3
"""Segmentation CLI entrypoint for the paper-reader skill.

PDF supplement handling: when a paper has multiple PDFs (for example, main paper
and `*-supp.pdf`), run this script separately for each source directory/PDF.
Each invocation writes one coherent segment set and source_pages metadata.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)

# S-003: Named sections that must never be merged regardless of size.
_PROTECTED_SECTION_NAMES = {'introduction', 'discussion', 'conclusion', 'abstract'}

# Ensure this script's directory is importable
_SCRIPTS_DIR = Path(__file__).parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_SEGMENT_MANIFEST_REL_PATH = Path('segments/_segment_manifest.json')
_EQUATION_RE = re.compile(
    r'\$|\\\[|\\begin\{(?:equation|align|gather|multline|eqnarray)[*]?\}'
)


def _parse_frontmatter_field(frontmatter: str, field: str) -> str:
    """Extract a field value from a YAML frontmatter string."""
    for line in frontmatter.splitlines():
        if line.startswith(field + ':'):
            return line[len(field) + 1:].strip().strip('"')
    return ''


def _parse_frontmatter_int_list(frontmatter: str, field: str) -> list[int]:
    """Extract a YAML list of ints from frontmatter for fields like source_pages."""
    lines = frontmatter.splitlines()
    values: list[int] = []
    for idx, line in enumerate(lines):
        if not line.startswith(field + ':'):
            continue
        inline = line[len(field) + 1:].strip()
        if inline.startswith('[') and inline.endswith(']'):
            values.extend(int(v) for v in re.findall(r'\d+', inline))
            return values
        scan = idx + 1
        while scan < len(lines):
            item = lines[scan]
            m = re.match(r'^\s*-\s*(\d+)\s*$', item)
            if not m:
                break
            values.append(int(m.group(1)))
            scan += 1
        return values
    return values


def _build_manifest_entry(seg_path: Path, paper_root: Path) -> dict:
    """Build a single manifest entry by reading the segment file."""
    content = seg_path.read_text(encoding='utf-8')
    # Extract frontmatter between first pair of ---
    fm = ''
    if content.startswith('---'):
        end = content.find('\n---', 3)
        if end != -1:
            fm = content[3:end]
            body = content[end + 4:]
        else:
            body = content
    else:
        body = content

    segment_id = _parse_frontmatter_field(fm, 'segment_id') or seg_path.stem
    title = _parse_frontmatter_field(fm, 'title') or segment_id
    section_type = _parse_frontmatter_field(fm, 'section_type') or 'other'
    comprehension_status = _parse_frontmatter_field(fm, 'comprehension_status') or 'pending'
    source_format = _parse_frontmatter_field(fm, 'source_format') or 'latex'
    token_estimate = len(content) // 4
    has_equations = bool(_EQUATION_RE.search(body))
    rel_file = str(seg_path.relative_to(paper_root))
    source_pages = _parse_frontmatter_int_list(fm, 'source_pages')
    figure_numbers = _parse_frontmatter_int_list(fm, 'figure_numbers')
    table_numbers = _parse_frontmatter_int_list(fm, 'table_numbers')

    return {
        'segment_id': segment_id,
        'title': title,
        'file': rel_file,
        'section_type': section_type,
        'source_format': source_format,
        'source_pages': source_pages,
        'figure_numbers': figure_numbers,
        'table_numbers': table_numbers,
        'token_estimate': token_estimate,
        'comprehension_status': comprehension_status,
        'has_equations': has_equations,
    }


def _write_segment_summary(cite_key: str, segments: list, manifest_dir: Path) -> Path:
    """Write _segment_summary.md alongside the manifest for human review."""
    summary_path = manifest_dir / '_segment_summary.md'
    header = (
        f'# Segment Summary: {cite_key}\n\n'
        f'| # | Segment ID | Title | Type | Source Pages | Figures | Tables |\n'
        f'|---|---|---|---|---|---|---|\n'
    )
    rows = []
    for idx, seg in enumerate(segments, start=1):
        seg_id = seg.get('segment_id', '')
        title = seg.get('title', seg_id)
        sec_type = seg.get('section_type', '')
        pages = seg.get('source_pages', [])
        page_ref = ', '.join(str(p) for p in pages) if pages else '—'
        fig_count = len(seg.get('figure_numbers', []))
        tbl_count = len(seg.get('table_numbers', []))
        rows.append(f'| {idx} | {seg_id} | {title} | {sec_type} | {page_ref} | {fig_count} | {tbl_count} |')
    summary_path.write_text(header + '\n'.join(rows) + '\n', encoding='utf-8')
    return summary_path


def write_manifest(cite_key: str, output_dir: Path, written: list) -> Path:
    """Write the manifest to the canonical paper-level segments path."""
    paper_root = output_dir.parent if output_dir.name == 'segments' else output_dir
    segments = sorted(
        [_build_manifest_entry(p, paper_root) for p in written],
        key=lambda e: e['segment_id'],
    )
    manifest = {
        'cite_key': cite_key,
        'segmentation_version': 1,
        'segment_count': len(segments),
        'segments': segments,
    }
    manifest_path = paper_root / _SEGMENT_MANIFEST_REL_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    summary_path = _write_segment_summary(cite_key, segments, manifest_path.parent)
    print(f"Summary written  → {summary_path}")
    return manifest_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='Segment a paper into per-section Markdown files.'
    )
    p.add_argument('--cite-key', required=True, help='Citation key for the paper.')
    p.add_argument(
        '--source-dir', required=True, type=Path,
        help='Directory containing the paper source files.'
    )
    p.add_argument(
        '--output-dir', required=True, type=Path,
        help='Directory to write segment .md files into.'
    )
    p.add_argument(
        '--format', default='latex', choices=['latex', 'markdown', 'pdf', 'html'],
        help='Source format of the paper (default: latex). Use --format html for HTML-sourced papers.'
    )
    p.add_argument(
        '--max-size', type=int, default=16384,
        help='Maximum segment size in bytes (default: 16384).'
    )
    p.add_argument(
        '--min-size', type=int, default=1024,
        help='Minimum segment size in bytes (default: 1024).'
    )
    p.add_argument(
        '--pdf-pages-per-group',
        type=int,
        default=3,
        help=(
            'Pages per Tier-1 PDF chunk (default: 3). '
            'Used by PDF segmentation and Phase 2 re-segmentation.'
        ),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = args.source_dir.expanduser().resolve()
    resolved_cite_key = args.cite_key
    output_dir = args.output_dir.expanduser()

    if not source_dir.is_dir():
        print(f"source_dir must be an existing directory: {source_dir}", file=sys.stderr)
        sys.exit(1)

    if args.format == 'latex':
        from segmenters.latex_segmenter import LatexSegmenter
        segmenter = LatexSegmenter()
    elif args.format == 'markdown':
        from segmenters.markdown_segmenter import MarkdownSegmenter
        segmenter = MarkdownSegmenter()
    elif args.format == 'pdf':
        from segmenters.pdf_segmenter import PdfSegmenter
        segmenter = PdfSegmenter()
    elif args.format == 'html':
        from segmenters.html_segmenter import HtmlSegmenter
        segmenter = HtmlSegmenter()
    else:
        print(f"Unsupported format: {args.format}", file=sys.stderr)
        sys.exit(1)

    print(f"Segmenting '{resolved_cite_key}' [{args.format}]")
    print(f"  source : {source_dir}")
    print(f"  output : {output_dir}")

    segment_kwargs = {
        'cite_key': resolved_cite_key,
        'source_dir': source_dir,
        'output_dir': output_dir,
        'max_size': args.max_size,
        'min_size': args.min_size,
    }
    if args.format == 'pdf':
        segment_kwargs['pdf_pages_per_group'] = max(1, int(args.pdf_pages_per_group))
        # T-002: Prefer translated_full.md over translated_full_pdf.md.
        _primary = source_dir / 'translated_full.md'
        _fallback = source_dir / 'translated_full_pdf.md'
        if _primary.exists():
            segment_kwargs['source_md'] = _primary
        elif _fallback.exists():
            segment_kwargs['source_md'] = _fallback
        # S-003: Pass protected section names (introduction, discussion, conclusion, abstract).
        segment_kwargs['protected_sections'] = _PROTECTED_SECTION_NAMES

    written = segmenter.segment(**segment_kwargs)

    print(f"Done — {len(written)} segment(s) written to {output_dir}")

    manifest_path = write_manifest(resolved_cite_key, output_dir, written)
    print(f"Manifest written → {manifest_path}")


if __name__ == '__main__':
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
