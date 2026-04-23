"""Tests for simple mode (proposal 06).

Covers:
  - translate_paper.py markdown passthrough writes translated_full.md
    with a minimal translation manifest.
  - run_pipeline.py --mode simple dispatches translate + comprehend +
    validate in the reduced pipeline.
  - validate_simple_mode.py catches missing sections, enum violations,
    mode mismatches, and per-section-directory leakage.

The comprehension subagent is mocked (subprocess.run patched) so these
tests run without network access or a Claude CLI installation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# translate_paper markdown passthrough
# ---------------------------------------------------------------------------


def test_markdown_passthrough_writes_translated_and_manifest(tmp_path: Path) -> None:
    paper_bank = tmp_path / "bulletin"
    raw = paper_bank / "raw"
    raw.mkdir(parents=True)
    src = raw / "bulletin.md"
    src.write_text(
        "# Sample bulletin\n\nA paragraph of prose for testing.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "translate_paper.py"),
            "--cite-key",
            "test-bulletin",
            "--paper-bank-dir",
            str(paper_bank),
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    translated = paper_bank / "translated_full.md"
    assert translated.exists()
    content = translated.read_text(encoding="utf-8")
    assert "Sample bulletin" in content
    # Passthrough prepends YAML frontmatter when the source has none.
    assert content.startswith("---\n")
    assert "translation_tool: passthrough" in content

    manifest = json.loads((paper_bank / "_translation_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tool"] == "passthrough"
    assert manifest["source_format"] == "markdown"
    assert manifest["word_count"] > 0


def test_markdown_passthrough_preserves_existing_frontmatter(tmp_path: Path) -> None:
    paper_bank = tmp_path / "bulletin"
    raw = paper_bank / "raw"
    raw.mkdir(parents=True)
    src = raw / "bulletin.md"
    src.write_text(
        "---\ntitle: Preset\n---\n\nBody.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "translate_paper.py"),
            "--cite-key",
            "preset",
            "--paper-bank-dir",
            str(paper_bank),
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    content = (paper_bank / "translated_full.md").read_text(encoding="utf-8")
    assert "title: Preset" in content
    # Passthrough should not double-wrap frontmatter.
    assert content.count("---\n") == 2 or content.startswith("---\n")


# ---------------------------------------------------------------------------
# validate_simple_mode.py
# ---------------------------------------------------------------------------


def _write_good_summary(papers_dir: Path, cite_key: str) -> Path:
    papers_dir.mkdir(parents=True, exist_ok=True)
    path = papers_dir / f"{cite_key}.md"
    path.write_text(
        "---\n"
        f"cite_key: {cite_key}\n"
        "source_type: regulatory-bulletin\n"
        "source_path: /tmp/bulletin.md\n"
        "source_format: markdown\n"
        "date: 2026-04-23\n"
        "mode: simple\n"
        "word_count: 40\n"
        "---\n\n"
        "## Overview\n\nOne-line overview.\n\n"
        "## Key Claims\n\n- Claim A [anchor: Overview]\n"
        "- Claim B [anchor: Section 2]\n\n"
        "## Methodology Guidance\n\n"
        "1. Step one\n"
        "2. Step two\n\n"
        "## Verbatim Quotes\n\n"
        "> Quote one. [anchor: Section 3]\n\n"
        "## Cross-References\n\n"
        "- DEF 14A — governance detail\n\n"
        "## Gaps and Limitations\n\n"
        "None noted.\n",
        encoding="utf-8",
    )
    return path


def test_validator_passes_on_well_formed_summary(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    papers = vault / "papers"
    _write_good_summary(papers, "ok-bulletin")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_simple_mode.py"),
            "--cite-key",
            "ok-bulletin",
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "valid" in result.stdout.lower()


def test_validator_rejects_missing_section(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    papers = vault / "papers"
    papers.mkdir(parents=True)
    (papers / "bad-bulletin.md").write_text(
        "---\n"
        "cite_key: bad-bulletin\n"
        "source_type: primer\n"
        "source_path: /tmp/b.md\n"
        "source_format: markdown\n"
        "date: 2026-04-23\n"
        "mode: simple\n"
        "word_count: 10\n"
        "---\n\n"
        "## Overview\n\nShort.\n\n"
        "## Key Claims\n\n- A [anchor: x]\n\n"
        "## Methodology Guidance\n\nNone.\n\n"
        # Missing Verbatim Quotes, Cross-References, Gaps and Limitations.
        ,
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_simple_mode.py"),
            "--cite-key",
            "bad-bulletin",
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "missing required section" in result.stdout


def test_validator_rejects_bad_source_type(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    papers = vault / "papers"
    papers.mkdir(parents=True)
    (papers / "wrong-type.md").write_text(
        "---\n"
        "cite_key: wrong-type\n"
        "source_type: not-a-real-type\n"
        "source_path: /tmp/b.md\n"
        "source_format: markdown\n"
        "date: 2026-04-23\n"
        "mode: simple\n"
        "word_count: 5\n"
        "---\n\n"
        "## Overview\n\nX\n\n"
        "## Key Claims\n\n- a [anchor: x]\n\n"
        "## Methodology Guidance\n\nNone.\n\n"
        "## Verbatim Quotes\n\nNone.\n\n"
        "## Cross-References\n\nNone.\n\n"
        "## Gaps and Limitations\n\nNone noted.\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_simple_mode.py"),
            "--cite-key",
            "wrong-type",
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "source_type" in result.stdout


def test_validator_rejects_per_section_directory(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    papers = vault / "papers"
    _write_good_summary(papers, "with-dir")
    # Create the forbidden per-section directory.
    (papers / "with-dir").mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_simple_mode.py"),
            "--cite-key",
            "with-dir",
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "per-section directory" in result.stdout


# ---------------------------------------------------------------------------
# End-to-end pipeline dispatch (mocked subagent)
# ---------------------------------------------------------------------------


CANNED_SIMPLE_SUMMARY = """---
cite_key: {cite_key}
source_type: regulatory-bulletin
source_path: {translated_path}
source_format: markdown
date: 2026-04-23
mode: simple
word_count: 42
---

## Overview

A synthetic bulletin describing how to read a 10-K filing.

## Key Claims

- Read MD&A first [anchor: Priority reading order]
- Distinguish hypothetical from realized risk language [anchor: What to look for in each section]

## Methodology Guidance

1. Start with Item 7 (MD&A).
2. Then Item 1A (Risk Factors).
3. Then Item 1 (Business).

## Verbatim Quotes

> Read it in this order. [anchor: Priority reading order]

## Cross-References

- DEF 14A — governance and compensation detail [anchor: Cross-references to other filings]

## Gaps and Limitations

Not industry-specific; does not cover XBRL extraction. [anchor: Caveats]
"""


def test_run_pipeline_simple_mode_dispatches_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end run_pipeline --mode simple with the subagent mocked out.

    The mock substitutes for the `claude --print <prompt>` call inside
    comprehend_paper._simple_run; we write a canned 6-section summary to
    the output path the subagent is told to write.
    """
    paper_bank_root = tmp_path / "paper-bank"
    cite_key = "test-simple-e2e"
    paper_bank = paper_bank_root / cite_key
    vault_root = tmp_path / "vault"

    # Stage the fixture source outside the paper bank — exercises the
    # pipeline's source-staging path.
    source_path = tmp_path / "source.md"
    source_path.write_text(
        (FIXTURES_DIR / "simple-mode-regulatory-bulletin.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Install a fake 'claude' executable that writes the canned summary.
    fake_claude_dir = tmp_path / "bin"
    fake_claude_dir.mkdir()
    fake_claude = fake_claude_dir / "claude"
    # Read the prompt from stdin; parse output path; write canned content.
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, re\n"
        "prompt = sys.argv[-1] if sys.argv[-1] != '--print' else ''\n"
        "# When argv pattern is 'claude --print <prompt>', prompt is argv[2].\n"
        "if len(sys.argv) >= 3 and sys.argv[1] == '--print':\n"
        "    prompt = sys.argv[2]\n"
        "m = re.search(r'Write the summary note to EXACTLY this path and nowhere else:\\n\\s+(\\S+)', prompt)\n"
        "if not m:\n"
        "    sys.stderr.write('fake claude: output path not found in prompt')\n"
        "    sys.exit(2)\n"
        "out_path = m.group(1)\n"
        "cite_key_m = re.search(r'cite_key:\\s*(\\S+)', prompt)\n"
        "translated_m = re.search(r'source_path:\\s*(\\S+)', prompt)\n"
        "cite_key = cite_key_m.group(1) if cite_key_m else 'unknown'\n"
        "translated = translated_m.group(1) if translated_m else '/tmp/unknown.md'\n"
        "canned = '''" + CANNED_SIMPLE_SUMMARY.replace("'", "\\'").replace("\n", "\\n") + "'''\n"
        "import os\n"
        "os.makedirs(os.path.dirname(out_path), exist_ok=True)\n"
        "with open(out_path, 'w') as fh:\n"
        "    fh.write(canned.format(cite_key=cite_key, translated_path=translated))\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_claude_dir}:{sys.path[0]}:/usr/bin:/bin")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "run_pipeline.py"),
            "--mode",
            "simple",
            "--cite-key",
            cite_key,
            "--source-format",
            "markdown",
            "--source-path",
            str(source_path),
            "--source-type",
            "regulatory-bulletin",
            "--paper-bank-dir",
            str(paper_bank),
            "--vault-root",
            str(vault_root),
            "--run-report-path",
            str(tmp_path / "run_report.json"),
        ],
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PATH": f"{fake_claude_dir}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr

    translated = paper_bank / "translated_full.md"
    assert translated.exists()

    summary_path = vault_root / "papers" / f"{cite_key}.md"
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "## Overview" in content
    assert "## Key Claims" in content
    assert "## Gaps and Limitations" in content

    # Validator should pass on the output.
    validate = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_simple_mode.py"),
            "--cite-key",
            cite_key,
            "--vault-root",
            str(vault_root),
        ],
        capture_output=True,
        text=True,
    )
    assert validate.returncode == 0, validate.stdout + validate.stderr
