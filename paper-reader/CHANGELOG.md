# Changelog — paper-reader

All notable changes to this skill are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.0.0] — 2026-03-21

**This release is an in-place replacement of v1.** The skill directory, entry
points, and manifest contract are unchanged. The internal model is a complete
replacement: the v1 one-pass extraction pipeline is replaced by a 15-step
comprehension pipeline driven by subagents and guided by the Reading
Constitution.

### Breaking Changes

- **Output format** — v1 produced a flat claims JSON (`claims/<cite_key>.json`)
  from a mechanical extraction pass. v2 requires every claim to include a
  `source_anchor` object with `source_type`, `locator`, and `confidence`. Old
  v1 claims files are **not** forward-compatible and must be regenerated.
- **Validation rules** — `validate_extraction.py` now enforces v2 checks:
  catalog file per paper, all per-section notes non-empty and not in
  `status: draft`, summary note must include `## Knowledge Gaps`, and
  `claims/<cite_key>.json` must follow v2 format. Runs validated only against
  v1 outputs will fail these checks.
- **Preflight checks** — three new required checks added (`translation_ready`,
  `vault_connected`, `paper_bank_ready`). Environments that passed v1 preflight
  may now report `overall != "ready"` if MinerU is unavailable or the Citadel
  vault is not mounted.
- **Step count** — the pipeline expanded from a single extraction pass to 15
  numbered steps. Any orchestration scripts that assumed a linear
  download-extract-write flow must be updated to drive all 15 steps.
- **Note locations** — v1 wrote a single summary note per paper. v2 writes
  per-section notes (`intro.md`, `notation.md`, `model.md`, `method.md`,
  `theory.md`, `proofs.md`, `empirical.md`, `gaps.md`) under
  `citadel/papers/<cite_key>/` in addition to the top-level summary. Consumers
  that read only the summary note will miss per-section depth.

### Added (v2 new capabilities)

- 15-step orchestrated pipeline with defined input/output contracts per step.
- Source acquisition with format priority: LaTeX source > arXiv PDF > other PDF
  > manual queue.
- Translation to structured markdown (`translate_paper.py`) with
  format-based fallback selection and `_translation_manifest.json`.
- Segmentation with relevance hints (`segment_paper.py`), producing
  `segments/_index.json` and `seg-*.md` with YAML frontmatter.
- Comprehension via three specialized subagents: positioning, technical, and
  empirical (Steps 6–8), each guided by SKILL.md + Reading Constitution
  (Layer A) + domain meta-notes (Layer B, up to 3 per subagent).
- Reading Constitution (`reading-constitution.md`) as a version-stamped
  Layer A context loaded by every comprehension subagent.
- Automatic re-segmentation (Step 9) when boundary issues are detected by the
  comprehension orchestrator.
- Vault integration via `_vault-write-requests.json` routed through
  knowledge-maester for cross-paper operations.
- Summary and polish step (Step 11) with quiz and faithfulness checks.
- Self-improvement signal generation (Step 15) from per-run feedback stored in
  `_feedback.yaml`.
- `comprehend_paper.py` orchestrator with `--dry-run` dispatch verification.
- Catalog construction and maintenance (`build_catalog.py`).
- `identity.py` — cite-key and canonical-id identity rules enforced across
  the pipeline.
- `notation_extractor.py` and `notation_dict.yaml` for formal symbol
  disambiguation.
- `intro_positioner.py`, `intro_reader.py` — intro-specific positioning pass.
- `author_note_writer.py`, `xref_writer.py` — author and cross-reference notes.
- `context_loader.py`, `meta_note_query.py` — Layer B meta-note loading.
- `subagent_contracts.py` — typed contracts for subagent dispatch.
- `build_theorem_index.py`, `format_theorems.py`, `number_equations.py`,
  `expand_macros.py` — theorem and equation processing pipeline.

### Extended (v1 capabilities kept and expanded)

These capabilities existed in v1 and are **Kept** or **Extended** in v2.

- **[Kept]** `download_arxiv_sources.sh` — unchanged from v1; still downloads
  LaTeX source archives from arXiv.
- **[Kept]** `download_pdfs.sh` — unchanged from v1; still downloads PDFs.
- **[Kept]** `generate_bibtex.py` — unchanged from v1; still generates
  `refs.bib` from the paper manifest.
- **[Kept]** `sync_zotero.py` — unchanged from v1; still syncs metadata to
  Zotero and reconciles citation keys.
- **[Kept]** `render_note_from_claims.py` — retained for backward-compatible
  rendering; not called by the v2 pipeline.
- **[Extended]** `preflight_extraction.py` — v1 checks (pdftotext, pdfinfo,
  python3, jq) are preserved; three new v2 checks added.
- **[Extended]** `validate_extraction.py` — v1 output checks preserved; v2
  adds catalog, per-section note, and claims-format checks.
- **[Extended]** `manage_paper_bank.py` — v1 paper-bank storage retained;
  v2 adds `_translation_manifest.json`, `segments/`, and
  `_vault-write-requests.json` management.
- **[Extended]** Claims JSON — v1 flat structure retained as starting point;
  v2 requires `source_anchor` on every substantive claim.

### Replaced (v1 items superseded by v2 equivalents)

- **[Replaced]** One-pass extraction model → 15-step comprehension pipeline.
  The v1 pass read the paper once and emitted claims directly. v2 translates,
  segments, and reads section-by-section via subagents.
- **[Replaced]** Single summary note output → per-section notes + summary.
  v1 wrote only `papers/<cite_key>.md`. v2 writes eight per-section notes plus
  the summary.
- **[Replaced]** Flat claims JSON (no locators) → v2 claims JSON with
  `source_anchor` required on every substantive claim.
- **[Replaced]** Manual note formatting → polish step with quiz and faithfulness
  checks (Step 11).

---

## [DEPRECATED - v2] Deprecation Policy

The following v1 behaviors are deprecated as of v2.0.0 and will be removed in a
future major release:

- **Flat claims JSON without `source_anchor`** — v1-format claims files are
  accepted by `validate_extraction.py` only if the `--legacy` flag is passed.
  The flag will be removed once all existing runs are migrated.
- **`render_note_from_claims.py` as a pipeline step** — this script is retained
  for backward compatibility but is no longer invoked by the v2 orchestration.
  Downstream consumers relying on its output format should migrate to the v2
  summary step output.
- **Single-pass orchestration** — any external script that calls extraction
  steps directly (bypassing `comprehend_paper.py`) bypasses v2 faithfulness
  checks and produces outputs that will fail v2 validation.

Deprecated items will be marked with `# [DEPRECATED - v2]` in source code
comments at the point of removal.

---

## [1.x] — prior releases

v1 was a one-pass extraction model. It downloaded papers, ran `pdftotext`-based
extraction, emitted a flat claims JSON, and wrote a single summary note per
paper. No segmentation, no subagents, no Reading Constitution.

Detailed v1 release notes were not maintained. The v2 in-place replacement
supersedes all v1 incremental entries.
