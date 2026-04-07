#!/usr/bin/env python3
"""Build _catalog.yaml from _segment_manifest.json and an optional .bib file."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# Ensure the scripts directory is importable when run directly.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from catalog.catalog_schema import (
    CatalogSchema,
    PaperMetadata,
    SectionEntry,
    SegmentEntry,
    XrefIndexSchema,
)


# ── BibTeX helpers ─────────────────────────────────────────────────────────────

def _find_bib_entry(bib_text: str, cite_key: str) -> Optional[str]:
    """Return the raw text of the bib entry whose key matches cite_key, or None."""
    pattern = re.compile(
        rf'@\w+\{{\s*{re.escape(cite_key)}\s*,',
        re.IGNORECASE,
    )
    m = pattern.search(bib_text)
    if not m:
        return None
    depth = 0
    i = m.start()
    while i < len(bib_text):
        if bib_text[i] == '{':
            depth += 1
        elif bib_text[i] == '}':
            depth -= 1
            if depth == 0:
                return bib_text[m.start(): i + 1]
        i += 1
    return bib_text[m.start():]


def _get_field(entry_text: str, field_name: str) -> Optional[str]:
    """Extract a field value from a bib entry, handling nested braces."""
    pattern = re.compile(rf'\b{field_name}\s*=\s*', re.IGNORECASE)
    m = pattern.search(entry_text)
    if not m:
        return None
    pos = m.end()
    if pos >= len(entry_text):
        return None
    ch = entry_text[pos]
    if ch == '{':
        depth = 0
        start = pos
        i = pos
        while i < len(entry_text):
            if entry_text[i] == '{':
                depth += 1
            elif entry_text[i] == '}':
                depth -= 1
                if depth == 0:
                    return entry_text[start + 1: i]
            i += 1
        return entry_text[start + 1:]
    if ch == '"':
        end = entry_text.find('"', pos + 1)
        if end != -1:
            return entry_text[pos + 1: end]
    return None


def _clean_latex(text: str) -> str:
    """Remove common LaTeX markup and decode a few common accent commands."""
    text = text.replace('{\\c{c}}', 'ç').replace('{\\c{C}}', 'Ç')
    text = text.replace("{\\'e}", 'é').replace("{\\'E}", 'É')
    text = text.replace("{\\'o}", 'ó').replace("{\\'a}", 'á')
    text = text.replace('{\\"{u}}', 'ü').replace('{\\"{o}}', 'ö')
    text = re.sub(r'\\[a-zA-Z]+\{([^{}]*)\}', r'\1', text)
    text = re.sub(r'[{}]', '', text)
    return text.strip()


def _parse_bib_meta(bib_path: Path, cite_key: str) -> Dict:
    """Parse title, authors, year, journal from the bib entry matching cite_key."""
    try:
        bib_text = bib_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return {'title': '', 'authors': [], 'year': None, 'journal': ''}

    entry = _find_bib_entry(bib_text, cite_key)
    if not entry:
        return {'title': '', 'authors': [], 'year': None, 'journal': ''}

    title = _clean_latex(_get_field(entry, 'title') or '')
    journal = _clean_latex(_get_field(entry, 'journal') or '')
    year_raw = _clean_latex(_get_field(entry, 'year') or '')
    year = int(year_raw) if year_raw.isdigit() else None

    author_raw = _clean_latex(_get_field(entry, 'author') or '')
    authors: List[str] = []
    if author_raw:
        for part in re.split(r'\s+and\s+', author_raw, flags=re.IGNORECASE):
            part = part.strip()
            if ',' in part:
                last, first = part.split(',', 1)
                part = f'{first.strip()} {last.strip()}'
            if part:
                authors.append(part)

    return {'title': title, 'authors': authors, 'year': year, 'journal': journal}


# ── Section grouping ───────────────────────────────────────────────────────────

def _normalize_section_type(value: object) -> str:
    """Return a safe section_type string for catalog grouping."""
    if not isinstance(value, str):
        return 'unknown'
    normalized = value.strip().lower()
    return normalized or 'unknown'


def _group_into_sections(segments: List[Dict]) -> List[Dict]:
    """Create one section per segment, using segment_id as the primary section key.

    Each segment becomes its own section entry. section_type is preserved as a
    field on each entry but is NOT used as the grouping key, so every segment
    gets a unique section and none can be silently dropped due to section_type
    collisions (S-002 fix).

    Document order is preserved; each entry gets depth=0 and
    comprehension_status=pending (M2 constraint — subsection nesting deferred).
    """
    sections: List[Dict] = []
    for i, seg in enumerate(segments):
        segment_id = seg['segment_id']
        section_type = _normalize_section_type(seg.get('section_type'))
        heading = seg.get('title', '').strip() or section_type.replace('_', ' ').title()
        sec_id = f'sec_{i + 1:03d}_{segment_id}'
        sections.append({
            'id': sec_id,
            'heading': heading,
            'section_type': section_type,
            'depth': 0,
            'segments': [segment_id],
            'comprehension_status': 'pending',
            'summary': None,
            'key_terms': [],
            'notes': [],
            'children': [],
        })
    return sections


# ── Main builder ───────────────────────────────────────────────────────────────

def build_catalog(cite_key: str, work_dir: Path) -> CatalogSchema:
    manifest_path = work_dir / 'segments' / '_segment_manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f'Manifest not found: {manifest_path}')

    with open(manifest_path, encoding='utf-8') as fh:
        manifest = json.load(fh)

    raw_segments: List[Dict] = manifest['segments']
    normalized_segments: List[Dict] = []
    for segment in raw_segments:
        normalized = dict(segment)
        normalized['section_type'] = _normalize_section_type(segment.get('section_type'))
        normalized_segments.append(normalized)

    # Find and parse first .bib file in raw/
    bib_meta = {'title': '', 'authors': [], 'year': None, 'journal': ''}
    bib_file_name: Optional[str] = None
    raw_dir = work_dir / 'raw'
    if raw_dir.is_dir():
        bib_files = sorted(raw_dir.glob('*.bib'))
        if bib_files:
            bib_file_name = bib_files[0].name
            bib_meta = _parse_bib_meta(bib_files[0], cite_key)

    # Infer source_format from segment metadata; override from _translation_manifest.json if present.
    source_format = normalized_segments[0].get('source_format', '') if normalized_segments else ''
    translation_manifest_path = work_dir / '_translation_manifest.json'
    if translation_manifest_path.exists():
        try:
            with open(translation_manifest_path, encoding='utf-8') as fh:
                translation_manifest = json.load(fh)
            if 'source_format' in translation_manifest:
                source_format = translation_manifest['source_format']
        except (OSError, json.JSONDecodeError):
            pass

    now_iso = datetime.now(timezone.utc).isoformat()

    sections_data = _group_into_sections(normalized_segments)
    section_count = len(sections_data)
    segment_count = len(normalized_segments)
    knowledge_gaps_file = '_knowledge_gaps.yaml'

    paper = PaperMetadata(
        cite_key=cite_key,
        title=bib_meta['title'],
        authors=bib_meta['authors'],
        year=bib_meta['year'],
        journal=bib_meta['journal'],
        source_format=source_format,
        source_dir=str(work_dir),
        bib_file=bib_file_name,
        segmentation_version=manifest.get('segmentation_version', 1),
        translation_version=0,
        catalog_version=1,
        comprehension_pass=0,
        created_at=now_iso,
        last_updated=now_iso,
        xref_index='_xref_index.yaml',
        knowledge_gaps_file=knowledge_gaps_file,
    )

    # Map segment_id → section_id for back-linking
    seg_to_sec: Dict[str, str] = {}
    for sec in sections_data:
        for sid in sec['segments']:
            seg_to_sec[sid] = sec['id']

    sections = [SectionEntry(**s) for s in sections_data]

    segments = [
        SegmentEntry(
            id=seg['segment_id'],
            file=seg['file'],
            section_id=seg_to_sec.get(seg['segment_id'], ''),
            section_type=seg.get('section_type', ''),
            token_estimate=seg.get('token_estimate', 0),
            has_equations=seg.get('has_equations', False),
            has_figures=seg.get('has_figures', False),
            has_tables=seg.get('has_tables', False),
            comprehension_status=seg.get('comprehension_status', 'pending'),
        )
        for seg in normalized_segments
    ]

    # Post-build assertion: core keyword segments must not be silently excluded.
    # All segments (including references and back-matter) are intentionally
    # included — no exclusion filter is applied anywhere in this builder.
    _CORE_SEGMENT_PATTERN = re.compile(
        r'introduction|method|bayesian|experiment|conclusion', re.IGNORECASE
    )
    catalog_segment_ids = {seg.id for seg in segments}
    for raw_seg in raw_segments:
        sid = raw_seg.get('segment_id', '')
        stype = raw_seg.get('section_type', '')
        if _CORE_SEGMENT_PATTERN.search(sid) or _CORE_SEGMENT_PATTERN.search(stype):
            if sid not in catalog_segment_ids:
                raise AssertionError(
                    f"Core segment '{sid}' (section_type={stype!r}) is missing"
                    " from the catalog — check the manifest and normalization logic."
                )

    return CatalogSchema(paper=paper, sections=sections, segments=segments)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Build _catalog.yaml from _segment_manifest.json.'
    )
    parser.add_argument('--cite-key', required=True, help='Paper cite key')
    parser.add_argument(
        '--work-dir', required=True, help='Paper working directory'
    )
    args = parser.parse_args()

    work_dir = Path(args.work_dir).expanduser().resolve()
    catalog = build_catalog(args.cite_key, work_dir)

    catalog_dict = catalog.model_dump()
    output_path = work_dir / '_catalog.yaml'
    with open(output_path, 'w', encoding='utf-8') as fh:
        yaml.dump(
            catalog_dict,
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    print(f'Wrote {output_path}')

    # Write _xref_index.yaml skeleton only if it does not already exist.
    xref_path = work_dir / '_xref_index.yaml'
    if not xref_path.exists():
        xref = XrefIndexSchema(
            cite_key=args.cite_key,
            catalog_version=catalog.paper.catalog_version,
        )
        with open(xref_path, 'w', encoding='utf-8') as fh:
            yaml.dump(
                xref.model_dump(),
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        print(f'Wrote {xref_path}')
    else:
        print(f'Skipped {xref_path} (already exists)')

    # Write _knowledge_gaps.yaml stub only if it does not already exist or is empty.
    gaps_path = work_dir / '_knowledge_gaps.yaml'
    if not gaps_path.exists() or gaps_path.stat().st_size == 0:
        gaps_stub = {'cite_key': args.cite_key, 'gaps': []}
        with open(gaps_path, 'w', encoding='utf-8') as fh:
            yaml.dump(
                gaps_stub,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        print(f'Wrote {gaps_path}')
    else:
        print(f'Skipped {gaps_path} (already exists)')


if __name__ == '__main__':
    main()
