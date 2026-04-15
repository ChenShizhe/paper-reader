# paper-reader

A literature pipeline for AI agents: discover papers, read them deeply, and coordinate batch reading sessions.

Built as a set of [Claude Code](https://claude.ai/claude-code) skills -- model-agnostic markdown instructions that any LLM agent runtime can follow.

**No API keys required** for core functionality (arXiv, OpenAlex).

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/ChenShizhe/paper-reader.git
cd paper-reader
python3 -m pip install -r paper-reader/requirements.txt

# 2. Create working directories
mkdir -p ~/Documents/paper-bank ~/Documents/citadel ~/.research-workdir

# 3. Preflight check -- verify your environment
python3 paper-discovery/scripts/preflight_discovery.py
python3 paper-reader/scripts/preflight_extraction.py
```

Both preflight scripts should report `OK`. See [SETUP.md](SETUP.md) for the full walkthrough including Zotero setup and cross-platform notes.

## Usage example

```
# 1. Discover papers on a topic (via paper-discovery skill)
/paper-discovery  "Find recent papers on transformer architectures"
#    -> produces paper_manifest.json under $WORK_ROOT

# 2. Read a paper from the manifest (via paper-reader skill)
/paper-reader  "Read cite_key=vaswani2017attention"
#    -> produces section-level notes and a summary in $PAPER_BANK

# 3. Check the vault output
ls ~/Documents/citadel/literature/
#    -> vault note written by knowledge-maester after reading completes
```

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
| Zotero MCP server | Zotero library search and citation sync. Without it, Zotero-based discovery and sync are skipped. |
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
