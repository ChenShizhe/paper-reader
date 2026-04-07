"""PDF segmentation orchestrator: ensures translated_full_pdf.md exists, then segments by page chunks."""

import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_LOG = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).parent.parent.resolve()
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

SOURCE_FORMAT = 'markdown'

# Overhead estimate for YAML frontmatter in bytes.
_FRONTMATTER_OVERHEAD = 400

# Section classification patterns: (section_type, regex_pattern)
_CLASSIFY_PATTERNS = [
    ('introduction', r'introduction'),
    ('model', r'model|framework|notation|assumption|identification|causal'),
    ('background', r'background|model|review'),
    ('theory', r'approach|concentration|inequality|theorem|theory|lemma|proof'),
    ('methods', r'estimation|regression|lasso|oracle|screen|selection|penali|cluster'),
    ('simulation', r'simulation'),
    ('application', r'data|application|real|spike'),
    ('discussion', r'discussion|conclusion'),
    ('appendix', r'appendix|technical|auxiliary|algorithm'),
]

_SECTION_NAME_ALIASES = {
    'abstract': 'Abstract',
    'introduction': 'Introduction',
    'related work': 'Related Work',
    'background': 'Background',
    'preliminaries': 'Preliminaries',
    'problem setup': 'Problem Setup',
    'problem formulation': 'Problem Formulation',
    'model': 'Model',
    'method': 'Method',
    'methods': 'Methods',
    'approach': 'Approach',
    'algorithm': 'Algorithm',
    'experiments': 'Experiments',
    'results': 'Results',
    'discussion': 'Discussion',
    'conclusion': 'Conclusion',
    'conclusions': 'Conclusions',
    'appendix': 'Appendix',
    'references': 'References',
}
_PAGE_RANGE_RE = re.compile(r'<!--\s*pages:\s*(\d+)\s*-\s*(\d+)\s*-->')
_NUMBERED_HEADER_RE = re.compile(r'^(\d+(?:\.\d+)*)[.)]?\s+(.+)$')
_KNOWN_HEADER_RE = re.compile(
    r'^(?:\d+(?:\.\d+)*[.)]?\s+)?'
    r'(?:abstract|introduction|related work|background|preliminaries|'
    r'problem setup|problem formulation|model|method(?:s)?|approach|'
    r'algorithm|experiments?|results?|discussion|conclusions?|appendix|references)$',
    re.IGNORECASE,
)
# Matches fallback page_chunk titles produced when no structural header is found.
_FALLBACK_TITLE_RE = re.compile(r'^Page Chunk \d+$')
# Figure and table reference patterns for scanning segment text.
_FIG_REF_RE = re.compile(r'Fig(?:ure)?[s]?\.?\s*(\d+)', re.IGNORECASE)
_TBL_REF_RE = re.compile(r'Tables?\s*(\d+)', re.IGNORECASE)

# T-001: Detects garbled letter-spaced segment names (e.g. "I n t r o d u c t i o n").
_SINGLE_CHAR_RUN_RE = re.compile(r'(?:\b\w\s){4,}')

# S-003: Named sections that must never be merged regardless of size.
_PROTECTED_SECTIONS = {'introduction', 'discussion', 'conclusion', 'abstract'}


def sanitize_segment_name(name: str, chunk_index: int) -> str:
    """Sanitize a segment name that appears letter-spaced or garbled.

    Detects single-character runs separated by spaces using pattern (\\b\\w\\s){4,}.
    Collapses the run by removing spaces between adjacent single-char tokens.
    Falls back to page_chunk_N when the collapsed result is unusable.
    Logs a warning when a name is sanitized or the fallback is used.
    """
    if _SINGLE_CHAR_RUN_RE.search(name):
        # Remove spaces between adjacent single-char word tokens.
        collapsed = re.sub(r'(\b\w) (?=\w)', r'\1', name).strip()
        if collapsed and len(collapsed) >= 2:
            _LOG.warning(
                "Sanitized letter-spaced segment name %r → %r", name, collapsed
            )
            return collapsed
        fallback = f"page_chunk_{chunk_index}"
        _LOG.warning(
            "Segment name %r is garbled (letter-spaced); falling back to %r",
            name, fallback,
        )
        return fallback
    return name


def _ensure_pdf_translation(
    source_dir: Path,
    cite_key: str,
    *,
    pages_per_chunk: int,
    source_md: Optional[Path] = None,
) -> Path:
    """Return path to translated markdown, running PDF translation if needed.

    T-002: Checks translated_full.md first, then translated_full_pdf.md.
    """
    # T-002: Use caller-supplied path if it already exists.
    if source_md is not None and source_md.exists():
        print(f"  pdf translation: {source_md.name} (provided)")
        return source_md
    # T-002: Prefer translated_full.md over translated_full_pdf.md.
    primary_md = source_dir / 'translated_full.md'
    if primary_md.exists():
        print(f"  pdf translation: {primary_md.name} (primary)")
        return primary_md
    pdf_md = source_dir / 'translated_full_pdf.md'
    if pdf_md.exists():
        print(f"  pdf translation: {pdf_md.name} (exists)")
        return pdf_md

    # Find a PDF to translate in source_dir or raw/ subdirectory
    pdf_files = sorted(source_dir.glob('*.pdf'), key=lambda p: p.stat().st_size, reverse=True)
    if not pdf_files:
        raw_dir = source_dir / 'raw'
        if raw_dir.is_dir():
            pdf_files = sorted(raw_dir.glob('*.pdf'), key=lambda p: p.stat().st_size, reverse=True)

    if not pdf_files:
        raise FileNotFoundError(
            f"No PDF found under {source_dir} and translated_full_pdf.md does not exist"
        )

    pdf_path = pdf_files[0]
    print(f"  pdf translation: running on {pdf_path.name}")
    from translators.pdf_translator import assemble_pdf_translation
    assemble_pdf_translation(
        pdf_path=str(pdf_path),
        cite_key=cite_key,
        output_path=str(pdf_md),
        pages_per_chunk=pages_per_chunk,
    )
    print(f"  pdf translation: done → {pdf_md.name}")
    return pdf_md


def _parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Parse simple key:value YAML frontmatter and return (frontmatter_dict, body)."""
    frontmatter: Dict[str, str] = {}
    if content.startswith('---'):
        end = content.find('\n---', 3)
        if end != -1:
            block = content[3:end]
            body = content[end + 4:]
            for line in block.splitlines():
                m = re.match(r'^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$', line.strip())
                if m:
                    frontmatter[m.group(1)] = m.group(2).strip()
            return frontmatter, body
    return frontmatter, content


def _split_pdf_chunks(body: str) -> List[str]:
    """Split PDF body at --- page-chunk boundaries."""
    return re.split(r'\n\n---\n\n', body)


def _load_persisted_chunk_markdown(source_dir: Path) -> List[str]:
    chunk_dir = source_dir / 'pdf_segments' / 'translated_chunks'
    if not chunk_dir.is_dir():
        return []
    chunks: List[str] = []
    for chunk_path in sorted(chunk_dir.glob('chunk_*.md')):
        text = chunk_path.read_text(encoding='utf-8', errors='replace').strip()
        if text:
            chunks.append(text)
    return chunks


def _extract_source_pages(chunk_text: str) -> Optional[List[int]]:
    m = _PAGE_RANGE_RE.search(chunk_text)
    if not m:
        return None
    start = int(m.group(1))
    end = int(m.group(2))
    if end < start:
        start, end = end, start
    return list(range(start, end + 1))


def _infer_source_pages(
    index: int,
    *,
    pages_per_group: int,
    total_pages: int,
) -> List[int]:
    start = (index - 1) * pages_per_group + 1
    end = start + pages_per_group - 1
    if total_pages > 0:
        end = min(end, total_pages)
    if end < start:
        end = start
    return list(range(start, end + 1))


def _derive_title(chunk: str, index: int) -> str:
    """Extract a candidate title from the first meaningful line of a PDF chunk."""
    # Remove tier2_fallback comment markers
    chunk = re.sub(r'<!--.*?-->\s*', '', chunk, flags=re.DOTALL).strip()
    for line in chunk.splitlines():
        line = line.strip()
        if line and len(line) > 8:
            return sanitize_segment_name(line[:60].rstrip(), index)
    return f"Page Chunk {index}"


def _classify_chunk(text: str) -> str:
    """Map chunk text to a coarse section_type tag."""
    t = text.lower()
    for section_type, pattern in _CLASSIFY_PATTERNS:
        if re.search(pattern, t):
            return section_type
    return 'section'


def _canonical_header_title(raw: str) -> str:
    normalized = re.sub(r'\s+', ' ', raw.strip().strip(':')).strip()
    if not normalized:
        return ''
    lowered = normalized.lower()
    if lowered in _SECTION_NAME_ALIASES:
        return _SECTION_NAME_ALIASES[lowered]
    return normalized


def _looks_like_structural_header(
    line: str,
    next_nonempty: str,
) -> Optional[str]:
    if not line:
        return None
    if line.startswith('#'):
        return _canonical_header_title(line.lstrip('#').strip())
    if len(line) > 90:
        return None

    numbered = _NUMBERED_HEADER_RE.match(line)
    if numbered:
        candidate = numbered.group(2).strip()
        if _KNOWN_HEADER_RE.match(line) or (candidate and len(candidate.split()) <= 8):
            return _canonical_header_title(candidate)

    if _KNOWN_HEADER_RE.match(line):
        return _canonical_header_title(line)

    tokens = re.findall(r'[A-Za-z]+', line)
    if not tokens or len(tokens) > 12:
        return None
    uppercase_tokens = sum(1 for t in tokens if len(t) > 1 and t.upper() == t)
    mostly_upper = uppercase_tokens >= max(2, len(tokens) - 1)
    next_is_body = bool(next_nonempty) and bool(re.search(r'[a-z]', next_nonempty))
    if mostly_upper and next_is_body:
        return _canonical_header_title(line.title())

    return None


def _phase2_resegment_chunk(chunk_text: str, index: int) -> List[Tuple[str, str]]:
    """Re-segment a PDF chunk using structural heading heuristics."""
    cleaned = re.sub(r'<!--.*?-->\s*', '', chunk_text, flags=re.DOTALL).strip()
    if not cleaned:
        return []

    lines = cleaned.splitlines()
    segments: List[Tuple[str, str]] = []
    current_title = f"Page Chunk {index}"
    current_lines: List[str] = []
    body_chars = 0

    for line_idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            if current_lines:
                current_lines.append('')
            continue

        next_nonempty = ''
        for look_ahead in lines[line_idx + 1:]:
            stripped = look_ahead.strip()
            if stripped:
                next_nonempty = stripped
                break

        header_title = _looks_like_structural_header(line, next_nonempty)
        if header_title is not None:
            header_title = sanitize_segment_name(header_title, index)
        if header_title is not None and body_chars == 0 and not current_lines:
            current_title = header_title
            continue

        can_split_here = header_title is not None and body_chars >= 120
        if can_split_here:
            body = '\n'.join(current_lines).strip()
            if body:
                segments.append((current_title, body))
            current_title = header_title or current_title
            current_lines = []
            body_chars = 0
            continue

        current_lines.append(raw_line.rstrip())
        body_chars += len(raw_line)

    final_body = '\n'.join(current_lines).strip()
    if final_body:
        segments.append((current_title, final_body))

    if not segments:
        fallback_title = _derive_title(cleaned, index)
        return [(fallback_title, cleaned)]

    return segments


def _size_split(body: str, max_bytes: int) -> List[str]:
    """Split body into chunks each <= max_bytes, breaking at paragraph boundaries."""
    para_re = re.compile(r'\n\n+')
    boundaries = [m.end() for m in para_re.finditer(body)] + [len(body)]

    chunks: List[str] = []
    start = 0
    while start < len(body):
        chunk = body[start:]
        if len(chunk.encode('utf-8')) <= max_bytes:
            chunks.append(chunk)
            break

        split_at: Optional[int] = None
        for b in reversed(boundaries):
            if b <= start:
                continue
            candidate = body[start:b]
            if len(candidate.encode('utf-8')) <= max_bytes:
                split_at = b
                break

        if split_at is None or split_at == start:
            chunk_bytes = body[start:].encode('utf-8')
            cut = len(chunk_bytes[:max_bytes].decode('utf-8', errors='ignore'))
            split_at = start + cut

        chunks.append(body[start:split_at].strip())
        start = split_at

    return [c for c in chunks if c.strip()]


def _merge_small_segments_in_memory(
    segments: List[Tuple[str, str, str, List[int]]],
    min_size: int,
    protected_sections: Optional[set] = None,
) -> List[Tuple[str, str, str, List[int]]]:
    """Merge undersized segments into adjacent neighbors (S-003).

    Each element is (title, section_type, body_text, source_pages).
    Segments whose title matches a protected name (introduction, discussion,
    conclusion, abstract) are never merged regardless of size.
    Merges are recorded in the receiving segment body via HTML comment metadata.
    """
    _protected = protected_sections if protected_sections is not None else _PROTECTED_SECTIONS
    result: List[Tuple[str, str, str, List[int]]] = list(segments)
    changed = True
    while changed:
        changed = False
        for i, (title, section_type, body_text, source_pages) in enumerate(result):
            seg_content = f"# {title}\n\n{body_text}\n"
            size = len(seg_content.encode('utf-8'))
            if size >= min_size:
                continue
            if title.strip().lower() in _protected:
                continue  # S-003: never merge protected named sections
            # Prefer next neighbor, then previous.
            if i + 1 < len(result):
                nb_title, nb_type, nb_body, nb_pages = result[i + 1]
                merged_meta = f"<!-- merged_from: {title} -->"
                merged_body = merged_meta + '\n' + body_text.strip() + '\n\n' + nb_body
                merged_pages = sorted(set(source_pages + nb_pages))
                result[i + 1] = (nb_title, nb_type, merged_body, merged_pages)
                del result[i]
                print(f"  [merge↓] '{title[:40]}' ({size}B) → '{nb_title[:40]}'")
                changed = True
                break
            elif i > 0:
                nb_title, nb_type, nb_body, nb_pages = result[i - 1]
                merged_meta = f"<!-- merged_from: {title} -->"
                merged_body = nb_body.rstrip() + '\n\n' + merged_meta + '\n' + body_text.strip()
                merged_pages = sorted(set(source_pages + nb_pages))
                result[i - 1] = (nb_title, nb_type, merged_body, merged_pages)
                del result[i]
                print(f"  [merge↑] '{title[:40]}' ({size}B) → '{nb_title[:40]}'")
                changed = True
                break
    return result


class PdfSegmenter:
    """Segments a PDF paper via translated markdown into per-section Markdown files."""

    def segment(
        self,
        cite_key: str,
        source_dir: Path,
        output_dir: Path,
        max_size: int = 16384,
        min_size: int = 1024,
        pdf_pages_per_group: int = 3,
        source_md: Optional[Path] = None,
        protected_sections: Optional[set] = None,
    ) -> List[Path]:
        source_dir = Path(source_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Clean existing segment files for a fresh run.
        for old in output_dir.glob('*.md'):
            old.unlink()

        # Step 1: Ensure translated_full_pdf.md exists (translate if needed).
        pages_per_group = max(1, int(pdf_pages_per_group))
        pdf_md = _ensure_pdf_translation(
            source_dir,
            cite_key,
            pages_per_chunk=pages_per_group,
            source_md=source_md,
        )
        print(f"  pdf source: {pdf_md.name}")
        print(f"  bounds   : min={min_size}B  max={max_size}B")
        print(f"  grouping : {pages_per_group} page(s) per chunk")

        content = pdf_md.read_text(encoding='utf-8')
        frontmatter, body = _parse_frontmatter(content)
        try:
            total_pages = int(frontmatter.get('page_count', '0') or 0)
        except ValueError:
            total_pages = 0

        # Step 2: Prefer persisted per-chunk markdown artifacts when available.
        raw_chunks = _load_persisted_chunk_markdown(source_dir)
        if raw_chunks:
            print(f"  raw chunks: {len(raw_chunks)} (from pdf_segments/translated_chunks)")
        else:
            raw_chunks = _split_pdf_chunks(body)
            print(f"  raw chunks: {len(raw_chunks)} (from translated_full_pdf.md)")

        effective_max = max_size - _FRONTMATTER_OVERHEAD

        source_pages_by_chunk: List[List[int]] = []
        for idx, chunk_text in enumerate(raw_chunks, start=1):
            pages = _extract_source_pages(chunk_text)
            if pages is None:
                pages = _infer_source_pages(
                    idx,
                    pages_per_group=pages_per_group,
                    total_pages=total_pages,
                )
            source_pages_by_chunk.append(pages)

        # Step 3: Phase 2 re-segmentation on structural headers.
        resegmented_chunks: List[Tuple[str, str, List[int]]] = []
        for i, chunk_text in enumerate(raw_chunks, start=1):
            pages = source_pages_by_chunk[i - 1]
            phase2_results = [(t, b) for t, b in _phase2_resegment_chunk(chunk_text, i) if b.strip()]
            # Suppress fallback page_chunk segments when named sub-segments exist.
            named_count = sum(1 for t, _ in phase2_results if not _FALLBACK_TITLE_RE.match(t))
            for title, body_text in phase2_results:
                if named_count > 0 and _FALLBACK_TITLE_RE.match(title):
                    continue  # no named sub-segments suppressed: parent page_chunk omitted
                resegmented_chunks.append((title, body_text, pages))
        print(f"  phase2 resegment: {len(resegmented_chunks)} candidate segment(s)")

        # Step 4: Build segment data, splitting oversized chunks.
        segments_data: List[Tuple[str, str, str, List[int]]] = []
        for i, (title, chunk_body, source_pages) in enumerate(resegmented_chunks, start=1):
            section_type = _classify_chunk((title + '\n' + chunk_body)[:500])
            chunk_bytes = len(chunk_body.encode('utf-8'))
            if chunk_bytes > effective_max:
                sub_chunks = _size_split(chunk_body, effective_max)
                for k, sub in enumerate(sub_chunks, start=1):
                    t = title if k == 1 else f"{title} (part {k})"
                    segments_data.append((t, section_type, sub, source_pages))
            else:
                display_title = title or _derive_title(chunk_body, i)
                segments_data.append((display_title, section_type, chunk_body, source_pages))

        # Fallback path: no usable chunks after re-segmentation.
        if not segments_data:
            for i, chunk_text in enumerate(raw_chunks, start=1):
                cleaned_chunk = re.sub(r'<!--.*?-->\s*', '', chunk_text, flags=re.DOTALL).strip()
                if not cleaned_chunk:
                    continue
                title = _derive_title(cleaned_chunk, i)
                section_type = _classify_chunk(cleaned_chunk[:500])
                segments_data.append((title, section_type, cleaned_chunk, source_pages_by_chunk[i - 1]))

        # Step 5: Merge segments below min_size into adjacent neighbors (S-003).
        # Protected sections (introduction, discussion, conclusion, abstract) are
        # never merged — they are preserved as standalone entries regardless of size.
        from segment_utils import slugify, write_segment

        kept = _merge_small_segments_in_memory(segments_data, min_size, protected_sections)

        # Step 6: Write segment files.
        written: List[Path] = []
        for i, (title, section_type, body_text, source_pages) in enumerate(kept, start=1):
            slug = slugify(title) or f'seg{i:03d}'
            # Scan segment text for figure and table reference numbers.
            fig_nums = sorted(set(int(m) for m in _FIG_REF_RE.findall(body_text)))
            tbl_nums = sorted(set(int(m) for m in _TBL_REF_RE.findall(body_text)))
            path = write_segment(
                output_dir=output_dir,
                cite_key=cite_key,
                index=i,
                slug=slug,
                section_type=section_type,
                title=title,
                body=body_text,
                source_format=SOURCE_FORMAT,
                source_pages=source_pages,
                figure_numbers=fig_nums or None,
                table_numbers=tbl_nums or None,
            )
            written.append(path)
            print(f"  [{i:03d}] {path.name}")

        return written
