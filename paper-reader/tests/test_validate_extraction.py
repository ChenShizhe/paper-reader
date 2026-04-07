from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "paper-reader"
VALIDATE_SCRIPT = SKILL_ROOT / "scripts" / "validate_extraction.py"


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


if __name__ == "__main__":
    unittest.main()
