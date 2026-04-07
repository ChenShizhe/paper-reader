#!/usr/bin/env python3
"""Generate one prompt file per downloaded paper from the acquisition list."""

import argparse
import os
import re
import sys
from pathlib import Path

_PAPER_BANK = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
_SKILLS_ROOT = os.environ.get("SKILLS_ROOT", os.path.expanduser("~/Documents/skills"))
_VAULT_ROOT = os.environ.get("VAULT_ROOT", os.path.expanduser("~/Documents/citadel"))


def expand(path_str):
    return Path(path_str).expanduser()


def detect_source_type(raw_dir: Path) -> str:
    """Return 'latex' if raw_dir contains .tex or .tar.gz files, else 'pdf'."""
    if raw_dir.exists():
        for f in raw_dir.iterdir():
            if f.suffix == ".tex" or f.name.endswith(".tar.gz"):
                return "latex"
    return "pdf"


PROMPT_TEMPLATE = """\
# Paper Reader Session: {title}

## Step 1: Read Context Files
Read the following files in $PAPER_BANK/session-context/ before doing anything else:
1. skill-paths.md
2. paper-reader-conventions.md
3. Any identity/preference files present in the directory.

## Step 2: Read the Paper
Use the paper-reader skill. Invoke it with:

- cite_key: {cite_key}
- title: {title}
- source_type: {source_type}
- source_path: $PAPER_BANK/raw/{cite_key}/
- paper_bank_dir: $PAPER_BANK/{cite_key}/
- vault_root: $VAULT_ROOT/
- acquisition_list: $PAPER_BANK/acquisition-list.md
- reference_queue: $VAULT_ROOT/literature/reference-queue.md

Use the paper-reader skill at $SKILLS_ROOT/paper-reader/SKILL.md.
Use the Direct Source Entry Point mode (no manifest required).

## Step 3: Post-Session Cleanup
After paper-reader completes:
1. Use experience-logger to log this session.
2. Move this prompt file to $PAPER_BANK/prompts/done/{cite_key}-prompt.md
"""


def parse_acquisition_list(path: Path):
    """Parse acquisition-list.md and return list of dicts for rows with status 'downloaded'."""
    rows = []
    if not path.exists():
        return rows

    header = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("|---") or line.startswith("| ---"):
            continue
        if re.match(r"^\|.*\|$", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if header is None:
                header = cells
            else:
                if len(cells) >= len(header):
                    row = dict(zip(header, cells))
                    if row.get("status", "").strip() == "downloaded":
                        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Generate prompt files for downloaded papers."
    )
    parser.add_argument(
        "--acquisition-list",
        default=os.path.join(_PAPER_BANK, "acquisition-list.md"),
        help="Path to acquisition-list.md (default: $PAPER_BANK/acquisition-list.md)",
    )
    parser.add_argument(
        "--prompts-dir",
        default=os.path.join(_PAPER_BANK, "prompts"),
        help="Output directory for prompt files (default: $PAPER_BANK/prompts/)",
    )
    parser.add_argument(
        "--session-context-dir",
        default=os.path.join(_PAPER_BANK, "session-context"),
        help="Path to session-context folder (default: $PAPER_BANK/session-context/)",
    )
    parser.add_argument(
        "--paper-reader-skill",
        default=os.path.join(_SKILLS_ROOT, "paper-reader", "SKILL.md"),
        help="Path to paper-reader SKILL.md (default: $SKILLS_ROOT/paper-reader/SKILL.md)",
    )
    parser.add_argument(
        "--vault-root",
        default=_VAULT_ROOT,
        help="Vault root directory (default: $VAULT_ROOT/)",
    )
    args = parser.parse_args()

    acquisition_list = expand(args.acquisition_list)
    prompts_dir = expand(args.prompts_dir)
    paper_bank_raw = Path(_PAPER_BANK) / "raw"

    prompts_dir.mkdir(parents=True, exist_ok=True)

    rows = parse_acquisition_list(acquisition_list)

    # Process source: user rows before source: reference-queue rows.
    # Missing source column is treated as 'user'. Within each tier, original
    # file order is preserved (Python sort is stable).
    rows.sort(key=lambda r: 0 if r.get("source", "user").strip() != "reference-queue" else 1)

    generated = 0

    for row in rows:
        cite_key = row.get("cite_key", "").strip()
        title = row.get("title", "").strip()
        if not cite_key:
            continue

        raw_dir = paper_bank_raw / cite_key
        source_type = detect_source_type(raw_dir)

        content = PROMPT_TEMPLATE.format(
            cite_key=cite_key,
            title=title,
            source_type=source_type,
        )

        out_path = prompts_dir / f"{cite_key}-prompt.md"
        out_path.write_text(content, encoding="utf-8")
        generated += 1

    print(f"Generated {generated} prompt files in {prompts_dir}")


if __name__ == "__main__":
    main()
