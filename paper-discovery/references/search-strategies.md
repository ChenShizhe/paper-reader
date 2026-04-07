# Search Strategies

Use this file to design and debug discovery runs.

## Source Priority
1. Zotero (`search_zotero.py`) to reuse existing library items
2. arXiv (`search_arxiv.py`) for primary external recall
3. OpenAlex for DOI + citation graph enrichment
4. PubMed for biomedical gaps
5. Web fallback only when primary coverage is insufficient

## Query Expansion
Before external search, generate a compact query family from the topic brief:
- exact phrase
- synonym phrase(s)
- method phrase
- application phrase
- seed-paper title backfill

Keep the expansion log in `WORK_ROOT/logs/query-expansion.json`.

## Zotero Helper

```bash
python3 skills/paper-discovery/scripts/search_zotero.py \
  --query "topic keyword" \
  --query "topic synonym" \
  --mode auto \
  --max-results 20 \
  --output "<WORK_ROOT>/manifests/raw/zotero.json" \
  --log-output "<WORK_ROOT>/logs/zotero-search.json"
```

`--mode auto` tries semantic search first and falls back to keyword search.

## arXiv Helper

```bash
python3 skills/paper-discovery/scripts/search_arxiv.py \
  --query 'all:"topic keyword"' \
  --query 'all:"topic synonym"' \
  --max-results 15 \
  --delay-seconds 3.0 \
  --output "<WORK_ROOT>/manifests/raw/arxiv.json" \
  --log-output "<WORK_ROOT>/logs/arxiv-search.json"
```

Never parallelize arXiv requests.

## OpenAlex Expansion
Use OpenAlex to:
- enrich records with DOI, OpenAlex ID, and citation count
- expand from seeds via `referenced_works` and/or citing works
- default to depth 1 and stop at depth 2 unless explicitly requested

Log depth and stopping conditions in `WORK_ROOT/logs/openalex-expansion.json`.

## Fallback Rules
- PubMed: only for biomedical topics or missing domain coverage.
- Web: keep only records that can be normalized into strong metadata.

## Budget and Hygiene
- Prefer a few high-signal queries over many noisy queries.
- Stop widening recall once high-quality candidates exceed `max_papers`.
- Preserve all raw source artifacts and logs for reproducibility.
