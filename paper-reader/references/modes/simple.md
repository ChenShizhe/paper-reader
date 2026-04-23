# Simple mode

Simple mode targets short-form non-academic content that benefits from uniform structured extraction but does not warrant the full academic pipeline. Typical inputs: regulatory investor-education bulletins, credit rating-action press releases, sell-side analyst snippets, equity-research summaries, industry-press deep-dives, practitioner methodology primers, short-form legal commentary.

Indicative upper bound: around 15 KB of markdown or 6–8 pages of formatted content. Longer structured academic papers use the default `paper` mode; multi-chapter institutional reports use `book` mode; primary SEC filings (10-K, 10-Q, DEF 14A, 8-K) use their own dedicated modes.

## When to use

| Input shape | Mode |
|---|---|
| ~2–15 KB, short commentary / primer / rating action | `simple` |
| Academic research paper, 10–50 pages | `paper` |
| Book-length institutional report with chapter plan | `book` |
| Sell-side value-chain / thematic report with exhibit tables | `chain_map` |
| Primary SEC Form 10-K filing | `10k` |

## Invocation

```bash
python3 scripts/run_pipeline.py \
  --mode simple \
  --cite-key <cite_key> \
  --source-format <markdown|html|pdf|text> \
  --source-path <path> \
  --source-type <primer|rating-action|sell-side-note|earnings-commentary|industry-press|regulatory-bulletin|short-academic-note|other> \
  --paper-bank-dir <PAPER_BANK>/<cite_key> \
  --vault-root <VAULT_ROOT>
```

`--source-type` is required; it drives the comprehension subagent's emphasis (e.g., a rating action surfaces drivers and outlook horizon; a primer surfaces the method taught).

## Pipeline steps

Simple mode runs a minimal pipeline — no segmentation, no per-section comprehension fan-out, no catalog, no claims sidecar, no Zotero, no `refs.bib`:

1. **Translate passthrough / extraction.** `translate_paper.py` with `--format markdown` copies the source to `<paper-bank>/translated_full.md` unchanged. `html` sources go through pandoc; `pdf` goes through PyMuPDF (simple-mode content is assumed born-digital); `text` is wrapped in a markdown code block.
2. **Comprehension (single subagent).** `comprehend_paper.py --mode simple` dispatches one subagent that reads the full translated content and writes the 6-section summary directly to `<vault-root>/papers/<cite_key>.md`. Source-type-specific guidance is injected into the prompt.
3. **Validation.** `validate_simple_mode.py` checks frontmatter completeness, required sections present and ordered, no per-section subdirectory created, and that declared `word_count` matches the body within tolerance.

## Output schema

```yaml
---
cite_key: <slug>
source_type: primer | rating-action | sell-side-note | earnings-commentary | industry-press | regulatory-bulletin | short-academic-note | other
source_path: <path to source file>
source_format: markdown | html | pdf | text
date: YYYY-MM-DD
mode: simple
word_count: <int>
---
```

Fixed section headings, in order:

1. `## Overview` — 1–2 sentences. What the source is, who wrote it, what question it addresses.
2. `## Key Claims` — bulleted list. Each claim ends with `[anchor: <section heading or quoted phrase>]` for traceability.
3. `## Methodology Guidance` — present if the source teaches a method, reading strategy, or analytical procedure; otherwise `None.`. Numbered steps or bullets.
4. `## Verbatim Quotes` — up to three short quotes (≤ 3 sentences each) carrying the highest methodology or decision-relevant signal. Each tagged with `[anchor: ...]`.
5. `## Cross-References` — other filings, frameworks, datasets, regulations, or named methods mentioned in the source. Each as `<reference> — <why it matters>`.
6. `## Gaps and Limitations` — what the source does not address; where the argument is hand-wavy or depends on unstated assumptions. `None noted.` only if genuinely thorough.

Sections are never reordered or renamed. An inapplicable section uses `None.` or `None noted.` as its sole content line.

## What simple mode does NOT produce

- Per-section note directory (`<vault-root>/papers/<cite_key>/`).
- Claims sidecar (`<vault-root>/claims/<cite_key>.json`).
- `refs.bib` entry (no academic citation semantics).
- Zotero sync.
- `_catalog.yaml`, `_segment_manifest.json`, or any paper-bank machinery beyond `translated_full.md` and `_translation_manifest.json`.

## Input bounds

Sources longer than ~15 KB of body content should use a different mode (`paper` for structured academic work, `book` for chapter-organized reports, `10k` for primary SEC filings). Simple mode's single-subagent pass does not chunk or re-segment; very long inputs risk model-context issues.
