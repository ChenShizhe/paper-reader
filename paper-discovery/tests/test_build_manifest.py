from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "paper-discovery"
FIXTURE_ROOT = SKILL_ROOT / "tests" / "fixtures"
BUILD_SCRIPT = SKILL_ROOT / "scripts" / "build_manifest.py"
VALIDATE_SCRIPT = SKILL_ROOT / "scripts" / "validate_manifest.py"


class BuildManifestTest(unittest.TestCase):
    def build_manifest(self) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "paper_manifest.json"
            subprocess.run(
                [
                    sys.executable,
                    str(BUILD_SCRIPT),
                    "--topic",
                    "neural methods for time series",
                    "--zotero-results",
                    str(FIXTURE_ROOT / "zotero_results.json"),
                    "--arxiv-results",
                    str(FIXTURE_ROOT / "arxiv_results.json"),
                    "--openalex-results",
                    str(FIXTURE_ROOT / "openalex_results.json"),
                    "--web-results",
                    str(FIXTURE_ROOT / "web_results.json"),
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
                    str(output_path),
                ],
                check=True,
                cwd=REPO_ROOT,
            )
            subprocess.run(
                [sys.executable, str(VALIDATE_SCRIPT), str(output_path)],
                check=True,
                cwd=REPO_ROOT,
            )
            return json.loads(output_path.read_text(encoding="utf-8"))

    def test_manifest_is_schema_valid_and_deduplicated(self) -> None:
        manifest = self.build_manifest()

        self.assertEqual(manifest["schema_version"], "1")
        self.assertEqual(manifest["search_sources"], ["zotero", "arxiv", "openalex", "web"])
        self.assertEqual(len(manifest["entries"]), 4)

        canonical_ids = {entry["canonical_id"] for entry in manifest["entries"]}
        self.assertIn("arxiv:2401.12345", canonical_ids)
        self.assertIn("arxiv:2403.67890", canonical_ids)
        self.assertIn("doi:10.5678/example.2024.002", canonical_ids)
        self.assertTrue(any(canonical_id.startswith("manual:") for canonical_id in canonical_ids))

        zhang = next(entry for entry in manifest["entries"] if entry["canonical_id"] == "arxiv:2401.12345")
        self.assertEqual(zhang["openalex_id"], "W7700005678")
        self.assertEqual(zhang["doi"], "10.1234/5678-9012/24/3/0451")
        self.assertEqual(zhang["cite_key"], "zhang2024methods_zot")
        self.assertEqual(zhang["seed_distance"], 0)
        self.assertEqual(zhang["search_source"], "zotero")
        self.assertEqual(manifest["entries"][0]["canonical_id"], "arxiv:2401.12345")

    def test_cite_keys_are_deterministic_and_collision_free(self) -> None:
        manifest = self.build_manifest()
        cite_keys = [entry["cite_key"] for entry in manifest["entries"]]
        self.assertEqual(len(cite_keys), len(set(cite_keys)))
        self.assertIn("zhang2024methods_zot", cite_keys)
        self.assertIn("zhang2024methods", cite_keys)

        manual_entry = next(entry for entry in manifest["entries"] if entry["canonical_id"].startswith("manual:"))
        self.assertEqual(manual_entry["cite_key"], "morgan2024attention")
        self.assertIsNone(manual_entry["arxiv_id"])
        self.assertIsNone(manual_entry["doi"])
        self.assertIsNone(manual_entry["openalex_id"])

    def test_build_manifest_requires_at_least_one_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "paper_manifest.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(BUILD_SCRIPT),
                    "--topic",
                    "neural methods for time series",
                    "--output",
                    str(output_path),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("At least one source result file is required", result.stdout)

    def test_validator_rejects_duplicate_strong_identifiers(self) -> None:
        invalid_manifest = {
            "schema_version": "1",
            "topic": "invalid fixture",
            "created_at": "2026-02-27T05:00:00Z",
            "search_sources": ["arxiv"],
            "entries": [
                {
                    "canonical_id": "arxiv:2401.12345",
                    "cite_key": "zhang2024methods",
                    "arxiv_id": "2401.12345",
                    "openalex_id": None,
                    "doi": None,
                    "pmid": None,
                    "title": "Paper A",
                    "authors": ["Author One"],
                    "year": 2024,
                    "abstract": "",
                    "pdf_url": "https://arxiv.org/pdf/2401.12345.pdf",
                    "categories": ["cs.LG"],
                    "relevance_score": 0.9,
                    "seed_distance": 0,
                    "citation_count": 10,
                    "search_source": "arxiv",
                },
                {
                    "canonical_id": "manual:deadbeef",
                    "cite_key": "authortwo2024paper",
                    "arxiv_id": "2401.12345",
                    "openalex_id": None,
                    "doi": None,
                    "pmid": None,
                    "title": "Paper B",
                    "authors": ["Author Two"],
                    "year": 2024,
                    "abstract": "",
                    "pdf_url": None,
                    "categories": ["cs.LG"],
                    "relevance_score": 0.5,
                    "seed_distance": None,
                    "citation_count": 0,
                    "search_source": "arxiv",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "invalid_manifest.json"
            manifest_path.write_text(json.dumps(invalid_manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATE_SCRIPT), str(manifest_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicates a prior entry", result.stdout)
        self.assertIn("manual canonical_id cannot coexist with stronger identifiers", result.stdout)

    def test_pubmed_inputs_merge_on_pmid_and_do_not_fake_arxiv_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "paper_manifest.json"
            subprocess.run(
                [
                    sys.executable,
                    str(BUILD_SCRIPT),
                    "--topic",
                    "cortical markers for cognitive decline",
                    "--openalex-results",
                    str(FIXTURE_ROOT / "openalex_pubmed_bridge.json"),
                    "--pubmed-results",
                    str(FIXTURE_ROOT / "pubmed_results.json"),
                    "--seed-papers",
                    "pmid:40221188",
                    "--keywords",
                    "cognitive decline,cortical markers,triage",
                    "--date-start",
                    "2020",
                    "--date-end",
                    "2026",
                    "--max-papers",
                    "10",
                    "--output",
                    str(output_path),
                ],
                check=True,
                cwd=REPO_ROOT,
            )
            subprocess.run(
                [sys.executable, str(VALIDATE_SCRIPT), str(output_path)],
                check=True,
                cwd=REPO_ROOT,
            )
            manifest = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["search_sources"], ["openalex", "pubmed"])
        self.assertEqual(len(manifest["entries"]), 2)

        merged_entry = next(entry for entry in manifest["entries"] if entry["canonical_id"] == "openalex:W3300007777")
        self.assertEqual(merged_entry["pmid"], "40221188")
        self.assertEqual(merged_entry["seed_distance"], 0)

        doi_entry = next(entry for entry in manifest["entries"] if entry["canonical_id"] == "doi:10.5678/example.2024.004")
        self.assertIsNone(doi_entry["arxiv_id"])
        self.assertEqual(doi_entry["pmid"], "40330299")
        self.assertEqual(doi_entry["seed_distance"], 1)

    def test_validator_accepts_multi_segment_cite_keys(self) -> None:
        valid_manifest = {
            "schema_version": "1",
            "topic": "valid fixture",
            "created_at": "2026-02-27T05:00:00Z",
            "search_sources": ["pubmed"],
            "entries": [
                {
                    "canonical_id": "manual:deadbeef",
                    "cite_key": "kim2024paper_m4022_alt",
                    "arxiv_id": None,
                    "openalex_id": None,
                    "doi": None,
                    "pmid": "40221188",
                    "title": "Paper C",
                    "authors": ["Author Three"],
                    "year": 2024,
                    "abstract": "",
                    "pdf_url": None,
                    "categories": ["Sleep Medicine"],
                    "relevance_score": 0.5,
                    "seed_distance": None,
                    "citation_count": 0,
                    "search_source": "pubmed",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "valid_manifest.json"
            manifest_path.write_text(json.dumps(valid_manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATE_SCRIPT), str(manifest_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
