# Mode: book

`book` mode processes long-form institutional documents — monographs, annual
energy-transition reports, multi-chapter research volumes — by dispatching one
reader subagent per chapter and then synthesising the results into a single
structured summary.

---

## Overview

Use `book` mode when the source document cannot be read as a single IMRaD paper:

- The document has an explicit chapter structure (≥ 3 chapters, each with its
  own scope and findings).
- Chapters are long enough that passing the full PDF to one subagent would
  exceed context limits or produce shallow coverage.
- The reading goal is cross-chapter pattern recognition (shared scenarios,
  conflicting projections, cumulative recommendations) in addition to
  per-chapter extraction.

**When NOT to use book mode.** A 30-page working paper with section numbers
is still `paper` mode. Use `book` only when the chapter plan has meaningful
per-chapter depth distinctions and the synthesis requires combining findings
across at least two chapters.

**Canonical example — IEA World Energy Outlook 2025.**
The IEA WEO 2025 is a 340-page institutional report structured into an
Executive Summary, five scenario chapters, three sector chapters, and a
Statistical Annex. Each chapter receives an independent subagent pass under
the `energy-transition` domain lens. The dispatcher runs chapters 1–8 at
`depth: deep` and the annex at `depth: summary`. The synthesis assembles a
500-word cross-scenario projection summary plus a full policy-recommendation
inventory.

Activated by:

```
run_pipeline.py --mode book --chapter-plan plans/iea_weo_2025.md
```

or equivalently:

```
comprehend_paper.py --mode book --chapter-plan plans/iea_weo_2025.md
```

---

## Inputs

### --chapter-plan path

`--chapter-plan` accepts a path to a Markdown file that contains:

1. A YAML frontmatter block (between `---` delimiters).
2. A GFM pipe table whose rows define individual chapters.

The file is parsed by `scripts/chapter_plan_parser.py` via `parse_chapter_plan()`.

### YAML frontmatter fields

| Field | Required | Default | Description |
|---|---|---|---|
| `cite_key` | yes | — | Catalog key for the source document (e.g. `iea_weo_2025`) |
| `source_pdf` | yes | — | Relative path to the PDF being processed |
| `claim_domain` | yes | — | Domain applied to all extracted claims (`institutional` for book mode) |
| `page_offset` | no | `0` | Integer to add to every page number in `page_range`; use when PDF logical pages differ from physical pages |
| `synthesis_target_words` | no | `5000` | Approximate target word count for the synthesis document |

All five fields are read by the dispatcher before any subagent is launched.
Missing required fields raise `ValueError` and abort the run.

### GFM table columns

Every row in the chapter table maps to one potential subagent dispatch.
The following columns are required:

| Column | Allowed values | Description |
|---|---|---|
| `slug` | Any non-empty string, unique within the plan | Short identifier used to name subagent output files |
| `page_range` | `N-M` where N ≤ M | Inclusive PDF page range for the chapter |
| `role` | Free text | Human-readable chapter role (e.g. `executive_summary`, `scenario_chapter`) |
| `depth` | `deep`, `summary`, `skip` | Controls extraction depth for this chapter |
| `include_in_synthesis` | `true` / `false` | Whether the chapter output is passed to the synthesis step |
| `domain_lens` | Free text | Thematic lens label applied during extraction (e.g. `energy-transition`) |

Additional columns are preserved in `ChapterRow.extra` and passed through
to the subagent prompt as supplementary context, but are not validated by
the parser.

**Minimal chapter plan example:**

```markdown
---
cite_key: iea_weo_2025
source_pdf: downloads/iea_weo_2025.pdf
claim_domain: institutional
page_offset: 0
synthesis_target_words: 5000
---

| slug              | page_range | role               | depth   | include_in_synthesis | domain_lens       |
|-------------------|------------|--------------------|---------|----------------------|-------------------|
| exec_summary      | 1-18       | executive_summary  | deep    | true                 | energy-transition |
| ch1_context       | 19-55      | scenario_chapter   | deep    | true                 | energy-transition |
| ch2_demand        | 56-98      | scenario_chapter   | deep    | true                 | energy-transition |
| ch3_supply        | 99-145     | scenario_chapter   | deep    | true                 | energy-transition |
| statistical_annex | 290-340    | annex              | summary | false                | energy-transition |
```

---

## Dispatch

The dispatcher iterates over the chapter table and launches one subagent per
row that has `depth` ≠ `skip`.

### Concurrency cap

At most **5** chapter subagents run concurrently. When N > 5, the dispatcher
queues remaining chapters and launches each new subagent as a slot becomes free.

### Retry-once semantics

If a chapter subagent exits with a non-zero return code or produces no output
file, the dispatcher retries that chapter exactly once. A second failure marks
the chapter as `failed` in the run manifest and continues with remaining
chapters. The synthesis step will note any failed chapters in its `## Gaps`
section.

### Output-path rule

Each subagent writes its extraction output to:

```
<output_dir>/<cite_key>/chapters/<slug>.json
```

where `output_dir` defaults to `extractions/` relative to the working
directory. The dispatcher collects all per-chapter JSON files before invoking
synthesis.

### Skip rows

Rows with `depth: skip` are recorded in the run manifest as `skipped` and
excluded from dispatch. No subagent is launched and no output file is written.

---

## Subagent prompt template

The dispatcher fills in one instance of this template per chapter row before
launching the subagent. Fields in `{{double_braces}}` are substituted by the
dispatcher; all other text is passed verbatim.

```
You are a chapter reader for book mode.

Document: {{cite_key}}
Source PDF: {{source_pdf}}
Chapter slug: {{slug}}
Pages: {{page_range}} (physical pages; apply page_offset={{page_offset}} if
       your reader reports logical page numbers)
Chapter role: {{role}}
Extraction depth: {{depth}}
Domain lens: {{domain_lens}}
Claim domain: {{claim_domain}}

{{#if extra_fields}}
Additional context from the chapter plan:
{{extra_fields}}
{{/if}}

Your task
---------
1. Read only the pages in the range above.
2. Extract all claims that fall within the allowed claim types for
   claim_domain={{claim_domain}}.  See references/modes/book.md § Claim types.
3. For cross-chapter connections known at this stage, use claim type
   `connection` with a `connected_claims` list (see § Cross-chapter claims).
4. Write your extraction to the path provided by the dispatcher.
5. Do not read pages outside {{page_range}}.
6. If depth is `summary`, extract only top-level findings and
   policy-recommendations; skip theorems, assumptions, and methodology details.

Output format: the standard claims sidecar JSON (schema_version 1) scoped to
this chapter. Set `cite_key` to "{{cite_key}}__{{slug}}" so synthesis can
distinguish per-chapter sidecars from the merged document sidecar.
```

---

## Synthesis

After all dispatched subagents complete, the dispatcher invokes the synthesis
step. Synthesis reads every chapter sidecar whose row has
`include_in_synthesis: true` and produces a single Markdown document at:

```
<output_dir>/<cite_key>/synthesis.md
```

### Required sections

The synthesis document must contain exactly these five top-level sections, in
this order:

1. **`## Overview`** — 2–4 sentence executive framing of the document's
   purpose and scope.
2. **`## Key Findings`** — Bulleted list of the most important empirical and
   analytical results, drawn from all included chapters.
3. **`## Policy Recommendations`** — All `policy-recommendation` claims merged
   and de-duplicated across chapters, grouped by `recommended_to` entity.
4. **`## Projections`** — All `projection` claims, grouped by `scenario_label`
   then sorted by `horizon_year`.
5. **`## Gaps`** — Any chapters that were skipped, failed, or have
   `include_in_synthesis: false`, with a brief note on what they covered.

### synthesis_target_words

The synthesis step uses `synthesis_target_words` from the frontmatter as a
soft target. Sections 2–4 are scaled proportionally; `## Overview` is always
≤ 100 words and `## Gaps` is always ≤ 50 words regardless of the target.

### Overlength handling

If the draft synthesis exceeds `synthesis_target_words × 1.25`, the synthesis
step compresses `## Key Findings` first (merging closely related bullets),
then `## Projections` (dropping lower-confidence entries), and finally
`## Policy Recommendations` (grouping by `recommended_to`). It does not
truncate sections wholesale.

---

## Claim types

`book` mode sets `claim_domain: institutional` by default (and requires the
chapter plan to declare `claim_domain: institutional` explicitly).

`validate_extraction.py` enforces the following claim-type subset when
`claim_domain` is `institutional`:

| Claim type              | Description                                                   |
|-------------------------|---------------------------------------------------------------|
| `theorem`               | Formally stated mathematical result or lemma.                 |
| `assumption`            | Explicit model or analysis assumption.                        |
| `methodology`           | Described procedure, algorithm, or design choice.             |
| `empirical`             | Observation or result backed by experiment or data.           |
| `connection`            | Stated relationship to another chapter, paper, or result.     |
| `limitation`            | Acknowledged scope restriction or failure mode.               |
| `policy-recommendation` | Normative recommendation directed at governments or bodies.   |
| `projection`            | Forward-looking quantitative estimate under a named scenario.  |
| `data-availability`     | Information about data access or release.                     |
| `code-availability`     | Information about code or artifact release.                   |

Claim types that are **not** permitted in `institutional` domain:
`company-thesis`, `supply-chain-fact`, and any custom types not listed above.
Submissions containing disallowed types are rejected by the validator with a
descriptive error listing the offending claims.

---

## Cross-chapter claims

When a chapter subagent identifies a finding that explicitly relates to a
claim in another chapter (e.g., Chapter 3's demand projection is constrained
by Chapter 2's supply scenario), it should use the existing `connection` claim
type extended with a `connected_claims` list:

```json
{
  "type": "connection",
  "text": "The 2030 demand projection in ch2_demand assumes the supply build-out trajectory described in ch3_supply; without that build-out the demand scenario is infeasible.",
  "connected_claims": [
    "iea_weo_2025__ch3_supply:claim:14"
  ],
  "source_anchor": {
    "source_type": "pdf",
    "locator": "IEA WEO 2025, Chapter 2, p. 78",
    "confidence": "high"
  }
}
```

The `connected_claims` field is a list of claim identifiers in the format
`<cite_key>__<slug>:claim:<index>`. The synthesis step resolves these
references when merging chapter sidecars. If a referenced claim does not exist
in any included chapter sidecar, the synthesis step logs a warning and
preserves the connection claim with an `unresolved` flag.

Cross-chapter `connection` claims may also reference claims in the merged
document sidecar using the plain `cite_key` (without `__<slug>`) when the
connection spans the whole document rather than a specific chapter.

---

## Edge cases

### Skip rows

Rows with `depth: skip` are silently excluded from dispatch and from synthesis.
The `## Gaps` section of the synthesis document lists all skipped slugs. Do
not create placeholder output files for skipped rows.

### include_in_synthesis: false

A chapter subagent still runs (unless `depth: skip`) and writes its output
file, but its sidecar is excluded from the synthesis merge. Use this for
chapters that need to be read for completeness but whose content should not
influence the cross-chapter summary (e.g., a statistical annex or a
methodology appendix whose details are already covered by another chapter).

### Overlapping page ranges

The parser allows overlapping `page_range` values (e.g., an executive summary
that reprints page 1–10 of Chapter 1). The dispatcher does not deduplicate
overlapping pages. Each subagent reads exactly its declared range. The
synthesis step is responsible for deduplicating any identical claims that arise
from overlapping coverage.

### Mixed domain_lens labels

`validate_chapter_plan()` raises `ValueError` if rows with
`include_in_synthesis: true` carry more than one distinct `domain_lens` value.
All included rows must share a single lens label. Rows with
`include_in_synthesis: false` may carry any `domain_lens` value without
triggering this error.

To process a document where different chapters genuinely belong to different
lenses, either (a) run two separate book-mode pipelines with filtered chapter
plans, or (b) assign the broadest applicable lens to all included rows and
note the per-chapter specialisation in the `role` column.

---

## Reference

- **Chapter plan schema** — `references/chapter-plan-schema.md` (canonical
  field definitions, JSON Schema, and validation rules)
- **Canonical IEA plan** — `plans/iea_weo_2025.md` (reference chapter plan
  used for integration testing; demonstrates all depth levels, mixed
  include_in_synthesis settings, and page_offset usage)
- **Parser source** — `scripts/chapter_plan_parser.py`
  (`parse_chapter_plan`, `validate_chapter_plan`, `iter_rows`)
- **Claim types (extended)** — `references/extraction-templates.md`
  § Extended Claim Types (field definitions for `policy-recommendation`,
  `projection`, `supply-chain-fact`, `company-thesis`)
