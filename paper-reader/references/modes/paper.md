# Mode: paper (default)

This document describes the default `paper` mode of the paper-reader pipeline.
It serves as the reference baseline for comparing future reader modes
(e.g., `book`, `chain_map`, `institutional`).

No pipeline behavior is defined here; this is a reference document only.

---

## Overview

`paper` mode is the pipeline default. It targets a single academic paper
written in the conventional IMRaD structure and processes it through a
three-phase subagent dispatch: positioning, technical, and empirical.

Activated by passing `--mode paper` (or omitting `--mode`, since `paper`
is the default in `run_pipeline.py`, `comprehend_paper.py`, and
`validate_extraction.py`).

---

## Expected Paper Structure (IMRaD)

The mode assumes papers follow the IMRaD convention:

| Section group      | Typical section_type values in `_catalog.yaml`              |
|--------------------|--------------------------------------------------------------|
| Introduction       | `introduction`, `abstract`, `background`, `related_work`,   |
|                    | `literature_review`, `preliminaries`                        |
| Methods / Model    | `model`, `model_method`, `model_theory`, `formulation`,      |
|                    | `method`, `methods`, `method_theory`, `algorithm`,          |
|                    | `implementation`                                             |
| Theory / Proofs    | `theory`, `proof`, `analysis`, `appendix`                   |
| Results / Empirical| `simulation`, `real_data`, `experiments`, `results`,        |
|                    | `evaluation`, `discussion`, `conclusion`                    |

Papers that substantially deviate from IMRaD (e.g., survey papers, extended
abstracts, whitepapers) may still be processed in `paper` mode but may
produce incomplete comprehension coverage for non-standard section types.

---

## Three-Subagent Dispatch

`comprehend_paper.py` orchestrates comprehension across three sequential
drivers. Each driver is a dedicated subagent (or inline subprocess) that
handles a logical phase of reading:

### 1. Positioning subagent — `comprehend_intro.py`

Handles Steps 4.1–4.3 of the reading protocol:

- **Step 4.1** (`intro_positioner.py`): Classifies introduction blocks into
  `BACKGROUND`, `PRIOR_WORK`, `GAP`, `CONTRIBUTION`, `MOTIVATION`, `ROADMAP`.
- **Step 4.2** (`intro_reader.py`): Structured extraction from the introduction;
  writes sections to `intro.md` in the Citadel vault.
- **Step 4.3** (`notation_extractor.py`): Extracts paper-specific notation into
  `notation_dict.yaml`.

Also runs `dummy_note_writer.py`, `author_note_writer.py`, and
`xref_writer.py` as downstream transforms.

Sets catalog comprehension_status → `intro_complete`.

### 2. Technical subagent — `comprehend_technical.py`

Handles Steps 5.1–5.6 of the reading protocol:

- **Step 5.1** (`model_reader.py`): Model section reading.
- **Step 5.2** (`method_reader.py`): Method section reading.
- **Step 5.3** (`theory_reader.py`): Theory / proof section reading.
- **Step 5.4** (`convergence_rate_extractor.py`): Structural transform for
  convergence rates.
- **Step 5.5** (`assumption_writer.py`): Writes assumption notes.
- **Step 5.6** (`xref_tech_writer.py`): Updates cross-references for technical
  sections.

Also merges `notation_dict.yaml` (skip-on-collision strategy).

Sets catalog comprehension_status → `technical_complete`.

### 3. Empirical subagent — `comprehend_empirical.py`

Handles Steps 6.1–6.6 of the reading protocol:

- **Step 6.1** (`simulation_reader.py`): Simulation / experiment section reading.
- **Step 6.2** (`real_data_reader.py`): Real data / application section reading.
- **Step 6.3** (`discussion_reader.py`): Discussion, conclusion, appendix reading.
- **Step 6.4** (`ref_resolver.py`): Reference list resolution.
- **Step 6.5** (`gap_consolidator.py`): Knowledge gap consolidation.
- **Step 6.6** (`claim_verifier.py`): Final claim verification.

Prerequisite: catalog must have `comprehension_status: technical_complete`.

Sets catalog comprehension_status → `empirical_complete`.

### Dispatch execution modes

The calling agent can control how subagents are invoked:

| `--llm-dispatch` value | Behavior                                                      |
|------------------------|---------------------------------------------------------------|
| `inline` (default)     | Each driver runs as a subprocess of the main agent.           |
| `subagent`             | `comprehend_paper.py` emits a dispatch-plan JSON and exits;   |
|                        | the caller spawns one subagent per plan entry.                |
| `auto`                 | Resolved from `PAPER_READER_LLM_DISPATCH` env var, then      |
|                        | `PAPER_READER_USE_SUBAGENT` bool env var, then `inline`.      |

---

## Claim Domain and Claim Types

### Default claim_domain

`paper` mode sets `claim_domain: academic` by default.

`validate_extraction.py` reads `claim_domain` from `_catalog.yaml`
(`paper.claim_domain`). When the field is absent or the catalog does not
exist, it falls back to `"academic"`.

### Academic claim-type subset

In `academic` mode, only the following eight claim types are permitted in
the claims sidecar (`<cite_key>-claims.json`):

| Claim type           | Description                                              |
|----------------------|----------------------------------------------------------|
| `theorem`            | A formally stated mathematical result or lemma.          |
| `assumption`         | An explicit model or analysis assumption.                |
| `methodology`        | A described procedure, algorithm, or design choice.      |
| `empirical`          | An observation or result backed by experiment or data.   |
| `connection`         | A stated relationship to prior work or other results.    |
| `limitation`         | An acknowledged scope restriction or failure mode.       |
| `data-availability`  | Information about data access or release.                |
| `code-availability`  | Information about code / artifact release.               |

All other claim types (e.g., `policy-recommendation`, `projection`,
`company-thesis`, `supply-chain-fact`) are rejected by `validate_extraction.py`
when `claim_domain` is `academic`.

---

## Required intro.md Heading

After `comprehend_intro.py` runs, the paper's `intro.md` file in the Citadel
vault **must** contain a contributions heading. The validator and downstream
summary steps accept any of these three heading variants (case-sensitive,
level 2):

```
## Claimed Contributions
## Main Contributions
## Contributions
```

The canonical heading written by `intro_reader.py` is `## Claimed Contributions`
(defined as `SECTION_CLAIMED_CONTRIBUTIONS` in `intro_reader.py`).

If the heading is absent, the comprehension pipeline marks the intro pass as
incomplete and downstream summary layers may be empty.

---

## Key Files

| File                            | Role                                              |
|---------------------------------|---------------------------------------------------|
| `scripts/run_pipeline.py`       | Top-level orchestrator; accepts `--mode paper`    |
| `scripts/comprehend_paper.py`   | Comprehension orchestrator; dispatches 3 drivers  |
| `scripts/comprehend_intro.py`   | Positioning subagent driver (Steps 4.x)           |
| `scripts/comprehend_technical.py` | Technical subagent driver (Steps 5.x)           |
| `scripts/comprehend_empirical.py` | Empirical subagent driver (Steps 6.x)           |
| `scripts/validate_extraction.py`| Validates claims sidecar against academic subset  |
| `scripts/subagent_contracts.py` | `SubagentInput` / `SubagentOutput` dataclasses    |
