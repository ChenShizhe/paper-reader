#!/usr/bin/env python3
"""Expand preamble-defined LaTeX macros into a Pandoc-friendly .tex export.

This script:
- extracts \\newcommand, \\renewcommand, and \\DeclareMathOperator definitions
  from the preamble (before \\begin{document})
- expands *simple* macros (0–2 positional args) throughout the document body
- flags complex macros (conditionals, optional defaults, 3+ args, deep nesting)
  into a warnings log and does not expand them
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class MacroDef:
    name: str
    kind: str  # newcommand | renewcommand | DeclareMathOperator
    num_args: int
    template: str
    complex_reason: str | None = None
    source_definition: str | None = None

    @property
    def is_complex(self) -> bool:
        return self.complex_reason is not None

    def canonical_definition(self) -> str:
        if self.source_definition:
            return self.source_definition
        if self.kind == "DeclareMathOperator":
            return f"\\DeclareMathOperator{{\\{self.name}}}{{{self.template}}}"
        if self.num_args:
            return f"\\{self.kind}{{\\{self.name}}}[{self.num_args}]{{{self.template}}}"
        return f"\\{self.kind}{{\\{self.name}}}{{{self.template}}}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expand preamble macros in a LaTeX file")
    parser.add_argument("--input", required=True, help="Input .tex file (original)")
    parser.add_argument("--output", required=True, help="Output .tex file (expanded)")
    parser.add_argument("--warnings-log", required=True, help="Path to warnings log (always created)")
    return parser.parse_args()


def _skip_ws(text: str, idx: int) -> int:
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx


def _skip_comment(text: str, idx: int) -> int:
    if idx < len(text) and text[idx] == "%":
        while idx < len(text) and text[idx] != "\n":
            idx += 1
    return idx


def _read_control_word(text: str, idx: int) -> tuple[str, int]:
    """Reads a TeX control sequence starting at a backslash."""
    if idx >= len(text) or text[idx] != "\\":
        return "", idx
    idx += 1
    if idx >= len(text):
        return "", idx
    if not (text[idx].isalpha() or text[idx] == "@"):
        # Control symbol like \{ or \% — treat as 1-char name.
        return text[idx], idx + 1
    start = idx
    while idx < len(text) and (text[idx].isalpha() or text[idx] == "@"):
        idx += 1
    return text[start:idx], idx


def _consume_balanced(text: str, idx: int, open_ch: str, close_ch: str) -> tuple[str, int]:
    if idx >= len(text) or text[idx] != open_ch:
        raise ValueError(f"Expected '{open_ch}' at index {idx}")
    idx += 1
    depth = 1
    out: list[str] = []
    while idx < len(text):
        ch = text[idx]
        if ch == "\\":
            # Preserve escapes verbatim.
            out.append(ch)
            idx += 1
            if idx < len(text):
                out.append(text[idx])
                idx += 1
            continue
        if ch == open_ch:
            depth += 1
            out.append(ch)
            idx += 1
            continue
        if ch == close_ch:
            depth -= 1
            if depth == 0:
                idx += 1
                return "".join(out), idx
            out.append(ch)
            idx += 1
            continue
        out.append(ch)
        idx += 1
    raise ValueError(f"Unbalanced '{open_ch}{close_ch}' starting near index {idx}")


def _consume_braced(text: str, idx: int) -> tuple[str, int]:
    return _consume_balanced(text, idx, "{", "}")


def _consume_bracketed(text: str, idx: int) -> tuple[str, int]:
    return _consume_balanced(text, idx, "[", "]")


def _split_preamble(tex: str) -> tuple[str, str]:
    marker = r"\begin{document}"
    pos = tex.find(marker)
    if pos == -1:
        raise ValueError("Input TeX is missing \\begin{document}")
    return tex[:pos], tex[pos:]


def _parse_macro_name(raw: str) -> str:
    token = raw.strip()
    if token.startswith("\\"):
        token = token[1:]
    name = []
    for ch in token:
        if ch.isalpha() or ch == "@":
            name.append(ch)
        else:
            break
    return "".join(name)


def extract_preamble_macros(preamble: str) -> dict[str, MacroDef]:
    macros: dict[str, MacroDef] = {}
    idx = 0
    while idx < len(preamble):
        idx = _skip_comment(preamble, idx)
        if idx >= len(preamble):
            break
        if preamble[idx] != "\\":
            idx += 1
            continue

        command, after = _read_control_word(preamble, idx)
        idx = after
        starred = False
        if idx < len(preamble) and preamble[idx] == "*":
            starred = True
            idx += 1
        if command not in {"newcommand", "renewcommand", "DeclareMathOperator"}:
            continue

        kind = "DeclareMathOperator" if command == "DeclareMathOperator" else command
        idx = _skip_ws(preamble, idx)

        # macro name: either {\foo} or \foo
        if idx < len(preamble) and preamble[idx] == "{":
            group, idx = _consume_braced(preamble, idx)
            macro_name = _parse_macro_name(group)
        else:
            macro_name, idx = _read_control_word(preamble, idx)

        if not macro_name:
            continue

        idx = _skip_ws(preamble, idx)
        complex_reason: str | None = None

        if kind == "DeclareMathOperator":
            if idx >= len(preamble) or preamble[idx] != "{":
                continue
            operator, idx = _consume_braced(preamble, idx)
            template = r"\operatorname*{" + operator + "}" if starred else r"\operatorname{" + operator + "}"
            src = (
                f"\\DeclareMathOperator*{{\\{macro_name}}}{{{operator}}}"
                if starred
                else f"\\DeclareMathOperator{{\\{macro_name}}}{{{operator}}}"
            )
            macros[macro_name] = MacroDef(
                name=macro_name,
                kind="DeclareMathOperator",
                num_args=0,
                template=template,
                complex_reason=None,
                source_definition=src,
            )
            continue

        # newcommand / renewcommand: optional [n] and optional [default]
        num_args = 0
        if idx < len(preamble) and preamble[idx] == "[":
            bracket, idx = _consume_bracketed(preamble, idx)
            try:
                num_args = int(bracket.strip() or "0")
            except ValueError:
                num_args = 0
                complex_reason = "non-integer argument count"
            idx = _skip_ws(preamble, idx)
            if idx < len(preamble) and preamble[idx] == "[":
                _default, idx = _consume_bracketed(preamble, idx)
                complex_reason = complex_reason or "optional default argument"
                idx = _skip_ws(preamble, idx)

        if idx >= len(preamble) or preamble[idx] != "{":
            continue
        template, idx = _consume_braced(preamble, idx)

        if num_args > 2:
            complex_reason = complex_reason or "3+ arguments"
        if "#3" in template or "#4" in template:
            complex_reason = complex_reason or "uses #3+ placeholders"
        if any(token in template for token in (r"\if", r"\else", r"\fi")):
            complex_reason = complex_reason or "contains conditionals (\\if/\\else/\\fi)"

        macros[macro_name] = MacroDef(
            name=macro_name,
            kind=kind,
            num_args=num_args,
            template=template,
            complex_reason=complex_reason,
            source_definition=None if not starred else f"\\{kind}*{{\\{macro_name}}}[{num_args}]{{{template}}}",
        )
    return macros


def harvest_macro_definitions(tex: str) -> list[str]:
    """Return canonical macro definitions extracted from the LaTeX preamble."""
    preamble, _body = _split_preamble(tex)
    macros = extract_preamble_macros(preamble)
    return [macro.canonical_definition() for macro in macros.values()]


def _macro_dependencies(template: str, candidates: set[str]) -> set[str]:
    deps: set[str] = set()
    idx = 0
    while idx < len(template):
        if template[idx] == "%":
            idx = _skip_comment(template, idx)
            continue
        if template[idx] != "\\":
            idx += 1
            continue
        name, after = _read_control_word(template, idx)
        idx = after
        if name in candidates:
            deps.add(name)
    return deps


def _compute_nesting_depth(macros: dict[str, MacroDef]) -> tuple[dict[str, int], dict[str, str]]:
    """Returns (depths, cycle_reasons). Depth is longest dependency chain length."""
    names = set(macros.keys())
    deps_map = {name: _macro_dependencies(defn.template, names) for name, defn in macros.items()}

    visiting: set[str] = set()
    visited: dict[str, int] = {}
    cycle_reason: dict[str, str] = {}

    def dfs(name: str) -> int:
        if name in visited:
            return visited[name]
        if name in visiting:
            cycle_reason[name] = "recursive/cyclic macro dependency"
            return 10**9
        visiting.add(name)
        max_child = 0
        for dep in deps_map.get(name, set()):
            if macros[dep].is_complex:
                max_child = max(max_child, 10**9)
                continue
            max_child = max(max_child, dfs(dep))
        visiting.remove(name)
        depth = 1 + max_child
        visited[name] = depth
        return depth

    for name in list(names):
        dfs(name)
    return visited, cycle_reason


def _apply_complexity_from_nesting(macros: dict[str, MacroDef]) -> dict[str, MacroDef]:
    depths, cycles = _compute_nesting_depth(macros)
    updated: dict[str, MacroDef] = {}
    for name, macro in macros.items():
        reason = macro.complex_reason
        if name in cycles:
            reason = reason or cycles[name]
        depth = depths.get(name, 1)
        if depth > 2:
            reason = reason or f"nesting depth {depth} (>2)"
        # If any dependency is complex, depth becomes huge; flag explicitly.
        if depth >= 10**9:
            reason = reason or "depends on complex macro(s)"
        updated[name] = MacroDef(
            name=macro.name,
            kind=macro.kind,
            num_args=macro.num_args,
            template=macro.template,
            complex_reason=reason,
            source_definition=macro.source_definition,
        )
    return updated


# ---------------------------------------------------------------------------
# Pure-styling macro reduction (B1)
# ---------------------------------------------------------------------------

# Styling commands that carry no mathematical meaning.  A macro body that
# consists exclusively of these (plus braces, whitespace, and literal text)
# is a pure-styling wrapper and can be safely reduced to just #1.
_PURE_STYLING_COMMANDS: tuple[str, ...] = (
    r"\color",
    r"\textcolor",
    r"\leavevmode",
    r"\textbf",
    r"\textit",
    r"\texttt",
    r"\textrm",
    r"\textsf",
    r"\textsc",
    r"\underline",
    r"\emph",
)

# If any of these tokens appear in a macro body the macro is treated as a
# math macro and must NOT be reduced.
_MATH_CONTENT_INDICATORS: tuple[str, ...] = (
    "^",
    "_",
    r"\frac",
    r"\sum",
    r"\int",
    r"\prod",
    r"\partial",
    r"\sqrt",
    r"\limits",
    r"\infty",
    r"\nabla",
    r"\mathbf",
    r"\mathit",
    r"\mathrm",
    r"\mathcal",
    r"\mathbb",
    r"\mathsf",
    r"\mathtt",
    r"\mathfrak",
    r"\operatorname",
    r"\left",
    r"\right",
    r"\begin",
    r"\end",
    r"\alpha",
    r"\beta",
    r"\gamma",
    r"\delta",
    r"\epsilon",
    r"\theta",
    r"\lambda",
    r"\mu",
    r"\pi",
    r"\sigma",
    r"\omega",
)


def _is_pure_styling_macro(template: str, num_args: int) -> bool:
    """Return True when *template* is a pure-styling wrapper with no math content.

    Criteria:
    - num_args >= 1 and the body references #1 (so there is something to pass through)
    - The body does NOT contain math indicators (^, _, \\frac, \\sum, \\int, …)
    - The body contains at least one styling command (\\color, \\leavevmode, …)
    """
    if num_args < 1 or "#1" not in template:
        return False
    for indicator in _MATH_CONTENT_INDICATORS:
        if indicator in template:
            return False
    for cmd in _PURE_STYLING_COMMANDS:
        if cmd in template:
            return True
    return False


def _reduce_styling_macros(macros: dict[str, MacroDef]) -> dict[str, MacroDef]:
    """Return a new macro dict where pure-styling macros are reduced to ``#1``.

    Example::

        \\newcommand{\\red}[1]{{\\leavevmode\\color{red}{#1}}}
        → \\newcommand{\\red}[1]{#1}

    This ensures that when expand_macros rewrites the body, Pandoc receives
    plain text instead of silently dropping the styling wrapper.
    Math macros are never reduced.
    """
    updated: dict[str, MacroDef] = {}
    for name, macro in macros.items():
        if (
            not macro.is_complex
            and macro.kind in ("newcommand", "renewcommand")
            and _is_pure_styling_macro(macro.template, macro.num_args)
        ):
            updated[name] = MacroDef(
                name=macro.name,
                kind=macro.kind,
                num_args=macro.num_args,
                template="#1",
                complex_reason=None,
                source_definition=None,
            )
        else:
            updated[name] = macro
    return updated


def _rewrite_preamble_styling_macros(preamble: str, reduced_names: set[str]) -> str:
    """Rewrite pure-styling \\newcommand definitions in *preamble* to pass-through form.

    For each name in *reduced_names*, the original ``{body}`` is replaced with
    ``{#1}`` so that Pandoc also sees the reduced definition.
    All other preamble content is preserved verbatim.
    """
    if not reduced_names:
        return preamble

    out: list[str] = []
    idx = 0

    while idx < len(preamble):
        ch = preamble[idx]

        # Preserve TeX comments verbatim (% … \n).
        if ch == "%":
            start = idx
            while idx < len(preamble) and preamble[idx] != "\n":
                idx += 1
            out.append(preamble[start:idx])
            continue

        if ch != "\\":
            out.append(ch)
            idx += 1
            continue

        start_pos = idx
        command, after_cmd = _read_control_word(preamble, idx)

        if command not in ("newcommand", "renewcommand"):
            out.append(preamble[start_pos:after_cmd])
            idx = after_cmd
            continue

        # Advance past the command name.
        idx = after_cmd

        # Optional starred form.
        if idx < len(preamble) and preamble[idx] == "*":
            idx += 1

        idx = _skip_ws(preamble, idx)

        # Read the macro name (braced or bare).
        if idx >= len(preamble):
            out.append(preamble[start_pos:idx])
            continue

        if preamble[idx] == "{":
            group, idx_after_name = _consume_braced(preamble, idx)
            macro_name = _parse_macro_name(group)
        elif preamble[idx] == "\\":
            macro_name, idx_after_name = _read_control_word(preamble, idx)
        else:
            out.append(preamble[start_pos:idx])
            continue

        if macro_name not in reduced_names:
            # Not a target — output verbatim up to (and including) the name.
            out.append(preamble[start_pos:idx_after_name])
            idx = idx_after_name
            continue

        # Target macro: skip the remainder of the original definition.
        idx = idx_after_name
        idx = _skip_ws(preamble, idx)

        num_args = 0
        if idx < len(preamble) and preamble[idx] == "[":
            bracket, idx = _consume_bracketed(preamble, idx)
            try:
                num_args = int(bracket.strip() or "0")
            except ValueError:
                pass
            idx = _skip_ws(preamble, idx)
            # Skip optional default argument bracket.
            if idx < len(preamble) and preamble[idx] == "[":
                _, idx = _consume_bracketed(preamble, idx)
                idx = _skip_ws(preamble, idx)

        if idx < len(preamble) and preamble[idx] == "{":
            _, idx = _consume_braced(preamble, idx)

        # Emit the reduced definition.
        if num_args:
            out.append(f"\\{command}{{\\{macro_name}}}[{num_args}]{{#1}}")
        else:
            out.append(f"\\{command}{{\\{macro_name}}}{{#1}}")

    return "".join(out)


def _consume_invocation_args(text: str, idx: int, count: int) -> tuple[list[str], int] | None:
    args: list[str] = []
    cursor = idx
    for _ in range(count):
        cursor = _skip_ws(text, cursor)
        cursor = _skip_comment(text, cursor)
        cursor = _skip_ws(text, cursor)
        if cursor >= len(text) or text[cursor] != "{":
            return None
        group, cursor = _consume_braced(text, cursor)
        args.append(group)
    return args, cursor


def _expand_fragment(text: str, macros: dict[str, MacroDef], depth_remaining: int) -> str:
    if depth_remaining <= 0:
        return text

    out: list[str] = []
    idx = 0
    while idx < len(text):
        if text[idx] == "%":
            start = idx
            idx = _skip_comment(text, idx)
            out.append(text[start:idx])
            continue
        if text[idx] != "\\":
            out.append(text[idx])
            idx += 1
            continue

        name, after = _read_control_word(text, idx)
        if not name or name not in macros:
            out.append(text[idx:after])
            idx = after
            continue

        macro = macros[name]
        if macro.is_complex:
            out.append(text[idx:after])
            idx = after
            continue

        if macro.num_args == 0:
            rendered = _expand_fragment(macro.template, macros, depth_remaining - 1)
            out.append(rendered)
            idx = after
            continue

        consumed = _consume_invocation_args(text, after, macro.num_args)
        if consumed is None:
            # Leave untouched when arguments aren't parseable.
            out.append(text[idx:after])
            idx = after
            continue
        args, idx = consumed
        # Arguments are part of the current body text; expand within them at the
        # current remaining depth budget.
        args = [_expand_fragment(arg, macros, depth_remaining) for arg in args]
        rendered = macro.template
        for pos, arg in enumerate(args, start=1):
            rendered = rendered.replace(f"#{pos}", arg)
        rendered = _expand_fragment(rendered, macros, depth_remaining - 1)
        out.append(rendered)
    return "".join(out)


def expand_body(body: str, macros: dict[str, MacroDef]) -> str:
    # Expand up to 2 levels of macro nesting.
    return _expand_fragment(body, macros, 2)


def write_warnings_log(path: Path, complex_macros: Iterable[MacroDef]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for macro in sorted(complex_macros, key=lambda m: m.name):
        reason = macro.complex_reason or "complex macro"
        lines.append(f"{macro.canonical_definition()}  # not expanded: {reason}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> int:
    args = parse_args()
    in_path = Path(args.input).expanduser()
    out_path = Path(args.output).expanduser()
    warnings_path = Path(args.warnings_log).expanduser()

    tex = in_path.read_text(encoding="utf-8")
    preamble, body = _split_preamble(tex)
    raw_macros = extract_preamble_macros(preamble)
    macros = _apply_complexity_from_nesting(raw_macros)

    # Reduce pure-styling macros to pass-through (#1) form before expansion.
    reduced_macros = _reduce_styling_macros(macros)
    reduced_names = {
        name for name, m in reduced_macros.items() if m.template != macros[name].template
    }
    if reduced_names:
        preamble = _rewrite_preamble_styling_macros(preamble, reduced_names)

    complex_macros = [m for m in reduced_macros.values() if m.is_complex]
    write_warnings_log(warnings_path, complex_macros)

    expanded_body = expand_body(body, reduced_macros)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(preamble + expanded_body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
