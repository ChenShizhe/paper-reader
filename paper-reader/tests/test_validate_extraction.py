from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_SCRIPT = REPO_ROOT / "scripts" / "validate_extraction.py"

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "claims"

# Import validate_claim_record directly from the script without requiring it
# to be installed as a package.
def _load_validate_extraction():
    spec = importlib.util.spec_from_file_location(
        "validate_extraction", _SCRIPTS_DIR / "validate_extraction.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ve_mod = _load_validate_extraction()
_validate_claim_record = _ve_mod.validate_claim_record


class ValidateExtractionTest(unittest.TestCase):
    def test_validate_extraction_accepts_minimal_valid_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir) / "vault"
            papers = vault / "papers"
            claims = vault / "claims"
            papers.mkdir(parents=True)
            claims.mkdir(parents=True)

            auto_block = "\n".join(
                [
                    "<!-- AUTO-GENERATED:BEGIN -->",
                    "## Abstract",
                    "not found",
                    "",
                    "## Key Theorems / Results",
                    "- not found",
                    "",
                    "## Key Assumptions",
                    "- not found",
                    "",
                    "## Methodology / Key Techniques",
                    "- not found",
                    "",
                    "## Empirical Findings",
                    "- not found",
                    "",
                    "## Connections To Other Papers",
                    "- not found",
                    "",
                    "## Data & Code Availability",
                    "- Data: not found",
                    "- Code: not found",
                    "",
                    "## Limitations",
                    "- not found",
                    "<!-- AUTO-GENERATED:END -->",
                ]
            )
            auto_hash = hashlib.sha256(auto_block.encode("utf-8")).hexdigest()

            note_text = "\n".join(
                [
                    "---",
                    'schema_version: "1"',
                    'canonical_id: "manual:demo1234"',
                    'cite_key: "demo2026paper"',
                    "arxiv_id: null",
                    "doi: null",
                    "openalex_id: null",
                    'title: "Demo Paper"',
                    "authors:",
                    '  - "Jane Doe"',
                    "year: 2026",
                    "tags: []",
                    "date_read: null",
                    "last_read_at: null",
                    'source_type: "manual"',
                    'source_path: "downloads/demo2026paper.pdf"',
                    'bank_path: "/tmp/paper-bank/demo2026paper"',
                    'source_parse_status: "failed"',
                    'bibliography_status: "missing"',
                    'content_status: "metadata-only"',
                    'extraction_confidence: "low"',
                    'validation_status: "pending"',
                    'review_status: "auto"',
                    f'auto_block_hash: "{auto_hash}"',
                    "dataset_links: []",
                    "code_links: []",
                    "supplementary_links: []",
                    "---",
                    "",
                    auto_block,
                    "",
                    "## Reading Notes",
                    "_User-owned section. Never rewrite automatically._",
                    "",
                ]
            )

            (papers / "demo2026paper.md").write_text(note_text, encoding="utf-8")
            (claims / "demo2026paper.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "cite_key": "demo2026paper",
                        "canonical_id": "manual:demo1234",
                        "content_status": "metadata-only",
                        "extraction_confidence": "low",
                        "claims": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (vault / "refs.bib").write_text(
                "@misc{demo2026paper,\n  title = {Demo Paper},\n  author = {Doe, Jane},\n  year = {2026},\n}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(VALIDATE_SCRIPT), str(vault)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("Extraction outputs valid", result.stdout)


class ClaimTypeValidationTest(unittest.TestCase):
    """Unit tests for validate_claim_record using small JSON fixtures."""

    def _load(self, fixture_name: str) -> Path:
        return _FIXTURES_DIR / fixture_name

    def test_policy_recommendation_accepted(self) -> None:
        path = self._load("policy_recommendation.json")
        errors = _validate_claim_record(path, "policytest2024", "manual:policytest2024", claim_domain="institutional")
        self.assertEqual(errors, [], msg=f"Unexpected errors: {errors}")

    def test_projection_accepted_with_required_fields(self) -> None:
        path = self._load("projection_valid.json")
        errors = _validate_claim_record(path, "projtest2024", "manual:projtest2024", claim_domain="institutional")
        self.assertEqual(errors, [], msg=f"Unexpected errors: {errors}")

    def test_supply_chain_fact_accepted(self) -> None:
        path = self._load("supply_chain_fact.json")
        errors = _validate_claim_record(path, "scftest2024", "manual:scftest2024", claim_domain="sell_side")
        self.assertEqual(errors, [], msg=f"Unexpected errors: {errors}")

    def test_company_thesis_accepted(self) -> None:
        path = self._load("company_thesis.json")
        errors = _validate_claim_record(path, "cthesis2024", "manual:cthesis2024", claim_domain="sell_side")
        self.assertEqual(errors, [], msg=f"Unexpected errors: {errors}")

    def test_missing_required_field_rejected(self) -> None:
        # projection_missing_field.json omits scenario_label; expect a clear error.
        path = self._load("projection_missing_field.json")
        errors = _validate_claim_record(path, "projmissing2024", "manual:projmissing2024", claim_domain="institutional")
        combined = " ".join(errors)
        self.assertTrue(any("scenario_label" in e for e in errors), msg=f"Expected scenario_label error, got: {combined}")

    def test_institutional_rejects_academic_type(self) -> None:
        # claim_domain=institutional + type=theorem → rejection
        path = self._load("institutional_theorem.json")
        errors = _validate_claim_record(path, "instthm2024", "manual:instthm2024", claim_domain="institutional")
        combined = " ".join(errors)
        self.assertTrue(
            any("not allowed in claim_domain" in e for e in errors),
            msg=f"Expected domain-restriction error, got: {combined}",
        )

    def test_hybrid_accepts_union(self) -> None:
        # claim_domain=hybrid allows any type, including theorem.
        path = self._load("hybrid_theorem.json")
        errors = _validate_claim_record(path, "hybridthm2024", "manual:hybridthm2024", claim_domain="hybrid")
        self.assertEqual(errors, [], msg=f"Unexpected errors: {errors}")

    def test_backwards_compat_academic_without_claim_domain(self) -> None:
        # Legacy fixture: no claim_domain passed → defaults to 'academic', theorem is valid.
        path = self._load("legacy_academic.json")
        errors = _validate_claim_record(path, "legacyacad2024", "manual:legacyacad2024")
        self.assertEqual(errors, [], msg=f"Unexpected errors: {errors}")


if __name__ == "__main__":
    unittest.main()
