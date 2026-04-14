# Setup Guide

This guide walks through setting up paper-reader on a fresh machine. It is written for both human users and AI agents (Claude Code, Codex, etc.).

## Prerequisites

| Requirement | Check command | Notes |
|-------------|--------------|-------|
| Python 3.12+ | `python3 --version` | macOS: `brew install python@3.12`. Windows: download from python.org |
| pip | `python3 -m pip --version` | Usually bundled with Python |
| Claude Code CLI | `claude --version` | Or any agent runtime that supports SKILL.md instructions |
| Git | `git --version` | For cloning this repo |

### Optional dependencies

| Dependency | What it enables | Install |
|------------|----------------|---------|
| [Zotero](https://www.zotero.org/) + Zotero MCP | Citation library search and sync | See [Zotero MCP setup](#zotero-mcp-setup) below |
| [MinerU](https://github.com/opendatalab/MinerU) | PDF-to-Markdown translation (for papers without LaTeX source) | `pip install mineru` |
| [memory-skill](https://github.com/ChenShizhe/memory-skill) | Session memory, vault integration | See that repo's SETUP.md |

## Step 1: Clone the repo

```bash
git clone https://github.com/ChenShizhe/paper-reader.git
cd paper-reader
```

## Step 2: Install Python dependencies

```bash
python3 -m pip install -r paper-reader/requirements.txt
```

Key packages installed: PyYAML, Pydantic, bibtexparser, PyMuPDF (PDF reading), pymupdf4llm.

## Step 3: Create the directory structure

```bash
# Paper storage (sources, translations, reading artifacts)
mkdir -p ~/Documents/paper-bank

# Knowledge vault (optional, for vault integration)
mkdir -p ~/Documents/citadel

# Working directory for pipeline intermediates
mkdir -p ~/.research-workdir
```

**Windows equivalent:**
```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\Documents\paper-bank"
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\Documents\citadel"
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.research-workdir"
```

## Step 4: Run preflight checks

```bash
# Check paper-reader extraction pipeline
python3 paper-reader/scripts/preflight_extraction.py

# Check paper-discovery search backends
python3 paper-discovery/scripts/preflight_discovery.py
```

**Done signal:** Both scripts should print a status table. arXiv and OpenAlex should show `OK`. Zotero will show `SKIP` if not configured — that is fine.

## Step 5: Install the skills

```bash
SKILL_DIR=~/.claude/skills
mkdir -p "$SKILL_DIR"

cp -R paper-reader "$SKILL_DIR/paper-reader"
cp -R paper-discovery "$SKILL_DIR/paper-discovery"
cp -R paper-batch-coordinator "$SKILL_DIR/paper-batch-coordinator"
```

## Step 6: Verify with a test paper

The fastest way to verify the full pipeline:

```bash
# Search arXiv for a paper (no API key needed)
python3 paper-discovery/scripts/search_arxiv.py \
  --query "attention mechanisms transformers" \
  --max-results 3 \
  --output ~/.research-workdir/test_arxiv_results.json

# Check the output exists and has entries
python3 -c "import json; d=json.load(open('$HOME/.research-workdir/test_arxiv_results.json')); print(f'Found {len(d)} papers'); assert len(d) > 0"
```

**Done signal:** The second command should print `Found N papers` with N > 0.

## API Keys

**None required for core functionality.** arXiv, OpenAlex, and PubMed are free public APIs.

Optional API keys for enhanced features:

| Variable | Purpose | How to get |
|----------|---------|-----------|
| `ZOTERO_LIBRARY_ID` | Zotero library access | [Zotero API settings](https://www.zotero.org/settings/keys) |
| `ZOTERO_API_KEY` | Zotero API authentication | Same page as above |

### Zotero MCP setup

If you use Zotero for citation management:

1. Install the Zotero desktop app
2. Install a Zotero MCP server at `~/Documents/MCPs/zotero-mcp` (search for "zotero mcp server" for available implementations)
3. Set `ZOTERO_LIBRARY_ID` and `ZOTERO_API_KEY` in your shell profile
4. Re-run `preflight_discovery.py` — Zotero should now show `OK`

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PAPER_BANK` | `~/Documents/paper-bank` | Root for paper storage |
| `VAULT_ROOT` | `~/Documents/citadel` | Knowledge vault root |
| `WORK_ROOT` | `~/.research-workdir` | Pipeline working directory |
| `ZOTERO_MCP_ROOT` | `~/Documents/MCPs/zotero-mcp` | Zotero MCP server path |
| `ZOTERO_LIBRARY_ID` | *(none)* | Zotero library ID |
| `ZOTERO_API_KEY` | *(none)* | Zotero API key |

## Cross-Platform Notes

- **macOS:** Works out of the box.
- **Windows:** Use `%USERPROFILE%\Documents\paper-bank` etc. The shell scripts `download_arxiv_sources.sh` and `download_pdfs.sh` require Git Bash or WSL. All Python scripts work natively.
- **Linux:** Same as macOS.

## Running Tests

```bash
python3 -m unittest discover -s paper-reader/tests -p 'test_*.py'
python3 -m unittest discover -s paper-discovery/tests -p 'test_*.py'
```

Some tests require Zotero MCP and skip automatically if absent.

## What to do next

1. Ask your agent to use `paper-discovery` to find papers on a topic.
2. Ask it to use `paper-reader` to do a deep read of one paper.
3. For batch operations, use `paper-batch-coordinator`.
