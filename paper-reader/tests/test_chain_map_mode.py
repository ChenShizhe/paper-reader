"""
test_chain_map_mode.py — chain_map mode test suite.

All tests are mocked; no real LLM, no external API, no real MinerU invocations.
Synthetic PDF fixture is generated at test time via reportlab.

Test nodes (13 total):
  Extractor (3):
    test_exhibit_detection_finds_all
    test_row_extraction_schema_conformance
    test_graphical_only_exhibit_returns_empty_rows
  Ticker normalizer (5):
    test_cn_code_starting_6_to_sh
    test_cn_code_starting_0_to_sz
    test_hk_preserved
    test_us_suffix_stripped
    test_unknown_format_preserved_with_warning
  Cross-sourcer (2):
    test_watchlist_cross_source_emits_overlap_gaps_emphasis
    test_malformed_watchlist_fails_loud
  Rendering (2):
    test_render_report_from_synthetic_rows
    test_no_standalone_csv_written
  End-to-end (1):
    test_dry_run_with_synthetic_pdf
"""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
WATCHLIST_FIXTURE = FIXTURES_DIR / "example-watchlist.md"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from exhibit_extractor import (
    DEFAULT_SCHEMA,
    ExhibitSpan,
    detect_exhibits,
    extract_rows,
)
from ticker_normalizer import normalize_ticker
from watchlist_cross_source import cross_source, parse_watchlist
import summarize_paper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synthetic_pdf(path: Path) -> None:
    """Generate a minimal PDF with three exhibit headings using reportlab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter

    y = height - 72
    lines = [
        "Exhibit 1: Companies — Supply Chain Tier List",
        "AAPL  Apple Inc  integrator  US  80%  $3000B  28x  Leader in devices",
        "TSM   Taiwan Semi  component  Taiwan  60%  $600B  18x  Foundry champion",
        "NVDA  Nvidia Corp  component  US  95%  $2000B  50x  AI accelerators",
        "",
        "Exhibit 2: Value Chain Map",
        "MSFT  Microsoft Corp  integrator  US  70%  $2800B  32x  Cloud and AI",
        "INTC  Intel Corp  component  US  40%  $200B  15x  Legacy chipmaker",
        "AMD   Advanced Micro  component  US  50%  $250B  22x  Rising challenger",
        "",
        "Exhibit 3: Geographic Breakdown (see figure below)",
        "This exhibit is a chart. Please see the graphical figure for details.",
    ]
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
        if y < 72:
            c.showPage()
            y = height - 72

    c.save()


def _synthetic_text_with_three_exhibits() -> str:
    return (
        "Exhibit 1: Companies in Supply Chain\n"
        "AAPL  Apple Inc  integrator  US  80%  $3000B  28x  Consumer devices\n"
        "TSM   Taiwan Semiconductor  component  Taiwan  60%  $600B  18x  Foundry\n"
        "\n"
        "Exhibit 2: Value Chain Map for AI Hardware\n"
        "NVDA  Nvidia Corp  component  US  95%  $2000B  50x  GPU leader\n"
        "AMD   Advanced Micro Devices  component  US  50%  $250B  22x  Challenger\n"
        "\n"
        "Exhibit 3: Integrators and System Providers\n"
        "MSFT  Microsoft Corp  integrator  US  70%  $2800B  32x  Cloud platform\n"
        "AMZN  Amazon.com Inc  integrator  US  60%  $1800B  45x  AWS leader\n"
    )


# ===========================================================================
# Extractor tests
# ===========================================================================


class TestExhibitDetectionFindsAll(unittest.TestCase):
    def test_exhibit_detection_finds_all(self):
        text = _synthetic_text_with_three_exhibits()
        spans = detect_exhibits(text)
        self.assertEqual(len(spans), 3, f"Expected 3 exhibits, got {len(spans)}: {[s.exhibit_number for s in spans]}")
        numbers = {s.exhibit_number for s in spans}
        self.assertEqual(numbers, {"1", "2", "3"})


class TestRowExtractionSchemaConformance(unittest.TestCase):
    def test_row_extraction_schema_conformance(self):
        text = _synthetic_text_with_three_exhibits()
        spans = detect_exhibits(text)
        self.assertGreater(len(spans), 0)

        result = extract_rows(spans[0])
        self.assertFalse(result.graphical_only, "First exhibit should not be graphical-only")
        self.assertGreater(len(result.rows), 0, "First exhibit should yield at least one row")

        required_keys = set(DEFAULT_SCHEMA)
        for row in result.rows:
            missing = required_keys - set(row.keys())
            self.assertEqual(missing, set(), f"Row missing schema keys: {missing}")


class TestGraphicalOnlyExhibitReturnsEmptyRows(unittest.TestCase):
    def test_graphical_only_exhibit_returns_empty_rows(self):
        graphical_text = (
            "Exhibit 4: Supply Chain Diagram\n"
            "This exhibit is a figure/chart. Please see the diagram for details.\n"
        )
        spans = detect_exhibits(graphical_text)
        self.assertEqual(len(spans), 1)
        result = extract_rows(spans[0])
        self.assertTrue(result.graphical_only, "Exhibit with only graphical hints should be graphical_only=True")
        self.assertEqual(result.rows, [], "Graphical-only exhibit should return empty rows")
        self.assertEqual(result.confidence, 0.0)


# ===========================================================================
# Ticker normalizer tests
# ===========================================================================


class TestTickerNormalizerCN(unittest.TestCase):
    def test_cn_code_starting_6_to_sh(self):
        norm, fmt = normalize_ticker("600519-CN")
        self.assertEqual(norm, "600519.SH")
        self.assertEqual(fmt, "mapped_exchange")

    def test_cn_code_starting_0_to_sz(self):
        norm, fmt = normalize_ticker("000858-CN")
        self.assertEqual(norm, "000858.SZ")
        self.assertEqual(fmt, "mapped_exchange")

    def test_hk_preserved(self):
        norm, fmt = normalize_ticker("0700.HK")
        self.assertIn(".HK", norm)
        self.assertEqual(fmt, "preserved_known")

    def test_us_suffix_stripped(self):
        norm, fmt = normalize_ticker("AAPL-US")
        self.assertEqual(norm, "AAPL")
        self.assertEqual(fmt, "us_bare")

    def test_unknown_format_preserved_with_warning(self):
        raw = "XYZ-UNKNOWN"
        stderr_capture = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_capture):
            norm, fmt = normalize_ticker(raw)
        self.assertEqual(norm, raw)
        self.assertEqual(fmt, "preserved_unknown")
        self.assertIn("WARNING", stderr_capture.getvalue())


# ===========================================================================
# Cross-sourcer tests
# ===========================================================================


class TestWatchlistCrossSource(unittest.TestCase):
    def _make_inventory(self) -> list[dict]:
        return [
            {"ticker": "AAPL", "company_name": "Apple Inc", "tier": "integrator", "market_cap_usd_bn": "$3000B"},
            {"ticker": "NVDA", "company_name": "Nvidia Corp", "tier": "component", "market_cap_usd_bn": "$2000B"},
            {"ticker": "TSM", "company_name": "Taiwan Semiconductor", "tier": "component", "market_cap_usd_bn": "$600B"},
            {"ticker": "600519.SH", "company_name": "Kweichow Moutai", "tier": "material", "market_cap_usd_bn": "$200B"},
            # INTC is intentionally NOT in the watchlist fixture → will appear as a gap
            {"ticker": "INTC", "company_name": "Intel Corp", "tier": "component", "market_cap_usd_bn": "$150B"},
        ]

    def test_watchlist_cross_source_emits_overlap_gaps_emphasis(self):
        watchlist = parse_watchlist(WATCHLIST_FIXTURE)
        inventory = self._make_inventory()
        report_text = "## Standouts\n\nAAPL is a top standout.\n\n## Other\n"
        result = cross_source(watchlist, inventory, report_text=report_text)

        self.assertIn("overlap", result)
        self.assertIn("gaps", result)
        self.assertIn("emphasis", result)

        overlap_tickers = {item.split()[1] for item in result["overlap"]}
        self.assertIn("AAPL", overlap_tickers, "AAPL should be in overlap (in both watchlist and inventory)")

        gap_tickers = {item.split()[1] for item in result["gaps"]}
        self.assertIn("INTC", gap_tickers, "INTC should be a gap (in inventory but not in watchlist)")

        emphasis_tickers = {item.split()[1] for item in result["emphasis"]}
        self.assertIn("AAPL", emphasis_tickers, "AAPL should be in emphasis (overlap + standout)")

    def test_malformed_watchlist_fails_loud(self):
        malformed = "| only_one_col |\n|---|\n| value |\n"
        with self.assertRaises(ValueError) as ctx:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as f:
                f.write(malformed)
                tmp_path = f.name
            parse_watchlist(tmp_path)
        self.assertIn("missing required", str(ctx.exception).lower())


# ===========================================================================
# Rendering tests
# ===========================================================================


class TestRenderChainMapReport(unittest.TestCase):
    def _make_chain_map_data(self) -> dict:
        return {
            "title": "AI Hardware Supply Chain 2024",
            "authors": ["Jane Analyst"],
            "year": "2024",
            "thesis": "AI accelerators are consolidating around a few dominant chipmakers.",
            "chain_tiers": [
                {"tier": "1", "label": "Materials", "example_companies": "AMAT, LRCX", "notes": ""},
                {"tier": "2", "label": "Components", "example_companies": "TSM, NVDA", "notes": ""},
                {"tier": "3", "label": "Integrators", "example_companies": "AAPL, MSFT", "notes": ""},
            ],
            "geographic_summary": "Concentrated in US and Taiwan.",
            "countries": [
                {"country": "US", "n_companies": 5, "share_pct": 60.0},
                {"country": "Taiwan", "n_companies": 2, "share_pct": 25.0},
            ],
            "standouts": "NVDA stands out for AI revenue concentration.",
            "watchlist_alignment": "Strong overlap with watchlist positions.",
            "companies": [
                {"company_name": "Nvidia Corp", "ticker": "NVDA", "tier": "component", "country": "US", "notes": "GPU"},
                {"company_name": "Apple Inc", "ticker": "AAPL", "tier": "integrator", "country": "US", "notes": "Devices"},
                {"company_name": "Taiwan Semi", "ticker": "TSM", "tier": "component", "country": "Taiwan", "notes": "Foundry"},
            ],
            "key_data_points": [
                "NVDA revenue from AI datacenter: 85%",
                "TSM advanced node capacity: 65% global share",
            ],
            "editorial_credibility": "Exhibits 1-3 fully parsed; Exhibit 4 graphical-only.",
            "unparseable_exhibits": ["Exhibit 4: geographic heatmap"],
        }

    def test_render_report_from_synthetic_rows(self):
        data = self._make_chain_map_data()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paper_bank = tmp_path / "bank"
            paper_bank.mkdir()
            chain_map_path = paper_bank / "_chain_map.json"
            import json
            chain_map_path.write_text(json.dumps(data), encoding="utf-8")

            vault_path = tmp_path / "vault"
            vault_path.mkdir()

            result = summarize_paper.synthesize_chain_map(
                cite_key="test2024chain",
                paper_bank_dir=paper_bank,
                vault_path=vault_path,
            )

            output_file = vault_path / "literature" / "papers" / "test2024chain.md"
            self.assertTrue(output_file.exists(), "Report file should be written")

            content = output_file.read_text(encoding="utf-8")

            required_sections = [
                "## Top-line thesis",
                "## Chain structure",
                "## Geographic breakdown",
                "## Standouts",
                "## Portfolio lens",
                "## Company inventory",
                "## Key data points worth tracking",
                "## Editorial credibility",
            ]
            for section in required_sections:
                self.assertIn(section, content, f"Missing section: {section!r}")

            self.assertIn("## Company inventory", content)
            self.assertIn("```csv", content, "Company inventory should contain fenced csv block")

            self.assertIn("data_sections", content, "Frontmatter should include data_sections field")

            no_csv_files = list(output_file.parent.glob("*.csv"))
            self.assertEqual(no_csv_files, [], "No standalone .csv file should be written")

    def test_no_standalone_csv_written(self):
        data = self._make_chain_map_data()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paper_bank = tmp_path / "bank"
            paper_bank.mkdir()
            chain_map_path = paper_bank / "_chain_map.json"
            import json
            chain_map_path.write_text(json.dumps(data), encoding="utf-8")

            vault_path = tmp_path / "vault"
            vault_path.mkdir()

            summarize_paper.synthesize_chain_map(
                cite_key="test2024chain",
                paper_bank_dir=paper_bank,
                vault_path=vault_path,
            )

            output_dir = vault_path / "literature" / "papers"
            csv_files = list(output_dir.glob("*.csv")) if output_dir.exists() else []
            self.assertEqual(csv_files, [], f"No .csv files should be written alongside the report; found: {csv_files}")


# ===========================================================================
# End-to-end test
# ===========================================================================


class TestDryRunWithSyntheticPdf(unittest.TestCase):
    _pdf_path: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        fixtures_dir = FIXTURES_DIR
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        cls._pdf_path = fixtures_dir / "synthetic-chain-map.pdf"
        _make_synthetic_pdf(cls._pdf_path)

    def test_dry_run_with_synthetic_pdf(self):
        self.assertIsNotNone(self._pdf_path)
        self.assertTrue(self._pdf_path.exists(), "Synthetic PDF should have been generated by setUpClass")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "run_pipeline.py"),
                "--cite-key", "testchain2024",
                "--source-path", str(self._pdf_path),
                "--source-format", "pdf",
                "--mode", "chain_map",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"--dry-run should exit 0.\nstdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}",
        )
        import json
        plan_data = json.loads(result.stdout)
        self.assertTrue(plan_data.get("dry_run"), "Output should confirm dry_run=true")
        self.assertEqual(plan_data.get("cite_key"), "testchain2024")


# ===========================================================================
# unittest.mock import guard
# ===========================================================================

import unittest.mock  # noqa: E402 — needed for test_unknown_format_preserved_with_warning


if __name__ == "__main__":
    unittest.main()
