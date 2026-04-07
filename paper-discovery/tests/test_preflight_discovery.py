from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "paper-discovery"
PREFLIGHT_SCRIPT = SKILL_ROOT / "scripts" / "preflight_discovery.py"

_ZOTERO_MCP_ROOT = Path(
    os.environ.get("ZOTERO_MCP_ROOT", str(Path.home() / "Documents" / "MCPs" / "zotero-mcp"))
)
_HAS_ZOTERO_MCP = _ZOTERO_MCP_ROOT.is_dir()


class DiscoveryPreflightTest(unittest.TestCase):
    def test_preflight_is_ready_without_external_arxiv_cli(self) -> None:
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

        self.assertEqual(report["module"], "discovery")
        self.assertIn("manifest-from-saved-artifacts", report["legal_paths"])
        self.assertIn("stdlib-arxiv-search", report["legal_paths"])
        self.assertEqual(report["blocked_paths"], [])

        tools = {check["tool"]: check for check in report["checks"]}
        self.assertIn("python3", tools)
        self.assertIn("search_arxiv.py", tools)
        self.assertIn("search_zotero.py", tools)
        self.assertNotIn("arxiv-cli", tools)
        self.assertNotIn("curl", tools)
        self.assertEqual(tools["python3"]["status"], "found")
        self.assertEqual(tools["search_arxiv.py"]["status"], "found")
        self.assertEqual(tools["search_zotero.py"]["status"], "found")

    @unittest.skipUnless(_HAS_ZOTERO_MCP, "Zotero MCP directory not present")
    def test_zotero_mcp_and_library_search(self) -> None:
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

        self.assertEqual(report["overall"], "ready")
        self.assertIn("zotero-library-search", report["legal_paths"])

        tools = {check["tool"]: check for check in report["checks"]}
        self.assertIn("zotero-mcp-root", tools)


if __name__ == "__main__":
    unittest.main()
