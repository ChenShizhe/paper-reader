# paper-reader

A literature pipeline for AI agents: discover papers, read them deeply, and coordinate batch reading sessions.

## Quick Start (new machine)

```bash
git clone https://github.com/ChenShizhe/paper-reader.git
cd paper-reader
python3 -m pip install -r paper-reader/requirements.txt
mkdir -p ~/Documents/paper-bank ~/Documents/citadel ~/.research-workdir
python3 paper-discovery/scripts/preflight_discovery.py
```

**Done:** Preflight shows `OK` for arXiv and OpenAlex. No API keys needed for core functionality. See [SETUP.md](SETUP.md) for the full walkthrough including Zotero setup and cross-platform notes.

## Skills included

| Skill | Purpose |
|-------|---------|
| **paper-reader** | Deep structured reading of individual papers (LaTeX or PDF). Produces section-level comprehension artifacts, cross-reference notes, and vault-ready summaries. |
| **paper-discovery** | Discover candidate papers via arXiv, OpenAlex, PubMed, Zotero, and web search. Builds a `paper_manifest.json` for batch coordination. |
| **paper-batch-coordinator** | Orchestrates multi-paper reading sessions: manifest intake, source acquisition, sequential reading dispatch, and vault integration. |

## Prerequisites

- Python 3.12+
- An AI agent runtime that supports skill-based instructions (e.g., Claude Code, Codex)

### Optional dependencies

| Dependency | What it enables |
|------------|----------------|
| [Zotero MCP](https://github.com/example/zotero-mcp) | Zotero library search and citation sync. Without it, Zotero-based discovery and sync are skipped. |
| [MinerU](https://github.com/opendatalab/MinerU) | PDF-to-Markdown translation for the PDF reading path. Without it, only LaTeX sources are supported. |
| [memory-skill](https://github.com/ChenShizhe/memory-skill) | Session memory and experience logging. Without it, the pipeline runs standalone but loses session context and vault integration. |
| [knowledge-maester](https://github.com/ChenShizhe/memory-skill) | Vault note writing (part of memory-skill). Without it, `_vault-write-requests.json` files accumulate but are not applied. |

## Configuration

Set these environment variables to override default paths:

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPER_BANK` | `~/Documents/paper-bank` | Root directory for paper storage (sources, translations, reading artifacts) |
| `VAULT_ROOT` | `~/Documents/citadel` | Root of the knowledge vault for finished reading notes |
| `WORK_ROOT` | `~/.research-workdir` | Working directory for pipeline intermediate artifacts |
| `SKILLS_ROOT` | *(relative to repo)* | Root directory of other skill installations (for cross-skill references) |
| `ZOTERO_MCP_ROOT` | `~/Documents/MCPs/zotero-mcp` | Path to Zotero MCP server installation |
| `ZOTERO_LIBRARY_ID` | *(none)* | Zotero library ID for remote API access |
| `ZOTERO_API_KEY` | *(none)* | Zotero API key for remote API access |

## Quick start

1. **Preflight check** -- verify your environment:
   ```bash
   python3 paper-reader/scripts/preflight_extraction.py
   python3 paper-discovery/scripts/preflight_discovery.py
   ```

2. **Discover papers** -- build a manifest of candidate papers:
   ```bash
   # Use paper-discovery skill instructions to search arXiv, OpenAlex, etc.
   # Produces paper_manifest.json under $WORK_ROOT
   ```

3. **Read a paper** -- run the full reading pipeline on a single paper:
   ```bash
   python3 paper-reader/scripts/run_pipeline.py --cite-key <cite_key>
   ```

4. **Batch coordination** -- use paper-batch-coordinator to orchestrate reading multiple papers from a manifest.

## Directory layout

```
paper-reader/
  paper-reader/         # Core reading skill
    SKILL.md            # Skill instructions
    scripts/            # 90+ pipeline scripts
    tests/              # Unit tests
    references/         # Templates and validation rules
  paper-discovery/      # Discovery skill
    SKILL.md            # Skill instructions
    scripts/            # Discovery and search scripts
    tests/              # Unit tests and fixtures
    references/         # Contracts and strategies
  paper-batch-coordinator/
    SKILL.md            # Batch coordination instructions
  README.md
  .gitignore
```

## Running tests

```bash
python3 -m unittest discover -s paper-reader/tests -p 'test_*.py'
python3 -m unittest discover -s paper-discovery/tests -p 'test_*.py'
```

Some tests require Zotero MCP to be installed and will be skipped automatically if it is absent.

## License

MIT
