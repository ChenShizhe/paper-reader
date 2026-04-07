from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "paper-reader"
PREFLIGHT_SCRIPT = SKILL_ROOT / "scripts" / "preflight_extraction.py"

_ZOTERO_MCP_ROOT = Path(
    os.environ.get("ZOTERO_MCP_ROOT", str(Path.home() / "Documents" / "MCPs" / "zotero-mcp"))
)
_HAS_ZOTERO_MCP = _ZOTERO_MCP_ROOT.is_dir()


class ExtractionPreflightTest(unittest.TestCase):
    def test_preflight_is_ready_with_expected_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "preflight_report.json"
            result = subprocess.run(
                [sys.executable, str(PREFLIGHT_SCRIPT), "--output", str(output_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(report["module"], "paper-reader")
        self.assertIn("paper-bank-storage", report["legal_paths"])
        self.assertIn("zotero-sync", report["legal_paths"])

        tools = {check["tool"]: check for check in report["checks"]}
        self.assertEqual(tools["python3"]["status"], "found")
        self.assertEqual(tools["manage_paper_bank.py"]["status"], "found")
        self.assertEqual(tools["sync_zotero.py"]["status"], "found")

    @unittest.skipUnless(_HAS_ZOTERO_MCP, "Zotero MCP directory not present")
    def test_zotero_mcp_root_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "preflight_report.json"
            result = subprocess.run(
                [sys.executable, str(PREFLIGHT_SCRIPT), "--output", str(output_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            report = json.loads(output_path.read_text(encoding="utf-8"))

        tools = {check["tool"]: check for check in report["checks"]}
        self.assertEqual(tools["zotero-mcp-root"]["status"], "found")
        self.assertEqual(tools["paper-bank-root"]["status"], "found")
        self.assertEqual(report["overall"], "ready")


if __name__ == "__main__":
    unittest.main()
