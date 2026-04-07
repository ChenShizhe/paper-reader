# Citation Validation Protocol

## Purpose
Prevent unsupported or malformed citations from propagating downstream. The
minimum bar for extraction is structural integrity and cite-key consistency.

## Validation Levels

### Level 1 - Offline Structural Checks
Always run these before handing extraction artifacts to another module.

Preferred implementation:

```bash
python3 skills/paper-reader/scripts/validate_extraction.py "<VAULT_ROOT>"
```

Required checks:
1. `refs.bib` is parseable, or at minimum structurally well-formed if only
   regex validation is available.
2. Every BibTeX entry has non-empty `author`, `title`, and `year`.
3. BibTeX cite keys are unique.
4. Every paper note frontmatter `cite_key` exists in `refs.bib`.
5. Every `claims/<cite_key>.json` file matches an existing paper note and the
   same `cite_key`.
6. `canonical_id` is consistent across note frontmatter and claims sidecar.
7. No orphan BibTeX entry exists without a corresponding note.
8. `auto_block_hash` matches the actual machine-owned markdown block.

Blocking failures:
- malformed BibTeX
- duplicate cite keys
- note/frontmatter and `refs.bib` mismatch
- note/frontmatter and claims sidecar mismatch

Non-blocking warnings:
- missing DOI
- missing venue fields
- notes with `content_status: metadata-only`

### Level 2 - Online Metadata Checks
Run only when network access is available and the caller opts in.

Checks:
1. arXiv ID verification for entries with `arxiv_id`
2. DOI resolution for entries with `doi`
3. Optional author/title cross-check against authoritative metadata

These checks add warnings or errors, but they do not change extraction
artifacts silently.

### Level 3 - Advisory Semantic Review
Optional and non-blocking.

Use an LLM only to flag suspiciously generic claims or bibliography anomalies.
Never let semantic review invent metadata or override Level 1 structural facts.

## Validation Summary Shape

```json
{
  "total_entries": 25,
  "passed": 22,
  "warnings": [
    {
      "cite_key": "smith2020xyz",
      "issue": "DOI not found",
      "severity": "medium"
    }
  ],
  "errors": [
    {
      "cite_key": "fake2021paper",
      "issue": "duplicate cite_key",
      "severity": "high"
    }
  ],
  "recommendation": "Fix structural errors before synthesis."
}
```

## Severity Guide
- `high`: artifact is unsafe for downstream use
- `medium`: metadata inconsistency that should be reviewed soon
- `low`: formatting or completeness issue

## Operational Rules
- Offline Level 1 validation is the extraction gate.
- Online checks are additive and opt-in.
- Do not delete or rename notes automatically during validation.
- When in doubt, flag and continue rather than guessing.
