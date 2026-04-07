from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "paper-reader"
SCRIPT = SKILL_ROOT / "scripts" / "generate_bibtex.py"


class GenerateBibtexTest(unittest.TestCase):
    def test_generate_bibtex_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "paper_manifest.json"
            output_path = root / "refs.bib"
            manifest_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "title": "Demo Paper",
                                "authors": ["Jane Doe"],
                                "year": 2026,
                                "arxiv_id": "1234.56789",
                            }
                        ]
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(manifest_path), "--output", str(output_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            bib = output_path.read_text(encoding="utf-8")
            self.assertIn("@article{doe2026demo", bib)
            self.assertIn("archivePrefix", bib)


if __name__ == "__main__":
    unittest.main()
