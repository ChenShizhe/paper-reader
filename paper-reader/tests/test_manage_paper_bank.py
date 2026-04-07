from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from manage_paper_bank import init_paper_bank, manage_paper_bank  # type: ignore


class ManagePaperBankTest(unittest.TestCase):
    def test_manage_paper_bank_moves_files_and_updates_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paper_bank = root / "paper-bank"
            downloads = root / "downloads"
            downloads.mkdir(parents=True, exist_ok=True)

            pdf_path = downloads / "input.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n")
            source_dir = downloads / "src"
            source_dir.mkdir()
            (source_dir / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
            metadata_path = root / "metadata.json"
            metadata_path.write_text(
                json.dumps({"cite_key": "demo2026paper", "title": "Demo Paper"}) + "\n",
                encoding="utf-8",
            )

            report = manage_paper_bank(
                cite_key="demo2026paper",
                canonical_id="arxiv:1234.56789",
                title="Demo Paper",
                paper_bank_root=paper_bank,
                pdf_paths=[str(pdf_path)],
                source_paths=[str(source_dir)],
                supplementary_paths=[],
                metadata_json=str(metadata_path),
                copy_mode=True,
            )

            self.assertEqual(report["cite_key"], "demo2026paper")
            self.assertTrue((paper_bank / "demo2026paper" / "demo2026paper.pdf").exists())
            self.assertTrue((paper_bank / "demo2026paper" / "source" / "src" / "main.tex").exists())

            manifest = json.loads((paper_bank / "_manifest.json").read_text(encoding="utf-8"))
            self.assertIn("demo2026paper", manifest["papers"])
            self.assertEqual(
                manifest["papers"]["demo2026paper"]["canonical_id"],
                "arxiv:1234.56789",
            )

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["bank_path"], str(paper_bank / "demo2026paper"))

    def test_init_paper_bank_creates_raw_and_segments_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paper_bank = root / "paper-bank"

            report = init_paper_bank(cite_key="demo2026paper", paper_bank_root=paper_bank)
            self.assertEqual(report["cite_key"], "demo2026paper")
            self.assertTrue((paper_bank / "demo2026paper" / "raw").is_dir())
            self.assertTrue((paper_bank / "demo2026paper" / "segments").is_dir())

            sentinel = paper_bank / "demo2026paper" / "raw" / "sentinel.txt"
            sentinel.write_text("do not delete\n", encoding="utf-8")

            init_paper_bank(cite_key="demo2026paper", paper_bank_root=paper_bank)
            self.assertTrue(sentinel.exists())

    def test_cli_init_accepts_paper_bank_root_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paper_bank = root / "paper-bank"
            script = SCRIPT_ROOT / "manage_paper_bank.py"

            first = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--cite-key",
                    "demo2026paper",
                    "--init",
                    "--paper-bank-root",
                    str(paper_bank),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertTrue((paper_bank / "demo2026paper" / "raw").is_dir())
            self.assertTrue((paper_bank / "demo2026paper" / "segments").is_dir())

            sentinel = paper_bank / "demo2026paper" / "raw" / "sentinel-cli.txt"
            sentinel.write_text("do not delete\n", encoding="utf-8")

            second = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--cite-key",
                    "demo2026paper",
                    "--init",
                    "--paper-bank-root",
                    str(paper_bank),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertTrue(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
