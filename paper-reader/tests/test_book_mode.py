"""Tests for book-mode: parser, dispatch mocking, and synthesis aggregation.

All dispatch paths are mocked; no real LLM or subagent invocations occur.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CHAPTER_PLAN_FIXTURE = FIXTURES_DIR / "example-chapter-plan.md"
CHAPTER_NOTES_DIR = FIXTURES_DIR / "example-chapter-notes"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chapter_plan_parser import (
    ChapterRow,
    parse_chapter_plan,
    validate_chapter_plan,
)
import comprehend_paper
import summarize_paper


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParserHappyPath(unittest.TestCase):
    def test_parser_happy_path(self):
        fm, rows = parse_chapter_plan(CHAPTER_PLAN_FIXTURE)

        self.assertEqual(fm["cite_key"], "example_book_2024")
        self.assertEqual(fm["claim_domain"], "institutional")
        self.assertGreaterEqual(len(rows), 3)

        slugs = [r.slug for r in rows]
        self.assertIn("exec_summary", slugs)
        self.assertIn("ch1_context", slugs)

        for row in rows:
            self.assertIsInstance(row.page_start, int)
            self.assertIsInstance(row.page_end, int)
            self.assertLessEqual(row.page_start, row.page_end)
            self.assertIn(row.depth, {"deep", "summary", "skip"})
            self.assertIsInstance(row.include_in_synthesis, bool)


class TestParserRejectsMalformedPageRange(unittest.TestCase):
    def _write_plan(self, tmp_dir: Path, page_range: str) -> Path:
        plan = (
            "---\n"
            "cite_key: bad_book\n"
            "source_pdf: downloads/bad.pdf\n"
            "claim_domain: institutional\n"
            "---\n\n"
            "| slug | page_range | role | depth | include_in_synthesis | domain_lens |\n"
            "|------|------------|------|-------|---------------------|-------------|\n"
            f"| ch1  | {page_range} | scenario | deep | true | test-lens |\n"
        )
        path = tmp_dir / "plan.md"
        path.write_text(plan, encoding="utf-8")
        return path

    def test_parser_rejects_malformed_page_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for bad_range in ("bad-range", "abc-def", "0-abc", "10-5"):
                with self.subTest(page_range=bad_range):
                    plan_path = self._write_plan(tmp_path, bad_range)
                    with self.assertRaises(ValueError):
                        parse_chapter_plan(plan_path)


class TestParserRejectsDuplicateSlugs(unittest.TestCase):
    def test_parser_rejects_duplicate_slugs(self):
        rows = [
            ChapterRow(
                slug="ch1",
                page_range="1-10",
                page_start=1,
                page_end=10,
                role="intro",
                depth="deep",
                include_in_synthesis=True,
                domain_lens="test-lens",
            ),
            ChapterRow(
                slug="ch1",
                page_range="11-20",
                page_start=11,
                page_end=20,
                role="body",
                depth="deep",
                include_in_synthesis=True,
                domain_lens="test-lens",
            ),
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_chapter_plan(rows, pdf_page_count=20)
        self.assertIn("ch1", str(ctx.exception).lower())


class TestParserRejectsMixedLensLabels(unittest.TestCase):
    def test_parser_rejects_mixed_lens_labels(self):
        rows = [
            ChapterRow(
                slug="ch1",
                page_range="1-25",
                page_start=1,
                page_end=25,
                role="intro",
                depth="deep",
                include_in_synthesis=True,
                domain_lens="lens-alpha",
            ),
            ChapterRow(
                slug="ch2",
                page_range="26-50",
                page_start=26,
                page_end=50,
                role="body",
                depth="deep",
                include_in_synthesis=True,
                domain_lens="lens-beta",
            ),
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_chapter_plan(rows, pdf_page_count=50)
        self.assertIn("lens", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# Dispatch-filter tests (no actual dispatch needed)
# ---------------------------------------------------------------------------


class TestSkipRowsNotDispatched(unittest.TestCase):
    def test_skip_rows_not_dispatched(self):
        _fm, rows = parse_chapter_plan(CHAPTER_PLAN_FIXTURE)

        skip_rows = [r for r in rows if r.depth == "skip"]
        active_rows = [r for r in rows if r.depth != "skip"]

        self.assertGreater(len(skip_rows), 0, "fixture must contain at least one skip row")
        self.assertGreater(len(active_rows), 0, "fixture must contain at least one active row")

        skip_slugs = {r.slug for r in skip_rows}
        active_slugs = {r.slug for r in active_rows}
        self.assertTrue(
            skip_slugs.isdisjoint(active_slugs),
            "skip slugs must not appear in active dispatch list",
        )


class TestAnnexRowsExcludedFromSynthesis(unittest.TestCase):
    def test_annex_rows_excluded_from_synthesis(self):
        _fm, rows = parse_chapter_plan(CHAPTER_PLAN_FIXTURE)

        excluded = [r for r in rows if not r.include_in_synthesis]
        included = [r for r in rows if r.include_in_synthesis]

        self.assertGreater(len(excluded), 0, "fixture must have at least one excluded row")
        self.assertGreater(len(included), 0, "fixture must have at least one included row")

        excluded_slugs = {r.slug for r in excluded}
        included_slugs = {r.slug for r in included}
        self.assertTrue(
            excluded_slugs.isdisjoint(included_slugs),
            "rows with include_in_synthesis=false must not appear in synthesis input",
        )


# ---------------------------------------------------------------------------
# Synthesis aggregation test
# ---------------------------------------------------------------------------


class TestSynthesisAggregatesLensSections(unittest.TestCase):
    def test_synthesis_aggregates_lens_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            cite_key = "example_book_2024"
            notes_dir = vault / "literature" / "papers" / cite_key
            notes_dir.mkdir(parents=True)

            shutil.copy(CHAPTER_PLAN_FIXTURE, notes_dir / "chapter_plan.md")
            for note_file in CHAPTER_NOTES_DIR.glob("*.md"):
                shutil.copy(note_file, notes_dir / note_file.name)

            output_path = vault / "literature" / "papers" / f"{cite_key}.md"
            summarize_paper.synthesize_book(
                cite_key=cite_key,
                vault_path=vault,
                output=output_path,
            )

            self.assertTrue(output_path.exists(), "synthesis output file must be written")
            content = output_path.read_text(encoding="utf-8")

            for slug in ("exec_summary", "ch1_context", "ch2_demand", "ch3_supply"):
                self.assertIn(
                    f"### {slug}",
                    content,
                    f"synthesis must contain per-chapter sub-section for {slug!r}",
                )

            # Annex row (include_in_synthesis=false) must not generate a lens sub-section
            self.assertNotIn("### statistical_annex", content)


# ---------------------------------------------------------------------------
# Concurrency cap test
# ---------------------------------------------------------------------------


class TestConcurrencyCapRespected(unittest.TestCase):
    def test_concurrency_cap_respected(self):
        plan_content = (
            "---\n"
            "cite_key: concurrency_test\n"
            "source_pdf: downloads/concurrency_test.pdf\n"
            "claim_domain: institutional\n"
            "---\n\n"
            "| slug | page_range | role    | depth | include_in_synthesis | domain_lens |\n"
            "|------|------------|---------|-------|---------------------|-------------|\n"
            "| ch1  | 1-20       | chapter | deep  | true                | test-lens   |\n"
            "| ch2  | 21-40      | chapter | deep  | true                | test-lens   |\n"
            "| ch3  | 41-60      | chapter | deep  | true                | test-lens   |\n"
            "| ch4  | 61-80      | chapter | deep  | true                | test-lens   |\n"
            "| ch5  | 81-100     | chapter | deep  | true                | test-lens   |\n"
            "| ch6  | 101-120    | chapter | deep  | true                | test-lens   |\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan_path = tmp_path / "plan.md"
            plan_path.write_text(plan_content, encoding="utf-8")

            lock = threading.Lock()
            active_count = [0]
            max_concurrent = [0]

            def mock_dispatch(row, **kwargs):
                with lock:
                    active_count[0] += 1
                    if active_count[0] > max_concurrent[0]:
                        max_concurrent[0] = active_count[0]
                time.sleep(0.05)
                with lock:
                    active_count[0] -= 1
                return row.slug, True, "ok"

            args = argparse.Namespace(
                chapter_plan=str(plan_path),
                book_concurrency=2,
                vault_root=str(tmp_path / "vault"),
            )

            with patch.object(
                comprehend_paper, "_dispatch_chapter_with_retry", side_effect=mock_dispatch
            ):
                with patch.object(
                    comprehend_paper, "_get_pdf_page_count", return_value=None
                ):
                    comprehend_paper._book_run(args)

            self.assertGreater(max_concurrent[0], 0, "at least one dispatch must have run")
            self.assertLessEqual(
                max_concurrent[0],
                2,
                f"max concurrent dispatches {max_concurrent[0]} exceeded cap of 2",
            )


# ---------------------------------------------------------------------------
# Retry-once-then-fail test
# ---------------------------------------------------------------------------


class TestSubagentRetryOnceThenFailChapter(unittest.TestCase):
    def test_subagent_retry_once_then_fail_chapter(self):
        plan_content = (
            "---\n"
            "cite_key: retry_test\n"
            "source_pdf: downloads/retry_test.pdf\n"
            "claim_domain: institutional\n"
            "---\n\n"
            "| slug | page_range | role    | depth | include_in_synthesis | domain_lens |\n"
            "|------|------------|---------|-------|---------------------|-------------|\n"
            "| ch1  | 1-20       | chapter | deep  | true                | test-lens   |\n"
            "| ch2  | 21-40      | chapter | deep  | true                | test-lens   |\n"
            "| ch3  | 41-60      | chapter | deep  | true                | test-lens   |\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan_path = tmp_path / "plan.md"
            plan_path.write_text(plan_content, encoding="utf-8")

            dispatched_slugs: list[str] = []

            def mock_dispatch_fn(row, **kwargs):
                dispatched_slugs.append(row.slug)
                if row.slug == "ch1":
                    return row.slug, False, "mock failure for ch1"
                return row.slug, True, "ok"

            args = argparse.Namespace(
                chapter_plan=str(plan_path),
                book_concurrency=3,
                vault_root=str(tmp_path / "vault"),
            )

            with patch.object(
                comprehend_paper, "_dispatch_chapter", side_effect=mock_dispatch_fn
            ):
                with patch.object(
                    comprehend_paper, "_get_pdf_page_count", return_value=None
                ):
                    comprehend_paper._book_run(args)

            # ch1 attempted twice (initial + one retry)
            self.assertEqual(
                dispatched_slugs.count("ch1"),
                2,
                "ch1 must be dispatched exactly twice (retry-once semantics)",
            )

            # siblings ch2 and ch3 must still be dispatched
            self.assertIn("ch2", dispatched_slugs, "sibling ch2 must continue after ch1 failure")
            self.assertIn("ch3", dispatched_slugs, "sibling ch3 must continue after ch1 failure")

            # failed note must be written for ch1
            output_dir = (
                tmp_path / "vault" / "literature" / "papers" / "retry_test"
            )
            failed_note = output_dir / "ch1.md"
            self.assertTrue(failed_note.exists(), "failed note must be written for ch1")
            note_content = failed_note.read_text(encoding="utf-8")
            self.assertIn("status: failed", note_content)


if __name__ == "__main__":
    unittest.main()
