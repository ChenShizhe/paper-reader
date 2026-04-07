"""[DEPRECATED - v2] Legacy v1 renderer tests.

Retained for backward compatibility but is no longer invoked by the v2 orchestration.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from render_note_from_claims import render_note  # type: ignore


class RenderNoteTest(unittest.TestCase):
    def test_rendered_frontmatter_contains_bank_path(self) -> None:
        metadata = {
            "schema_version": "1",
            "canonical_id": "arxiv:1234.56789",
            "cite_key": "demo2026paper",
            "title": "Demo Paper",
            "authors": ["Jane Doe"],
            "year": 2026,
            "source_type": "arxiv-latex",
            "source_path": "downloads/arxiv/1234.56789",
            "bank_path": "/tmp/paper-bank/demo2026paper",
            "source_parse_status": "full",
            "bibliography_status": "full",
            "content_status": "full",
            "extraction_confidence": "high",
            "validation_status": "validated",
        }
        claims = {
            "schema_version": "1",
            "cite_key": "demo2026paper",
            "canonical_id": "arxiv:1234.56789",
            "content_status": "full",
            "extraction_confidence": "high",
            "claims": [],
        }

        rendered, _ = render_note(metadata, claims)
        self.assertIn('bank_path: "/tmp/paper-bank/demo2026paper"', rendered)
        self.assertIn("<!-- AUTO-GENERATED:BEGIN -->", rendered)


if __name__ == "__main__":
    unittest.main()
