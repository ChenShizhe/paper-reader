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
