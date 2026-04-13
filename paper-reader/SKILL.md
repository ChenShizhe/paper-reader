---
name: paper-reader
version: 2.0.0
description: >
  Materialize deep, structured paper-reading artifacts from a schema-v1
  paper_manifest.json. Performs source acquisition, translation, segmentation,
  comprehension (section-by-section via subagents), vault integration,
  and summary synthesis. Produces per-section notes, a catalog, and a
  summary note in the Citadel vault.
---

# Paper Reader v2

## Mission

Given a validated `paper_manifest.json`, achieve deep comprehension of each
paper — not flat claims rendering — and persist structured knowledge artifacts
in the Citadel vault.

V2 replaces the v1 one-pass extraction model with a 15-step pipeline that
translates, segments, and reads each paper section by section using subagents
guided by the Reading Constitution. The final output is a synthesis of
per-section comprehension notes, not a mechanical rendering from a claims JSON.

Paper-reader owns:
- source acquisition and format-based fallback selection
- paper-bank storage and manifest updates
- translation to structured markdown
- segmentation with relevance hints
- catalog construction and maintenance
- comprehension via subagents (positioning, technical, empirical)
- vault integration and knowledge-maester coordination
- summary synthesis and polishing
- Zotero item create/update and cite-key synchronization
- `refs.bib` generation and Level 1 citation validation
- self-improvement signal generation from feedback

Paper-reader does not own discovery ranking, synthesis across papers, or
survey drafting.

## Read First

- [references/extraction-templates.md](references/extraction-templates.md)
- [references/citation-validation.md](references/citation-validation.md)
- [reading-constitution.md](reading-constitution.md) — Layer A reading strategies
  loaded by every comprehension subagent

## Inputs

- `<WORK_ROOT>/manifests/paper_manifest.json` from discovery
- `VAULT_ROOT`: configurable (e.g. `~/Documents/citadel/`)
  _Scripts append `literature/papers/<cite_key>/` internally; pass the vault root, not the `literature/` subdirectory._
- `WORK_ROOT`: configurable (e.g. `~/.research-workdir/`)
- `PAPER_BANK`: configurable (e.g. `~/Documents/paper-bank/`)
- Optional batch controls such as `max_extract` and `max_download`

## Required Outputs

**In Citadel (knowledge artifacts):**
- `<VAULT_ROOT>/papers/<cite_key>.md` — final summary note
- `<VAULT_ROOT>/papers/<cite_key>/intro.md`, `notation.md`, `model.md`,
  `method.md`, `theory.md`, `proofs.md`, `empirical.md`, `gaps.md` — per-section
  comprehension notes (status: draft during pipeline; reviewed after polish)
- `<VAULT_ROOT>/<cite_key>-catalog.md` — paper catalog
- `<VAULT_ROOT>/claims/<cite_key>.json` — v2 claims JSON
- `<VAULT_ROOT>/refs.bib`
- `<VAULT_ROOT>/_papers-needing-download.md` when papers cannot be acquired

**In paper-bank (files and structured data):**
- `<PAPER_BANK>/<cite_key>/raw/` — source files (PDF, LaTeX archive)
- `<PAPER_BANK>/<cite_key>/translated_full.md`
- `<PAPER_BANK>/<cite_key>/_translation_manifest.json`
- `<PAPER_BANK>/<cite_key>/segments/_index.json` and `seg-*.md`
- `<PAPER_BANK>/<cite_key>/notation_dict.yaml`
- `<PAPER_BANK>/<cite_key>/author_keywords.txt` — author-provided keywords extracted during translation
- `<PAPER_BANK>/<cite_key>/_vault-write-requests.json`
- `<PAPER_BANK>/_manifest.json`
- `<WORK_ROOT>/preflight_report.json`

## Preflight

Run preflight before acquisition:

```bash
python3 skills/paper-reader/scripts/preflight_extraction.py \
  --output "<WORK_ROOT>/preflight_report.json"
```

Preflight checks (v2):
- All v1 tool checks (pdftotext, pdfinfo, python3, jq)
- `translation_ready`: MinerU availability for PDF-path translation
- `vault_connected`: Citadel vault directory exists and is writable
- `paper_bank_ready`: paper-bank root exists

If preflight reports `overall != "ready"`, stop and report the missing required
tool or directory.

## Direct Source Entry Point (No Manifest)

When running a single-paper extraction without `paper_manifest.json`, use the
pipeline orchestrator directly with `--cite-key` and `--source-path`.

```bash
python3 skills/paper-reader/scripts/run_pipeline.py \
  --cite-key "<cite_key>" \
  --source-format "<pdf|latex|html|markdown>" \
  --source-path "<path to source directory or file>" \
  --paper-bank-dir "<PAPER_BANK>/<cite_key>" \
  --vault-root "<VAULT_ROOT>" \
  --run-report-path "<WORK_ROOT>/logs/<cite_key>-run-report.json"
```

Notes:
- Use `--source-format pdf` for direct PDF ingestion; the orchestrator will run
  PDF segmentation before translation.
- `--source-path` can point to a source directory or a single file. For PDF
  files, the parent directory is used for segmentation input.

## Workflow

This skill executes a pipeline beginning with a reading plan stage (Step 0)
followed by 15 execution steps. Each step has defined input artifacts and
output artifacts. Steps run in order; re-segmentation (Step 9) is automatic
when triggered by the comprehension orchestrator.

### Step 0 — Draft and write reading plan

Before executing any pipeline steps, draft a structured reading plan and write
it to disk. The plan is written to `paper-bank/<cite_key>/_reading_plan.md`
**before** any comprehension phase begins, so it survives session interruptions.

**Inputs:** cite_key, source path, estimated page/section count.

**Resolve vault output paths before drafting.** Before writing any subagent
prompt, call `comprehend_paper.py --dry-run` (or replicate `resolve_papers_dir`
/ `resolve_claims_dir` logic inline) to determine the correct absolute paths:

```
vault_papers_dir  = VAULT_ROOT/literature/papers/  (if it exists, else VAULT_ROOT/papers/)
vault_claims_dir  = VAULT_ROOT/literature/claims/  (if it exists, else VAULT_ROOT/claims/)
```

Embed these resolved paths — not `VAULT_ROOT` plus a hardcoded suffix — into
every subagent prompt's output paths. This prevents subagents from writing
artifacts to a flat `VAULT_ROOT/papers/` when the vault uses a `literature/`
hierarchy.

**Path Verification Block — run `comprehend_paper.py --dry-run` to confirm resolved paths before writing any subagent prompt:**

```bash
python3 skills/paper-reader/scripts/comprehend_paper.py \
  --dry-run \
  --vault-root "<VAULT_ROOT>" \
  --cite-key "<cite_key>"
```

Capture `vault_papers_dir` and `vault_claims_dir` from stdout. If `--dry-run`
is unavailable, replicate the inline logic shown above. Confirm both values
are absolute paths before continuing. **Every subagent prompt in the reading
plan must use these constants — never `VAULT_ROOT + "/papers/"` or
`VAULT_ROOT + "/claims/"` as literal strings.**

**Process:**

1. **Draft the plan** using the canonical template defined in
   `testrun6/vossler-combined-issues.md` under F-002.

   The reading plan file must begin with a **Resolved Paths** header block that
   locks in the absolute vault paths resolved above:

       ## Resolved Vault Paths (do not change)
       - vault_papers_dir: <absolute resolved path>
       - vault_claims_dir: <absolute resolved path>
       - vault_catalog_dir: <VAULT_ROOT>/literature/ (or VAULT_ROOT/ if no literature/)

   Every subagent prompt block in the plan must copy `vault_papers_dir` and
   `vault_claims_dir` from this header verbatim — never re-derive paths inline.

   The plan must contain one entry per phase:
   - Phase 1: Translation & Segmentation — main agent
   - Phase 2: Introduction Comprehension — subagent
   - Phase 3: Technical Comprehension — subagent
   - Phase 4: Empirical & Gaps Comprehension — subagent
   - Phase 5: Vault Note Synthesis — main agent (or subagent for long papers)

   Each comprehension phase entry must include an embedded subagent prompt so
   nothing is lost if the session is interrupted mid-run.

   At the end of the plan, include the following Post-Processing section:

   ## Phase 6: Post-Processing
   - PP1: Collect dummy-note-backed references — read `<WORK_ROOT>/logs/<cite_key>-run-report.json` for `dummy_notes_written` entries; only papers with dummy notes enter the reference queue
   - PP2: Write reference queue patch file to `$PAPER_BANK/rq-patches/<cite_key>-rq-patch.md` as a markdown table (columns: arxiv_id, cite_key, title, importance_score, sessions_cited, first_seen, status); each new row gets importance_score:1, sessions_cited:1, first_seen:<today>, status:mentioned
   - PP3: Update `$PAPER_BANK/acquisition-list.md` status from `downloaded` → `read`; append `<ISO timestamp> | read | <cite_key> | <arxiv_id> | pipeline complete` to `$PAPER_BANK/acquisition-log.md`; if cite_key not found log warning but do not fail
   - PP4: Move `$PAPER_BANK/prompts/<cite_key>-prompt.md` to `$PAPER_BANK/prompts/done/<cite_key>-prompt.md` if it exists
   - Run experience-logger for this session

2. **Proofread the drafted plan** — check for missing sections, coverage gaps,
   and inconsistencies. Verify that every phase has an assigned agent, a
   "Write after:" artifact list, and an embedded prompt. Revise any gaps before
   proceeding.

3. **Write** the finalized plan to `paper-bank/<cite_key>/_reading_plan.md`.

4. **Default (automatic) mode** — proceed immediately after writing the plan.
   No pause required. The plan is available for retroactive inspection.

5. **Approval mode (explicit opt-in)** — activated only when the user
   explicitly requests review before execution (e.g., "show me the plan first"
   or "read this paper and show me the plan first"). In this mode, pause and
   wait for user sign-off. The user may edit the plan before approving.

**Output:** `paper-bank/<cite_key>/_reading_plan.md`

> **Execution discipline (applies to all subsequent phases):** Write every
> artifact to disk immediately upon completion — section notes, theorem index
> entries, claim sidecars. Do not accumulate content in context. Comprehension
> phases (Steps 6–8) are dispatched as subagents per the reading plan.

---

### Step 1 — Load manifest and preserve identity rules

- Read schema-v1 `paper_manifest.json` from discovery.
- Treat manifest-level `cite_key` as provisional until note creation.
- If stronger identifiers are discovered before note creation, promote
  `canonical_id` and re-derive `cite_key` before materializing the note.
- Once `papers/<cite_key>.md` exists, `canonical_id` and `cite_key` are frozen
  unless Zotero sync explicitly returns a new citation key.

**Output:** resolved `cite_key`, `canonical_id`, source format detected.

### Step 2 — Acquire sources

Prefer formats in this order:
1. arXiv LaTeX source (`https://arxiv.org/e-print/<arxiv_id>`)
2. arXiv PDF fallback
3. Other PDF URL from the manifest
4. Manual download queue

All acquired files are saved to `paper-bank/<cite_key>/raw/`. V2 attempts all
available formats (PDF, LaTeX source archive, HTML) so that the translator can
select the best format.

```bash
bash skills/paper-reader/scripts/download_arxiv_sources.sh \
  "<WORK_ROOT>/manifests/paper_manifest.json" \
  "<WORK_ROOT>/downloads/arxiv" \
  "<WORK_ROOT>/downloads/arxiv-extracted"

bash skills/paper-reader/scripts/download_pdfs.sh \
  "<WORK_ROOT>/manifests/paper_manifest.json" \
  "<WORK_ROOT>/downloads/pdfs"

python3 skills/paper-reader/scripts/manage_paper_bank.py \
  --cite-key "<cite_key>" \
  --canonical-id "<canonical_id>" \
  --title "<paper title>" \
  --pdf "<WORK_ROOT>/downloads/pdfs/<cite_key>.pdf" \
  --source "<WORK_ROOT>/downloads/arxiv-extracted/<arxiv_id>" \
  --metadata-json "<WORK_ROOT>/tmp/<cite_key>-metadata.json" \
  --output "<WORK_ROOT>/logs/<cite_key>-paper-bank.json"
```

**Output:** `paper-bank/<cite_key>/raw/`; `_manifest.json` updated.

### Step 3 — Translate paper to structured markdown

Convert the best available source to a unified markdown representation.
After translation, scan the translated text for "Keywords:" or "Key words:"
sections and extract author-provided keywords to
`<PAPER_BANK>/<cite_key>/author_keywords.txt`.

```bash
python3 skills/paper-reader/scripts/translate_paper.py \
  --cite-key "<cite_key>" \
  --bank-dir "<PAPER_BANK>/<cite_key>" \
  --output "<PAPER_BANK>/<cite_key>/translated_full.md"
```

**Output:** `translated_full.md`, `_translation_manifest.json`,
`_translation_warnings.log`, `_theorem_index.json`, `author_keywords.txt`
(when keywords are found in the source).

### Step 4 — Segment paper

Split the translated markdown into indexed chunks with section labels and
relevance hints.

```bash
python3 skills/paper-reader/scripts/segment_paper.py \
  --cite-key "<cite_key>" \
  --input "<PAPER_BANK>/<cite_key>/translated_full.md" \
  --output-dir "<PAPER_BANK>/<cite_key>/segments/"
```

**Output:** `segments/_index.json` and `segments/seg-*.md`. Each segment file
has YAML frontmatter with `cite_key`, `segment_id`, `section_label`,
`token_estimate`, `source_pages`, `comprehension_status: pending`.

### Step 5 — Build initial catalog

Initialize the paper catalog in Citadel with metadata, source format, and
segment inventory.

```bash
python3 skills/paper-reader/scripts/build_catalog.py \
  --cite-key "<cite_key>" \
  --metadata-json "<WORK_ROOT>/tmp/<cite_key>-metadata.json" \
  --segment-index "<PAPER_BANK>/<cite_key>/segments/_index.json" \
  --vault-root "<VAULT_ROOT>" \
  --output "<VAULT_ROOT>/<cite_key>-catalog.md"
```

**Output:** `citadel/literature/<cite_key>-catalog.md` (status: draft).

### Step 6 — Comprehension: Positioning and Literature

Spawn the positioning subagent. The subagent reads the abstract, introduction,
and notation segments, guided by SKILL.md + `reading-constitution.md` + relevant
domain meta-notes (Layer B, up to 3 per section type).

The subagent writes comprehension notes directly to Citadel with `status: draft`.
The intro subagent explicitly notes the paper's self-identified keywords and
contribution areas: it lists author-provided keywords verbatim under an
`## Author Keywords` heading in `intro.md`.

**Output:**
- `citadel/papers/<cite_key>/intro.md` (includes `## Author Keywords` section)
- `citadel/papers/<cite_key>/notation.md`
- Dummy reference stubs in Citadel with `status: stub`

### Step 7 — Comprehension: Technical (model, method, theory)

Spawn the technical subagent. Reads model, method, theory, and proof segments.
Extracts formal equations, parameter spaces, estimation procedures, algorithm
descriptions, convergence rates, and proof strategies.

The model and method subagents identify key methods used in the paper. Each
lists the primary statistical or computational methods under a `## Key Methods`
heading in `model.md` and `method.md` respectively.

**Output:**
- `citadel/papers/<cite_key>/model.md` (includes `## Key Methods` section)
- `citadel/papers/<cite_key>/method.md` (includes `## Key Methods` section)
- `citadel/papers/<cite_key>/theory.md`
- `citadel/papers/<cite_key>/proofs.md`
- `paper-bank/<cite_key>/notation_dict.yaml` (extended with new symbols)

### Step 8 — Comprehension: Empirical and Gaps

Spawn the empirical subagent. Reads simulation, real-data, and
discussion/conclusion segments. Applies ADEMP framework for simulation reviews.
Cross-checks claimed contributions from `intro.md` against evidence found in
empirical sections.

**Output:**
- `citadel/papers/<cite_key>/empirical.md`
- `citadel/papers/<cite_key>/gaps.md`
- Catalog updated with `comprehension_complete` status on empirical sections

### Step 9 — Re-segmentation (automatic when triggered)

The comprehension orchestrator monitors for segment boundary issues (split,
merge, or rebalance triggers). When detected, re-segmentation runs automatically.

```bash
python3 skills/paper-reader/scripts/resegment_paper.py \
  --cite-key "<cite_key>" \
  --catalog "<VAULT_ROOT>/<cite_key>-catalog.md" \
  --segment-dir "<PAPER_BANK>/<cite_key>/segments/"
```

**Output:** Updated segment index and catalog in paper-bank. Audit log written
even when no changes are made (no-op path must complete without error).

### Step 10 — Vault integration

Emit write requests for cross-paper vault operations. Do not write wikilinks,
concept notes, assumption notes, or meta-note updates directly — route them
through knowledge-maester.

```bash
python3 skills/paper-reader/scripts/integrate_vault.py \
  --cite-key "<cite_key>" \
  --vault-root "<VAULT_ROOT>" \
  --output "<PAPER_BANK>/<cite_key>/_vault-write-requests.json"
```

Then invoke knowledge-maester with the request file to apply writes.

**Output:** `paper-bank/<cite_key>/_vault-write-requests.json`; knowledge-maester
applies: cross-paper wikilinks, concept notes, assumption notes, meta-note updates,
dummy stub upgrades — all in Citadel.

### Step 11 — Summary and Polish

Synthesize per-section draft notes into a final literature note. Polish each
per-section note in place (status: draft → reviewed). Run quiz and faithfulness
checks.

The summary note frontmatter includes three additive fields (when data is
available):
- `author_keywords`: list sourced from `author_keywords.txt` or `intro.md`
- `summary`: one-to-two sentence TL;DR synthesized from section notes
- `methods`: list of key methods collected from `model.md`/`method.md`

```bash
python3 skills/paper-reader/scripts/summarize_paper.py \
  --cite-key "<cite_key>" \
  --vault-root "<VAULT_ROOT>" \
  --output "<VAULT_ROOT>/papers/<cite_key>.md"
```

**Output:**
- `citadel/papers/<cite_key>.md` — final polished summary note (with `author_keywords`, `summary`, `methods` in frontmatter)
- `citadel/papers/<cite_key>/` — per-section notes polished in place
- `citadel/literature/claims/<cite_key>.json` — v2 claims format
- `citadel/literature/<cite_key>-catalog.md` — finalized

### Step 12 — Zotero sync

```bash
python3 skills/paper-reader/scripts/sync_zotero.py \
  "<VAULT_ROOT>/papers/<cite_key>.md" \
  --output "<WORK_ROOT>/logs/<cite_key>-zotero-sync.json"
```

- Metadata updated from note frontmatter.
- No PDF upload (raw files stay in paper-bank).
- If Zotero returns a different citation key, update `cite_key` in frontmatter.

### Step 13 — Generate refs.bib

```bash
python3 skills/paper-reader/scripts/generate_bibtex.py \
  "<WORK_ROOT>/manifests/paper_manifest.json" \
  --output "<VAULT_ROOT>/refs.bib"
```

### Step 14 — Validate extraction outputs

```bash
python3 skills/paper-reader/scripts/validate_extraction.py \
  --vault-root "<VAULT_ROOT>"
```

V2 validation checks (in addition to v1 checks):
- Catalog file exists per paper
- All per-section notes are non-empty and not in `status: draft`
- Summary note includes `## Knowledge Gaps` section
- `claims/<cite_key>.json` exists and follows v2 format
- No cite-key mismatch across notes, claims, and `refs.bib`

Highlight in handoff:
- any `metadata-only` papers
- any note with `source_parse_status != full`
- any papers added to `_papers-needing-download.md`
- any cite-key mismatch

### Step 15 — Self-improvement signal

If feedback from a prior run exists in `paper-bank/<cite_key>/_feedback.yaml`,
trigger the Level 2 improvement pass.

```bash
python3 skills/paper-reader/scripts/self_improve.py \
  --cite-key "<cite_key>" \
  --feedback "<PAPER_BANK>/<cite_key>/_feedback.yaml" \
  --constitution "skills/paper-reader/reading-constitution.md" \
  --proposals "skills/paper-reader/reading-constitution-proposals.md"
```

**Output:** `reading-constitution-proposals.md` updated with new rule candidates.

---

### Post-Processing Step: Reference Queue and Status Update

Run after all pipeline steps complete and vault notes are confirmed written.

#### PP1: Collect dummy-note-backed references

Read the session's dummy note write log. During comprehension, `dummy_note_writer.py`
creates or updates notes for papers deemed important enough to stub out. Collect
all cite_keys and arxiv_ids for which dummy notes were created OR updated in this
session. These are the only papers that enter the reference queue — not all
citations in the paper, only those the agent deemed important enough to note.

To collect: check the paper-bank run report at
`<WORK_ROOT>/logs/<cite_key>-run-report.json` for `dummy_notes_written` entries,
OR scan `citadel/literature/papers/` for notes whose `status: stub` or
`status: dummy` were written/updated today.

#### PP2: Write a local reference-queue patch file

**Concurrency strategy (Option A):** Each session writes a local patch file instead of
directly updating `reference-queue.md`. After all sessions in a batch complete, the
user merges patches manually (or via a merge step before the next batch trigger).

**Write patch file to:**
`$PAPER_BANK/rq-patches/<cite_key>-rq-patch.md`

**Patch file format:**

```
# Reference Queue Patch
# Session: <cite_key>
# Generated: <ISO timestamp>

| arxiv_id | cite_key | title | importance_score | sessions_cited | first_seen | status |
|---|---|---|---|---|---|---|
| 2401.00001 | smith2024foo | Foo paper | 1 | 1 | <today> | mentioned |
```

For each paper collected in PP1, write one row with:
- `importance_score`: always 1 (the merge step applies cumulative increments)
- `sessions_cited`: always 1 (same reason)
- `first_seen`: today's date
- `status`: `mentioned`

#### PP3: Update acquisition-list.md

Directly update the row for the current paper in
`$PAPER_BANK/acquisition-list.md`:
- Change `status` column from `downloaded` → `read`

Then append to `$PAPER_BANK/acquisition-log.md`:

```
<ISO timestamp> | read | <cite_key> | <arxiv_id> | pipeline complete
```

If `acquisition-list.md` does not exist or the cite_key is not found:
log a warning to the run report but do not fail — the paper-reader pipeline
result is not contingent on the acquisition list.

#### PP4: Archive prompt file

Move `$PAPER_BANK/prompts/<cite_key>-prompt.md` to
`$PAPER_BANK/prompts/done/<cite_key>-prompt.md` if it exists.

## Orchestration

The skill is run by an AI agent (Claude or compatible) that drives the full
pipeline — beginning with the reading plan stage (Step 0) — invoking scripts
for I/O operations and spawning subagents for comprehension steps.

**Reading plan stage:**
- Step 0 drafts and proofreads a structured `_reading_plan.md` before any
  execution begins. The plan is written to `paper-bank/<cite_key>/` first.
- Default mode: write the plan and proceed immediately (no user pause).
- Approval mode: write the plan, then wait for explicit user sign-off before
  executing. Activated only when the user requests it.
- The reading plan embeds a subagent prompt for each comprehension phase so
  the plan is self-contained and resumable after interruption.

**Comprehension subagent pattern:**
- `comprehend_paper.py` is the comprehension orchestrator. It spawns per-section
  subagents for Steps 6, 7, and 8.
- Each subagent receives: shared Layer A context (SKILL.md + `reading-constitution.md`
  + `proof-patterns.md` where relevant) + section-specific Layer B meta-notes
  (up to 3 per subagent, queried from `citadel/literature/meta/`).
- Each subagent writes its section note directly to Citadel with `status: draft`.
- After each section note: the orchestrator runs a Reading Constitution self-critique
  pass.
- If the orchestrator detects a segment boundary issue: triggers `resegment_paper.py`
  automatically (Step 9).

```bash
python3 skills/paper-reader/scripts/comprehend_paper.py \
  --cite-key "<cite_key>" \
  --segment-dir "<PAPER_BANK>/<cite_key>/segments/" \
  --vault-root "<VAULT_ROOT>" \
  --constitution "skills/paper-reader/reading-constitution.md"
```

Use `--dry-run` to verify the dispatch plan without running subagents.

## Operational Rules

**Boundary rule — Citadel = knowledge vault; paper-bank = file store.**

- Citadel (`$VAULT_ROOT`) holds all knowledge artifacts: paper summary
  notes, per-section reading notes, concept notes, assumption notes, author notes,
  meta-notes, notation registry notes, dummy reference stubs, the catalog markdown
  file, the claims JSON, and refs.bib.
- Paper-bank (`$PAPER_BANK/`) holds raw source files, processed files
  (translated markdown, segments), and machine-readable structured data
  (`notation_dict.yaml`, `_vault-write-requests.json`, operational logs).
- Knowledge artifacts — even draft ones — live in Citadel from the moment they are
  created. Do not route knowledge artifacts through paper-bank.
- Cross-paper vault writes (wikilinks, concept notes, assumption notes, meta-note
  updates) go through knowledge-maester via `_vault-write-requests.json`. Do not
  write these directly from the pipeline.

**Faithfulness rules:**
- Do not fabricate claims, theorems, methodology, or bibliographic fields.
- Do not mutate discovery outputs in place.
- Every substantive claim in the v2 claims JSON must include `source_anchor` with
  `source_type`, `locator`, and `confidence`. If a locator cannot be resolved,
  use `locator: "not found"` and lower confidence.
- Connection claims must set `linked_paper_status` to `in-corpus` or
  `out-of-corpus`.
- `claims/<cite_key>.json` is authoritative when note markdown diverges.
- `metadata-only` notes must emit an empty `claims` array.

**Note formatting:**
- Use `$...$` for inline math and `$$...$$` for display math in markdown notes.

## Self-Improvement

The Reading Constitution (`reading-constitution.md`) defines Layer A reading
strategies. It is loaded by every comprehension subagent and version-stamped in
the catalog.

Feedback from completed runs is stored in `paper-bank/<cite_key>/_feedback.yaml`
and `_quiz_failures.yaml`. Step 15 processes feedback into proposed rule updates
in `reading-constitution-proposals.md`.

See `reading-constitution.md` for the current rule set and
`reading-constitution-proposals.md` for pending proposals.

## Summary Note Frontmatter Fields (additive)

The final summary note (`<cite_key>.md`) includes three additive frontmatter
fields introduced by the keyword/summary extraction pipeline. These fields
are purely additive — papers read before this feature was added are
unaffected (they have no `author_keywords`, `summary`, or `methods` fields,
which is valid).

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `author_keywords` | `list[str]` | `author_keywords.txt` (Step 3) or `intro.md` (Step 6) | Author-provided keywords extracted verbatim from the paper source |
| `summary` | `str` | Synthesized (Step 11) | One-to-two sentence TL;DR synthesized from section notes |
| `methods` | `list[str]` | `model.md` / `method.md` (Step 7) | Key statistical or computational methods identified during comprehension |
| `controlled_keywords` | `list[str]` | Assigned downstream by knowledge-maester | Normalized taxonomy keywords — **not assigned by paper-reader**; added by `knowledge-maester/scripts/normalize_keywords.py` after ingestion |

**Note:** `controlled_keywords` is documented here for completeness but is
**not** written by paper-reader. It is assigned downstream by the
knowledge-maester skill during post-ingestion keyword normalization. Paper-reader
only extracts the raw `author_keywords`; controlled vocabulary mapping is a
separate concern.

## Out Of Scope

- search, ranking, or manifest construction
- digest or field-summary generation
- survey drafting
- synthesis across multiple papers
- cross-paper vault operations (delegated to knowledge-maester)

## Acceptance

**Tier 1 — Unit tests (automated):**
```bash
python3 -m unittest discover -s skills/paper-reader/tests -p 'test_*.py'
```

**Tier 2 — Integration (manual before release):**
```bash
python3 skills/paper-reader/scripts/preflight_extraction.py \
  --output "<WORK_ROOT>/preflight_report.json"
```

```bash
python3 skills/paper-reader/scripts/validate_extraction.py \
  --vault-root "<VAULT_ROOT>"
```

Run the full pipeline on at least one example paper (LaTeX path and PDF path).
Assert that:
- The catalog file exists per paper
- All per-section notes are non-empty and not in `status: draft`
- The summary note includes `## Knowledge Gaps`
- `claims/<cite_key>.json` exists in v2 format
- No cite-key mismatch across notes, claims, and `refs.bib`

**Tier 3 — Quality (human-in-the-loop):**
Read the generated summary note and mark it using the feedback form. A quality
test passes when the summary receives a non-failing verdict.
