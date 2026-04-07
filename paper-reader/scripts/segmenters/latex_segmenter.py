"""LaTeX section splitter: segments a LaTeX paper into per-section Markdown files."""

import re
from pathlib import Path
from typing import List, Optional, Tuple

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent))
from segment_utils import slugify, write_segment

# Environments that must not be split across segment boundaries.
_COHESION_ENVS = ['equation', 'proof', 'align', 'align*', 'lemma', 'theorem',
                  'proposition', 'corollary', 'remark']

# Overhead estimate (frontmatter + title heading) in bytes.
_FRONTMATTER_OVERHEAD = 350


def _strip_latex_comments(text: str) -> str:
    """Remove LaTeX line comments (% ... to end of line, unless % is escaped with \\)."""
    return re.sub(r'(?<!\\)%[^\n]*', '', text)


def _extract_figure_table_numbers(text: str):
    """Return (figure_numbers, table_numbers) as sorted deduplicated int lists.

    Scan order (highest to lowest confidence):
    1. Explicit inline numbers: "Figure 3" / "Table 3"
    2. Cross-reference labels: Figure~\ref{fig:3} — digits extracted from the label key
    3. Fallback: count \\begin{figure} / \\begin{table} environments that contain \\caption{}
    """
    fig_nums: set = set()
    tbl_nums: set = set()

    # 1. Explicit inline numbers
    fig_nums.update(int(n) for n in re.findall(r'[Ff]igure\s+(\d+)', text))
    tbl_nums.update(int(n) for n in re.findall(r'[Tt]able\s+(\d+)', text))

    # 2. Cross-reference labels: Figure~\ref{label} or Figure \ref{label}
    for i, label in enumerate(
        re.findall(r'[Ff]igure[~\s]+\\ref\{([^}]+)\}', text), 1
    ):
        digits = re.findall(r'\d+', label)
        fig_nums.add(int(digits[0]) if digits else i)
    for i, label in enumerate(
        re.findall(r'[Tt]able[~\s]+\\ref\{([^}]+)\}', text), 1
    ):
        digits = re.findall(r'\d+', label)
        tbl_nums.add(int(digits[0]) if digits else i)

    # 3. Fallback: environment blocks with \caption{}
    if not fig_nums:
        for i, env in enumerate(
            re.findall(r'\\begin\{figure[*]?\}.*?\\end\{figure[*]?\}', text, re.DOTALL), 1
        ):
            if r'\caption' in env:
                fig_nums.add(i)
    if not tbl_nums:
        for i, env in enumerate(
            re.findall(r'\\begin\{table[*]?\}.*?\\end\{table[*]?\}', text, re.DOTALL), 1
        ):
            if r'\caption' in env:
                tbl_nums.add(i)

    return sorted(fig_nums), sorted(tbl_nums)


def _find_main_tex(source_dir: Path) -> Optional[Path]:
    """Find the root LaTeX file (contains \\begin{document}) anywhere under source_dir."""
    for tex in sorted(source_dir.rglob('*.tex')):
        try:
            text = tex.read_text(encoding='utf-8', errors='replace')
            if r'\begin{document}' in text:
                return tex
        except OSError:
            continue
    return None


def _resolve_input(path_str: str, base_dir: Path) -> Optional[Path]:
    """
    Resolve a \\input{path_str} reference relative to base_dir.
    Falls back to any .tex file with \\section{ in the same subdirectory.
    """
    path_str = path_str.strip()
    for suffix in ('', '.tex'):
        candidate = base_dir / (path_str + suffix)
        if candidate.exists():
            return candidate

    # Fallback: look in the referenced subdirectory for a .tex file with \section{
    parts = path_str.replace('\\', '/').split('/')
    if len(parts) > 1:
        subdir = base_dir / '/'.join(parts[:-1])
        if subdir.is_dir():
            section_files = [
                f for f in sorted(subdir.glob('*.tex'))
                if r'\section{' in f.read_text(encoding='utf-8', errors='replace')
            ]
            if section_files:
                return section_files[-1]
    return None


def _expand_inputs(content: str, base_dir: Path, _visited: Optional[set] = None) -> str:
    """Recursively expand \\input{} directives, avoiding cycles."""
    if _visited is None:
        _visited = set()

    def replace_input(m: re.Match) -> str:
        path_str = m.group(1).strip()
        resolved = _resolve_input(path_str, base_dir)
        if resolved is None:
            return f"% [INPUT NOT FOUND: {path_str}]\n"
        abs_path = resolved.resolve()
        if abs_path in _visited:
            return f"% [INPUT CYCLE: {path_str}]\n"
        _visited.add(abs_path)
        try:
            sub = resolved.read_text(encoding='utf-8', errors='replace')
            return _expand_inputs(sub, resolved.parent, _visited)
        except OSError:
            return f"% [INPUT ERROR: {path_str}]\n"

    return re.sub(r'\\input\{([^}]+)\}', replace_input, content)


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
    if 'screen' in t or 'selection' in t:
        return 'methods'
    if 'simulation' in t:
        return 'simulation'
    if 'data' in t or 'application' in t or 'real' in t:
        return 'application'
    if 'discussion' in t or 'conclusion' in t:
        return 'discussion'
    if 'proof' in t or 'appendix' in t or 'technical' in t or 'auxiliary' in t:
        return 'appendix'
    return 'section'


def _extract_abstract(content: str) -> Optional[Tuple[str, str]]:
    """
    Extract (paper_title, abstract_text) from a \\begin{frontmatter}...\\end{frontmatter} block.
    Returns None if not found.
    """
    fm_m = re.search(r'\\begin\{frontmatter\}(.*?)\\end\{frontmatter\}', content, re.DOTALL)
    if not fm_m:
        return None
    fm = fm_m.group(1)
    abstract_m = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', fm, re.DOTALL)
    abstract_text = abstract_m.group(1).strip() if abstract_m else ''
    title_m = re.search(r'\\title\{(.*?)\}', fm, re.DOTALL)
    paper_title = re.sub(r'\s+', ' ', title_m.group(1)).strip() if title_m else 'Abstract'
    return (paper_title, abstract_text)


def _split_on_sections(content: str) -> List[Tuple[str, str, bool]]:
    """
    Split fully-expanded LaTeX content into (title, body, is_appendix) tuples.
    Sections appearing after \\appendix are flagged as appendix.
    """
    appendix_pos: Optional[int] = None
    appendix_m = re.search(r'\\appendix\b', content)
    if appendix_m:
        appendix_pos = appendix_m.start()

    section_re = re.compile(r'\\section\*?\{([^}]+)\}')
    matches = list(section_re.finditer(content))

    results: List[Tuple[str, str, bool]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[body_start:body_end].strip()
        is_appendix = appendix_pos is not None and m.start() > appendix_pos
        results.append((title, body, is_appendix))

    return results


def _is_safe_position(text: str, pos: int) -> bool:
    """Return True if pos is outside all cohesion LaTeX environments."""
    prefix = text[:pos]
    for env in _COHESION_ENVS:
        opens = prefix.count(f'\\begin{{{env}}}')
        closes = prefix.count(f'\\end{{{env}}}')
        if opens > closes:
            return False
    return True


def _find_subsection_chunks(
    body: str,
) -> List[Tuple[Optional[str], str]]:
    """
    Split body into (subsection_title_or_None, chunk_text) pieces at \\subsection boundaries.
    The first piece may have title=None (intro text before first subsection).
    """
    subsec_re = re.compile(r'\\subsection\*?\{([^}]+)\}')
    matches = list(subsec_re.finditer(body))
    if not matches:
        return [(None, body)]

    pieces: List[Tuple[Optional[str], str]] = []

    # Intro text before first subsection
    intro = body[:matches[0].start()]
    if intro.strip():
        pieces.append((None, intro))

    for i, m in enumerate(matches):
        sub_title = m.group(1).strip()
        chunk_start = m.start()
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        pieces.append((sub_title, body[chunk_start:chunk_end]))

    return pieces


def _forced_split(
    title: str, section_type: str, body: str, max_size: int
) -> List[Tuple[str, str, str, str]]:
    """
    Split a single (title, body) at safe paragraph boundaries to fit within max_size.
    Returns list of (title, section_type, body_chunk, split_reason) tuples.
    """
    effective_max = max_size - _FRONTMATTER_OVERHEAD
    results: List[Tuple[str, str, str, str]] = []

    # Find safe paragraph boundary positions (outside cohesion envs)
    para_re = re.compile(r'\n\n+')
    boundaries = [m.start() for m in para_re.finditer(body) if _is_safe_position(body, m.start())]
    boundaries.append(len(body))

    part_num = 1
    start = 0
    while start < len(body):
        # Find largest chunk that fits within effective_max
        chunk = body[start:]
        if len(chunk.encode('utf-8')) <= effective_max:
            t = title if part_num == 1 else f"{title} (part {part_num})"
            results.append((t, section_type, chunk, 'overflow_split'))
            break

        # Binary-search friendly: scan boundaries from largest to smallest
        split_at = None
        for b in reversed(boundaries):
            if b <= start:
                continue
            candidate = body[start:b]
            if len(candidate.encode('utf-8')) <= effective_max:
                split_at = b
                break

        if split_at is None or split_at == start:
            # No safe boundary found; hard-cut at effective_max bytes
            chunk_bytes = body[start:].encode('utf-8')
            cut = len(chunk_bytes[:effective_max].decode('utf-8', errors='ignore'))
            split_at = start + cut

        t = title if part_num == 1 else f"{title} (part {part_num})"
        results.append((t, section_type, body[start:split_at], 'overflow_split'))
        start = split_at
        part_num += 1

    return results


def _overflow_split(
    title: str,
    section_type: str,
    body: str,
    max_size: int,
) -> List[Tuple[str, str, str, Optional[str]]]:
    """
    Split (title, section_type, body) into chunks each fitting within max_size bytes.
    First tries subsection boundaries; falls back to forced paragraph splits.

    Returns list of (title, section_type, body, split_reason) tuples.
    """
    effective_max = max_size - _FRONTMATTER_OVERHEAD
    full_content = f"# {title}\n\n{body}\n"
    if len(full_content.encode('utf-8')) <= effective_max:
        return [(title, section_type, body, None)]

    # Try splitting at subsection boundaries
    pieces = _find_subsection_chunks(body)

    if len(pieces) > 1:
        # Greedily pack pieces into chunks
        results: List[Tuple[str, str, str, Optional[str]]] = []
        current_title: Optional[str] = None
        current_parts: List[str] = []
        current_size = 0

        for piece_title, piece_text in pieces:
            piece_size = len(piece_text.encode('utf-8'))

            if current_parts and current_size + piece_size > effective_max:
                # Flush
                chunk_body = ''.join(current_parts)
                chunk_title = title if not results else (current_title or title)
                results.append((chunk_title, section_type, chunk_body, 'subsection_boundary'))
                current_parts = []
                current_size = 0
                current_title = piece_title

            if not current_parts:
                current_title = piece_title if piece_title else title

            current_parts.append(piece_text)
            current_size += piece_size

        if current_parts:
            chunk_body = ''.join(current_parts)
            chunk_title = title if not results else (current_title or title)
            results.append((chunk_title, section_type, chunk_body, 'subsection_boundary'))

        # Recursively handle any chunk still over max_size
        final: List[Tuple[str, str, str, Optional[str]]] = []
        for r_title, r_type, r_body, r_reason in results:
            content = f"# {r_title}\n\n{r_body}\n"
            if len(content.encode('utf-8')) > effective_max:
                sub = _forced_split(r_title, r_type, r_body, max_size)
                final.extend(sub)
            else:
                final.append((r_title, r_type, r_body, r_reason))
        return final

    # No subsections — forced paragraph split
    return _forced_split(title, section_type, body, max_size)


class LatexSegmenter:
    """Segments a LaTeX paper into per-section Markdown files."""

    def segment(
        self,
        cite_key: str,
        source_dir: Path,
        output_dir: Path,
        max_size: int = 16384,
        min_size: int = 1024,
    ) -> List[Path]:
        """
        Locate the main .tex file in source_dir, expand \\input{} directives,
        split by \\section{}, apply overflow splitting for large sections,
        and write segment files into output_dir.

        Returns the list of written Path objects.
        """
        source_dir = Path(source_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Remove existing segment files so a fresh run doesn't leave stale files.
        for old in output_dir.glob('*.md'):
            old.unlink()

        main_tex = _find_main_tex(source_dir)
        if main_tex is None:
            raise FileNotFoundError(f"No main LaTeX file found under {source_dir}")

        print(f"  main tex: {main_tex.relative_to(source_dir)}")
        print(f"  bounds  : min={min_size}B  max={max_size}B")
        raw = main_tex.read_text(encoding='utf-8', errors='replace')
        expanded = _expand_inputs(raw, main_tex.parent)

        # (title, section_type, body, split_reason)
        segments_data: List[Tuple[str, str, str, Optional[str]]] = []

        # Segment 0: abstract / frontmatter
        abstract_info = _extract_abstract(expanded)
        if abstract_info:
            paper_title, abstract_body = abstract_info
            segments_data.append(('Abstract', 'abstract',
                                   _strip_latex_comments(abstract_body), None))

        # Remaining segments: one per \section{}, with overflow splitting
        for title, body, is_appendix in _split_on_sections(expanded):
            section_type = 'appendix' if is_appendix else _classify_section(title)
            # Strip LaTeX line comments so commented-out env markers don't break balance.
            clean_body = _strip_latex_comments(body)
            chunks = _overflow_split(title, section_type, clean_body, max_size)
            segments_data.extend(chunks)

        written: List[Path] = []
        for i, (title, section_type, body, split_reason) in enumerate(segments_data, start=1):
            slug = slugify(title) or f'seg{i:03d}'
            figure_numbers, table_numbers = _extract_figure_table_numbers(body)
            path = write_segment(
                output_dir=output_dir,
                cite_key=cite_key,
                index=i,
                slug=slug,
                section_type=section_type,
                title=title,
                body=body,
                split_reason=split_reason,
                figure_numbers=figure_numbers,
                table_numbers=table_numbers,
            )
            written.append(path)
            reason_tag = f' [{split_reason}]' if split_reason else ''
            print(f"  [{i:03d}] {path.name}{reason_tag}")

        return written
