#!/usr/bin/env python3
"""Translate a LaTeX paper source tree into a single markdown file.

This script targets paper-bank layouts created by paper-reader:
  $PAPER_BANK/<cite_key>/
    raw/ (optional)
    source/ (optional)

It performs:
1) Root TeX detection (find a file containing \\documentclass + \\begin{document})
2) Lightweight \\input/\\include assembly (recursive, path-resolving)
3) Macro expansion using expand_macros.py (preamble-only, simple macros)
4) Pandoc conversion to GitHub-flavored markdown with $$ display math
5) Post-processing theorem-like blocks into blockquote convention
6) Emit translated_full.md with YAML frontmatter

For multi-file PDF sources (for example a main paper plus `*-supp.pdf`),
run separate invocations of this script for each file to keep downstream
page-based metadata and segmentation coherent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from cite_key_resolver import migrate_alias_dir_to_canonical, resolve_cite_key
from translation_utils import _score_root_tex, compute_common_root_name


SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate a LaTeX paper into markdown (translated_full.md).")
    parser.add_argument("--cite-key", required=True, help="Cite key used for the paper-bank folder")
    parser.add_argument(
        "--paper-bank-dir",
        required=True,
        help="Paper directory under paper-bank (e.g., $PAPER_BANK/<cite-key>)",
    )
    parser.add_argument(
        "--format",
        default="auto",
        choices=("auto", "pandoc", "pdf", "html"),
        help="Translation backend. 'pdf' forces the PDF extraction pipeline; "
             "'html' converts a local HTML file via the HTML translator; "
             "'auto' and 'pandoc' use the LaTeX/pandoc path.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output path. Default: <paper-bank-dir>/translated_full.md",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep intermediate files in a temp directory (prints path).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="For --format pdf: exit 0 immediately if translated_full.md already exists.",
    )
    parser.add_argument(
        "--pdf-pages-per-group",
        type=int,
        default=3,
        help="Pages per chunk for the PDF pipeline (default: 3).",
    )
    return parser.parse_args()


# Allowlist: only these directives cause the referenced file to be inlined.
# Any other include-like directive (e.g. \externaldocument, \includeonly) is
# detected by the same pattern but skipped with a warning (see _assemble_tex).
_ASSEMBLY_INLINE_ALLOWLIST = {"input", "include", "subfile"}
_ASSEMBLY_DIRECTIVE_RE = re.compile(
    r"\\(input|include|subfile|externaldocument|includeonly)(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}"
)


def _split_comment(line: str) -> tuple[str, str]:
    """Split line into (code, comment) where comment begins at first unescaped %."""
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "%":
            return line[:idx], line[idx:]
    return line, ""


def _fuzzy_find_tex(stem: str, directory: Path) -> Path | None:
    """Return the best-matching .tex file in *directory* when exact name fails.

    Scores candidates by the length of the common leading character sequence
    with *stem*.  Returns None when no candidate shares at least 4 characters.
    """
    if not directory.is_dir():
        return None
    candidates = list(directory.glob("*.tex"))
    if not candidates:
        return None

    def _prefix_score(p: Path) -> int:
        s = p.stem
        score = 0
        for a, b in zip(stem, s):
            if a == b:
                score += 1
            else:
                break
        return score

    best = max(candidates, key=_prefix_score)
    if _prefix_score(best) >= min(4, len(stem)):
        return best
    return None


def _resolve_include_path(arg: str, base_dir: Path) -> Path | None:
    raw = arg.strip()
    # Strip surrounding quotes sometimes produced in LaTeX sources.
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1]
    candidate = (base_dir / raw).expanduser()
    if candidate.suffix.lower() == ".aux":
        aux_tex = candidate.with_suffix(".tex")
        if aux_tex.exists():
            return aux_tex
        candidate = aux_tex
    if candidate.suffix.lower() != ".tex":
        candidate_tex = candidate.with_suffix(".tex")
        if candidate_tex.exists():
            return candidate_tex
    if candidate.exists():
        return candidate
    # Some sources omit path separators but include extension.
    if not candidate.suffix and (candidate.parent / (candidate.name + ".tex")).exists():
        return candidate.parent / (candidate.name + ".tex")
    # Fuzzy fallback: file doesn't exist with the exact name; find the closest
    # match in the same directory (handles versioned filenames like
    # intro-AoS-v4 vs intro-AoSr1-v4.tex).
    stem = candidate.stem if candidate.suffix else candidate.name
    fuzzy = _fuzzy_find_tex(stem, candidate.parent)
    if fuzzy is not None:
        return fuzzy
    return None


def _assemble_tex(path: Path, *, visited: set[Path], stack: list[Path]) -> str:
    resolved = path.resolve()
    if resolved in stack:
        cycle = " -> ".join(p.name for p in stack + [resolved])
        return f"% [translate_paper] cycle detected; skipping: {cycle}\n"
    if resolved in visited:
        return f"% [translate_paper] already included; skipping: {path}\n"

    visited.add(resolved)
    stack.append(resolved)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        stack.pop()
        return f"% [translate_paper] missing include: {path}\n"

    out: list[str] = []
    base_dir = path.parent

    for raw_line in text.splitlines(keepends=True):
        code, comment = _split_comment(raw_line)
        cursor = 0
        while True:
            match = _ASSEMBLY_DIRECTIVE_RE.search(code, cursor)
            if not match:
                out.append(code[cursor:] + comment)
                break

            out.append(code[cursor:match.start()])
            directive = match.group(1)
            include_arg = match.group(2)

            if directive not in _ASSEMBLY_INLINE_ALLOWLIST:
                print(
                    f"[translate_paper] warning: skipping non-allowlisted include-like directive "
                    f"\\{directive}{{{include_arg}}} — not inlining referenced file",
                    file=sys.stderr,
                )
                out.append(match.group(0))
                cursor = match.end()
                continue

            include_path = _resolve_include_path(include_arg, base_dir)
            if include_path is None:
                out.append(match.group(0) + comment)
                break

            out.append(
                f"\n% [translate_paper] BEGIN {directive} "
                f"{include_path.relative_to(base_dir) if include_path.is_relative_to(base_dir) else include_path}\n"
            )
            out.append(_assemble_tex(include_path, visited=visited, stack=stack))
            out.append(f"% [translate_paper] END {directive} {include_path.name}\n")
            cursor = match.end()

    stack.pop()
    return "".join(out)


def _pick_source_root(paper_bank_dir: Path) -> Path:
    for name in ("raw", "source"):
        candidate = paper_bank_dir / name
        if candidate.exists() and candidate.is_dir():
            return candidate
    return paper_bank_dir


def _detect_root_tex(source_root: Path) -> Path:
    tex_files = sorted(source_root.rglob("*.tex"))
    if not tex_files:
        raise FileNotFoundError(f"No .tex files found under: {source_root}")
    common_root_name = compute_common_root_name(tex_files)
    scored = sorted(
        ((_score_root_tex(p, common_root_name=common_root_name), p) for p in tex_files),
        reverse=True,
        key=lambda t: (t[0], -len(t[1].parts), str(t[1])),
    )
    best_score, best = scored[0]
    if best_score < 5:
        # Still return something deterministic, but warn loudly.
        raise ValueError(f"Could not confidently detect root TeX (best candidate: {best} score={best_score}).")
    return best


def _run_expand_macros(input_tex: Path, output_tex: Path, warnings_log: Path) -> None:
    script = SCRIPT_DIR / "expand_macros.py"
    cmd = [
        sys.executable,
        str(script),
        "--input",
        str(input_tex),
        "--output",
        str(output_tex),
        "--warnings-log",
        str(warnings_log),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"expand_macros.py failed ({proc.returncode}): {proc.stderr.strip()}")


_VSKIP_RE = re.compile(
    r"\\vskip\s*(?:\\[A-Za-z@]+|\d+(?:\.\d+)?(?:pt|em|ex|cm|mm|in|bp|cc|dd|sp|pc)?)"
)
_REMAINING_INPUT_RE = re.compile(r"(?m)^([ \t]*\\(?:input|include)\s*\{[^}]*\}[ \t]*)$")


def _sanitize_tex_for_pandoc(tex_text: str) -> str:
    """Remove/replace constructs that cause pandoc's LaTeX reader to abort.

    1. Strip ``\\vskip<dimen>`` everywhere (pandoc cannot parse ``\\vskip`` in
       ``\\newenvironment`` end-code, which causes a hard parse error on every
       ``\\end{proof}`` call).
    2. Comment out any ``\\input``/``\\include`` lines that were not inlined
       during assembly (unresolved includes cause pandoc exit 64).
    """
    tex_text = _VSKIP_RE.sub("", tex_text)
    tex_text = _REMAINING_INPUT_RE.sub(
        lambda m: f"% [translate_paper sanitized] {m.group(1).strip()}",
        tex_text,
    )
    return tex_text


def _run_pandoc(input_tex: Path, output_md: Path) -> None:
    cmd = [
        "pandoc",
        str(input_tex),
        "--from",
        "latex",
        "--to",
        "gfm+tex_math_dollars",
        "--wrap=none",
        "--markdown-headings=atx",
        "--shift-heading-level-by=1",
        "--output",
        str(output_md),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed ({proc.returncode}): {proc.stderr.strip()}")


def _load_equation_numberer() -> callable:
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from number_equations import process_markdown  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Failed to import number_equations.py: {exc}") from exc
    return process_markdown


def _load_theorem_indexer() -> callable:
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from build_theorem_index import build_theorem_index  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Failed to import build_theorem_index.py: {exc}") from exc
    return build_theorem_index


def _load_formatter() -> callable:
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from format_theorems import format_theorem_blocks  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Failed to import format_theorems.py: {exc}") from exc
    return format_theorem_blocks


def _load_macro_harvester() -> callable:
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from expand_macros import harvest_macro_definitions  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Failed to import expand_macros.py macro harvester: {exc}") from exc
    return harvest_macro_definitions


def _yaml_escape(value: str) -> str:
    value = value.replace("\n", " ").strip()
    if not value:
        return '""'
    if any(ch in value for ch in [":", "{", "}", "[", "]", "#", "&", "*", "!", "|", ">", "%", "@", "`", '"', "'"]):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _extract_preamble_field(tex_text: str, field_name: str) -> str:
    """Extract content of a LaTeX command like \\title{...}, handling nested braces."""
    pattern = re.compile(rf"\\{re.escape(field_name)}\s*\{{")
    m = pattern.search(tex_text)
    if not m:
        return ""
    start = m.end()
    depth = 1
    i = start
    while i < len(tex_text) and depth > 0:
        c = tex_text[i]
        if c == "\\" and i + 1 < len(tex_text):
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return tex_text[start : i - 1].strip()


def _is_macro_only(value: str) -> bool:
    """Return True if *value* is empty or consists only of LaTeX formatting macros."""
    cleaned = re.sub(r"\\[A-Za-z@]+\s*(?:\{[^{}]*\})*", "", value).strip()
    return not bool(re.search(r"[A-Za-z0-9]", cleaned))


def _extract_preamble_metadata(tex_text: str) -> dict[str, str]:
    r"""Extract \author, \title, and \date from the LaTeX preamble.

    Handles standard LaTeX \author{...} as well as ICML \icmlauthor{Name}{affil}.
    Returns a dict with whichever fields could be found and are not macro-only.
    """
    preamble_m = re.search(r"\\begin\{document\}", tex_text)
    preamble = tex_text[: preamble_m.start()] if preamble_m else tex_text
    result: dict[str, str] = {}
    for field in ("title", "author", "date"):
        value = _extract_preamble_field(preamble, field)
        if value and not _is_macro_only(value):
            result[field] = re.sub(r"\s+", " ", value)
    # Fallback: ICML papers use \icmlauthor{Name}{affil} instead of \author{...}
    if "author" not in result:
        icml_names = re.findall(r"\\icmlauthor\{([^}]+)\}", preamble)
        if icml_names:
            result["author"] = " and ".join(icml_names)
    return result


def _load_catalog_metadata(paper_bank_dir: Path) -> dict[str, str]:
    """Return title/author/date from _catalog.yaml if present."""
    catalog_path = paper_bank_dir / "_catalog.yaml"
    if not catalog_path.exists():
        return {}
    try:
        text = catalog_path.read_text(encoding="utf-8", errors="replace")
        result: dict[str, str] = {}
        for line in text.splitlines():
            m = re.match(r"^(title|author|date):\s*(.+)", line, re.IGNORECASE)
            if m:
                val = m.group(2).strip().strip("\"'")
                if val:
                    result[m.group(1).lower()] = val
        return result
    except Exception:
        return {}


def _extract_macro_names(macro_definitions: list[str]) -> list[str]:
    """Extract macro names from a list of LaTeX definition strings."""
    names: list[str] = []
    for defn in macro_definitions:
        m = re.match(r"\\(?:newcommand|renewcommand|providecommand)\s*\{?\\([A-Za-z@]+)\}?", defn.strip())
        if not m:
            m = re.match(r"\\def\s*\\([A-Za-z@]+)", defn.strip())
        if m:
            names.append("\\" + m.group(1))
    return sorted(set(names))


_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_GFM_MATH_BLOCK_RE = re.compile(r"^``` ?math\n(.*?)^```", re.MULTILINE | re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<!\\)\$(?!\$)([^$\n]|\\\$)+(?<!\\)\$")
# Display-math environments that Pandoc may emit as bare LaTeX without $$ delimiters.
_DISPLAY_MATH_ENV_RE = re.compile(
    r"^\\begin\{"
    r"(align\*?|aligned\*?|equation\*?|gather\*?|multline\*?|"
    r"flalign\*?|eqnarray\*?|IEEEeqnarray\*?|split\*?)"
    r"\}"
)


def _convert_gfm_math_to_dollars(markdown: str) -> str:
    """Convert pandoc 3.x GFM ```math blocks to $$ notation.

    Pandoc 3+ outputs display math as ```math...``` in GFM mode.
    This converts them to $$...$$ so downstream tools and verification
    checks that look for '$$' work correctly.
    """
    def _replace(m: re.Match) -> str:
        inner = m.group(1)
        if not inner.endswith("\n"):
            inner += "\n"
        return f"$$\n{inner}$$"

    return _GFM_MATH_BLOCK_RE.sub(_replace, markdown)


def _wrap_bare_display_math(markdown: str) -> str:
    """Add $$ delimiters around display-math environments that lack them.

    Pandoc sometimes emits ``\\begin{align}...\\end{align}`` (and similar
    environments) as bare LaTeX without enclosing ``$$`` delimiters, causing
    renderers to display raw LaTeX instead of formatted equations.  This
    post-processing pass detects such blocks and wraps them with ``$$``.

    Blocks that are already enclosed in a ``$$`` pair are left unchanged.
    Lines inside code fences are skipped.
    """
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    in_code_fence = False
    in_display_math = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip("\n").strip()

        # Track code fences (only outside display-math blocks).
        if not in_display_math and _FENCE_RE.match(stripped):
            in_code_fence = not in_code_fence
            out.append(line)
            i += 1
            continue

        if in_code_fence:
            out.append(line)
            i += 1
            continue

        # Track $$ blocks so we never double-wrap.
        if stripped == "$$":
            in_display_math = not in_display_math
            out.append(line)
            i += 1
            continue

        if in_display_math:
            out.append(line)
            i += 1
            continue

        # Detect a bare display-math environment (not yet wrapped in $$).
        env_match = _DISPLAY_MATH_ENV_RE.match(stripped)
        if not env_match:
            out.append(line)
            i += 1
            continue

        # Collect the full environment block up to and including \end{env}.
        env_name = env_match.group(1)
        end_marker = f"\\end{{{env_name}}}"
        block_lines: list[str] = [line]
        i += 1
        while i < len(lines):
            block_lines.append(lines[i])
            if end_marker in lines[i]:
                i += 1
                break
            i += 1

        # Emit the block wrapped in $$ delimiters.
        out.append("$$\n")
        out.extend(block_lines)
        if block_lines and not block_lines[-1].endswith("\n"):
            out.append("\n")
        out.append("$$\n")

    return "".join(out)


_DISPLAY_MATH_ENV_NAMES = (
    "align", "align*", "aligned", "aligned*",
    "multline", "multline*",
    "gather", "gather*",
    "flalign", "flalign*",
    "eqnarray", "eqnarray*",
    "IEEEeqnarray", "IEEEeqnarray*",
    "split", "split*",
    "equation", "equation*",
)

_DISPLAY_ENV_TAG_RE = re.compile(
    r"^\\(?:begin|end)\{("
    + "|".join(re.escape(e) for e in _DISPLAY_MATH_ENV_NAMES)
    + r")\}\s*$"
)


def _strip_display_math_env_tags(markdown: str) -> str:
    r"""Strip \begin{<env>} / \end{<env>} tags from inside $$ blocks.

    Pandoc wraps display-math environments in ``$$`` delimiters but retains
    the inner ``\begin{align}...\end{align}`` (and variant) tags.  This pass
    removes those redundant environment tags so renderers receive clean math
    content without the outer LaTeX wrapper.

    Environments stripped: align, align*, aligned, multline, multline*,
    gather, gather*, flalign, eqnarray (and starred / related variants).
    Tags outside $$ blocks are left unchanged.
    """
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    in_display_math = False
    in_code_fence = False

    for line in lines:
        stripped = line.rstrip("\n").strip()

        if not in_display_math and _FENCE_RE.match(stripped):
            in_code_fence = not in_code_fence
            out.append(line)
            continue

        if in_code_fence:
            out.append(line)
            continue

        if stripped == "$$":
            in_display_math = not in_display_math
            out.append(line)
            continue

        if in_display_math and _DISPLAY_ENV_TAG_RE.match(stripped):
            # Drop redundant env tag inside $$ block
            continue

        out.append(line)

    return "".join(out)


def _normalize_inline_math(markdown: str) -> str:
    r"""Convert \(...\) and \[...\] math notation to $...$ and $$...$$ notation."""
    markdown = re.sub(r"\\\((.+?)\\\)", lambda m: f"${m.group(1)}$", markdown, flags=re.DOTALL)
    markdown = re.sub(r"\\\[(.+?)\\\]", lambda m: f"$$\n{m.group(1).strip()}\n$$", markdown, flags=re.DOTALL)
    return markdown


def _markdown_contains_math(markdown: str) -> bool:
    if "$$" in markdown:
        return True
    if r"\(" in markdown or r"\[" in markdown:
        return True
    return bool(_INLINE_MATH_RE.search(markdown))


def _build_macro_preamble_block(macro_definitions: list[str]) -> str:
    lines = [
        "<!-- preamble: latex-macros -->",
        "$$",
    ]
    lines.extend(macro_definitions)
    lines.extend(["$$", ""])
    return "\n".join(lines)


def _inject_macro_preamble(markdown: str, macro_definitions: list[str]) -> str:
    if not macro_definitions or not _markdown_contains_math(markdown):
        return markdown

    preamble_block = _build_macro_preamble_block(macro_definitions)
    lines = markdown.splitlines()
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                head = "\n".join(lines[: idx + 1])
                body = "\n".join(lines[idx + 1 :]).lstrip("\n")
                if body:
                    return f"{head}\n\n{preamble_block}\n{body}"
                return f"{head}\n\n{preamble_block}\n"
    return f"{preamble_block}\n{markdown.lstrip()}"


def _strip_yaml_frontmatter(markdown: str) -> str:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1 :]).lstrip("\n")
    return markdown


def _count_sections(markdown: str) -> int:
    count = 0
    for line in markdown.splitlines():
        if re.match(r"^\s*##\s+\S", line) and not re.match(r"^\s*###\s+", line):
            count += 1
    return count


def _count_words(markdown: str) -> int:
    body = _strip_yaml_frontmatter(markdown)
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", body))


def _count_display_math_blocks(markdown: str) -> int:
    lines = markdown.splitlines()
    in_fence = False
    idx = 0
    count = 0
    while idx < len(lines):
        line = lines[idx]
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            idx += 1
            continue
        if in_fence:
            idx += 1
            continue

        stripped = line.strip()
        if stripped == "$$":
            idx += 1
            while idx < len(lines):
                if lines[idx].strip() == "$$":
                    count += 1
                    idx += 1
                    break
                idx += 1
            continue

        if stripped.startswith("$$") and stripped.endswith("$$") and stripped != "$$":
            count += 1
            idx += 1
            continue

        idx += 1
    return count


def _count_equations(markdown: str) -> int:
    marker_count = len(re.findall(r"<!--\s*eq:", markdown))
    if marker_count:
        return marker_count
    return _count_display_math_blocks(markdown)


def _find_heavy_chunks(chunks: list[dict]) -> list[str]:
    """Return chunk IDs that qualify as Tier 2 (equation count >= 3 in raw text).

    Counts dollar signs, display math delimiters, and equation environments
    in each chunk's raw_text. A chunk is classified Tier 2 only if the count
    is >= 3, preventing introduction-only or prose chunks from triggering MinerU.
    """
    heavy: list[str] = []
    for chunk in chunks:
        raw = chunk.get("raw_text", "")
        eq_count = len(re.findall(r'\$|\\\[|\\begin\{(?:equation|align|gather|multline)', raw))
        if eq_count >= 3:
            heavy.append(chunk["chunk_id"])
    return heavy


def _write_translation_warnings_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not lines:
        path.write_text("", encoding="utf-8")
        return
    normalized = [ln.rstrip("\n") for ln in lines if ln.strip() != ""]
    path.write_text("\n".join(normalized) + "\n", encoding="utf-8")


def _write_translation_manifest(
    path: Path,
    *,
    tool: str,
    source_file: str,
    timestamp: str,
    equation_count: int,
    section_count: int,
    word_count: int,
    validation_status: str,
    cite_key: str = "",
    source_format: str = "latex",
    has_equations: bool = False,
    custom_macros_expanded: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "cite_key": cite_key,
        "custom_macros_expanded": custom_macros_expanded or [],
        "equation_count": int(equation_count),
        "has_equations": has_equations,
        "section_count": int(section_count),
        "source_file": source_file,
        "source_format": source_format,
        "timestamp": timestamp,
        "tool": tool,
        "validation_status": validation_status,
        "word_count": int(word_count),
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _finalize_translation_artifacts(
    *,
    cite_key: str,
    paper_bank_dir: Path,
    translated_markdown_path: Path,
    source_file: str,
    macro_warnings_text: str,
    source_format: str = "latex",
    custom_macros_expanded: list[str] | None = None,
) -> dict[str, object]:
    markdown = translated_markdown_path.read_text(encoding="utf-8", errors="replace")

    equation_count = _count_equations(markdown)
    section_count = _count_sections(markdown)
    word_count = _count_words(markdown)

    validation_warnings: list[str] = []
    if section_count == 0:
        validation_warnings.append("[validate] no '##' section headings found")
    elif section_count < 3:
        validation_warnings.append(f"[validate] low section_count={section_count} (< 3)")
    if equation_count == 0:
        validation_warnings.append("[validate] no equations detected (no <!-- eq: --> markers and no $$ blocks)")
    elif equation_count < 5:
        validation_warnings.append(f"[validate] low equation_count={equation_count} (< 5)")
    if "\\eqref{" in markdown:
        validation_warnings.append("[validate] found unresolved LaTeX \\eqref{...} cross-references")

    theorem_index_path = paper_bank_dir / "_theorem_index.json"
    theorem_count = 0
    if theorem_index_path.exists():
        try:
            theorem_obj = json.loads(theorem_index_path.read_text(encoding="utf-8"))
            theorem_count = len(theorem_obj.get("theorems", []) or [])
        except Exception:
            validation_warnings.append("[validate] _theorem_index.json exists but is not valid JSON")
    else:
        validation_warnings.append("[validate] missing _theorem_index.json (theorem indexing did not run)")

    if theorem_count == 0:
        validation_warnings.append("[validate] theorem index is empty")

    warnings_lines: list[str] = []
    macro_lines = [ln for ln in macro_warnings_text.splitlines() if ln.strip() != ""]
    warnings_lines.extend(macro_lines)
    warnings_lines.extend(validation_warnings)

    if section_count == 0:
        validation_status = "failed"
    else:
        has_warnings = bool(warnings_lines)
        if equation_count >= 5 and section_count >= 3 and not has_warnings:
            validation_status = "passed"
        else:
            validation_status = "warnings" if has_warnings or equation_count < 5 or section_count < 3 else "passed"

    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")

    warnings_path = paper_bank_dir / "_translation_warnings.log"
    manifest_path = paper_bank_dir / "_translation_manifest.json"
    _write_translation_warnings_log(warnings_path, warnings_lines)
    _write_translation_manifest(
        manifest_path,
        tool="pandoc",
        source_file=source_file,
        timestamp=timestamp,
        equation_count=equation_count,
        section_count=section_count,
        word_count=word_count,
        validation_status=validation_status,
        cite_key=cite_key,
        source_format=source_format,
        has_equations=equation_count > 0,
        custom_macros_expanded=custom_macros_expanded or [],
    )

    return {
        "manifest_path": str(manifest_path),
        "warnings_path": str(warnings_path),
        "equation_count": equation_count,
        "section_count": section_count,
        "word_count": word_count,
        "validation_status": validation_status,
        "warnings_count": len(warnings_lines),
    }


def _find_pdf_in_paper_bank(paper_bank_dir: Path) -> Path:
    """Return the main PDF file found under paper_bank_dir (raw/ > source/ > root)."""
    for subdir_name in ("raw", "source", ""):
        search_dir = paper_bank_dir / subdir_name if subdir_name else paper_bank_dir
        if not search_dir.is_dir():
            continue
        pdfs = sorted(search_dir.glob("*.pdf"))
        if pdfs:
            return min(pdfs, key=lambda p: len(p.name))
    raise FileNotFoundError(f"No PDF found under {paper_bank_dir}")


def _parse_pdf_md_frontmatter(md_path: Path) -> dict:
    """Parse simple YAML frontmatter from a markdown file into a plain dict."""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    front = text[4:end]

    result: dict = {}
    current_list_key: str | None = None
    for line in front.splitlines():
        list_m = re.match(r"^\s{2}-\s+(.+)", line)
        key_m = re.match(r"^([\w][\w_-]*):\s*(.*)", line)
        if list_m and current_list_key is not None:
            result.setdefault(current_list_key, []).append(list_m.group(1).strip())
        elif key_m:
            current_list_key = None
            key, value = key_m.group(1), key_m.group(2).strip()
            if value in ("", "[]"):
                current_list_key = key
                result[key] = [] if value == "[]" else []
            else:
                try:
                    result[key] = int(value)
                except ValueError:
                    result[key] = value
    return result


_PDF_HEADER_ALIASES = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "related work": "Related Work",
    "background": "Background",
    "preliminaries": "Preliminaries",
    "problem setup": "Problem Setup",
    "problem formulation": "Problem Formulation",
    "method": "Method",
    "methods": "Methods",
    "approach": "Approach",
    "model": "Model",
    "experiments": "Experiments",
    "results": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
    "conclusions": "Conclusions",
    "appendix": "Appendix",
    "references": "References",
}
_PDF_NUMBERED_HEADER_RE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(.+)$")
_PDF_KNOWN_HEADER_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]?\s+)?"
    r"(?:abstract|introduction|related work|background|preliminaries|"
    r"problem setup|problem formulation|method(?:s)?|approach|model|"
    r"experiments?|results?|discussion|conclusions?|appendix|references)$",
    re.IGNORECASE,
)
_FENCE_START_RE = re.compile(r"^\s*(```|~~~)")


def _split_frontmatter_and_body(markdown: str) -> tuple[str, str]:
    if markdown.startswith("---\n"):
        end = markdown.find("\n---\n", 4)
        if end >= 0:
            split = end + 5
            return markdown[:split], markdown[split:]
    return "", markdown


def _normalize_header_candidate(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().strip(":")).strip()


def _canonicalize_pdf_header(line: str) -> str:
    candidate = _normalize_header_candidate(line)
    lowered = candidate.lower()
    if lowered in _PDF_HEADER_ALIASES:
        return _PDF_HEADER_ALIASES[lowered]
    return candidate


def _infer_pdf_header(line: str, next_nonempty: str) -> str | None:
    if not line or line.startswith("#"):
        return None
    candidate = _normalize_header_candidate(line)
    if len(candidate) < 3 or len(candidate) > 90:
        return None

    if _PDF_KNOWN_HEADER_RE.match(candidate):
        if numbered := _PDF_NUMBERED_HEADER_RE.match(candidate):
            return _canonicalize_pdf_header(numbered.group(2))
        return _canonicalize_pdf_header(candidate)

    if numbered := _PDF_NUMBERED_HEADER_RE.match(candidate):
        text = _normalize_header_candidate(numbered.group(2))
        token_count = len(re.findall(r"[A-Za-z]+", text))
        if token_count <= 8:
            return _canonicalize_pdf_header(text)

    tokens = re.findall(r"[A-Za-z]+", candidate)
    if not tokens or len(tokens) > 12:
        return None
    upper_token_count = sum(1 for token in tokens if len(token) > 1 and token.upper() == token)
    mostly_upper = upper_token_count >= max(2, len(tokens) - 1)
    next_is_body = bool(next_nonempty) and bool(re.search(r"[a-z]", next_nonempty))
    if mostly_upper and next_is_body:
        return _canonicalize_pdf_header(candidate.title())

    return None


def _inject_pdf_section_headers(markdown: str) -> tuple[str, int]:
    """Inject markdown section headers inferred from MinerU output structure."""
    frontmatter, body = _split_frontmatter_and_body(markdown)
    lines = body.splitlines()
    if not lines:
        return markdown, 0

    out: list[str] = []
    injected = 0
    in_code_fence = False
    in_display_math = False

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if _FENCE_START_RE.match(stripped):
            in_code_fence = not in_code_fence
            out.append(raw_line)
            continue
        if stripped == "$$":
            in_display_math = not in_display_math
            out.append(raw_line)
            continue
        if in_code_fence or in_display_math:
            out.append(raw_line)
            continue

        next_nonempty = ""
        for look_ahead in lines[idx + 1:]:
            look = look_ahead.strip()
            if look:
                next_nonempty = look
                break

        header = _infer_pdf_header(stripped, next_nonempty)
        if header:
            if out and out[-1].strip():
                out.append("")
            out.append(f"## {header}")
            out.append("")
            injected += 1
            continue

        out.append(raw_line)

    rebuilt_body = "\n".join(out).strip("\n")
    merged = f"{frontmatter}{rebuilt_body}\n" if frontmatter else f"{rebuilt_body}\n"
    return merged, injected


def _find_html_in_paper_bank(paper_bank_dir: Path) -> Path:
    """Return the main HTML file found under paper_bank_dir (raw/ > source/ > root)."""
    for subdir_name in ("raw", "source", ""):
        search_dir = paper_bank_dir / subdir_name if subdir_name else paper_bank_dir
        if not search_dir.is_dir():
            continue
        htmls = sorted(search_dir.glob("*.html")) + sorted(search_dir.glob("*.htm"))
        if htmls:
            return min(htmls, key=lambda p: len(p.name))
    raise FileNotFoundError(f"No HTML file found under {paper_bank_dir}")


def _translate_html_format(
    *,
    cite_key: str,
    paper_bank_dir: Path,
    output_path: Path,
) -> Path:
    """Run the HTML translation pipeline using html_translator."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from translators.html_translator import translate_html  # type: ignore

    html_path = _find_html_in_paper_bank(paper_bank_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    translate_html(input_path=html_path, output_path=output_path, cite_key=cite_key)
    print(f"[translate_paper] HTML translation written: {output_path}", file=sys.stderr)
    return output_path


def _translate_pdf_format(
    *,
    cite_key: str,
    paper_bank_dir: Path,
    output_path: Path,
    pdf_pages_per_group: int,
) -> Path:
    """Run the PDF translation pipeline and write the PDF manifest.

    Steps:
      1. Locate the PDF under paper_bank_dir.
      2. Call assemble_pdf_translation() to produce translated_full.md.
         (also persists per-chunk PDFs + chunk markdown under pdf_segments/).
      3. Re-run extract_tier1() to collect per-chunk metadata (page ranges,
         is_heavy flags).
      4. Parse fallback_chunks and timestamps from the written markdown's YAML
         frontmatter.
      5. Write _translation_manifest_pdf.json via write_pdf_manifest().
    """
    sys.path.insert(0, str(SCRIPT_DIR))
    from translators.pdf_translator import assemble_pdf_translation, extract_tier1  # type: ignore
    from translation_utils import write_pdf_manifest  # type: ignore

    pdf_path = _find_pdf_in_paper_bank(paper_bank_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    assemble_pdf_translation(
        str(pdf_path),
        cite_key,
        str(output_path),
        pages_per_chunk=max(1, int(pdf_pages_per_group)),
    )

    translated = output_path.read_text(encoding="utf-8", errors="replace")
    injected_markdown, inserted_count = _inject_pdf_section_headers(translated)
    if inserted_count > 0 and injected_markdown != translated:
        output_path.write_text(injected_markdown, encoding="utf-8")
        print(
            f"[translate_paper] injected {inserted_count} inferred section header(s) into MinerU output",
            file=sys.stderr,
        )

    chunks = extract_tier1(
        str(pdf_path),
        pages_per_chunk=max(1, int(pdf_pages_per_group)),
    )

    fm = _parse_pdf_md_frontmatter(output_path)
    fallback_chunks = fm.get("fallback_chunks", [])
    if isinstance(fallback_chunks, str):
        fallback_chunks = [fallback_chunks]
    tool = fm.get("translation_tool", "pymupdf")
    translation_timestamp = fm.get("translation_timestamp", datetime.now().astimezone().isoformat(timespec="seconds"))
    page_count = fm.get("page_count", chunks[-1]["end_page"] if chunks else 0)
    chunk_artifacts_dir = fm.get("chunk_artifacts_dir", "pdf_segments")

    manifest_path = write_pdf_manifest(
        paper_bank_dir,
        cite_key=cite_key,
        tool=tool,
        translation_timestamp=translation_timestamp,
        page_count=page_count,
        chunks=chunks,
        fallback_chunks=fallback_chunks,
        chunk_artifacts_dir=str(chunk_artifacts_dir),
    )
    print(f"[translate_paper] PDF manifest written: {manifest_path}", file=sys.stderr)

    # M-002: add has_equations to the PDF manifest
    heavy_ids = _find_heavy_chunks(chunks)
    has_equations = len(heavy_ids) > 0
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_data["has_equations"] = has_equations
        manifest_path.write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass

    return output_path


def translate_paper(
    *,
    cite_key: str,
    paper_bank_dir: Path,
    output_path: Path,
    fmt: str,
    keep_temp: bool,
    dry_run: bool = False,
    pdf_pages_per_group: int = 3,
) -> Path:
    paper_bank_dir = paper_bank_dir.expanduser()
    paper_bank_dir.mkdir(parents=True, exist_ok=True)
    original_paper_bank_dir = paper_bank_dir
    auto_detected_cite_key, cite_key_resolution = resolve_cite_key(
        requested_cite_key=cite_key,
        paper_bank_dir=paper_bank_dir,
    )
    requested_cite_key = cite_key.strip()
    if requested_cite_key:
        resolved_cite_key = requested_cite_key
        if auto_detected_cite_key and auto_detected_cite_key != requested_cite_key:
            source_path = cite_key_resolution.get("source_path") or "metadata inference"
            print(
                (
                    "[translate_paper] keeping user-supplied cite key "
                    f"'{requested_cite_key}' (auto-detected '{auto_detected_cite_key}' from {source_path})"
                ),
                file=sys.stderr,
            )
    else:
        resolved_cite_key = auto_detected_cite_key
    # I-005 guard: only run cite_key migration when a genuine LaTeX source tree is
    # present (i.e. at least one .tex file exists under paper_bank_dir).  When a
    # supplement PDF is staged without any .tex files the migration must not run —
    # it would otherwise create a spurious nested canonical directory.
    has_tex_files = any(paper_bank_dir.rglob("*.tex"))
    if has_tex_files:
        paper_bank_dir, migration_report = migrate_alias_dir_to_canonical(
            requested_dir=paper_bank_dir,
            resolved_cite_key=resolved_cite_key,
        )
    else:
        migration_report = {
            "alias_applied": False,
            "requested_dir": str(paper_bank_dir),
            "canonical_dir": str(paper_bank_dir),
            "moved_items": [],
        }
    if migration_report.get("alias_applied"):
        print(
            (
                "[translate_paper] canonical paper-bank path: "
                f"{original_paper_bank_dir} -> {paper_bank_dir}"
            ),
            file=sys.stderr,
        )
    if migration_report.get("moved_items"):
        moved_items = ", ".join(migration_report["moved_items"])
        print(f"[translate_paper] migrated artifacts: {moved_items}", file=sys.stderr)
    if output_path.parent == original_paper_bank_dir:
        output_path = paper_bank_dir / output_path.name
    if fmt == "auto":
        fmt = "pandoc"
    if fmt == "pdf":
        if dry_run and output_path.exists():
            print(f"[translate_paper] dry-run: {output_path} already exists, skipping.", file=sys.stderr)
            return output_path
        return _translate_pdf_format(
            cite_key=resolved_cite_key,
            paper_bank_dir=paper_bank_dir,
            output_path=output_path,
            pdf_pages_per_group=max(1, int(pdf_pages_per_group)),
        )
    if fmt == "html":
        return _translate_html_format(
            cite_key=resolved_cite_key,
            paper_bank_dir=paper_bank_dir,
            output_path=output_path,
        )
    if fmt != "pandoc":
        raise ValueError(f"Unsupported --format: {fmt}")

    try:
        paper_bank_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot create or write to paper-bank dir: {paper_bank_dir} (sandbox or permissions)."
        ) from exc

    source_root = _pick_source_root(paper_bank_dir)
    root_tex = _detect_root_tex(source_root)

    formatter = _load_formatter()
    number_equations = _load_equation_numberer()
    build_theorem_index = _load_theorem_indexer()
    harvest_macro_definitions = _load_macro_harvester()

    temp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
    temp_dir_path: Path
    if keep_temp:
        temp_dir_path = Path(tempfile.mkdtemp(prefix=f"translate_{resolved_cite_key}_"))
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix=f"translate_{resolved_cite_key}_")
        temp_dir_path = Path(temp_dir_obj.name)

    try:
        assembled_tex = temp_dir_path / "assembled.tex"
        expanded_tex = temp_dir_path / "expanded.tex"
        warnings_log = temp_dir_path / "macro_warnings.txt"
        pandoc_md = temp_dir_path / "pandoc.md"

        visited: set[Path] = set()
        assembled = _assemble_tex(root_tex, visited=visited, stack=[])
        macro_definitions = harvest_macro_definitions(assembled)
        custom_macros_expanded = _extract_macro_names(macro_definitions)
        assembled_tex.write_text(assembled, encoding="utf-8")

        _run_expand_macros(assembled_tex, expanded_tex, warnings_log)

        sanitized_tex = temp_dir_path / "sanitized.tex"
        sanitized_tex.write_text(
            _sanitize_tex_for_pandoc(expanded_tex.read_text(encoding="utf-8", errors="replace")),
            encoding="utf-8",
        )

        _run_pandoc(sanitized_tex, pandoc_md)

        md_body = pandoc_md.read_text(encoding="utf-8", errors="replace")
        md_body = _convert_gfm_math_to_dollars(md_body)
        md_body = _wrap_bare_display_math(md_body)
        md_body = _strip_display_math_env_tags(md_body)
        md_body = _normalize_inline_math(md_body)
        md_body = formatter(md_body)

        preamble_meta = _extract_preamble_metadata(assembled)
        catalog_meta = _load_catalog_metadata(paper_bank_dir)
        title_val = preamble_meta.get("title") or catalog_meta.get("title") or ""
        author_val = preamble_meta.get("author") or catalog_meta.get("author") or ""
        date_val = preamble_meta.get("date") or catalog_meta.get("date") or ""

        frontmatter_parts: list[str] = [
            "---",
            f"cite_key: {_yaml_escape(resolved_cite_key)}",
        ]
        if title_val:
            frontmatter_parts.append(f"title: {_yaml_escape(title_val)}")
        if author_val:
            frontmatter_parts.append(f"authors: {_yaml_escape(author_val)}")
        else:
            frontmatter_parts.append("authors: [unknown]")
        if date_val:
            frontmatter_parts.append(f"date: {_yaml_escape(date_val)}")
        frontmatter_parts.extend([
            f"source_root_tex: {_yaml_escape(str(root_tex.relative_to(source_root)) if root_tex.is_relative_to(source_root) else str(root_tex))}",
            "translation: pandoc",
            "---",
            "",
            f"## {title_val or resolved_cite_key}",
            "",
        ])
        out_text = "\n".join(frontmatter_parts) + md_body.lstrip("\n")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(out_text, encoding="utf-8")

        numbered = number_equations(out_text)
        if numbered != out_text:
            output_path.write_text(numbered, encoding="utf-8")

        post_md = output_path.read_text(encoding="utf-8", errors="replace")
        post_md_with_preamble = _inject_macro_preamble(post_md, macro_definitions)
        if post_md_with_preamble != post_md:
            output_path.write_text(post_md_with_preamble, encoding="utf-8")
            post_md = post_md_with_preamble
        theorems = build_theorem_index(post_md, max_preview_chars=280)
        theorem_index_path = paper_bank_dir / "_theorem_index.json"
        theorem_index_path.parent.mkdir(parents=True, exist_ok=True)
        theorem_index_path.write_text(
            json.dumps({"cite_key": resolved_cite_key, "theorems": theorems}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        macro_warnings_text = ""
        try:
            macro_warnings_text = warnings_log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            macro_warnings_text = ""

        source_file = str(root_tex.relative_to(source_root)) if root_tex.is_relative_to(source_root) else str(root_tex)
        _finalize_translation_artifacts(
            cite_key=resolved_cite_key,
            paper_bank_dir=paper_bank_dir,
            translated_markdown_path=output_path,
            source_file=source_file,
            macro_warnings_text=macro_warnings_text,
            source_format="latex",
            custom_macros_expanded=custom_macros_expanded,
        )
    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()
        elif keep_temp:
            print(f"[translate_paper] kept temp dir: {temp_dir_path}", file=sys.stderr)

    return output_path


def main() -> int:
    args = parse_args()
    paper_bank_dir = Path(args.paper_bank_dir).expanduser()
    if args.output:
        output_path = Path(args.output).expanduser()
    elif args.format == "pdf":
        output_path = paper_bank_dir / "translated_full.md"
    else:
        output_path = paper_bank_dir / "translated_full.md"

    result = translate_paper(
        cite_key=args.cite_key,
        paper_bank_dir=paper_bank_dir,
        output_path=output_path,
        fmt=args.format,
        keep_temp=bool(args.keep_temp),
        dry_run=bool(args.dry_run),
        pdf_pages_per_group=max(1, int(args.pdf_pages_per_group)),
    )
    # Write a secondary copy at translated_full_pdf.md for backward compatibility
    # with pdf_segmenter.py, which still reads that specific filename.
    if args.format == "pdf" and not args.output and result.exists():
        compat_path = paper_bank_dir / "translated_full_pdf.md"
        if compat_path != result:
            compat_path.write_bytes(result.read_bytes())
    print(str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
