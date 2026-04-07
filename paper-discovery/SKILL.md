---
name: paper-discovery
description: >
  Discover candidate papers for literature review with Zotero-first retrieval,
  arXiv recall, OpenAlex enrichment, and optional PubMed/web fallback. Use when
  asked to build or update a schema-v1 paper_manifest.json under WORK_ROOT.
  Produces deterministic canonical_id/cite_key outputs and preserves Zotero cite
  keys when available.
---

# Paper Discovery

## Mission
Given a topic brief, produce `<WORK_ROOT>/manifests/paper_manifest.json` without
writing notes or downloading papers. Discovery owns search, enrichment,
deduplication, identity assignment, and ranking.

## WORK_ROOT
`WORK_ROOT` is always `$PAPER_BANK/`. All discovery artifacts (manifests,
preflight reports, logs) live here. Do NOT use a temporary project folder.

## Read First
- [references/topic-brief-contract.md](references/topic-brief-contract.md)
- [references/manifest-contract.md](references/manifest-contract.md)
- [references/search-strategies.md](references/search-strategies.md)

## Required Outputs
- `<WORK_ROOT>/preflight_report.json`
- `<WORK_ROOT>/manifests/paper_manifest.json`

## Inputs

A topic brief with keywords is the default input. An optional reference queue may supplement or replace keyword search.

### Optional: Reference Queue Input Mode

When `reference-queue.md` is provided, paper-discovery supplements its keyword
search with high-importance unread papers surfaced by prior reading sessions.

```bash
python3 skills/paper-discovery/scripts/read_reference_queue.py \
  --reference-queue "$VAULT_ROOT/literature/reference-queue.md" \
  --top-n 20 \
  --output "<WORK_ROOT>/manifests/raw/reference-queue-seeds.json"
```

The output seeds are merged with Zotero results in Step 2 of the workflow. Seed papers
from the reference queue skip the keyword search step and proceed directly to
enrichment (Step 4: OpenAlex) and deduplication (Step 5).

After paper-discovery completes, update reference-queue entries that were
added to the acquisition list: set `status: pending`.

## Preflight
Run before search:

```bash
python3 skills/paper-discovery/scripts/preflight_discovery.py \
  --output "<WORK_ROOT>/preflight_report.json"
```

If `overall != "ready"`, stop and report the missing dependency.

## Workflow

### 1) Parse and expand the brief
- Extract topic, keywords, seed papers, date range, and `max_papers`.
- Generate a compact synonym list with an LLM (2-6 high-signal expansions).
- Keep the expansion auditable in `WORK_ROOT/logs/query-expansion.json`.

### 2) Search Zotero first
Use local Zotero results before external sources to avoid rediscovering existing items.

```bash
python3 skills/paper-discovery/scripts/search_zotero.py \
  --query "ai memory systems" \
  --query "memory-augmented agents" \
  --mode auto \
  --max-results 20 \
  --output "<WORK_ROOT>/manifests/raw/zotero.json" \
  --log-output "<WORK_ROOT>/logs/zotero-search.json"
```

### Step 2b (optional): Inject reference-queue seeds

If `reference-queue-seeds.json` is present in `WORK_ROOT/manifests/raw/`:
1. For each seed, treat the `arxiv_id` as a known arXiv ID — skip the arXiv
   keyword search for these; instead fetch their metadata directly:
   ```bash
   python3 skills/paper-discovery/scripts/search_arxiv.py \
     --arxiv-id "<arxiv_id>" \
     --output "<WORK_ROOT>/manifests/raw/arxiv-rq-<cite_key>.json"
   ```
2. Merge results into the main candidate pool.
3. Apply a score boost: importance_score from reference queue adds to the
   paper-discovery relevance score (multiplier: 0.2 per importance point, capped at 1.0).

### 3) Search arXiv for recall
Run sequentially with a courtesy delay:

```bash
python3 skills/paper-discovery/scripts/search_arxiv.py \
  --query 'all:"ai memory systems"' \
  --query 'all:"memory-augmented agents"' \
  --max-results 20 \
  --delay-seconds 10.0 \
  --output "<WORK_ROOT>/manifests/raw/arxiv.json" \
  --log-output "<WORK_ROOT>/logs/arxiv-search.json"
```

### 4) Enrich and expand with OpenAlex
- Recover DOI, OpenAlex IDs, citation counts, and OA links.
- Run controlled citation-graph expansion from seeds (default depth 1; max depth 2).
- Keep expansion logs in `WORK_ROOT/logs/openalex-expansion.json`.

### 5) Optional fallback sources
- Add PubMed only for biomedical coverage gaps.
- Add web fallback only when primary sources underfill target recall.

### 6) Build manifest
Pass every source artifact that exists:

```bash
python3 skills/paper-discovery/scripts/build_manifest.py \
  --topic "ai memory systems" \
  --zotero-results "<WORK_ROOT>/manifests/raw/zotero.json" \
  --arxiv-results "<WORK_ROOT>/manifests/raw/arxiv.json" \
  --openalex-results "<WORK_ROOT>/manifests/raw/openalex.json" \
  --seed-papers "arxiv:1502.04592,doi:10.1088/1469-7688/15/7/1147" \
  --keywords "ai memory,agent memory,retrieval" \
  --date-start 2015 \
  --date-end 2026 \
  --max-papers 30 \
  --output "<WORK_ROOT>/manifests/paper_manifest.json"
```

If needed, also pass `--pubmed-results` and/or `--web-results`.

### 7) Validate
```bash
python3 skills/paper-discovery/scripts/validate_manifest.py \
  "<WORK_ROOT>/manifests/paper_manifest.json"
```

### 8) Zotero sync handoff
When callers request sync, use the manifest entries to create/update a Zotero collection.
Preserve Zotero-native citation keys where they already exist; only provisional keys
should be generated for new items.

## Operational Rules
- Source order: `zotero -> arxiv -> openalex -> web -> pubmed`.
- Deduplicate only by strong IDs (`arxiv_id`, `doi`, `openalex_id`, `pmid`).
- Prefer explicit Zotero cite keys over provisional generated keys.
- Keep all run artifacts in `WORK_ROOT`; do not write vault content.
- **arXiv rate limits**: always pass `--delay-seconds 10.0` to `search_arxiv.py`. If `search_arxiv.py` is called more than once in a session (e.g. keyword search followed by seed lookup), add `sleep 15` between the two script invocations. For source downloads, `download_papers.py` enforces a 30-second inter-download delay and a `[30, 60, 120]`-second backoff on 429 — do not reduce these.

## Out Of Scope
- PDF downloading and extraction.
- Writing notes, claims, digests, or survey drafts.

## Acceptance
```bash
python3 -m unittest discover -s skills/paper-discovery/tests -p 'test_*.py'
```

```bash
python3 skills/paper-discovery/scripts/preflight_discovery.py \
  --output "<WORK_ROOT>/preflight_report.json"
```

```bash
python3 skills/paper-discovery/scripts/validate_manifest.py \
  "<WORK_ROOT>/manifests/paper_manifest.json"
```

### Acquisition Pipeline

After discovery produces a `paper_manifest.json`, run the following three scripts in order to build and populate the paper-bank for paper-reader sessions.

#### Scripts

**1. `export_acquisition_list.py`**
- **Purpose:** Convert `paper_manifest.json` to `acquisition-list.md`, the shared table used to track download and read status.
- **Default paths:**
  - `--manifest-path`: (required) path to `paper_manifest.json`
  - `--output-path`: `$PAPER_BANK/acquisition-list.md`
- **Key flag:** `--append` — skip cite_keys already in the list (for subsequent batches).
- **Output:** `acquisition-list.md` with all rows set to `status: pending`.

**2. `download_papers.py`**
- **Purpose:** Read `acquisition-list.md`, download arXiv source tarballs or PDFs for each `pending` entry, extract to `paper-bank/raw/<cite_key>/`, and update statuses to `downloaded`.
- **Default paths:**
  - `--acquisition-list`: `$PAPER_BANK/acquisition-list.md`
  - `--paper-bank`: the parent of `paper-bank/` (e.g., `~/Documents`), NOT `$PAPER_BANK/` itself. The script appends `paper-bank/raw/<cite_key>/` internally. Passing `$PAPER_BANK/` will create a double-nested `paper-bank/paper-bank/raw/` path.
- **Key flags:** `--max-downloads` (integer cap), `--dry-run` (preview without downloading).
- **Standard invocation (no flags needed):** `python3 .../download_papers.py` — the defaults resolve correctly with no arguments.
- **Output:** Files at `$PAPER_BANK/raw/<cite_key>/`; statuses updated in `acquisition-list.md`. Papers with no arXiv ID or that return HTTP 404 are marked `manual-pending` in `acquisition-list.md` and appended to `$PAPER_BANK/manual-download-list.md` for manual retrieval. On subsequent runs the script auto-detects manually placed files and promotes them to `downloaded`.

**3. `generate_prompts.py`**
- **Purpose:** Read `acquisition-list.md` and write one self-contained prompt file per `downloaded` paper. Each prompt file instructs an agent to read session-context files and invoke the paper-reader skill.
- **Default paths:**
  - `--acquisition-list`: `$PAPER_BANK/acquisition-list.md`
  - `--prompts-dir`: `$PAPER_BANK/prompts/`
  - `--session-context-dir`: `$PAPER_BANK/session-context/`
  - `--paper-reader-skill`: `$SKILLS_ROOT/paper-reader/SKILL.md`
  - `--vault-root`: `$VAULT_ROOT/`
- **Source type detection:** if `paper-bank/raw/<cite_key>/` contains `.tex` or `.tar.gz` → `latex`; else → `pdf`.
- **Output:** `$PAPER_BANK/prompts/<cite_key>-prompt.md` for each downloaded paper.

#### Recommended Run Order

```bash
# 1. Export manifest to acquisition list
python3 paper-discovery/scripts/export_acquisition_list.py \
  --manifest-path <WORK_ROOT>/manifests/paper_manifest.json \
  --output-path $PAPER_BANK/acquisition-list.md

# 2. Download papers (review acquisition-list.md first and remove unwanted rows)
python3 paper-discovery/scripts/download_papers.py

# 3. Generate prompt files for paper-reader sessions
python3 paper-discovery/scripts/generate_prompts.py
```

All three scripts use sane defaults — no flags required for standard single-batch runs.

### Batch Setup

Refreshes `$PAPER_BANK/session-context/` so every generated prompt file references current paths and conventions. Run this before `download_papers.py` on each new batch (and anytime paths or conventions change).

#### Step 1 — Copy session-context files

If session-context memory files exist (e.g., identity and preference files), copy them into `$PAPER_BANK/session-context/` so generated prompts can reference them. Overwrite on every refresh to mirror the current snapshot.

#### Step 2 — Write `skill-paths.md`

Write the following content verbatim to `$PAPER_BANK/session-context/skill-paths.md`:

```markdown
# Skill and Path Conventions

## Skill Locations
All skills live at: $SKILLS_ROOT/
- paper-reader: $SKILLS_ROOT/paper-reader/SKILL.md
- paper-discovery: $SKILLS_ROOT/paper-discovery/SKILL.md

Do not use provider-specific skill mounts; always reference skills from $SKILLS_ROOT.

## Key Directory Paths
- Paper bank: $PAPER_BANK/
- Raw downloaded sources: $PAPER_BANK/raw/<cite_key>/
  (or $PAPER_BANK/<cite_key>/raw/ after paper-reader processes it)
- Prompt files: $PAPER_BANK/prompts/
- Vault root: $VAULT_ROOT/
- Vault notes: $VAULT_ROOT/literature/papers/<cite_key>/
- Reference queue: $VAULT_ROOT/literature/reference-queue.md
```

#### Step 3 — Write `paper-reader-conventions.md`

Write the following content verbatim to `$PAPER_BANK/session-context/paper-reader-conventions.md`:

```markdown
# Paper Reader Operational Conventions

## Pre-Translation Check (MANDATORY)
Before running the translation step, always run:
  grep -r '\\externaldocument' <source_path>/
If any matches are found, comment out those lines in the paper-bank copy
before proceeding. Failure to do this causes translation errors.

## Vault Path Conventions
All vault notes must land under $VAULT_ROOT/literature/:
- Paper notes: $VAULT_ROOT/literature/papers/<cite_key>/
- Catalog: $VAULT_ROOT/literature/<cite_key>-catalog.md
- Claims: $VAULT_ROOT/literature/claims/<cite_key>.json

Do NOT write to $VAULT_ROOT/papers/ or $VAULT_ROOT/claims/
(flat vault paths — these are the bug paths).

## VAULT_ROOT Parameter
Always pass VAULT_ROOT as $VAULT_ROOT/ (the root).
Paper-reader scripts append literature/ internally.

## Subagent Path Anchoring
Every subagent prompt must contain explicit absolute paths, not
relative or template paths. Use resolved paths from Step 0.

## Source Format
- LaTeX (preferred): source_type=latex
- PDF (fallback): source_type=pdf — requires MinerU for translation

## Reference Queue
After processing, update $VAULT_ROOT/literature/reference-queue.md
via knowledge-maester with dummy-note-backed references from this session.
```

After every batch, update `paper-reader-conventions.md` to incorporate any new lessons from the experience logs of that batch.

---

### Conversational Entry Points

Three canonical entry points that map to full script sequences.

#### 1. `Find papers on <topic> [from seed <paper>]`

Runs a full discovery → acquisition → download → prompt-generation cycle.

```bash
# Step 1: Search Zotero
python3 paper-discovery/scripts/search_zotero.py \
  --query "<topic>" \
  --mode auto \
  --max-results 20 \
  --output "<WORK_ROOT>/manifests/raw/zotero.json" \
  --log-output "<WORK_ROOT>/logs/zotero-search.json"

# Wait before hitting arXiv
sleep 15

# Step 2: Search arXiv
python3 paper-discovery/scripts/search_arxiv.py \
  --query 'all:"<topic>"' \
  --max-results 20 \
  --delay-seconds 10.0 \
  --output "<WORK_ROOT>/manifests/raw/arxiv.json" \
  --log-output "<WORK_ROOT>/logs/arxiv-search.json"

# (Optional) If seed paper provided, fetch it directly by arXiv ID
# Wait before second arXiv call
sleep 15
python3 paper-discovery/scripts/search_arxiv.py \
  --query "id:<seed_arxiv_id>" \
  --max-results 1 \
  --delay-seconds 10.0 \
  --output "<WORK_ROOT>/manifests/raw/arxiv-seed.json"

# Step 3: Build manifest
python3 paper-discovery/scripts/build_manifest.py \
  --topic "<topic>" \
  --zotero-results "<WORK_ROOT>/manifests/raw/zotero.json" \
  --arxiv-results "<WORK_ROOT>/manifests/raw/arxiv.json" \
  --max-papers 30 \
  --output "<WORK_ROOT>/manifests/paper_manifest.json"

# Step 4: Export acquisition list (--source user marks these as user-requested)
python3 paper-discovery/scripts/export_acquisition_list.py \
  --manifest-path "<WORK_ROOT>/manifests/paper_manifest.json" \
  --output-path $PAPER_BANK/acquisition-list.md \
  --source user

# Step 5: Download papers (--paper-bank defaults to ~/Documents, resolves paper-bank/ internally)
python3 paper-discovery/scripts/download_papers.py \
  --acquisition-list $PAPER_BANK/acquisition-list.md

# Step 6: Generate prompt files
python3 paper-discovery/scripts/generate_prompts.py \
  --acquisition-list $PAPER_BANK/acquisition-list.md \
  --prompts-dir $PAPER_BANK/prompts/ \
  --session-context-dir $PAPER_BANK/session-context/ \
  --paper-reader-skill $SKILLS_ROOT/paper-reader/SKILL.md \
  --vault-root $VAULT_ROOT/

# Step 7: Run Batch Setup (see ### Batch Setup above)
```

For subsequent batches, add `--append` to `export_acquisition_list.py` to skip already-listed cite_keys.

#### 2. `Download and prepare the current list`

Operates on the existing `acquisition-list.md` without running discovery.

```bash
# Step 1: Download papers (--paper-bank defaults to ~/Documents, resolves paper-bank/ internally)
python3 paper-discovery/scripts/download_papers.py \
  --acquisition-list $PAPER_BANK/acquisition-list.md

# Step 2: Generate prompt files
python3 paper-discovery/scripts/generate_prompts.py \
  --acquisition-list $PAPER_BANK/acquisition-list.md \
  --prompts-dir $PAPER_BANK/prompts/ \
  --session-context-dir $PAPER_BANK/session-context/ \
  --paper-reader-skill $SKILLS_ROOT/paper-reader/SKILL.md \
  --vault-root $VAULT_ROOT/

# Step 3: Run Batch Setup (see ### Batch Setup above)
```

#### 3. `Wrap up this batch / check what is left to read`

Reports current batch status without downloading or generating anything.

```bash
python3 paper-discovery/scripts/read_reference_queue.py \
  --reference-queue $VAULT_ROOT/literature/reference-queue.md \
  --top-n 20 \
  --output /tmp/rq-status.json
```

Then read `$PAPER_BANK/acquisition-list.md` and report counts by status:
- `read` — paper-reader session completed
- `pending` — not yet downloaded
- `downloaded` — downloaded and prompts generated; awaiting paper-reader session
- `rate-limited` — download failed due to rate limiting; retry when available
- `manual-pending` — not on arXiv; see `$PAPER_BANK/manual-download-list.md`
