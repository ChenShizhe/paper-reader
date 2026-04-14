#!/usr/bin/env python3
"""Translate a local HTML file to GitHub-flavored markdown.

CLI usage:
    python3 html_translator.py \\
        --input paper.html \\
        --output translated.md \\
        --cite-key mykey

The tool tries pandoc first (HTML -> GFM + tex_math_dollars) and falls back
to markdownify if pandoc is unavailable.

Math preservation:
    - <script type="math/tex; mode=display">...</script> → $$...$$
    - <script type="math/tex">...</script>              → $...$
    - <span class="math inline">\\(...\\)</span>         → $...$
    - <div class="math display">\\[...\\]</div>          → $$...$$

Writes _translation_manifest.json alongside the output file with keys:
    cite_key, source_format, tool, translation_timestamp, output_path,
    validation_status.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Chrome stripping
# ---------------------------------------------------------------------------

# CSS selectors (and tag names) that identify non-content navigation chrome.
_CHROME_SELECTORS = [
    "nav",
    "header",
    "footer",
    '[class*="share"]',
    '[class*="social"]',
    '[class*="sidebar"]',
    '[class*="toc"]',
    '[id*="toc"]',
    '[class*="cookie"]',
    '[id*="cookie"]',
    '[class*="advertisement"]',
    '[class*="banner"]',
]


def _strip_chrome_blocks(html_text: str) -> tuple[str, int, str]:
    """Remove common non-content chrome blocks from *html_text*.

    Uses BeautifulSoup when available; falls back to regex for nav/header/footer.

    Returns (stripped_html, chrome_blocks_removed, content_root_selector).
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html_text, "html.parser")
        removed = 0
        for selector in _CHROME_SELECTORS:
            for el in soup.select(selector):
                el.decompose()
                removed += 1
        # Identify the content root element.
        root_el = soup.find("article") or soup.find("main") or soup.find("body")
        content_root_selector = root_el.name if root_el else "body"
        return str(soup), removed, content_root_selector
    except ImportError:
        # Regex fallback: strip nav, header, footer tags.
        removed = 0
        for tag in ("nav", "header", "footer"):
            pattern = re.compile(
                rf"<{tag}(?:\s[^>]*)?>.*?</{tag}>",
                re.DOTALL | re.IGNORECASE,
            )
            new_text, n = pattern.subn("", html_text)
            html_text = new_text
            removed += n
        return html_text, removed, "body"


def _extract_math_placeholders(html_text: str) -> tuple[str, dict[str, str]]:
    """Extract math elements from HTML and replace with alphanumeric placeholders.

    Handles:
    - <script type="math/tex; mode=display">...</script>
    - <script type="math/tex">...</script>
    - <div class="math display">\\[...\\]</div>
    - <span class="math inline">\\(...\\)</span>

    Returns (modified_html, placeholder_map) where placeholder_map maps
    placeholder strings to their dollar-delimited markdown replacements.
    """
    placeholders: dict[str, str] = {}
    counter = [0]

    def _make_placeholder(replacement: str) -> str:
        key = f"MTHPH{counter[0]}END"
        counter[0] += 1
        placeholders[key] = replacement
        return key

    # <script type="math/tex; mode=display">...</script>
    def _display_script(m: re.Match) -> str:
        content = m.group(1).strip()
        return _make_placeholder(f"\n$$\n{content}\n$$\n")

    html_text = re.sub(
        r'<script\b[^>]*\btype=["\']math/tex;\s*mode=display["\'][^>]*>(.*?)</script>',
        _display_script,
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # <script type="math/tex">...</script>
    def _inline_script(m: re.Match) -> str:
        content = m.group(1).strip()
        return _make_placeholder(f"${content}$")

    html_text = re.sub(
        r'<script\b[^>]*\btype=["\']math/tex["\'][^>]*>(.*?)</script>',
        _inline_script,
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # <div class="math display">\[...\]</div>
    def _display_div(m: re.Match) -> str:
        content = m.group(1).strip()
        if content.startswith("\\["):
            content = content[2:]
        if content.endswith("\\]"):
            content = content[:-2]
        content = content.strip()
        return _make_placeholder(f"\n$$\n{content}\n$$\n")

    html_text = re.sub(
        r'<div\b[^>]*\bclass=["\'][^"\']*math\s+display[^"\']*["\'][^>]*>(.*?)</div>',
        _display_div,
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # <span class="math inline">\(...\)</span>
    def _inline_span(m: re.Match) -> str:
        content = m.group(1).strip()
        if content.startswith("\\("):
            content = content[2:]
        if content.endswith("\\)"):
            content = content[:-2]
        content = content.strip()
        return _make_placeholder(f"${content}$")

    html_text = re.sub(
        r'<span\b[^>]*\bclass=["\'][^"\']*math\s+inline[^"\']*["\'][^>]*>(.*?)</span>',
        _inline_span,
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    return html_text, placeholders


def _restore_math_placeholders(md_text: str, placeholders: dict[str, str]) -> str:
    """Restore alphanumeric math placeholders with their dollar-delimited equivalents."""
    for key, value in placeholders.items():
        md_text = md_text.replace(key, value)
    return md_text


# ---------------------------------------------------------------------------
# Table handling
# ---------------------------------------------------------------------------

def _find_table_spans(html_text: str) -> list[tuple[int, int]]:
    """Return (start, end) char spans for each top-level <table>...</table> block."""
    spans: list[tuple[int, int]] = []
    depth = 0
    current_start = -1
    for m in re.finditer(r'<\s*(/\s*)?table(?:\s[^>]*)?\s*>', html_text, re.IGNORECASE):
        is_closing = bool(m.group(1))
        if is_closing:
            if depth == 1:
                spans.append((current_start, m.end()))
                current_start = -1
            if depth > 0:
                depth -= 1
        else:
            if depth == 0:
                current_start = m.start()
            depth += 1
    return spans


def _is_complex_table(table_html: str) -> bool:
    """Return True when the table cannot be represented as a simple flat markdown table.

    Complex conditions:
    - Contains rowspan or colspan attributes
    - Contains a nested <table> element
    - Has multiple <tr> rows inside a single <thead> (multi-row header)
    """
    lower = table_html.lower()
    if "rowspan" in lower or "colspan" in lower:
        return True
    # Nested table: more than one <table opening tag
    if len(re.findall(r'<\s*table(?:\s[^>]*)?\s*>', table_html, re.IGNORECASE)) > 1:
        return True
    # Multiple header rows: more than one <tr> inside <thead>
    thead_m = re.search(
        r'<\s*thead[^>]*>(.*?)</\s*thead\s*>', table_html, re.IGNORECASE | re.DOTALL
    )
    if thead_m:
        tr_count = len(re.findall(r'<\s*tr(?:\s[^>]*)?\s*>', thead_m.group(1), re.IGNORECASE))
        if tr_count > 1:
            return True
    return False


def _count_tables(html_text: str) -> tuple[int, int]:
    """Classify every top-level <table> in *html_text*.

    Returns (tables_simple, tables_passthrough).
    """
    simple = 0
    passthrough = 0
    for start, end in _find_table_spans(html_text):
        if _is_complex_table(html_text[start:end]):
            passthrough += 1
        else:
            simple += 1
    return simple, passthrough


def _extract_complex_tables(html_text: str) -> tuple[str, dict[str, str]]:
    """Replace complex tables in *html_text* with alphanumeric placeholders.

    Returns (modified_html, placeholder_map) so the caller can restore them
    after markdownify conversion.  Simple tables are left in place for
    markdownify to convert.
    """
    spans = _find_table_spans(html_text)
    placeholders: dict[str, str] = {}
    result = html_text
    # Iterate in reverse so earlier offsets stay valid as we splice
    for idx, (start, end) in enumerate(reversed(spans)):
        table_html = result[start:end]
        if _is_complex_table(table_html):
            key = f"CXTBLPH{idx}END"
            placeholders[key] = table_html
            result = result[:start] + key + result[end:]
    return result, placeholders


def _try_pandoc(html_path: Path, md_path: Path) -> bool:
    """Run pandoc HTML->GFM. Returns True on success."""
    cmd = [
        "pandoc",
        str(html_path),
        "--from", "html",
        "--to", "gfm+tex_math_dollars",
        "--wrap=none",
        "--markdown-headings=atx",
        "--output", str(md_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def _try_markdownify(html_path: Path, md_path: Path) -> bool:
    """Convert HTML to markdown using markdownify. Returns True on success.

    Complex tables (rowspan/colspan/nested/multi-header) are extracted before
    conversion and re-injected as raw HTML blocks afterward, so they survive
    the markdownify pass intact.
    """
    try:
        import markdownify  # type: ignore
        html_text = html_path.read_text(encoding="utf-8", errors="replace")
        # Protect complex tables from markdownify conversion
        protected_html, tbl_placeholders = _extract_complex_tables(html_text)
        md_text = markdownify.markdownify(protected_html, heading_style="ATX")
        # Restore complex tables as raw HTML
        for key, table_html in tbl_placeholders.items():
            md_text = md_text.replace(key, "\n\n" + table_html + "\n\n")
        md_path.write_text(md_text, encoding="utf-8")
        return True
    except ImportError:
        return False


def _count_h2_sections(markdown: str) -> int:
    return sum(1 for line in markdown.splitlines() if line.startswith("## "))


def translate_html(*, input_path: Path, output_path: Path, cite_key: str) -> dict:
    """Convert input HTML to markdown, write output, and return the manifest dict.

    Tries pandoc first, then markdownify. Raises RuntimeError if both fail.
    Math elements (math/tex script tags, math inline spans, math display divs)
    are extracted before translation and restored as dollar-delimited markdown math.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_text = input_path.read_text(encoding="utf-8", errors="replace")
    html_text, chrome_blocks_removed, content_root_selector = _strip_chrome_blocks(html_text)
    tables_simple, tables_passthrough = _count_tables(html_text)
    processed_html, placeholders = _extract_math_placeholders(html_text)

    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", mode="w", encoding="utf-8", delete=False
    )
    tmp_path = Path(tmp.name)
    try:
        tmp.write(processed_html)
        tmp.close()

        tool_used: str | None = None
        if _try_pandoc(tmp_path, output_path):
            tool_used = "pandoc"
        elif _try_markdownify(tmp_path, output_path):
            tool_used = "markdownify"
        else:
            raise RuntimeError(
                "Neither pandoc nor markdownify could convert the HTML file. "
                "Install pandoc or `pip install markdownify`."
            )
    finally:
        tmp_path.unlink(missing_ok=True)

    md_text = output_path.read_text(encoding="utf-8", errors="replace")
    if placeholders:
        md_text = _restore_math_placeholders(md_text, placeholders)
        output_path.write_text(md_text, encoding="utf-8")

    section_count = _count_h2_sections(md_text)
    validation_status = "passed" if section_count >= 1 else "warnings"

    manifest: dict = {
        "chrome_blocks_removed": chrome_blocks_removed,
        "cite_key": cite_key,
        "content_root_selector": content_root_selector,
        "output_path": str(output_path),
        "source_format": "html",
        "tables_passthrough": tables_passthrough,
        "tables_simple": tables_simple,
        "tool": tool_used,
        "translation_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "validation_status": validation_status,
    }

    manifest_path = output_path.parent / "_translation_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate a local HTML file to GitHub-flavored markdown."
    )
    parser.add_argument("--input", required=True, help="Path to the input HTML file.")
    parser.add_argument(
        "--output", required=True, help="Path to write the output markdown file."
    )
    parser.add_argument("--cite-key", required=True, help="Cite key for the paper.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser()
    output_path = Path(args.output).expanduser()

    if not input_path.exists():
        print(
            f"[html_translator] ERROR: input file not found: {input_path}",
            file=sys.stderr,
        )
        return 1

    manifest = translate_html(
        input_path=input_path,
        output_path=output_path,
        cite_key=args.cite_key,
    )
    print(
        f"[html_translator] translated: {output_path} "
        f"(tool={manifest['tool']}, status={manifest['validation_status']})",
        file=sys.stderr,
    )
    print(str(output_path))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
