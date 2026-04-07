# Topic Brief Contract

Use this reference when paper-discovery is asked to find papers.

## Minimum Required Inputs
- `topic` or `research_question`
- one of:
  - `keywords`
  - `seed_papers`

If both keywords and seeds are missing, stop and ask for clarification.

## Recommended Inputs
- `keywords`: 3-8 high-signal terms
- `synonyms`: optional recall expansions
- `seed_papers`: arXiv IDs, DOIs, OpenAlex IDs, or Zotero item keys
- `date_range`: `[start_year, end_year]`
- `max_papers`: desired manifest size
- `min_papers`: threshold for fallback triggering
- `source_preferences`: ordered subset of `zotero`, `arxiv`, `openalex`, `web`, `pubmed`

## Search Artifact Contract
Before `build_manifest.py`, persist raw JSON to `<WORK_ROOT>/manifests/raw/`:
- `zotero.json`
- `arxiv.json`
- `openalex.json`
- `web.json`
- `pubmed.json`

Example helpers:

```bash
python3 skills/paper-discovery/scripts/search_zotero.py \
  --query "ai memory systems" \
  --output "<WORK_ROOT>/manifests/raw/zotero.json" \
  --log-output "<WORK_ROOT>/logs/zotero-search.json"
```

```bash
python3 skills/paper-discovery/scripts/search_arxiv.py \
  --query 'all:"ai memory systems"' \
  --output "<WORK_ROOT>/manifests/raw/arxiv.json" \
  --log-output "<WORK_ROOT>/logs/arxiv-search.json"
```

## Handoff Contract
Discovery hands off:
- `<WORK_ROOT>/preflight_report.json`
- `<WORK_ROOT>/manifests/paper_manifest.json`
- raw artifact paths used to build the manifest

Discovery does not hand off notes, BibTeX exports, or vault content.
