# Chapter Plan Schema

A chapter plan is a Markdown file with a YAML frontmatter block followed by a
GFM pipe table. It tells `book` mode which chapters to read, at what depth, and
how to label their extracted claims. The file is parsed by
`scripts/chapter_plan_parser.py` via `parse_chapter_plan()`.

---

## Frontmatter schema

The frontmatter block opens and closes with `---` delimiters and must appear at
the very start of the file.

| Field | Required | Default | Type | Description |
|---|---|---|---|---|
| `cite_key` | yes | — | string | Catalog key for the source document (e.g. `iea_weo_2025`). Used to name output directories and to prefix per-chapter sidecar files. |
| `source_pdf` | yes | — | string | Relative path to the PDF being processed (e.g. `downloads/iea_weo_2025.pdf`). |
| `claim_domain` | yes | — | string | Domain applied to every extracted claim. Use `institutional` for book-mode reports; this controls which claim types the validator permits. |
| `page_offset` | no | `0` | integer | Integer added to every `page_range` value before the subagent reads the file. Use this when the PDF's physical page 1 is not the document's logical page 1 (e.g. a 12-page front matter means `page_offset: 12`). |
| `synthesis_target_words` | no | `5000` | integer | Soft target word count for the synthesis document. Sections scale proportionally; `## Overview` is always ≤ 100 words. |

**Example frontmatter block:**

```yaml
---
cite_key: iea_weo_2025
source_pdf: downloads/iea_weo_2025.pdf
claim_domain: institutional
page_offset: 0
synthesis_target_words: 6000
---
```

Missing required fields raise `ValueError` immediately and abort the run before
any subagent is launched.

---

## Table schema

Each row in the GFM pipe table maps to one potential subagent dispatch. All six
columns below are required; additional columns are silently passed through as
supplementary context.

| Column | Type | Allowed values | Description |
|---|---|---|---|
| `slug` | string | Any non-empty, unique string | Short identifier for the chapter. Used to name the subagent output file (`extractions/<cite_key>/chapters/<slug>.json`). Must be unique within the plan. |
| `page_range` | string | `N-M` where N ≤ M | Inclusive physical page range (after applying `page_offset`). Both N and M must be positive integers. |
| `role` | string | Free text | Human-readable label for the chapter's function (e.g. `executive_summary`, `scenario_chapter`, `methodology_annex`). Passed verbatim to the subagent prompt. |
| `depth` | enum | `deep`, `summary`, `skip` | Extraction depth. `deep` — full claim extraction including methodology and assumptions. `summary` — top-level findings and policy recommendations only. `skip` — no subagent is launched; chapter is noted in synthesis `## Gaps`. |
| `include_in_synthesis` | boolean | `true`, `false` (also `yes`/`no`, `1`/`0`) | Whether this chapter's sidecar is passed to the synthesis step. Set `false` for annexes or chapters read for completeness but not for cross-chapter conclusions. |
| `domain_lens` | string | Free text | Thematic label for extraction (e.g. `energy-transition`, `climate-policy`). All rows with `include_in_synthesis: true` must share a single label; mixing labels raises a validation error. |

**Optional column:**

| Column | Type | Description |
|---|---|---|
| `subagent_prompt` | string | Custom prompt fragment appended to the standard dispatcher template for this row. Leave blank to use the default template. |

---

## Authoring workflow

### 1 — Eyeball the PDF's chapter structure

Open the PDF and scan the table of contents (usually pages 1–5). Note:

- Chapter titles and their starting pages.
- Whether the document has a statistical annex, glossary, or bibliography that
  you may want to `skip` or read at `summary` depth.
- Any executive summary — almost always worth `deep` + `include_in_synthesis: true`.

If the PDF has no machine-readable ToC, use the bookmarks panel in your PDF
viewer or run `scripts/pdf_probe.py` to list detected heading pages.

### 2 — Assign page ranges

Use the physical page numbers shown in the PDF viewer (bottom toolbar), not the
logical numbers printed inside the document. If the first content page is
physically page 13, set `page_offset: 12` in the frontmatter so subagents
receive logical page references without manual arithmetic.

Check that `N ≤ M` for every row and that no row starts at page 0.

### 3 — Pick depth

Use this decision tree:

- **`deep`** — chapters central to your research question; chapters with
  quantitative results, scenarios, or policy recommendations you need to capture
  completely.
- **`summary`** — background chapters, introductory surveys, or technical
  appendices where only the headline findings matter.
- **`skip`** — bibliography, index, legal notices, or chapters entirely outside
  your scope. Skipped rows cost no API budget and appear in synthesis `## Gaps`.

When in doubt, prefer `summary` over `skip` — you can always reprocess a chapter
at `deep` later, but `skip` produces no output to revisit.

### 4 — Write a useful `domain_lens`

The `domain_lens` narrows which claims the subagent foregrounds during
extraction. A good lens is:

- **Specific enough** to filter noise — `energy-transition` is more useful than
  `economics`.
- **Consistent** — use the same string across all `include_in_synthesis: true`
  rows; the validator rejects mixed labels.
- **Descriptive** — a future reader should understand your intent from the label
  alone (e.g. `climate-finance-policy` over `finance`).

Rows with `include_in_synthesis: false` may carry any lens value without
triggering the mixed-label error.

---

## Worked example

The IEA World Energy Outlook 2025 (340 pages) illustrates every depth level and
demonstrates the `include_in_synthesis` distinction.

```markdown
---
cite_key: iea_weo_2025
source_pdf: downloads/iea_weo_2025.pdf
claim_domain: institutional
page_offset: 0
synthesis_target_words: 6000
---

| slug              | page_range | role               | depth   | include_in_synthesis | domain_lens       |
|-------------------|------------|--------------------|---------|----------------------|-------------------|
| exec_summary      | 1-18       | executive_summary  | deep    | true                 | energy-transition |
| ch1_context       | 19-55      | scenario_chapter   | deep    | true                 | energy-transition |
| ch2_demand        | 56-98      | scenario_chapter   | deep    | true                 | energy-transition |
| ch3_supply        | 99-145     | scenario_chapter   | deep    | true                 | energy-transition |
| ch4_investment    | 146-195    | scenario_chapter   | deep    | true                 | energy-transition |
| ch5_policy        | 196-240    | policy_chapter     | deep    | true                 | energy-transition |
| ch6_sectors       | 241-289    | sector_overview    | summary | true                 | energy-transition |
| statistical_annex | 290-340    | annex              | summary | false                | energy-transition |
```

**Annotation:**

- `exec_summary` — `deep` + `true`: the executive summary concentrates key
  projections; full extraction maximises synthesis recall.
- `ch1_context` through `ch5_policy` — core scenario and policy chapters; all
  `deep` and included so the synthesis can compare scenarios across chapters.
- `ch6_sectors` — useful context but secondary to the scenario chapters; `summary`
  depth keeps API cost proportional to its analytical weight.
- `statistical_annex` — raw data tables; `summary` + `include_in_synthesis: false`
  means the subagent runs (for completeness) but its output is excluded from the
  cross-chapter synthesis merge.

---

## Validation errors

The parser raises `ValueError` with a descriptive message for every schema
violation. The most common errors are listed below with their exact message
patterns.

| Error | Parser message pattern | Fix |
|---|---|---|
| Missing `---` opening delimiter | `Chapter plan file must begin with a YAML frontmatter block (---)` | Ensure the file starts with exactly `---` on its own line. |
| Unclosed frontmatter block | `Frontmatter block is not closed (missing closing ---)` | Add a closing `---` line after the last frontmatter key. |
| Missing required frontmatter key | `Missing required frontmatter fields: ['cite_key']` (lists all missing keys) | Add the missing keys with valid values. |
| No table found in body | `No GFM table found in chapter plan body` | Add a pipe table after the frontmatter block; every row must begin with `\|`. |
| Missing required table column | `Table is missing required columns: ['domain_lens']` | Add the column to the header row; column names are case-insensitive. |
| Empty slug | `Row N: slug is empty` | Provide a non-empty unique string for every slug cell. |
| Malformed `page_range` | `Row N (slug='foo'): malformed page_range '5'; expected 'N-M'` | Use `start-end` format, e.g. `19-55`. |
| `page_range` start > end | `Row N (slug='foo'): page_range start 55 > end 19` | Swap start and end values. |
| Invalid `depth` value | `Row N (slug='foo'): depth 'full' must be one of ['deep', 'skip', 'summary']` | Use exactly `deep`, `summary`, or `skip`. |
| Invalid `include_in_synthesis` | `Row N (slug='foo'): include_in_synthesis 'maybe' must be a boolean` | Use `true` or `false` (also `yes`/`no`, `1`/`0`). |
| Duplicate slug | `Duplicate slug 'ch1_context' found at row 4 (first seen at row 2)` | Rename one of the duplicate rows. |
| Page range outside PDF | `Row N (slug='foo'): page_range '300-360' is outside PDF bounds 1-340` | Correct the page range to fit within the PDF's actual page count. |
| Mixed `domain_lens` labels | `Mixed domain_lens labels across synthesis rows: ['climate', 'energy-transition']` | Standardise to a single label across all `include_in_synthesis: true` rows. |
