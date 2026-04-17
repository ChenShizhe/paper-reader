# Chain-Map Extraction Schema Reference

Audience: developers extending `chain_map` mode or writing a downstream data-extraction
skill that reads `chain_map` reports. For mode overview and pipeline invocation, see
`references/modes/chain_map.md`.

---

## Required company_inventory columns

The company inventory is emitted as a fenced CSV block inside a `<details>` element under
`## Company Inventory`. The following 9 columns are **locked** — every row must supply
each column, in this order, as the leftmost columns of the CSV header.

| # | Column | Type | Nullable | Description |
|---|--------|------|----------|-------------|
| 1 | `ticker_normalised` | string | yes | Exchange-qualified ticker (`CODE.SUFFIX` or bare NASDAQ/NYSE symbol). `null` when normalisation failed. |
| 2 | `ticker_raw` | string | no | Ticker string exactly as it appeared in the PDF exhibit row. |
| 3 | `exchange` | string | no | Exchange suffix without the leading dot (e.g. `SZ`, `HK`, `T`). Use `unknown` when the format could not be resolved. |
| 4 | `company_name` | string | no | Company name as extracted from the exhibit row; strip trailing punctuation. |
| 5 | `exhibit_num` | string | no | Exhibit number(s) where the company appears; comma-separated when appearing in multiple exhibits (e.g. `3,7`). |
| 6 | `tier` | string | no | Analyst rating tier verbatim from the source: `top-pick`, `outperform`, `neutral`, `underperform`, or `not-rated`. Use `not-rated` when the exhibit contains a row but no rating. |
| 7 | `target_price` | string | yes | 12-month price target as written in the PDF (preserves currency symbol and formatting). `null` when absent. |
| 8 | `supply_chain_role` | string | no | Free-text description of the company's position in the supply chain (1–10 words, e.g. `thermal management / actuators`). |
| 9 | `in_watchlist` | boolean | no | `true` if the ticker appears in the supplied `--watchlist` file; `false` otherwise. Always `false` when no watchlist is passed. |

**Invariants enforced by `validate_extraction.py`:**
- Column order must match the table above; parsers rely on positional indexing for the first 9 columns.
- `ticker_raw` must never be empty; an empty cell means the row was not extracted correctly.
- `exchange` must be a value from the supported exchange list or the literal string `unknown`.
- `in_watchlist` must be the lowercase string `true` or `false` (CSV has no native boolean).

---

## Optional publisher extensions

Publisher-specific fields may be appended as additional columns **after** the required 9.
They must not shadow any required column name.

| Prefix | Reserved for |
|--------|-------------|
| `x_cicc_` | CICC-specific fields (e.g. `x_cicc_conviction_score`) |
| `x_gs_` | Goldman Sachs-specific fields |
| `x_ms_` | Morgan Stanley-specific fields |
| `x_ubs_` | UBS-specific fields |
| `x_` | Generic extension for any other publisher |

**Rules for adding an extension column:**

1. Name the column with the appropriate `x_<publisher>_` prefix.
2. Add it only to rows where the data is present; leave the cell empty (not `null`) for rows
   where the field does not apply.
3. Document the column in a comment at the top of the fenced CSV block using a `# ` prefix
   line (CSV parsers skip `#`-prefixed lines by convention).
4. Do not register extension column names in `validate_extraction.py`; the validator passes
   them through without inspection.
5. Extension columns must not be referenced in `data_sections` aggregates unless a
   schema-version bump is performed (see § Backwards compatibility rules).

---

## Ticker format

Tickers in the inventory follow the **exchange-qualified** format: `CODE.SUFFIX` for
non-US listings, and a bare uppercase symbol (no suffix) for NASDAQ/NYSE stocks.

### Supported exchange suffixes

| Suffix | Exchange | Typical code pattern |
|--------|----------|----------------------|
| `.SH` or `.SS` | Shanghai Stock Exchange | 6-digit, starts with `6` |
| `.SZ` | Shenzhen Stock Exchange | 6-digit, starts with `0`, `3`, `4`, or `8` |
| `.HK` | Hong Kong Stock Exchange | 4–5 digit, zero-padded to 4 digits |
| `.TW` | Taiwan Stock Exchange | 4-digit |
| `.TWO` | Taipei Exchange (OTC) | 4-digit |
| `.T` | Tokyo Stock Exchange | 4-digit |
| `.KS` | Korea Exchange (KOSPI) | 6-digit |
| `.KQ` | KOSDAQ | 6-digit |
| `.L` | London Stock Exchange | alphanumeric |
| `.PA` | Euronext Paris | alphanumeric |
| `.AS` | Euronext Amsterdam | alphanumeric |
| `.MI` | Borsa Italiana | alphanumeric |
| `.MC` | Bolsa de Madrid | alphanumeric |
| `.F` / `.DE` | Frankfurt / XETRA | alphanumeric |
| `.SI` | Singapore Exchange | alphanumeric |
| `.AX` | Australian Securities Exchange | alphanumeric |
| `.NZ` | New Zealand Exchange | alphanumeric |
| `.TO` / `.V` | Toronto / TSX Venture | alphanumeric |
| `.BO` / `.NS` | BSE India / NSE India | alphanumeric |
| `.BK` | Stock Exchange of Thailand | alphanumeric |
| `.JK` | Indonesia Stock Exchange | alphanumeric |
| `.KL` | Bursa Malaysia | alphanumeric |
| *(bare)* | NASDAQ / NYSE | 1–5 uppercase letters, no dot suffix |

### Mapping rules (applied in priority order by `ticker_normalizer.py`)

1. **Preserved known** — raw string already contains a recognised suffix (e.g. `002050.SZ`).
   Validate suffix against the table above; accept as-is.
2. **Mapped exchange** — raw string uses a country shorthand (`002050-CN`, `0700-HK`,
   `6954-JP`). Strip the dash and convert the country code to the exchange suffix using
   the prefix heuristics: CN 6-digit `6xxxxx` → `.SH`; CN `0xxxxx`/`3xxxxx` → `.SZ`;
   HK 4–5 digit → `.HK` (zero-pad to 4); TW 4-digit → `.TW`.
3. **US bare** — raw string ends with `-US`, `-UN`, or `-UW`. Strip the suffix and
   upper-case the remainder.
4. **Preserved unknown** — format not matched by any rule. Emit a `WARNING` to stderr,
   store raw string in `ticker_raw`, set `ticker_normalised: null`, set `exchange: unknown`.

---

## data_sections frontmatter

Every `chain_map.md` output file ends with a `## data_sections` section containing a
fenced YAML block. This block is the **machine-readable contract** between the pipeline
and downstream data skills. Skills must parse this block first and use its paths to
navigate the rest of the document; they must not rely on section position.

### Schema

```yaml
data_sections:
  schema_version: "1"           # Increment when a field is added or removed.
  cite_key: "<string>"          # Catalog key for the source document.
  mode: chain_map               # Literal; identifies the producer.
  claim_domain: sell_side       # Claim-type subset enforced by validate_extraction.py.
  source_pdf: "<path>"          # Path passed to --source.
  watchlist_path: "<path|null>" # Path passed to --watchlist; null when omitted.
  extraction_date: "<YYYY-MM-DD>"
  company_count: <int>          # Total rows in the inventory (including low-confidence).
  normalised_count: <int>       # Rows where ticker_normalised is not null.
  unknown_ticker_count: <int>   # Rows where exchange == 'unknown'.
  exhibit_count: <int>          # Total exhibits detected.
  empty_exhibit_count: <int>    # Exhibits that produced zero rows.
  watchlist_overlap_count: <int> # Companies with in_watchlist == true; 0 if no watchlist.
  watchlist_gap_count: <int>    # Watchlist tickers absent from the PDF; 0 if no watchlist.
  inventory_path: "extractions/<cite_key>/chain_map.md"
  claims_path: "claims/<cite_key>-claims.json"
```

### Example

```yaml
## data_sections

```yaml
data_sections:
  schema_version: "1"
  cite_key: cicc_humanoid_2024
  mode: chain_map
  claim_domain: sell_side
  source_pdf: downloads/cicc_humanoid_2024.pdf
  watchlist_path: watchlists/robotics.md
  extraction_date: "2026-04-17"
  company_count: 97
  normalised_count: 91
  unknown_ticker_count: 6
  exhibit_count: 11
  empty_exhibit_count: 1
  watchlist_overlap_count: 14
  watchlist_gap_count: 9
  inventory_path: extractions/cicc_humanoid_2024/chain_map.md
  claims_path: claims/cicc_humanoid_2024-claims.json
` ``
```

---

## Watchlist schema

The file passed to `--watchlist` must be Markdown containing at least one GFM pipe table.
`watchlist_cross_source.py` extracts the first such table.

### GFM table shape

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `ticker` | yes | string | Exchange-qualified ticker in the same format as `ticker_normalised`. Used as the primary join key. |
| `name` | yes | string | Human-readable company name. Used for fuzzy name-matching (≥ 0.85 similarity) when a ticker is not found in the PDF. |
| `tier` | no | string | User-defined priority tier; passed through to `## Watchlist Overlap` as supplementary context. |
| `track` | no | string | Tracking label (e.g. `core`, `monitor`); passed through without validation. |
| `notes` | no | string | Free-text position notes; passed through unchanged. |

Additional columns beyond those listed above are preserved in the parsed watchlist object
and forwarded verbatim to the `## Watchlist Overlap` output section.

### Minimal valid example

```markdown
| ticker    | name                       |
|-----------|----------------------------|
| 002050.SZ | Sanhua Intelligent Controls |
| 6954.T    | FANUC Corporation           |
| NVDA      | NVIDIA Corporation          |
```

The separator row (second line) is required; omitting it raises `ValueError`.
Empty `ticker` cells also raise `ValueError` and abort parsing.

---

## Backwards compatibility rules

These rules govern how the schema may evolve without breaking downstream readers that
were written against an earlier version.

### Safe changes (no schema-version bump required)

- Adding a new **optional** field to `data_sections` with a sensible default of `0`,
  `null`, or `false`. Readers that do not recognise the field should ignore it.
- Adding a new **optional** column to the watchlist table. Existing readers skip
  unknown columns.
- Adding a new exchange suffix to the supported-suffix table. Normaliser output is
  still valid `CODE.SUFFIX` format.
- Changing free-text fields (`supply_chain_role`, `tier` free-form values in source)
  — these are not parsed programmatically.

### Breaking changes (increment `schema_version`)

- Removing or renaming any of the 9 required `company_inventory` columns.
- Changing the column order of the required 9.
- Removing or renaming any field in `data_sections`.
- Changing the type or semantics of an existing `data_sections` field (e.g. changing
  `company_count` to exclude low-confidence rows).
- Adding a **required** column to the watchlist table.

### Version negotiation for downstream skills

When reading a `chain_map.md` file, a downstream skill should:

1. Parse `data_sections.schema_version` before reading any other field.
2. If `schema_version > <highest version the skill supports>`, emit a warning and
   proceed only if the fields the skill needs are present; abort if they are missing.
3. If `schema_version < <skill's minimum version>`, refuse to process and emit a
   clear error: `chain_map schema version N is below minimum supported version M`.
4. Never assume the presence of an extension (`x_`) column; check the CSV header row
   before accessing extension fields.
