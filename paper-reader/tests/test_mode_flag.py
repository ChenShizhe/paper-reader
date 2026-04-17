"""End-to-end tests for the --mode flag plumbing across run_pipeline and comprehend_paper."""
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_run_pipeline():
    spec = importlib.util.spec_from_file_location("run_pipeline", SCRIPTS_DIR / "run_pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_rp = _load_run_pipeline()

_BASE_ARGV = ["run_pipeline.py", "--cite-key", "test2024x", "--source-path", "/tmp/src"]


class ModeFlagTest(unittest.TestCase):

    def test_mode_paper_runs_as_today(self):
        """--mode=paper and no --mode produce identical args.mode == 'paper'."""
        with patch("sys.argv", _BASE_ARGV):
            args_default = _rp.parse_args()

        with patch("sys.argv", _BASE_ARGV + ["--mode", "paper"]):
            args_explicit = _rp.parse_args()

        self.assertEqual(args_default.mode, "paper")
        self.assertEqual(args_explicit.mode, "paper")

    def test_mode_book_requires_chapter_plan(self):
        """--mode=book without --chapter-plan exits with argparse error (code 2)."""
        argv = _BASE_ARGV + ["--mode", "book"]
        with patch("sys.argv", argv), patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as ctx:
                _rp.parse_args()

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("chapter-plan", mock_err.getvalue())

    def test_mode_book_missing_plan_exits_nonzero(self):
        """--mode=book with a nonexistent chapter plan exits non-zero with an error message."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "comprehend_paper.py"),
                "--cite-key", "test2024x",
                "--mode", "book",
                "--chapter-plan", "/tmp/nonexistent_chapter_plan_xyzabc.md",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("chapter plan", combined.lower())

    def test_mode_chain_map_raises_notimplemented(self):
        """--mode=chain_map raises NotImplementedError with m4 pointer."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "comprehend_paper.py"),
                "--cite-key", "test2024x",
                "--mode", "chain_map",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("NotImplementedError", combined)
        self.assertIn("m4", combined)


if __name__ == "__main__":
    unittest.main()
