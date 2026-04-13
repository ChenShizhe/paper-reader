---
name: paper-batch-coordinator
description: Orchestrate full paper-reading batches by delegating to paper-discovery, paper-reader, and knowledge-maester in the correct lifecycle order.
---

# Paper Batch Coordinator

## Mission

Coordinate end-to-end paper-reading batches. This skill does not implement discovery, reading, or vault writes itself — it sequences the canonical entry points of paper-discovery, paper-reader, and knowledge-maester and ensures all paths are wired consistently across every step.

## Canonical Paths

| Resource | Path |
|---|---|
| Skills root | `$SKILLS_ROOT/` |
| Paper bank | `$PAPER_BANK/` |
| Vault root | `$VAULT_ROOT/` |
| Reference queue | `$VAULT_ROOT/literature/reference-queue.md` |
| Acquisition list | `$PAPER_BANK/acquisition-list.md` |
| Manual download list | `$PAPER_BANK/manual-download-list.md` |

## Lifecycle Commands

### Read `<cite_key>`

Invoke the paper-reader skill for a single paper whose sources are already present in the paper bank. Derive `source_path` from `paper-bank/raw/<cite_key>/`.

Load `../paper-reader/SKILL.md` and execute it with the following parameters:

| Parameter | Value |
|---|---|
| `cite_key` | `<cite_key>` |
| `source_path` | `$PAPER_BANK/raw/<cite_key>/` |
| `VAULT_ROOT` | `$VAULT_ROOT/` |
| `PAPER_BANK` | `$PAPER_BANK/` |
| `WORK_ROOT` | `~/.research-workdir/` |
| `reference_queue` | `$VAULT_ROOT/literature/reference-queue.md` |
| `acquisition_list` | `$PAPER_BANK/acquisition-list.md` |

Before invoking paper-reader, confirm that `$PAPER_BANK/raw/<cite_key>/` exists and is non-empty. If missing, run the `Download and prepare the current list` command first or download the paper manually.

### Find papers on `<topic>` [from seed `<cite_key>`]

Runs the full discovery → download → prompts → batch-setup sequence defined in `../paper-discovery/SKILL.md`.

Follow the `Find papers on <topic> [from seed <paper>]` entry point in paper-discovery/SKILL.md exactly, substituting `<topic>` and (if provided) the seed `<cite_key>` as the `--arxiv-id` seed. After the sequence completes, run Batch Setup (see paper-discovery/SKILL.md § Batch Setup) to refresh `$PAPER_BANK/session-context/`.

High-level sequence:

1. Search Zotero, then wait 15 seconds, then search arXiv for `<topic>` (`--delay-seconds 10.0`); optionally inject seed paper by arXiv ID (add another `sleep 15` before the second arXiv call).
2. Build and validate `paper_manifest.json`.
3. Export to `$PAPER_BANK/acquisition-list.md` (`--append` for subsequent batches).
4. Download papers (`download_papers.py`).
5. Generate prompt files (`generate_prompts.py`).
6. Run Batch Setup to refresh `$PAPER_BANK/session-context/`.

### Update acquisition list from the reference queue

Reads `reference-queue.md`, identifies all unread papers (`status: mentioned`), and appends them to `acquisition-list.md` in the correct schema. Run this before "Download and prepare the current list" when the source of papers is the reference queue rather than a fresh discovery run.

**Step 1 — Collect unread references**

Read `$VAULT_ROOT/literature/reference-queue.md`. Collect every row where `status` is `mentioned` — these are papers cited in prior reading sessions but not yet acquired.

**Step 2 — Deduplicate**

Read `$PAPER_BANK/acquisition-list.md`. Collect all existing `cite_key` values (first column of every data row). Skip any reference queue entry whose `cite_key` is already present.

**Step 3 — Derive arXiv IDs**

For each new entry, derive `arxiv_id`:
- Use the `id` column value if it matches an arXiv ID pattern (digits, a dot or slash, more digits — e.g. `2203.02155`, `2302.07459`).
- Otherwise extract from the `url` column if it contains `arxiv.org/abs/`.
- Otherwise leave blank — `download_papers.py` will route it to `manual-download-list.md` automatically.

**Step 4 — Append rows to acquisition-list.md**

If `acquisition-list.md` does not exist, or its header does not match the required schema, create or replace the header:

```
| cite_key | arxiv_id | title | topic | priority | reason | status | source |
|---|---|---|---|---|---|---|---|
```

Append one row per new paper:

```
| <cite_key> | <arxiv_id_or_blank> | <title> | | medium | | pending | reference-queue |
```

**Step 5 — Mark exported entries as pending in reference-queue.md**

In `$VAULT_ROOT/literature/reference-queue.md`, update every exported entry's `status` from `mentioned` to `pending`.

After this step, run "Download and prepare the current list" to download and generate prompts.

---

### Download and prepare the current list

Operates on the existing `$PAPER_BANK/acquisition-list.md` without running discovery. Runs download → prompts → batch-setup on the current acquisition list.

```bash
# Step 1: Download papers (--paper-bank defaults to $PAPER_BANK parent dir, resolves paper-bank/ internally)
python3 ../paper-discovery/scripts/download_papers.py \
  --acquisition-list $PAPER_BANK/acquisition-list.md
# Papers with no arXiv ID or that return 404 are marked manual-pending and
# written to $PAPER_BANK/manual-download-list.md for manual retrieval.

# Step 2: Generate prompt files
python3 ../paper-discovery/scripts/generate_prompts.py \
  --acquisition-list $PAPER_BANK/acquisition-list.md \
  --prompts-dir $PAPER_BANK/prompts/ \
  --session-context-dir $PAPER_BANK/session-context/ \
  --paper-reader-skill ../paper-reader/SKILL.md \
  --vault-root $VAULT_ROOT/

# Step 3: Run Batch Setup (see paper-discovery/SKILL.md § Batch Setup)
```

### Wrap up this batch

Invoke knowledge-maester to merge all `rq-patches/` patch files produced during this batch into `$VAULT_ROOT/literature/reference-queue.md`.

Load `$SKILLS_ROOT/knowledge-maester/SKILL.md` (optional dependency — not included in this repo) and instruct it to:

1. Read every patch file under `$PAPER_BANK/rq-patches/` (one JSON patch per paper-reader session).
2. Merge each patch into `$VAULT_ROOT/literature/reference-queue.md`, adding new entries and updating existing ones according to the merge rules in knowledge-maester/SKILL.md.
3. Archive processed patch files (move to `$PAPER_BANK/rq-patches/applied/`).
4. Report the count of entries added, updated, and skipped.

After knowledge-maester confirms the merge, run the post-read organization steps below, then read `$PAPER_BANK/acquisition-list.md` and report batch completion counts by status (`read`, `downloaded`, `pending`, `rate-limited`, `manual-pending`). If any `manual-pending` rows exist, also read `$PAPER_BANK/manual-download-list.md` and list the papers that need manual retrieval.

#### Post-Read Organization Steps

After all papers in the batch are ingested and rq-patches are merged, run the following three steps to normalize keywords, update the SQLite index, and regenerate catalog MOCs.

**Error handling:** If any organization step fails, log the error and continue to the next step. Organization failures must **not** block batch completion — they are recoverable on the next run.

**Step O-1: Keyword Normalization**

Assign controlled keywords to all newly ingested papers that do not yet have them.

```bash
python3 knowledge-maester/scripts/normalize_keywords.py \
  --vault-path $VAULT_ROOT \
  --taxonomy taxonomy.yaml \
  --synonym-map synonym_map.json \
  --all-unclassified
```

- Matches author-provided keywords to canonical taxonomy terms via `synonym_map.json`.
- Papers that cannot be classified are appended to `pending_terms.yaml` for manual review.
- On failure: log the error; unclassified papers remain unclassified until the next run.

**Step O-2: SQLite Index Update**

Incrementally update the literature SQLite index with data from newly ingested papers.

```bash
python3 knowledge-maester/scripts/build_taxonomy_db.py \
  --vault-path $VAULT_ROOT \
  --taxonomy taxonomy.yaml \
  --synonym-map synonym_map.json \
  --incremental
```

- Updates `$VAULT_ROOT/literature/_index.db` with new papers, keywords, and relationships.
- On failure: log the error; the next `--incremental` or full rebuild will recover the index.

**Step O-3: Catalog MOC Regeneration**

Regenerate all catalog Map-of-Content pages to reflect the updated index.

```bash
python3 knowledge-maester/scripts/generate_catalog_mocs.py \
  --vault-path $VAULT_ROOT \
  --db-path $VAULT_ROOT/literature/_index.db \
  --all
```

- Writes per-keyword MOC pages to `$VAULT_ROOT/literature/_catalog/`.
- On failure: log the error; stale MOCs remain in place (not destructive) and are refreshed on the next successful run.

## How to Load This Skill

To activate this skill in a new session, paste the following trigger:

```
Read and follow paper-batch-coordinator/SKILL.md
```
