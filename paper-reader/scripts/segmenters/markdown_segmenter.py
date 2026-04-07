"""Markdown section splitter: segments a translated Markdown paper into per-section files."""

import re
from pathlib import Path
from typing import List, Optional, Tuple

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent))
from segment_utils import slugify, write_segment

# Overhead estimate (frontmatter + title heading) in bytes.
_FRONTMATTER_OVERHEAD = 400

SOURCE_FORMAT = 'markdown'


def _find_markdown_source(source_dir: Path) -> Optional[Path]:
    """Find the best markdown source file in source_dir."""
    # Prefer translated_full.md, then any .md file (largest wins)
    preferred = source_dir / 'translated_full.md'
    if preferred.exists():
        return preferred
    candidates = sorted(source_dir.glob('*.md'), key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0] if candidates else None


def _classify_section(title: str) -> str:
    """Map a section title to a coarse section_type tag."""
    t = title.lower()
    if 'introduction' in t:
        return 'introduction'
    if 'background' in t or 'model' in t or 'review' in t:
        return 'background'
    if 'approach' in t or 'concentration' in t or 'inequality' in t or 'theory' in t:
        return 'theory'
    if 'estimation' in t or 'regression' in t or 'lasso' in t or 'oracle' in t:
        return 'methods'
    if 'screen' in t or 'selection' in t or 'penali' in t or 'cluster' in t:
        return 'methods'
    if 'simulation' in t:
        return 'simulation'
    if 'data' in t or 'application' in t or 'real' in t or 'spike' in t:
        return 'application'
    if 'discussion' in t or 'conclusion' in t:
        return 'discussion'
    if 'proof' in t or 'appendix' in t or 'technical' in t or 'auxiliary' in t or 'algorithm' in t:
        return 'appendix'
    return 'section'


def _count_display_eq(text: str) -> int:
    """Count occurrences of $$ in text."""
    return text.count('$$')


def _is_eq_balanced_at(text: str, pos: int) -> bool:
    """Return True if the $$ count in text[:pos] is even (not inside a display block)."""
    return _count_display_eq(text[:pos]) % 2 == 0


def _split_into_sections(content: str) -> List[Tuple[str, str]]:
    """
    Split markdown content into (title, body) pairs at ## heading boundaries.
    Skips the initial frontmatter block and any pre-heading preamble.
    """
    # Strip YAML frontmatter if present
    body = content
    if content.startswith('---'):
        end = content.find('\n---', 3)
        if end != -1:
            body = content[end + 4:]

    section_re = re.compile(r'^## (.+)$', re.MULTILINE)
    matches = list(section_re.finditer(body))

    results: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end() + 1  # skip the newline after the heading
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_body = body[body_start:body_end].strip()
        results.append((title, section_body))

    return results


def _find_subsection_chunks(body: str) -> List[Tuple[Optional[str], str]]:
    """Split body at ### boundaries into (subtitle_or_None, text) pairs."""
    sub_re = re.compile(r'^### (.+)$', re.MULTILINE)
    matches = list(sub_re.finditer(body))
    if not matches:
        return [(None, body)]

    pieces: List[Tuple[Optional[str], str]] = []
    intro = body[:matches[0].start()]
    if intro.strip():
        pieces.append((None, intro.strip()))

    for i, m in enumerate(matches):
        sub_title = m.group(1).strip()
        chunk_start = m.start()
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        pieces.append((sub_title, body[chunk_start:chunk_end].strip()))

    return pieces


def _safe_paragraph_split(body: str, effective_max: int) -> List[str]:
    """
    Split body into chunks each <= effective_max bytes, breaking at paragraph
    boundaries that are outside display equation blocks.
    """
    para_re = re.compile(r'\n\n+')
    boundaries = []
    for m in para_re.finditer(body):
        pos = m.end()
        if _is_eq_balanced_at(body, pos):
            boundaries.append(pos)
    boundaries.append(len(body))

    chunks: List[str] = []
    start = 0
    while start < len(body):
        chunk = body[start:]
        if len(chunk.encode('utf-8')) <= effective_max:
            chunks.append(chunk)
            break

        # Find the largest boundary that fits
        split_at = None
        for b in reversed(boundaries):
            if b <= start:
                continue
            candidate = body[start:b]
            if len(candidate.encode('utf-8')) <= effective_max:
                split_at = b
                break

        if split_at is None or split_at == start:
            # Hard cut at effective_max bytes
            chunk_bytes = body[start:].encode('utf-8')
            cut = len(chunk_bytes[:effective_max].decode('utf-8', errors='ignore'))
            split_at = start + cut

        chunks.append(body[start:split_at].strip())
        start = split_at

    return [c for c in chunks if c.strip()]


def _overflow_split(
    title: str,
    section_type: str,
    body: str,
    max_size: int,
) -> List[Tuple[str, str, str, Optional[str]]]:
    """
    Split (title, body) into chunks each fitting within max_size bytes.
    Returns list of (title, section_type, body_chunk, split_reason).
    """
    effective_max = max_size - _FRONTMATTER_OVERHEAD
    full = f"# {title}\n\n{body}\n"
    if len(full.encode('utf-8')) <= effective_max:
        return [(title, section_type, body, None)]

    # Try subsection boundaries first
    pieces = _find_subsection_chunks(body)
    if len(pieces) > 1:
        results: List[Tuple[str, str, str, Optional[str]]] = []
        current_parts: List[str] = []
        current_size = 0

        for piece_title, piece_text in pieces:
            piece_size = len(piece_text.encode('utf-8'))
            if current_parts and current_size + piece_size > effective_max:
                chunk_body = '\n\n'.join(current_parts)
                chunk_title = title if not results else title
                results.append((chunk_title, section_type, chunk_body, 'subsection_boundary'))
                current_parts = []
                current_size = 0
            current_parts.append(piece_text)
            current_size += piece_size

        if current_parts:
            chunk_body = '\n\n'.join(current_parts)
            results.append((title, section_type, chunk_body, 'subsection_boundary'))

        # Recursively handle any still-oversized chunk
        final: List[Tuple[str, str, str, Optional[str]]] = []
        for r_title, r_type, r_body, r_reason in results:
            if len(f"# {r_title}\n\n{r_body}\n".encode('utf-8')) > effective_max:
                sub_chunks = _safe_paragraph_split(r_body, effective_max)
                for k, sub in enumerate(sub_chunks, start=1):
                    t = r_title if k == 1 else f"{r_title} (part {k})"
                    final.append((t, r_type, sub, 'overflow_split'))
            else:
                final.append((r_title, r_type, r_body, r_reason))
        return final

    # No subsections: paragraph split
    sub_chunks = _safe_paragraph_split(body, effective_max)
    result: List[Tuple[str, str, str, Optional[str]]] = []
    for k, sub in enumerate(sub_chunks, start=1):
        t = title if k == 1 else f"{title} (part {k})"
        result.append((t, section_type, sub, 'overflow_split' if k > 1 else None))
    return result


class MarkdownSegmenter:
    """Segments a translated Markdown paper into per-section files."""

    def segment(
        self,
        cite_key: str,
        source_dir: Path,
        output_dir: Path,
        max_size: int = 16384,
        min_size: int = 1024,
    ) -> List[Path]:
        """
        Locate the translated markdown file in source_dir, split by ## headings,
        apply overflow splitting for large sections, and write segment files.

        Returns the list of written Path objects.
        """
        source_dir = Path(source_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Remove existing segment files for a clean run.
        for old in output_dir.glob('*.md'):
            old.unlink()

        md_source = _find_markdown_source(source_dir)
        if md_source is None:
            raise FileNotFoundError(f"No Markdown source file found under {source_dir}")

        print(f"  md source: {md_source.relative_to(source_dir)}")
        print(f"  bounds   : min={min_size}B  max={max_size}B")

        content = md_source.read_text(encoding='utf-8')
        raw_sections = _split_into_sections(content)

        # Build segment data: (title, section_type, body, split_reason)
        segments_data: List[Tuple[str, str, str, Optional[str]]] = []
        for title, body in raw_sections:
            section_type = _classify_section(title)
            chunks = _overflow_split(title, section_type, body, max_size)
            segments_data.extend(chunks)

        # Filter out segments below min_size; renumber remaining ones.
        kept: List[Tuple[str, str, str, Optional[str]]] = []
        for title, section_type, body, split_reason in segments_data:
            seg_content = f"# {title}\n\n{body}\n"
            if len(seg_content.encode('utf-8')) >= min_size:
                kept.append((title, section_type, body, split_reason))
            else:
                print(f"  [skip] '{title}' ({len(seg_content.encode('utf-8'))}B < {min_size}B min)")

        written: List[Path] = []
        for i, (title, section_type, body, split_reason) in enumerate(kept, start=1):
            slug = slugify(title) or f'seg{i:03d}'
            path = write_segment(
                output_dir=output_dir,
                cite_key=cite_key,
                index=i,
                slug=slug,
                section_type=section_type,
                title=title,
                body=body,
                split_reason=split_reason,
                source_format=SOURCE_FORMAT,
            )
            written.append(path)
            reason_tag = f' [{split_reason}]' if split_reason else ''
            print(f"  [{i:03d}] {path.name}{reason_tag}")

        return written
