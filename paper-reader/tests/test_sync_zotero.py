from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from sync_zotero import sync_note_with_zotero  # type: ignore


class FakeZotero:
    def __init__(self, existing_item: dict | None = None) -> None:
        self._existing_item = existing_item
        self.updated = 0
        self.created = 0

    def items(self, q: str, limit: int = 25) -> list[dict]:
        if self._existing_item is None:
            return []
        if q:
            return [self._existing_item]
        return []

    def update_item(self, item: dict) -> bool:
        self.updated += 1
        self._existing_item = item
        return True

    def create_items(self, payload: list[dict]) -> dict:
        self.created += 1
        return {"success": {"NEW1234": payload[0]}}

    def item(self, item_key: str) -> dict:
        if self._existing_item is not None:
            return self._existing_item
        return {"data": {"key": item_key}}


class SyncZoteroTest(unittest.TestCase):
    def test_sync_updates_note_cite_key_when_zotero_has_different_key(self) -> None:
        note = """---
schema_version: "1"
canonical_id: "arxiv:1234.56789"
cite_key: "old2026key"
title: "Demo Paper"
authors:
  - "Jane Doe"
year: 2026
doi: null
arxiv_id: "1234.56789"
bank_path: "/tmp/paper-bank/old2026key"
---

<!-- AUTO-GENERATED:BEGIN -->
## Abstract
Demo abstract text.
<!-- AUTO-GENERATED:END -->
"""

        existing_item = {
            "data": {
                "key": "ABCD1234",
                "title": "Demo Paper",
                "extra": "Citation Key: zotero2026paper",
                "archiveLocation": "1234.56789",
                "date": "2026",
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = Path(tmpdir) / "old2026key.md"
            note_path.write_text(note, encoding="utf-8")
            zot = FakeZotero(existing_item=existing_item)

            report = sync_note_with_zotero(
                note_path=note_path,
                zot=zot,
                collection_keys=[],
                dry_run=True,
            )

            updated_note = note_path.read_text(encoding="utf-8")

        self.assertEqual(report["action"], "update")
        self.assertEqual(report["old_cite_key"], "old2026key")
        self.assertEqual(report["final_cite_key"], "zotero2026paper")
        self.assertTrue(report["note_updated"])
        self.assertIn('cite_key: "zotero2026paper"', updated_note)


if __name__ == "__main__":
    unittest.main()
