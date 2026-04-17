# Extraction Templates

## Contents
- Per-paper note frontmatter template
- Claims sidecar template
- Auto-generated note block template
- Metadata-only fallback template
- Authoring rules for claims and connections

## Source Of Truth Rule
`claims/<cite_key>.json` is the machine source of truth.

The note's auto-generated markdown block is rendered from that sidecar. If the
two diverge, regenerate the markdown block from the sidecar; never repair the
sidecar by scraping markdown back into JSON.

For deterministic rendering and update handling, prefer
`scripts/render_note_from_claims.py`.

## Per-Paper Note Frontmatter Template

```yaml
---
schema_version: "1"
canonical_id: "<canonical_id>"
cite_key: "<cite_key>"
arxiv_id: "<arxiv_id>"
doi: "<doi>"
openalex_id: "<openalex_id>"
title: "<paper title>"
authors:
  - "<First Author>"
  - "<Second Author>"
year: <YYYY>
tags:
  - "<tag-1>"
  - "<tag-2>"
date_read: <YYYY-MM-DD>
last_read_at: <YYYY-MM-DDTHH:MM:SSZ>
source_type: "arxiv-latex"
source_path: "extracted_corpus/sources/<arxiv_id>/"
bank_path: "$PAPER_BANK/<cite_key>"
source_parse_status: "full"
bibliography_status: "full"
content_status: "full"
extraction_confidence: "high"
validation_status: "validated"
review_status: "auto"
auto_block_hash: "<sha256-of-current-auto-generated-block>"
dataset_links: []
code_links: []
supplementary_links: []
---
```

Notes:
- `source_path` is relative to `WORK_ROOT`.
- `bank_path` points to `$PAPER_BANK/<cite_key>/`.
- `auto_block_hash` stores the hash of the current machine-owned markdown block.
- After note creation, `canonical_id` and `cite_key` are frozen.

## Claims Sidecar Template

```json
{
  "schema_version": "1",
  "cite_key": "<cite_key>",
  "canonical_id": "<canonical_id>",
  "content_status": "full",
  "extraction_confidence": "high",
  "claims": [
    {
      "type": "theorem",
      "text": "The estimator is consistent if the sample size exceeds the intrinsic dimension of the parameter space.",
      "source_anchor": {
        "source_type": "arxiv-latex",
        "locator": "Section 2.1, Theorem 3.1",
        "confidence": "high"
      }
    },
    {
      "type": "connection",
      "text": "The paper extends prior baseline methods to a new application domain.",
      "linked_paper": "smith2020baseline",
      "linked_paper_status": "out-of-corpus",
      "linked_canonical_id": null,
      "linked_doi": "10.xxxx/example.2020.001",
      "source_anchor": {
        "source_type": "arxiv-latex",
        "locator": "Section 1.2",
        "confidence": "high"
      }
    },
    {
      "type": "data-availability",
      "text": "Synthetic benchmark dataset; 1000 observations with known ground truth.",
      "source_anchor": {
        "source_type": "arxiv-latex",
        "locator": "Section 5.1",
        "confidence": "high"
      }
    }
  ]
}
```

Allowed claim types:
- `theorem`
- `assumption`
- `methodology`
- `empirical`
- `connection`
- `limitation`
- `data-availability`
- `code-availability`

Connection rules:
- `linked_paper_status` is required for `connection` claims
- use `in-corpus` only when the linked paper already exists in `papers/`
- for out-of-corpus links, keep `linked_paper` as a provisional citation label
  and store `linked_canonical_id` / `linked_doi` when available

## Auto-Generated Note Block Template

```markdown
<!-- AUTO-GENERATED:BEGIN -->
## Abstract
<Verbatim or faithfully extracted abstract text. If unavailable: "not found".>

## Key Theorems / Results
- <Claim text>. [Source: <locator>, confidence: <high|medium|low>]

## Key Assumptions
- <Claim text>. [Source: <locator>, confidence: <high|medium|low>]

## Methodology / Key Techniques
- <Claim text>. [Source: <locator>, confidence: <high|medium|low>]

## Empirical Findings
- <Claim text>. [Source: <locator>, confidence: <high|medium|low>]

## Connections To Other Papers
- [[<linked_paper>]] (<in-corpus|out-of-corpus; DOI/canonical_id when known>): <relationship>. [Source: <locator>, confidence: <high|medium|low>]

## Data & Code Availability
- Data: <literal description or "not found">. [Source: <locator>, confidence: <high|medium|low>]
- Code: <literal description or "not found">. [Source: <locator>, confidence: <high|medium|low>]

## Limitations
- <Claim text or "not found">. [Source: <locator>, confidence: <high|medium|low>]
<!-- AUTO-GENERATED:END -->

## Reading Notes
_User-owned section. Never rewrite automatically._
```

If the note has been user-edited inside the machine-owned block, keep the
original block intact and render new machine findings in a single replacement
update block:

```markdown
<!-- AUTO-GENERATED:UPDATE:2026-02-27 -->
## Extraction Update
- <New or revised machine-generated findings with anchors>
<!-- AUTO-GENERATED:UPDATE:END -->
```

## Metadata-Only Fallback Template

Use this when no substantive source extraction path is available:

```markdown
---
schema_version: "1"
canonical_id: "manual:3f8af912"
cite_key: "<cite_key>"
title: "Example Paper"
authors:
  - "Jane Smith"
year: 2020
source_type: "manual"
source_path: "downloads/<cite_key>.pdf"
bank_path: "$PAPER_BANK/<cite_key>"
source_parse_status: "failed"
bibliography_status: "missing"
content_status: "metadata-only"
extraction_confidence: "low"
validation_status: "pending"
review_status: "auto"
auto_block_hash: "<sha256-of-current-auto-generated-block>"
dataset_links: []
code_links: []
supplementary_links: []
---

<!-- AUTO-GENERATED:BEGIN -->
## Abstract
<Manifest abstract or "not found">

## Key Theorems / Results
- not found

## Key Assumptions
- not found

## Methodology / Key Techniques
- not found

## Empirical Findings
- not found

## Connections To Other Papers
- not found

## Data & Code Availability
- Data: not found
- Code: not found

## Limitations
- not found
<!-- AUTO-GENERATED:END -->

## Reading Notes
_User-owned section. Never rewrite automatically._
```

Matching sidecar:

```json
{
  "schema_version": "1",
  "cite_key": "<cite_key>",
  "canonical_id": "manual:3f8af912",
  "content_status": "metadata-only",
  "extraction_confidence": "low",
  "claims": []
}
```

## Authoring Rules
- No unsupported claims. Every non-trivial bullet maps to an explicit source anchor.
- If evidence is weak, lower confidence or say `not found`; do not infer.
- Preserve direct dataset/code URLs literally when present.
- Use the paper's terminology and notation where possible.
- When a cited paper is not in the local corpus, render the Obsidian link anyway;
  it should remain unresolved until that paper is ingested.
- Regeneration must be idempotent: unchanged metadata + unchanged sidecar should
  yield the same markdown block and the same `auto_block_hash`.

---

## Extended Claim Types

The following claim types extend the base set for industry reports, energy-transition
documents, and equity-research notes. They share the same `source_anchor` structure
as core claim types.

### Claim Type: `policy-recommendation`

Used when a document directs a specific body to take a defined action within a time
horizon, driven by an identified rationale.

**Required fields:**

| Field | Description |
|---|---|
| `recommended_to` | The entity or class of entities addressed (e.g., government, regulator, utility) |
| `time_horizon` | Deadline or urgency framing (e.g., "by 2030", "within this decade", "immediately") |
| `driver` | The underlying rationale or pressure motivating the recommendation |

**Example (IEA World Energy Outlook style):**

```json
{
  "type": "policy-recommendation",
  "text": "Governments should triple global renewable energy capacity to 11,000 GW by 2030 to stay on track for net-zero emissions by 2050.",
  "recommended_to": "national governments",
  "time_horizon": "by 2030",
  "driver": "NZE pathway requirement; current capacity trajectory falls 60% short of the 2030 milestone",
  "source_anchor": {
    "source_type": "pdf",
    "locator": "IEA World Energy Outlook 2023, Executive Summary, p. 15",
    "confidence": "high"
  }
}
```

---

### Claim Type: `projection`

Used when a document provides a forward-looking quantitative estimate under a named
scenario and set of assumptions.

**Required fields:**

| Field | Description |
|---|---|
| `scenario_label` | The scenario name as used by the source (e.g., "Stated Policies Scenario", "Net Zero Emissions by 2050") |
| `horizon_year` | The year to which the projection refers |
| `base_value` | The reference or current value being projected from (with unit) |
| `driver_assumptions` | Key assumptions that underpin the projection |

**Example (IEA Base Case / Stated Policies Scenario):**

```json
{
  "type": "projection",
  "text": "Under the Stated Policies Scenario, global CO₂ emissions from the energy sector reach 37 Gt in 2030, roughly flat versus 2022 levels.",
  "scenario_label": "Stated Policies Scenario (STEPS)",
  "horizon_year": 2030,
  "base_value": "36.8 Gt CO₂ (2022 actual)",
  "driver_assumptions": [
    "Nationally Determined Contributions implemented as announced but not beyond",
    "No additional policy tightening post-2023",
    "Continued coal-to-gas switching in Asia"
  ],
  "source_anchor": {
    "source_type": "pdf",
    "locator": "IEA World Energy Outlook 2023, Chapter 3, Table 3.2",
    "confidence": "high"
  }
}
```

---

### Claim Type: `supply-chain-fact`

Used for structural facts about a supply chain—sourcing shares, processing
concentrations, logistics dependencies—where the claim's credibility depends on
how it was attested.

**Required fields:**

| Field | Description |
|---|---|
| `attestation_source` | Who produced or directly measured the underlying data (e.g., USGS, IEA, company disclosure) |
| `verifiability` | One of `independent` (third-party audit or government statistics), `same-publisher` (self-reported by the entity covered), or `unattributed` (no clear primary source cited) |

**Example (rare-earth processing concentration):**

```json
{
  "type": "supply-chain-fact",
  "text": "China accounts for approximately 85–90% of global rare-earth element processing capacity, creating single-point-of-failure risk for permanent-magnet supply chains.",
  "attestation_source": "USGS Mineral Commodity Summaries 2023; IEA Critical Minerals Market Review 2023",
  "verifiability": "independent",
  "source_anchor": {
    "source_type": "pdf",
    "locator": "IEA Critical Minerals Market Review 2023, Chapter 2, Figure 2.4",
    "confidence": "high"
  }
}
```

---

### Claim Type: `company-thesis`

Used in equity-research and investment-memo contexts to capture an analyst's
forward-looking view on a specific publicly traded company, including the
investment tier and geographic scope.

**Required fields:**

| Field | Description |
|---|---|
| `ticker` | Exchange-qualified ticker symbol (e.g., `002050.SZ`) |
| `thesis_note` | One- or two-sentence rationale for the investment view |
| `tier` | Analyst conviction tier as used in the source (e.g., `top-pick`, `outperform`, `neutral`, `underperform`) |
| `geography` | Primary market or operating geography of the company |

**Example (Sanhua humanoid-actuator thesis):**

```json
{
  "type": "company-thesis",
  "text": "Sanhua Intelligent Controls is positioned as a primary beneficiary of humanoid-robot actuator demand given its thermal-management and micro-channel heat-exchanger expertise, which translates directly to joint-cooling requirements in next-generation bipedal robots.",
  "ticker": "002050.SZ",
  "thesis_note": "Sanhua's existing EV thermal-management relationships with Tesla and BYD provide a direct commercial pathway into humanoid-robot thermal and actuation sub-systems; margin uplift from robotics could reach 400–600 bps by 2027.",
  "tier": "top-pick",
  "geography": "China (A-share)",
  "source_anchor": {
    "source_type": "pdf",
    "locator": "CICC Robotics Supply Chain Initiation, March 2024, p. 34",
    "confidence": "medium"
  }
}
```

---

## Disambiguation: `company-thesis` vs `supply-chain-fact`

These two claim types can cover overlapping subject matter (e.g., a company that
dominates a supply-chain node) but serve different epistemic roles:

| Dimension | `company-thesis` | `supply-chain-fact` |
|---|---|---|
| **Binding identifier** | Ticker-bound — anchored to a specific listed entity | Structural — describes an industry node, not a single stock |
| **Subjectivity** | Inherently subjective; reflects analyst conviction and investment tier | Intended to be objective; relies on attestation from a named source |
| **Attestation** | No `attestation_source` field; credibility flows from analyst reputation and disclosed methodology | Requires `attestation_source` and explicit `verifiability` rating |
| **Decay rate** | High — thesis relevance changes with earnings, guidance, and market re-rating | Lower — structural facts (e.g., processing share) shift over years, not quarters |
| **Typical source** | Sell-side initiation note, investment memo, conference presentation | Government statistics (USGS, IEA), industry body report, audited company filing |

**When in doubt:** if the claim can be independently verified against a government
or third-party data source and does not depend on a specific stock view, use
`supply-chain-fact` with `attestation_source` filled in. If the claim is a forward
view that would change if the company were acquired or de-listed, use
`company-thesis`.
