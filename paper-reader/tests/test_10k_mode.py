"""Tests for 10-K mode (proposal 07).

Covers:
  - segment_10k.py detects all canonical Items in the synthetic fixture,
    records Part III items as incorporated_by_reference, and writes per-Item
    segments + a manifest with segmentation_version '10k_v1'.
  - translate_10k_html.py extracts iXBRL numeric tags and normalizes Item
    headings.
  - summarize_paper.synthesize_10k merges 3 canned subagent JSONs into the
    14-section summary, writes the claims sidecar with only permitted types,
    and mirrors per-Item notes into the vault.
  - validate_10k_mode.py passes on good output and fails on missing sections /
    disallowed claim types / missing per-Item notes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# segment_10k.py
# ---------------------------------------------------------------------------


def test_segment_10k_detects_all_canonical_items(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    import segment_10k as s10k  # type: ignore

    source_path = tmp_path / "translated_full.md"
    source_path.write_text(
        (FIXTURES_DIR / "synthetic-10k.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    out_dir = tmp_path / "segments"
    manifest_path = s10k.segment_10k_file(
        cite_key="NVDA_TEST",
        source_path=source_path,
        output_dir=out_dir,
    )
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["segmentation_version"] == "10k_v1"
    # 22 canonical Items: 1, 1A, 1B, 1C, 2, 3, 4, 5, 6, 7, 7A, 8, 9, 9A, 9B,
    # 10, 11, 12, 13, 14, 15, 16.
    assert len(manifest["segments"]) == 22

    by_number = {entry["item"]: entry for entry in manifest["segments"]}
    # Body-bearing Items (present=true):
    assert by_number["1"]["present"] == "true"
    assert by_number["1A"]["present"] == "true"
    assert by_number["1C"]["present"] == "true"
    assert by_number["7"]["present"] == "true"
    assert by_number["7A"]["present"] == "true"
    assert by_number["8"]["present"] == "true"
    assert by_number["9A"]["present"] == "true"

    # Part III Items incorporated by reference from the Proxy Statement.
    for n in ("10", "11", "12", "13", "14"):
        assert by_number[n]["present"] == "incorporated_by_reference", by_number[n]

    # Per-Item segment files exist for present Items.
    assert (out_dir / "item-1.md").exists()
    assert (out_dir / "item-7.md").exists()
    assert (out_dir / "item-10.md").exists()


def test_segment_10k_skips_absent_items(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    import segment_10k as s10k  # type: ignore

    source_path = tmp_path / "mini.md"
    source_path.write_text(
        "## Item 1. Business\n\nBody.\n\n"
        "## Item 7. MD&A\n\nBody.\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "segments"
    s10k.segment_10k_file(cite_key="MINI", source_path=source_path, output_dir=out_dir)
    manifest = json.loads((out_dir / "_segment_manifest.json").read_text(encoding="utf-8"))
    by_number = {entry["item"]: entry for entry in manifest["segments"]}
    assert by_number["1"]["present"] == "true"
    assert by_number["7"]["present"] == "true"
    assert by_number["1A"]["present"] == "false"
    assert by_number["15"]["present"] == "false"


# ---------------------------------------------------------------------------
# translate_10k_html.py
# ---------------------------------------------------------------------------


def test_translate_10k_html_extracts_ixbrl_and_normalizes_items(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    import translate_10k_html as t10k  # type: ignore

    html = """
<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
  <body>
    <h1>Part I</h1>
    <p>Item 1. Business</p>
    <p>Revenue for fiscal 2024 was
       <ix:nonfraction name="us-gaap:Revenues" contextRef="ctx_2024"
                       decimals="-6" unitRef="usd" scale="6">60.9</ix:nonfraction>
       billion.</p>
    <p>ITEM 1A. Risk Factors</p>
    <p>Concentration risk is elevated.</p>
  </body>
</html>
""".strip()
    input_path = tmp_path / "filing.html"
    output_path = tmp_path / "translated_full.md"
    input_path.write_text(html, encoding="utf-8")
    _, facts = t10k.translate_10k_html(
        input_path=input_path,
        output_path=output_path,
        cite_key="NVDA_TEST_HTML",
        paper_bank_dir=tmp_path,
    )
    assert len(facts) == 1
    assert facts[0]["name"] == "us-gaap:Revenues"
    assert facts[0]["value"] == "60.9"

    xbrl_json = json.loads((tmp_path / "_xbrl_tags.json").read_text(encoding="utf-8"))
    assert xbrl_json["fact_count"] == 1

    output = output_path.read_text(encoding="utf-8")
    assert "## Item 1. Business" in output
    assert "## Item 1A. Risk Factors" in output


# ---------------------------------------------------------------------------
# summarize_paper.synthesize_10k
# ---------------------------------------------------------------------------


CANNED_POSITIONING = {
    "subagent": "positioning",
    "items_read": ["1", "1A", "1C"],
    "sections": {
        "Company Snapshot": "NVIDIA Example Corp. designs GPU and accelerated computing platforms.",
        "Business and Segments": "Two segments: Compute and Networking; Graphics.",
        "Priority Risk Factors": "Customer concentration, foundry dependence, export-control exposure.",
    },
    "claims": [
        {
            "type": "company-thesis",
            "claim_text": "Data-center GPU franchise drives majority of revenue.",
            "source_anchor": {
                "source_type": "10-K Item",
                "locator": "1-Business",
                "confidence": "high",
            },
        }
    ],
}

CANNED_FINANCIAL = {
    "subagent": "financial",
    "items_read": ["7", "7A", "8"],
    "sections": {
        "MD&A Synthesis": "Revenue up materially YoY; gross margin expansion; opex grew slower than revenue.",
        "Segment Performance": "Compute and Networking drove the increase; Graphics was flat.",
        "Financial Position": "Strong liquidity and modest leverage.",
        "Cash Flow Quality": "Operating cash flow closely tracks net income.",
        "Notes Highlights": "Revenue recognized at shipment; effective tax rate benefited from reserve release.",
        "Non-GAAP and KPI Reconciliation": "Non-GAAP gross margin reconciles to GAAP via SBC and acquisition costs.",
    },
    "claims": [
        {
            "type": "empirical",
            "claim_text": "Fiscal 2024 revenue grew materially YoY.",
            "source_anchor": {
                "source_type": "10-K Item",
                "locator": "7-Results of Operations",
                "confidence": "high",
            },
        }
    ],
}

CANNED_RISK_FORWARD = {
    "subagent": "risk_forward",
    "items_read": ["9A", "9B"],
    "sections": {
        "Controls and Governance": "No material weakness identified; unqualified auditor opinion.",
        "Evolving-Topic Coverage": "Cybersecurity: substantive. AI materiality: implicit throughout Item 1/7.",
        "Forward-Looking Statements": "Management expects sustained data-center demand.",
    },
    "claims": [
        {
            "type": "projection",
            "claim_text": "Management expects sustained data-center demand.",
            "source_anchor": {
                "source_type": "10-K Item",
                "locator": "7-Known Trends",
                "confidence": "medium",
            },
        }
    ],
}


def _stage_synthesize_inputs(tmp_path: Path, cite_key: str) -> tuple[Path, Path]:
    """Build the paper-bank layout synthesize_10k expects; return (paper_bank, vault)."""
    paper_bank_parent = tmp_path / "paper-bank"
    paper_bank = paper_bank_parent / cite_key
    vault = tmp_path / "vault"
    (paper_bank / "segments").mkdir(parents=True)

    # Drop a canned manifest + per-Item segment files.
    manifest_entries = []
    items_with_bodies = {
        "1": "Business",
        "1A": "Risk Factors",
        "1C": "Cybersecurity",
        "7": "MD&A",
        "7A": "Market Risk Disclosures",
        "8": "Financial Statements",
        "9A": "Controls and Procedures",
    }
    canonical = ["1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8",
                 "9", "9A", "9B", "10", "11", "12", "13", "14", "15", "16"]
    for num in canonical:
        if num in items_with_bodies:
            fn = paper_bank / "segments" / f"item-{num}.md"
            fn.write_text(
                f"---\ncite_key: {cite_key}\nitem: {num}\n---\n\nBody for Item {num}.\n",
                encoding="utf-8",
            )
            manifest_entries.append({
                "item": num,
                "item_title": items_with_bodies[num],
                "present": "true",
                "word_count": 5,
                "file": f"segments/item-{num}.md",
            })
        elif num in ("10", "11", "12", "13", "14"):
            manifest_entries.append({
                "item": num,
                "item_title": f"Item {num}",
                "present": "incorporated_by_reference",
                "word_count": 3,
                "incorporation_pointer": "the Proxy Statement",
            })
        else:
            manifest_entries.append({
                "item": num,
                "item_title": f"Item {num}",
                "present": "false",
                "word_count": 0,
            })

    (paper_bank / "segments" / "_segment_manifest.json").write_text(
        json.dumps({
            "cite_key": cite_key,
            "segmentation_version": "10k_v1",
            "segments": manifest_entries,
            "segment_count": len(manifest_entries),
        }),
        encoding="utf-8",
    )

    # Drop canned subagent outputs.
    (paper_bank / "_10k_positioning.json").write_text(
        json.dumps(CANNED_POSITIONING), encoding="utf-8"
    )
    (paper_bank / "_10k_financial.json").write_text(
        json.dumps(CANNED_FINANCIAL), encoding="utf-8"
    )
    (paper_bank / "_10k_risk_forward.json").write_text(
        json.dumps(CANNED_RISK_FORWARD), encoding="utf-8"
    )

    # Filing metadata file.
    (tmp_path / "filing-metadata.json").write_text(
        json.dumps({
            "ticker": "NVDA",
            "company_name": "NVIDIA Example Corp.",
            "fiscal_year": "2024",
            "period_end": "2024-01-28",
            "filed": "2024-02-21",
            "cik": "0001045810",
            "accession_number": "0001045810-24-000000",
            "source_format": "pdf",
        }),
        encoding="utf-8",
    )

    return paper_bank, vault


def test_synthesize_10k_builds_14_section_summary(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    from summarize_paper import synthesize_10k  # type: ignore

    cite_key = "NVDA_10k_FY2024_TEST"
    paper_bank, vault = _stage_synthesize_inputs(tmp_path, cite_key)

    result = synthesize_10k(
        cite_key=cite_key,
        ticker="NVDA",
        paper_bank_dir=paper_bank,
        vault_path=vault,
        filing_metadata_path=tmp_path / "filing-metadata.json",
    )
    assert result["section_count"] == 14
    assert result["sections_populated"] >= 12  # All 12 subagent-populated sections.
    assert result["claim_count"] == 3
    assert result["per_item_notes_written"] == 22  # 22 canonical Items.

    summary_path = Path(result["summary_path"])
    assert summary_path.exists()
    text = summary_path.read_text(encoding="utf-8")
    for heading in (
        "## Company Snapshot",
        "## MD&A Synthesis",
        "## Textual-Analysis Flags",
        "## Forward-Looking Statements",
        "## Open Questions",
    ):
        assert heading in text

    claims_path = Path(result["claims_path"])
    assert claims_path.exists()
    claims_payload = json.loads(claims_path.read_text(encoding="utf-8"))
    assert claims_payload["cite_key"] == cite_key
    assert len(claims_payload["claims"]) == 3


def test_synthesize_10k_drops_disallowed_claim_types(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    from summarize_paper import synthesize_10k  # type: ignore

    cite_key = "TEST_DROP_CLAIMS"
    paper_bank, vault = _stage_synthesize_inputs(tmp_path, cite_key)

    # Inject a theorem claim into the positioning subagent output — the
    # filing claim-domain must reject it.
    pos = json.loads((paper_bank / "_10k_positioning.json").read_text(encoding="utf-8"))
    pos["claims"].append({
        "type": "theorem",
        "claim_text": "Should be dropped.",
        "source_anchor": {"source_type": "10-K Item", "locator": "1-X", "confidence": "high"},
    })
    (paper_bank / "_10k_positioning.json").write_text(json.dumps(pos), encoding="utf-8")

    result = synthesize_10k(
        cite_key=cite_key,
        ticker="TEST",
        paper_bank_dir=paper_bank,
        vault_path=vault,
        filing_metadata_path=tmp_path / "filing-metadata.json",
    )
    # The theorem-typed claim should be dropped by _validate_10k_claims.
    assert result["claim_count"] == 3


# ---------------------------------------------------------------------------
# validate_10k_mode.py
# ---------------------------------------------------------------------------


def test_validate_10k_mode_passes_on_synthesized_output(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS_DIR))
    from summarize_paper import synthesize_10k  # type: ignore

    cite_key = "VALIDATE_OK"
    paper_bank, vault = _stage_synthesize_inputs(tmp_path, cite_key)
    synthesize_10k(
        cite_key=cite_key,
        ticker="NVDA",
        paper_bank_dir=paper_bank,
        vault_path=vault,
        filing_metadata_path=tmp_path / "filing-metadata.json",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_10k_mode.py"),
            "--cite-key",
            cite_key,
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_validate_10k_mode_rejects_missing_sections(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    papers = vault / "papers"
    claims = vault / "claims"
    papers.mkdir(parents=True)
    claims.mkdir(parents=True)
    (papers / "MISSING.md").write_text(
        "---\n"
        "cite_key: MISSING\n"
        "ticker: NVDA\n"
        "filing_type: 10-K\n"
        "fiscal_year: 2024\n"
        "period_end: 2024-01-28\n"
        "filed: 2024-02-21\n"
        "source_format: pdf\n"
        "mode: 10k\n"
        "items_present: [\"1\", \"7\"]\n"
        "---\n\n"
        "## Company Snapshot\n\nx\n\n"
        # Missing most of the other 13 sections.
        ,
        encoding="utf-8",
    )
    (claims / "MISSING.json").write_text(
        json.dumps({
            "schema_version": "v2",
            "cite_key": "MISSING",
            "canonical_id": "MISSING",
            "content_status": "full",
            "extraction_confidence": "medium",
            "claims": [],
        }),
        encoding="utf-8",
    )
    # Per-Item note directory is empty, triggering missing-note errors too.
    (papers / "MISSING").mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_10k_mode.py"),
            "--cite-key",
            "MISSING",
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "missing required section" in result.stdout
