"""HTML-derived markdown segmenter: splits at h2 boundaries with h3 and paragraph fallback."""

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from segment_utils import slugify, write_segment

# Overhead estimate (frontmatter + title heading) in bytes.
_FRONTMATTER_OVERHEAD = 400

SOURCE_FORMAT = 'html'


def _find_html_source(source_dir: Path) -> Optional[Path]:
    """Find the translated markdown source for an HTML-sourced paper."""
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
    if 'theory' in t or 'concentration' in t or 'inequality' in t or 'approach' in t:
        return 'theory'
    if 'estimation' in t or 'method' in t or 'regression' in t or 'lasso' in t:
        return 'methods'
    if 'simulation' in t:
        return 'simulation'
    if 'data' in t or 'application' in t or 'real' in t or 'spike' in t:
        return 'application'
    if 'discussion' in t or 'conclusion' in t:
        return 'discussion'
    if 'reference' in t or 'appendix' in t or 'proof' in t or 'algorithm' in t:
        return 'appendix'
    return 'section'


def _strip_frontmatter(content: str) -> str:
    """Strip YAML frontmatter block if present."""
    if content.startswith('---'):
        end = content.find('\n---', 3)
        if end != -1:
            return content[end + 4:]
    return content


def _split_by_h2(content: str) -> List[Tuple[str, str]]:
    """Split content at ## (h2) boundaries. Returns list of (title, body) pairs."""
    body = _strip_frontmatter(content)
    section_re = re.compile(r'^## (.+)$', re.MULTILINE)
    matches = list(section_re.finditer(body))
    results: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end() + 1
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_body = body[body_start:body_end].strip()
        results.append((title, section_body))
    return results


def _split_by_h3(content: str) -> List[Tuple[str, str]]:
    """Split content at ### (h3) boundaries. Returns list of (title, body) pairs."""
    body = _strip_frontmatter(content)
    section_re = re.compile(r'^### (.+)$', re.MULTILINE)
    matches = list(section_re.finditer(body))
    results: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end() + 1
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_body = body[body_start:body_end].strip()
        results.append((title, section_body))
    return results


def _split_by_paragraph(content: str) -> List[Tuple[str, str]]:
    """Split content at blank-line paragraph boundaries as last resort."""
    body = _strip_frontmatter(content).strip()
    paras = re.split(r'\n\n+', body)
    results: List[Tuple[str, str]] = []
    for i, para in enumerate(paras, start=1):
        para = para.strip()
        if para:
            results.append((f'Section {i}', para))
    return results


def _safe_paragraph_split(body: str, effective_max: int) -> List[str]:
    """Split body into chunks each <= effective_max bytes at paragraph boundaries."""
    para_re = re.compile(r'\n\n+')
    boundaries = [m.end() for m in para_re.finditer(body)]
    boundaries.append(len(body))

    chunks: List[str] = []
    start = 0
    while start < len(body):
        chunk = body[start:]
        if len(chunk.encode('utf-8')) <= effective_max:
            chunks.append(chunk)
            break
        split_at = None
        for b in reversed(boundaries):
            if b <= start:
                continue
            candidate = body[start:b]
            if len(candidate.encode('utf-8')) <= effective_max:
                split_at = b
                break
        if split_at is None or split_at == start:
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
    """Split a section that exceeds max_size into paragraph-boundary chunks."""
    effective_max = max_size - _FRONTMATTER_OVERHEAD
    full = f"# {title}\n\n{body}\n"
    if len(full.encode('utf-8')) <= effective_max:
        return [(title, section_type, body, None)]

    sub_chunks = _safe_paragraph_split(body, effective_max)
    result: List[Tuple[str, str, str, Optional[str]]] = []
    for k, sub in enumerate(sub_chunks, start=1):
        t = title if k == 1 else f"{title} (part {k})"
        result.append((t, section_type, sub, 'overflow_split' if k > 1 else None))
    return result


class HtmlSegmenter:
    """Segments an HTML-derived markdown paper at h2 boundaries with fallback."""

    def segment(
        self,
        cite_key: str,
        source_dir: Path,
        output_dir: Path,
        max_size: int = 16384,
        min_size: int = 1024,
    ) -> List[Path]:
        """
        Locate translated_full.md in source_dir, split by ## headings (h2).
        Fallback: if no h2 -> split by ### (h3) with warning.
        Fallback: if no headings -> split by paragraph with warning.
        Write segment files with source_format: html in frontmatter.
        Returns list of written Path objects.
        """
        source_dir = Path(source_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for old in output_dir.glob('*.md'):
            old.unlink()

        md_source = _find_html_source(source_dir)
        if md_source is None:
            raise FileNotFoundError(f"No markdown source found under {source_dir}")

        print(f"  html source: {md_source.relative_to(source_dir)}")
        print(f"  bounds     : min={min_size}B  max={max_size}B")

        content = md_source.read_text(encoding='utf-8')

        # Primary: h2 split
        raw_sections = _split_by_h2(content)
        if raw_sections:
            split_strategy = 'h2'
        else:
            # Fallback 1: h3 split
            raw_sections = _split_by_h3(content)
            if raw_sections:
                split_strategy = 'h3'
                print(
                    f"  [warn] No h2 sections in {md_source.name}; falling back to h3",
                    file=sys.stderr,
                )
            else:
                # Fallback 2: paragraph split
                raw_sections = _split_by_paragraph(content)
                split_strategy = 'paragraph'
                print(
                    f"  [warn] No headings in {md_source.name}; falling back to paragraph split",
                    file=sys.stderr,
                )

        print(f"  strategy   : {split_strategy} ({len(raw_sections)} raw sections)")

        segments_data: List[Tuple[str, str, str, Optional[str]]] = []
        for title, body in raw_sections:
            section_type = _classify_section(title)
            chunks = _overflow_split(title, section_type, body, max_size)
            segments_data.extend(chunks)

        kept: List[Tuple[str, str, str, Optional[str]]] = []
        for title, section_type, body, split_reason in segments_data:
            seg_content = f"# {title}\n\n{body}\n"
            size = len(seg_content.encode('utf-8'))
            # Only apply min-size to overflow sub-fragments, not primary split sections.
            if split_reason == 'overflow_split' and size < min_size:
                print(f"  [skip] '{title}' overflow fragment ({size}B < {min_size}B min)")
            else:
                kept.append((title, section_type, body, split_reason))

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
