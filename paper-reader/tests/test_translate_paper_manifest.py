from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from translate_paper import _finalize_translation_artifacts  # type: ignore


class TranslatePaperManifestTest(unittest.TestCase):
    def test_finalize_writes_manifest_and_empty_warnings_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paper_bank_dir = Path(tmpdir) / "paper-bank" / "demo2026paper"
            paper_bank_dir.mkdir(parents=True, exist_ok=True)

            translated = paper_bank_dir / "translated_full.md"
            translated.write_text(
                "\n".join(
                    [
                        "---",
                        "cite_key: demo2026paper",
                        "---",
                        "",
                        "## Title",
                        "",
                        "## Section One",
                        "",
                        "<!-- eq:one (1) -->",
                        "$$",
                        "a=b",
                        "$$",
                        "",
                        "<!-- eq:two (2) -->",
                        "$$",
                        "c=d",
                        "$$",
                        "",
                        "<!-- eq:three (3) -->",
                        "$$",
                        "e=f",
                        "$$",
                        "",
                        "<!-- eq:four (4) -->",
                        "$$",
                        "g=h",
                        "$$",
                        "",
                        "<!-- eq:five (5) -->",
                        "$$",
                        "i=j",
                        "$$",
                        "",
                        "## Section Two",
                        "",
                        "Some words here.",
                        "",
                        "## Section Three",
                        "",
                        "More words here.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            (paper_bank_dir / "_theorem_index.json").write_text(
                json.dumps({"cite_key": "demo2026paper", "theorems": [{"label": "Theorem 1"}]}) + "\n",
                encoding="utf-8",
            )

            report = _finalize_translation_artifacts(
                cite_key="demo2026paper",
                paper_bank_dir=paper_bank_dir,
                translated_markdown_path=translated,
                source_file="main.tex",
                macro_warnings_text="",
            )

            manifest_path = paper_bank_dir / "_translation_manifest.json"
            warnings_path = paper_bank_dir / "_translation_warnings.log"

            self.assertTrue(manifest_path.exists())
            self.assertTrue(warnings_path.exists())
            self.assertEqual(warnings_path.read_text(encoding="utf-8"), "")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["tool"], "pandoc")
            self.assertEqual(manifest["source_file"], "main.tex")
            self.assertIn("timestamp", manifest)
            self.assertGreaterEqual(manifest["equation_count"], 5)
            self.assertGreaterEqual(manifest["section_count"], 3)
            self.assertGreater(manifest["word_count"], 0)
            self.assertEqual(manifest["validation_status"], "passed")

            self.assertEqual(report["validation_status"], "passed")


if __name__ == "__main__":
    unittest.main()

