# Manifest Contract

This reference defines discovery-specific rules for `paper_manifest.json`.

## Scope
Discovery emits one downstream artifact: `paper_manifest.json`.
It does not create notes or download papers.

## Identity Model

### `canonical_id`
Assign in priority order:
1. `arxiv:<arxiv_id>`
2. `doi:<doi>`
3. `openalex:<openalex_id>`
4. `manual:<hash>`

For `manual:<hash>`, use first 8 hex chars of SHA-256 over
`normalized_title + year`.

### `cite_key`
Default deterministic derivation:

`<first_author_surname_lower><year><title_first_noun_lower>`

Collision suffixes must be deterministic and independent of manifest order.

### Zotero precedence
If a merged record includes an explicit Zotero citation key, keep that value as
`cite_key`. Generated keys are fallback only.

### Lifecycle
- `paper_manifest.json` contains provisional keys.
- If a stronger identity replaces `manual:*`, regenerate provisional keys.
- After note materialization, both `canonical_id` and `cite_key` are frozen.

## Deduplication
Duplicate records share at least one strong identifier:
- `arxiv_id`
- `doi`
- `openalex_id`
- `pmid`

When duplicates collide:
- merge metadata, preferring richer values
- preserve all known identifiers
- emit one manifest entry

No fuzzy title-only deduplication.

## Manifest Schema

Top-level fields:
- `schema_version`
- `topic`
- `created_at`
- `search_sources`
- `entries`

Per-entry fields:
- `canonical_id`
- `cite_key`
- `arxiv_id`
- `openalex_id`
- `doi`
- `pmid`
- `title`
- `authors`
- `year`
- `abstract`
- `pdf_url`
- `categories`
- `relevance_score`
- `seed_distance`
- `citation_count`
- `search_source`

Valid `search_sources` order:
`zotero`, `arxiv`, `openalex`, `web`, `pubmed`.

## Ranking
Relevance scoring must be deterministic.
Seed proximity may use citation/reference graph metadata; when missing, use
`seed_distance = null` and rely on other signals.
