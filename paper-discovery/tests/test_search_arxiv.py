from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_SCRIPT = REPO_ROOT / "skills" / "paper-discovery" / "scripts" / "search_arxiv.py"
BUILD_SCRIPT = REPO_ROOT / "skills" / "paper-discovery" / "scripts" / "build_manifest.py"
VALIDATE_SCRIPT = REPO_ROOT / "skills" / "paper-discovery" / "scripts" / "validate_manifest.py"


def load_search_module():
    spec = importlib.util.spec_from_file_location("lit_review_discovery_search_arxiv", SEARCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load search_arxiv.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title>ArXiv Query Results</title>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <updated>2024-01-21T00:00:00Z</updated>
    <published>2024-01-20T00:00:00Z</published>
    <title> Neural methods for time series forecasting </title>
    <summary> A survey of neural methods for time series forecasting. </summary>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Johnson</name></author>
    <author><name>Carol Williams</name></author>
    <link rel="alternate" type="text/html" href="http://arxiv.org/abs/2401.12345v1" />
    <link title="pdf" rel="related" type="application/pdf" href="http://arxiv.org/pdf/2401.12345v1" />
    <arxiv:doi>10.1234/5678-9012/24/3/0451</arxiv:doi>
    <arxiv:primary_category term="cs.LG" scheme="http://arxiv.org/schemas/atom" />
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom" />
    <category term="stat.ML" scheme="http://arxiv.org/schemas/atom" />
  </entry>
</feed>
"""


class SearchArxivTest(unittest.TestCase):
    def test_parse_feed_normalizes_expected_fields(self) -> None:
        module = load_search_module()
        records = module.parse_feed(SAMPLE_FEED, source_query='all:"neural methods"')
        self.assertEqual(len(records), 1)

        record = records[0]
        self.assertEqual(record["id"], "http://arxiv.org/abs/2401.12345v1")
        self.assertEqual(record["title"], "Neural methods for time series forecasting")
        self.assertEqual(record["summary"], "A survey of neural methods for time series forecasting.")
        self.assertEqual(
            record["authors"],
            ["Alice Zhang", "Bob Johnson", "Carol Williams"],
        )
        self.assertEqual(record["published"], "2024-01-20T00:00:00Z")
        self.assertEqual(record["updated"], "2024-01-21T00:00:00Z")
        self.assertEqual(record["primary_category"], "cs.LG")
        self.assertEqual(record["categories"], ["cs.LG", "stat.ML"])
        self.assertEqual(record["doi"], "10.1234/5678-9012/24/3/0451")
        self.assertEqual(record["pdf_url"], "http://arxiv.org/pdf/2401.12345v1")
        self.assertEqual(record["source_query"], 'all:"neural methods"')

    def test_query_arxiv_respects_delay_and_logs_requests(self) -> None:
        module = load_search_module()
        with mock.patch.object(module, "fetch_url", return_value=SAMPLE_FEED) as fetch_mock:
            with mock.patch.object(module.time, "sleep") as sleep_mock:
                records, run_log = module.query_arxiv(
                    queries=['all:"neural methods"', 'all:"deep time series"'],
                    start=0,
                    max_results=5,
                    sort_by="relevance",
                    sort_order="descending",
                    delay_seconds=0.25,
                    timeout=10.0,
                    user_agent="test-agent",
                    base_url="https://example.test/api/query",
                )

        self.assertEqual(len(records), 2)
        self.assertEqual(fetch_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.25)
        self.assertEqual(run_log["query_count"], 2)
        self.assertEqual(run_log["entry_count"], 2)
        self.assertEqual(len(run_log["requests"]), 2)
        self.assertIn("search_query=all%3A%22neural+methods%22", run_log["requests"][0]["request_url"])
        self.assertIn(
            "search_query=all%3A%22deep+time+series%22",
            run_log["requests"][1]["request_url"],
        )

    def test_search_output_is_compatible_with_build_manifest(self) -> None:
        module = load_search_module()
        records = module.parse_feed(SAMPLE_FEED, source_query='all:"neural methods"')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            arxiv_path = tmpdir_path / "arxiv_results.json"
            manifest_path = tmpdir_path / "paper_manifest.json"
            arxiv_path.write_text(json.dumps(records), encoding="utf-8")

            build_result = subprocess.run(
                [
                    sys.executable,
                    str(BUILD_SCRIPT),
                    "--topic",
                    "neural methods for time series",
                    "--arxiv-results",
                    str(arxiv_path),
                    "--seed-papers",
                    "arxiv:2401.12345",
                    "--keywords",
                    "neural methods,time series,forecasting",
                    "--date-start",
                    "2020",
                    "--date-end",
                    "2026",
                    "--max-papers",
                    "10",
                    "--output",
                    str(manifest_path),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(build_result.returncode, 0, msg=build_result.stdout + build_result.stderr)

            validate_result = subprocess.run(
                [sys.executable, str(VALIDATE_SCRIPT), str(manifest_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(validate_result.returncode, 0, msg=validate_result.stdout + validate_result.stderr)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["search_sources"], ["arxiv"])
        self.assertEqual(len(manifest["entries"]), 1)
        self.assertEqual(manifest["entries"][0]["canonical_id"], "arxiv:2401.12345")
        self.assertEqual(manifest["entries"][0]["doi"], "10.1234/5678-9012/24/3/0451")


if __name__ == "__main__":
    unittest.main()
