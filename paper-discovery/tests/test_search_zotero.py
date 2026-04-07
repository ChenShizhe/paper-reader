from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
SEARCH_SCRIPT = REPO_ROOT / "skills" / "paper-discovery" / "scripts" / "search_zotero.py"


def load_search_module():
    spec = importlib.util.spec_from_file_location("paper_discovery_search_zotero", SEARCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load search_zotero.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SearchZoteroTest(unittest.TestCase):
    def test_to_record_extracts_cite_key_and_metadata(self) -> None:
        module = load_search_module()
        raw_item = {
            "data": {
                "key": "ABCD1234",
                "title": "AI memories for agent systems",
                "creators": [{"firstName": "Ada", "lastName": "Lovelace", "creatorType": "author"}],
                "date": "2025-11-08",
                "abstractNote": "Memory-augmented workflows for multi-agent systems.",
                "DOI": "10.1234/ai.2025.42",
                "archiveLocation": "arXiv:2501.01234",
                "extra": "Citation Key: Lovelace2025Memory",
                "tags": [{"tag": "AI"}, {"tag": "Memory"}],
            }
        }

        record = module._to_record(raw_item, query="ai memories", mode="keyword")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["item_key"], "ABCD1234")
        self.assertEqual(record["doi"], "10.1234/ai.2025.42")
        self.assertEqual(record["arxiv_id"], "2501.01234")
        self.assertEqual(record["cite_key"], "lovelace2025memory")
        self.assertEqual(record["authors"], ["Ada Lovelace"])
        self.assertEqual(record["categories"], ["AI", "Memory"])

    def test_query_zotero_auto_falls_back_to_keyword(self) -> None:
        module = load_search_module()

        class FakeZoteroClient:
            def items(self, **kwargs):
                _ = kwargs
                return [
                    {
                        "data": {
                            "key": "ZXCV1234",
                            "title": "Memory synthesis",
                            "creators": [{"name": "Jane Doe", "creatorType": "author"}],
                            "date": "2024",
                            "extra": "Citation Key: Doe2024Memory",
                        }
                    }
                ]

        with mock.patch.object(
            module,
            "_load_zotero_mcp",
            return_value={"mode": "python", "get_zotero_client": lambda: FakeZoteroClient(), "src_root": "/tmp"},
        ):
            with mock.patch.object(module, "_semantic_search", side_effect=RuntimeError("semantic unavailable")):
                records, run_log = module.query_zotero(
                    queries=["memory"],
                    max_results=5,
                    zotero_mcp_root=Path("/tmp/nonexistent"),
                    mode="auto",
                )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["item_key"], "ZXCV1234")
        self.assertEqual(run_log["requests"][0]["mode"], "keyword")
        self.assertIn("semantic-search-failed", run_log["requests"][0]["error"])


if __name__ == "__main__":
    unittest.main()
