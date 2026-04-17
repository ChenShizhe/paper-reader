# Mode: chain_map

`chain_map` mode processes sell-side research documents — equity initiation
notes, sector thematic reports, credit memos, and robotics/technology supply-chain
surveys — by extracting a structured company inventory from named exhibits and
mapping each company to its supply-chain tier, investment thesis, and supporting
facts.

---

## Overview

**Genre.** Sell-side equity research and thematic investment reports. These
documents are organised around investment theses, valuation tables, and
supply-chain fact chains rather than IMRaD conventions. They typically contain
numbered exhibits (tables and charts) that enumerate companies, tickers, target
prices, and analyst ratings.

**When to use.** Use `chain_map` mode when:

- The source PDF is a sell-side initiation note, sector survey, or thematic
  report that lists ≥ 5 named companies with tickers.
- The primary reading goal is to extract a structured company inventory
  (tickers, roles, theses) rather than to comprehend an academic argument.
- The document includes exhibit tables that can be parsed programmatically
  (born-digital PDF; text layer present).

**When NOT to use.** A single-company deep-dive with one stock covered is
better handled by `paper` mode with `claim_domain: sell_side`. Use `chain_map`
only when multiple companies appear in enumerated exhibit tables.

**Canonical example — Humanoid 100.**
A representative `chain_map` source is a broker's "Humanoid 100" supply-chain
initiation note: a 60–120-page born-digital PDF that opens with an executive
summary, then presents 8–12 numbered exhibits (e.g., "Exhibit 3: Actuator
Supply Chain Coverage Universe") listing ≈ 100 Chinese and global component
suppliers with A-share / H-share tickers, analyst tiers, 12-month target
prices, and short thesis narratives. The pipeline extracts every row from
every exhibit table, normalises tickers to exchange-qualified form, and emits
a structured company inventory plus per-company `company-thesis` claims.

Activated by:

```
run_pipeline.py --mode chain_map --source downloads/humanoid_100.pdf
```

or equivalently:

```
comprehend_paper.py --mode chain_map --source downloads/humanoid_100.pdf
```

An optional watchlist can be supplied:

```
run_pipeline.py --mode chain_map \
  --source downloads/humanoid_100.pdf \
  --watchlist watchlists/my_portfolio.md
```

---

## Inputs

### --source PDF

`--source` accepts a path to the PDF to process.

**Expected format:** born-digital PDF with a text layer. The extractor calls
`scripts/pdf_probe.py` to detect whether a text layer is present before
attempting exhibit extraction. If `pdf_probe` reports `text_layer: false`
(scanned / image-only document), the run aborts with an `IMAGE_ONLY_PDF` error
and a recommendation to OCR the file first.

**Page count.** No hard limit; typical chain_map sources are 40–200 pages.
Very large PDFs (> 300 pages) trigger a preflight warning but do not abort.

### --watchlist (optional)

`--watchlist` accepts a path to a Markdown file containing a GFM pipe table of
tickers the user already tracks. When supplied:

- Companies in both the PDF and the watchlist receive an `in_watchlist: true`
  flag in the company inventory.
- Companies on the watchlist that do not appear in any exhibit are listed in
  the `## Watchlist Gaps` output section.
- Companies that appear in multiple exhibits and are on the watchlist are
  emphasised (bold row) in the fenced CSV.

The watchlist file is parsed by `scripts/watchlist_cross_source.py`. See
[§ Watchlist input](#watchlist-input) for the GFM table schema.

### Born-digital requirement

`chain_map` mode requires a born-digital PDF. The preflight check sequence is:

1. `scripts/pdf_probe.py --source <path>` → must return `text_layer: true`.
2. If `text_layer: false`, exit code 2 + message:
   `IMAGE_ONLY_PDF: chain_map requires a born-digital PDF. Run OCR first.`
3. If the probe itself fails (corrupt file, wrong path), exit code 1 +
   `PREFLIGHT_FAILED`.

---

## Extraction

### Exhibit detection

`scripts/exhibit_extractor.py` scans every page of the PDF for exhibit headers
using the following regex patterns (applied to the raw text layer):

```python
EXHIBIT_HEADER_RE = re.compile(
    r"(?i)^(?:exhibit|figure|table|appendix)\s+(\d+[A-Za-z]?)"
    r"[\s:–—-]+(.+)$",
    re.MULTILINE,
)
```

Each match captures:
- **Group 1** — exhibit number (e.g., `3`, `3A`).
- **Group 2** — exhibit title (e.g., `Actuator Supply Chain Coverage Universe`).

The extractor records the start page of each exhibit header. Exhibits without
a matching close (next header or end-of-document) are treated as single-page.

### Row extraction

For each detected exhibit, the extractor attempts to parse tabular rows. A row
is accepted if it contains at least one of:

- A recognisable ticker pattern (see § Ticker normalization).
- A Chinese company name (≥ 4 CJK characters) followed by a number.

Each extracted row produces a raw record:

```json
{
  "exhibit_num": "3",
  "exhibit_title": "Actuator Supply Chain Coverage Universe",
  "page": 12,
  "raw_text": "Sanhua Intelligent Controls  002050.SZ  Buy  45.00  Top Pick",
  "parsed": {
    "company_name": "Sanhua Intelligent Controls",
    "ticker_raw": "002050.SZ",
    "rating": "Buy",
    "target_price": "45.00",
    "tier": "Top Pick"
  }
}
```

Fields present in `parsed` depend on what the row contains; absent fields are
omitted (not set to null) in the raw record.

### Confidence score

Each raw record receives a `confidence` field:

| Score | Condition |
|-------|-----------|
| `high` | Ticker normalised successfully + company name extracted |
| `medium` | Ticker present but exchange unknown, OR company name matched by fuzzy heuristic |
| `low` | Only a partial ticker or name fragment matched; row retained but flagged |

Records with `confidence: low` appear in the output inventory with a
`⚠ low-confidence` note and are excluded from `data_sections` aggregates.

---

## Ticker normalization

`scripts/ticker_normalizer.py` converts raw ticker strings to
exchange-qualified form. The priority order is:

1. **Exact match** — raw string already contains an exchange suffix (e.g.,
   `002050.SZ`, `6954.T`, `NVDA`). Validate suffix against the supported
   exchange list; if valid, accept as-is.
2. **Exchange prefix** — raw string contains an exchange prefix (e.g.,
   `SZ:002050`). Reformat to `<code>.<suffix>` form.
3. **Bare numeric** — 4–6 digit code with no suffix. Apply heuristics:
   - 6-digit code starting with `0`, `3`, or `6` → candidate for `.SZ` or
     `.SS`; apply the standard A-share routing rules (0xxxxx / 3xxxxx → `.SZ`;
     6xxxxx → `.SS`).
   - 4–5 digit code → candidate for `.HK` (Hong Kong) or `.T` (Tokyo).
   - If ambiguous, assign suffix `?` and set `confidence: medium`.
4. **CUSIP / ISIN** — if the raw string matches an ISIN or CUSIP pattern,
   store it in `isin` / `cusip` fields and set `ticker_normalised` to `null`
   with a note `ISIN_ONLY`.

### Supported exchanges

| Suffix | Exchange | Typical code pattern |
|--------|----------|----------------------|
| `.SZ` | Shenzhen Stock Exchange | 6-digit, 0xxxxx or 3xxxxx |
| `.SS` | Shanghai Stock Exchange | 6-digit, 6xxxxx |
| `.HK` | Hong Kong Stock Exchange | 4–5 digit |
| `.T` | Tokyo Stock Exchange | 4-digit |
| `.KS` | Korea Exchange | 6-digit |
| `.NS` / `.BO` | NSE / BSE India | alphanumeric |
| ` ` (bare) | NASDAQ / NYSE | 1–5 uppercase letters, no suffix |
| `.L` | London Stock Exchange | alphanumeric |
| `.AX` | Australian Securities Exchange | alphanumeric |

Exchanges not in this list are stored with suffix `?` and flagged as
`unknown_exchange`.

### Unknown-format handling

When a ticker cannot be normalised:

1. Store the raw string in `ticker_raw`.
2. Set `ticker_normalised: null`.
3. Set `exchange: unknown`.
4. Append the company to the `## Unknown Tickers` output section (see
   § Output sections).
5. Do not drop the company from the inventory; include it with `confidence`
   degraded by one level (high → medium, medium → low).

---

## Output sections

The pipeline writes a single Markdown output file per run at:

```
extractions/<cite_key>/chain_map.md
```

The file must contain exactly the following 8 top-level sections, in this
order:

### 1. `## Overview`

2–4 sentences describing the source document: publisher, title (if extractable
from the PDF metadata or cover page), page count, number of exhibits found,
and total companies extracted. Example:

> CICC Humanoid Robotics Initiation (March 2024), 88 pages. 11 exhibits
> detected; 97 companies extracted (91 with normalised tickers, 6 unknown
> format). Watchlist overlap: 14 of 23 watchlist tickers present.

### 2. `## Company Inventory`

The full structured company inventory. Format: fenced CSV block wrapped in a
`<details>` element so it collapses in rendered Markdown. See
[§ Company inventory format](#company-inventory-format) for the column
specification.

### 3. `## Supply-Chain Map`

A prose + bullet description of how the covered companies relate to each other
across the supply chain. Group by tier (e.g., raw materials → components →
sub-assemblies → integrators). Each bullet names the tier, lists 3–5 exemplar
companies with tickers, and notes the primary product or process that links
them.

### 4. `## Investment Theses`

One paragraph per company that has a `tier` of `top-pick` or `outperform`.
Each paragraph begins with the company name and exchange-qualified ticker in
bold, followed by the analyst's stated thesis (extracted verbatim or
paraphrased from the exhibit text), then the 12-month target price if present.
Companies with lower tiers (`neutral`, `underperform`) are listed in a
collapsed `<details>` block titled "Other Covered Companies."

### 5. `## Watchlist Overlap`

Present only when `--watchlist` is supplied. Two sub-sections:

- **In Report:** GFM table listing every watchlist ticker found in the PDF,
  with columns: `ticker`, `company_name`, `exhibit`, `tier`, `target_price`.
- **Gaps:** Bulleted list of watchlist tickers not found in any exhibit, with
  a note if the company name (not the ticker) appears in free text.

When no watchlist is supplied, this section contains the single line:
`No watchlist supplied.`

### 6. `## Unknown Tickers`

Bulleted list of companies whose tickers could not be normalised. Each bullet:
`- <company_name> (raw: <ticker_raw>) — <exhibit_num>, p. <page>`.

When all tickers normalised successfully, this section contains:
`All tickers normalised successfully.`

### 7. `## Extraction Notes`

Bullet list of non-fatal issues encountered during extraction:

- Exhibits that were detected but produced zero rows (empty exhibits).
- Exhibits that appeared to be image-only (no text layer on that page).
- Companies that appeared in multiple exhibits (listed with all exhibit
  numbers).
- Any confidence downgrades and their reasons.

When no issues occurred, this section contains: `No extraction issues.`

### 8. `## data_sections`

Machine-readable YAML block that summarises the extraction for downstream
data skills. See [§ data_sections contract](#data_sections-contract) for the
full schema.

---

## Company inventory format

The company inventory is written as a fenced CSV block inside a `<details>`
element:

````markdown
<details>
<summary>Company Inventory — 97 companies</summary>

```csv
ticker_normalised,ticker_raw,exchange,company_name,exhibit_num,tier,target_price,supply_chain_role,in_watchlist
002050.SZ,002050.SZ,SZ,Sanhua Intelligent Controls,3,top-pick,45.00,thermal management / actuators,true
6954.T,6954.T,T,FANUC Corporation,5,outperform,4200,industrial robots / controllers,false
```

</details>
````

### Required columns (9)

| Column | Type | Description |
|--------|------|-------------|
| `ticker_normalised` | string or `null` | Exchange-qualified ticker (`EXCHANGE.CODE` or bare NASDAQ/NYSE). `null` when normalisation failed. |
| `ticker_raw` | string | Ticker as it appeared in the PDF. |
| `exchange` | string | Exchange suffix without dot (e.g., `SZ`, `HK`, `T`). `unknown` when format could not be resolved. |
| `company_name` | string | Company name as extracted from the exhibit row. |
| `exhibit_num` | string | Exhibit number(s) where the company appears; comma-separated if multiple (e.g., `3,7`). |
| `tier` | string | Analyst rating tier as written in the source (e.g., `top-pick`, `outperform`, `neutral`, `underperform`, `not-rated`). |
| `target_price` | string or `null` | 12-month price target as written (preserves currency and formatting). `null` if absent. |
| `supply_chain_role` | string | Free-text description of the company's role in the supply chain (1–10 words). |
| `in_watchlist` | boolean | `true` if the ticker appears in the supplied watchlist; `false` otherwise. Always `false` when no watchlist supplied. |

### Publisher-specific extension columns

Downstream consumers and publisher-specific parsers may append additional
columns after the required 9. Reserved extension prefixes:

| Prefix | Use |
|--------|-----|
| `x_cicc_` | CICC-specific fields (e.g., `x_cicc_conviction_score`) |
| `x_gs_` | Goldman Sachs-specific fields |
| `x_ms_` | Morgan Stanley-specific fields |
| `x_ubs_` | UBS-specific fields |
| `x_` | Generic extension for unlisted publishers |

Extension columns must not use the names of any required column. The pipeline
passes extension columns through without validation.

---

## data_sections contract

The `## data_sections` section in `chain_map.md` contains a fenced YAML block
that exposes the extraction summary for downstream data skills (e.g., portfolio
overlap calculators, sector screeners, claim aggregators).

### Frontmatter schema

```yaml
data_sections:
  schema_version: "1"
  cite_key: "<cite_key>"
  mode: chain_map
  claim_domain: sell_side
  source_pdf: "<path as passed to --source>"
  watchlist_path: "<path as passed to --watchlist, or null>"
  extraction_date: "<YYYY-MM-DD>"
  company_count: <integer — total rows in inventory>
  normalised_count: <integer — rows with ticker_normalised != null>
  unknown_ticker_count: <integer — rows with exchange == 'unknown'>
  exhibit_count: <integer — exhibits detected>
  empty_exhibit_count: <integer — exhibits with zero rows>
  watchlist_overlap_count: <integer — companies with in_watchlist == true; 0 if no watchlist>
  watchlist_gap_count: <integer — watchlist tickers not found in PDF; 0 if no watchlist>
  inventory_path: "extractions/<cite_key>/chain_map.md"
  claims_path: "claims/<cite_key>-claims.json"
```

### How future data skills navigate

A downstream skill that needs to consume the company inventory should:

1. Read `extractions/<cite_key>/chain_map.md`.
2. Locate the `## data_sections` heading and parse the fenced YAML block
   immediately following it.
3. Use `inventory_path` to find the `## Company Inventory` section and parse
   the fenced CSV block inside the `<details>` element.
4. Use `claims_path` to load the full `company-thesis` and `supply-chain-fact`
   claims sidecar for richer text.
5. Filter on `in_watchlist`, `tier`, or `exchange` as needed.

Skills must not parse the `## Company Inventory` section by position; always
navigate via `data_sections.inventory_path` to be robust to section reordering.

---

## Watchlist input

The watchlist file passed to `--watchlist` must be a Markdown file whose body
contains at least one GFM pipe table with the following columns:

### Required GFM table columns

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | string | Exchange-qualified ticker (same format as `ticker_normalised`). Used as the join key against the inventory. |
| `company_name` | string | Human-readable company name. Used for fuzzy name-matching when a ticker is not found but the name appears in free text. |

### Optional columns

| Column | Type | Description |
|--------|------|-------------|
| `notes` | string | Free-text notes about the position (passed through; not used by the pipeline). |
| `emphasis` | boolean string (`true` / `false`) | When `true`, the company row in the inventory CSV is flagged with `x_watchlist_emphasis: true`. |

Additional columns are preserved in the parsed watchlist object and passed
through to the `## Watchlist Overlap` section as supplementary context.

### Overlap semantics

- **Overlap** — a watchlist ticker matches a `ticker_normalised` value exactly
  (case-insensitive, suffix-normalised). The company receives `in_watchlist: true`.
- **Name-only match** — ticker not found, but `company_name` from the watchlist
  appears (fuzzy, ≥ 0.85 similarity) in the PDF's extracted company names. The
  company receives `in_watchlist: true` and `x_watchlist_name_match: true`.
- **Gap** — ticker appears in the watchlist but in neither of the above. Listed
  in `## Watchlist Gaps`.

### Emphasis semantics

When a watchlist row has `emphasis: true`, the corresponding row in the fenced
CSV inventory is written in bold (all fields prefixed with `**` and suffixed
with `**` in rendered Markdown; in CSV the `x_watchlist_emphasis` column is
set to `true`). This signals to downstream UIs that the company warrants
additional attention.

---

## Edge cases

### Empty exhibits

An exhibit is **empty** when its header is detected but no rows pass the row
extraction filter. Empty exhibits are:

- Recorded in `data_sections.empty_exhibit_count`.
- Listed in `## Extraction Notes` with the exhibit number, title, and page.
- Not represented in the company inventory or claims sidecar.

Do not attempt to infer rows from surrounding text when an exhibit is empty.

### Image-only exhibits

A per-page image-only exhibit occurs when a single page within an otherwise
born-digital PDF has no text layer (e.g., a scanned chart inserted as an image).
Detection heuristic: fewer than 20 extracted characters on a page that the
exhibit header algorithm assigns to an exhibit.

When an image-only page is detected:
- The exhibit is logged in `## Extraction Notes` as `image-only page`.
- The exhibit is counted in `data_sections.empty_exhibit_count`.
- No rows are emitted for that page.
- The run continues; exit code is 0 (non-fatal).

If the entire PDF is image-only (detected by `pdf_probe`), the run aborts
before exhibit extraction with exit code 2 (`IMAGE_ONLY_PDF`).

### Companies in multiple exhibits

A company may appear in more than one exhibit (e.g., listed in the full
coverage universe exhibit and again in a sector-specific highlight table).

When duplicates are detected (same `ticker_normalised`):
- The `exhibit_num` field in the inventory row is set to a comma-separated
  list of all exhibit numbers where the company appears (e.g., `3,7`).
- The `tier` and `target_price` values are taken from the first exhibit where
  they appear (typically the main coverage table).
- A note is added to `## Extraction Notes`:
  `<company_name> (<ticker>) appears in exhibits <list>; merged into one row.`
- Only one claims sidecar entry is emitted for the company.

Companies with `ticker_normalised: null` are deduplicated by exact
`company_name` match (case-insensitive, strip punctuation).

### Unknown ticker formats

When `ticker_normalizer.py` cannot resolve a ticker (see § Ticker
normalization):

- `ticker_normalised` is set to `null` and `exchange` to `unknown`.
- The company is included in the inventory with whatever other fields were
  extracted.
- The company is listed in `## Unknown Tickers`.
- The company's `company-thesis` claim (if extracted) is still emitted in the
  claims sidecar with `ticker: null` and a `normalization_warning` field.
- `confidence` is degraded: a row that would otherwise be `high` becomes
  `medium`; a row that would be `medium` becomes `low`.
- The pipeline does not abort; it continues processing remaining rows.

---

## Claim types

`chain_map` mode sets `claim_domain: sell_side`. This value is written to
`_catalog.yaml` and to the `data_sections` YAML block.

`validate_extraction.py` enforces the following claim-type subset when
`claim_domain` is `sell_side`:

| Claim type | Description |
|------------|-------------|
| `company-thesis` | Core investment thesis for a named company or security. Required fields: `ticker`, `thesis_note`, `tier`, `geography`. See `references/extraction-templates.md` § company-thesis. |
| `projection` | Forward-looking financial or operational estimate. Required fields: `scenario_label`, `horizon_year`, `base_value`, `driver_assumptions`. |
| `supply-chain-fact` | Factual statement about a supply-chain relationship. Required fields: `attestation_source`, `verifiability`. |
| `methodology` | Described valuation or analytical procedure. |
| `empirical` | Observation or result backed by data or channel checks. |
| `connection` | Stated relationship between companies, tiers, or macro factors. |
| `limitation` | Acknowledged risk, caveat, or scope restriction. |
| `data-availability` | Information about data access or release. |

Claim types **not** permitted in `sell_side` domain:
`theorem`, `assumption`, `policy-recommendation`, `code-availability`, and any
custom types not listed above. Submissions containing disallowed types are
rejected by `validate_extraction.py` with a descriptive error listing the
offending claim indices.

The primary claim type for `chain_map` mode is `company-thesis`. Every company
in the inventory that has a non-null `tier` and a `supply_chain_role` should
have a corresponding `company-thesis` claim in the sidecar.

---

## Key files

| File | Role |
|------|------|
| `scripts/exhibit_extractor.py` | Exhibit detection, row extraction, confidence scoring |
| `scripts/ticker_normalizer.py` | Ticker normalisation; exchange routing |
| `scripts/watchlist_cross_source.py` | Watchlist parsing, overlap detection, gap reporting |
| `scripts/pdf_probe.py` | Born-digital preflight check |
| `scripts/comprehend_paper.py` | Top-level dispatcher; gains `chain_map` branch |
| `scripts/validate_extraction.py` | Enforces `sell_side` claim-type subset |
| `references/extraction-templates.md` | Field definitions for `company-thesis`, `supply-chain-fact`, `projection` |
