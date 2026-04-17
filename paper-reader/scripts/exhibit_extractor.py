"""
exhibit_extractor.py — detect, parse, and deduplicate exhibit rows from research paper text.

Public API:
    detect_exhibits(text)          -> list[ExhibitSpan]
    extract_rows(exhibit_span, schema) -> ExtractionResult
    dedup_rows(rows)               -> list[dict]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

DEFAULT_SCHEMA: list[str] = [
    "ticker",
    "company_name",
    "tier",
    "components",
    "geography",
    "revenue_exposure_pct",
    "market_cap_usd_bn",
    "pe_or_multiple",
    "thesis_note",
]

# Tier preference for dedup: higher index = more distinctive.
_TIER_RANK: dict[str, int] = {
    "material": 0,
    "materials": 0,
    "component": 1,
    "components": 1,
    "integrator": 2,
    "integrators": 2,
    "system integrator": 2,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExhibitSpan:
    """Metadata and raw text for a single detected exhibit."""
    exhibit_number: str          # e.g. "1", "2A"
    title: str                   # text after "Exhibit N:"
    start: int                   # character offset in source text
    end: int                     # character offset in source text
    raw_text: str                # substring of source between start and end


@dataclass
class ExtractionResult:
    """Output of extract_rows for one exhibit."""
    exhibit_number: str
    rows: list[dict[str, Any]]
    rows_expected: int           # estimated from title keywords
    confidence: float            # rows_extracted / rows_expected (capped 0–1)
    graphical_only: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# Exhibit detection
# ---------------------------------------------------------------------------

# Matches: Exhibit 1: Companies / Exhibit 2A: Value chain / Exhibit 3: Integrators / etc.
_EXHIBIT_HEADER = re.compile(
    r"Exhibit\s+(\w+)\s*[:\-–—]\s*"
    r"((?:Companies?|Value[\s\-]?[Cc]hain|Tier\s*\d*|Integrators?|"
    r"Supply[\s\-]?[Cc]hain|Market[\s\-]?[Mm]ap|"
    r"Component[\s\-]?[Ll]ist|[^\n]{0,80}))",
    re.IGNORECASE,
)

# Sentinel pattern used to find where the next exhibit starts (to bound current one).
_NEXT_EXHIBIT = re.compile(r"Exhibit\s+\w+\s*[:\-–—]", re.IGNORECASE)


def detect_exhibits(text: str) -> list[ExhibitSpan]:
    """Return ordered list of ExhibitSpan for every exhibit header found in *text*."""
    spans: list[ExhibitSpan] = []

    for m in _EXHIBIT_HEADER.finditer(text):
        exhibit_number = m.group(1)
        title = m.group(2).strip()
        header_end = m.end()

        # Find where this exhibit ends: the next exhibit header, or EOF.
        next_m = _NEXT_EXHIBIT.search(text, header_end)
        body_end = next_m.start() if next_m else len(text)

        spans.append(
            ExhibitSpan(
                exhibit_number=exhibit_number,
                title=title,
                start=m.start(),
                end=body_end,
                raw_text=text[m.start():body_end],
            )
        )

    return spans


# ---------------------------------------------------------------------------
# Row extraction helpers
# ---------------------------------------------------------------------------

# Typical ticker pattern: 2–5 uppercase letters (optionally prefixed by exchange).
_TICKER_RE = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b")

# Revenue / exposure percentages: "12%", "~15%", "10-20%"
_PCT_RE = re.compile(r"~?(\d{1,3}(?:\.\d+)?)\s*(?:–|-|to)?\s*(?:\d{1,3}(?:\.\d+)?)?\s*%")

# Market cap patterns: "$12.3B", "12.3bn", "USD 5B"
_MCAP_RE = re.compile(
    r"(?:USD?\s*\$?\s*|US\$\s*)?(\d{1,6}(?:\.\d+)?)\s*(?:B|bn|billion|T|tn|trillion)",
    re.IGNORECASE,
)

# P/E or EV/EBITDA style multiples: "25x", "12.5x", "30×"
_MULTIPLE_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*[x×]", re.IGNORECASE)

# Tier keywords
_TIER_KEYWORDS = re.compile(
    r"\b(integrators?|system\s+integrator|component[s]?|material[s]?|tier\s*\d)\b",
    re.IGNORECASE,
)

# Geography / region keywords
_GEO_KEYWORDS = re.compile(
    r"\b(US|USA|China|Taiwan|Japan|Korea|Europe|EU|Global|APAC|EM|Americas)\b",
    re.IGNORECASE,
)

# Lines that suggest the exhibit is graphical-only (no extractable data rows).
_GRAPHICAL_HINTS = re.compile(
    r"(?:figure|chart|diagram|image|photo|illustration|graph|see\s+(?:figure|chart))",
    re.IGNORECASE,
)

# Rough estimate of expected rows based on title noun phrases.
_TITLE_COUNT_HINTS = re.compile(
    r"\b(\d+)\s*(?:companies|companies?|integrators?|players?|names?|stocks?|tickers?)\b",
    re.IGNORECASE,
)

# A data row likely contains at least one ticker-like token plus some numbers.
_DATA_LINE_RE = re.compile(
    r"[A-Z]{2,5}.*\d",  # capital token + digit somewhere on the line
)


def _estimate_expected_rows(title: str) -> int:
    """Guess how many rows an exhibit should contain from its title."""
    m = _TITLE_COUNT_HINTS.search(title)
    if m:
        return int(m.group(1))
    # Default reasonable expectation for a company-list exhibit.
    return 10


def _parse_line_to_row(line: str, schema: list[str]) -> dict[str, Any] | None:
    """Attempt to extract field values from a single text line; return None if not a data line."""
    line = line.strip()
    if not line or not _DATA_LINE_RE.match(line):
        return None

    row: dict[str, Any] = {col: "NA" for col in schema}

    # Ticker
    tickers = _TICKER_RE.findall(line)
    if tickers:
        row["ticker"] = tickers[0]

    # Company name: heuristic — everything before the first number or separator.
    name_m = re.match(r"^([A-Za-z0-9&\.\s\-,\']{4,50?}?)(?:\s{2,}|\t|\|)", line)
    if name_m:
        row["company_name"] = name_m.group(1).strip()

    # Tier
    tier_m = _TIER_KEYWORDS.search(line)
    if tier_m:
        row["tier"] = tier_m.group(1).lower()

    # Components (comma-separated list of component names after tier)
    if tier_m:
        after_tier = line[tier_m.end():].strip()
        comp_m = re.match(r"([A-Za-z0-9,\s\-&]+?)(?:\s{2,}|\t|\||\d)", after_tier)
        if comp_m:
            row["components"] = comp_m.group(1).strip()

    # Geography
    geo_m = _GEO_KEYWORDS.search(line)
    if geo_m:
        row["geography"] = geo_m.group(1)

    # Revenue exposure %
    pct_m = _PCT_RE.search(line)
    if pct_m:
        row["revenue_exposure_pct"] = pct_m.group(0).strip()

    # Market cap
    mcap_m = _MCAP_RE.search(line)
    if mcap_m:
        row["market_cap_usd_bn"] = mcap_m.group(0).strip()

    # P/E or multiple
    mult_m = _MULTIPLE_RE.search(line)
    if mult_m:
        row["pe_or_multiple"] = mult_m.group(0).strip()

    # Thesis note: anything after the last numeric token
    parts = re.split(r"[\|\t]{1,}", line)
    if len(parts) >= 2:
        row["thesis_note"] = parts[-1].strip() or "NA"

    # Only return the row if we got at least a ticker or company name.
    if row["ticker"] == "NA" and row["company_name"] == "NA":
        return None

    return row


# ---------------------------------------------------------------------------
# extract_rows
# ---------------------------------------------------------------------------

def extract_rows(
    exhibit_span: ExhibitSpan,
    schema: list[str] | None = None,
) -> ExtractionResult:
    """
    Parse data rows from *exhibit_span*.

    Returns an ExtractionResult with:
    - rows: list of row dicts (keys = schema columns, missing values = 'NA')
    - confidence: rows_extracted / rows_expected (0.0–1.0)
    - graphical_only: True if the exhibit body has no extractable text rows
    - note: human-readable note for graphical-only or low-confidence exhibits
    """
    if schema is None:
        schema = DEFAULT_SCHEMA

    body = exhibit_span.raw_text
    rows_expected = _estimate_expected_rows(exhibit_span.title)

    # --- Graphical-only detection ---
    if _GRAPHICAL_HINTS.search(body):
        non_hint_text = _GRAPHICAL_HINTS.sub("", body)
        has_data_lines = any(
            _DATA_LINE_RE.match(ln.strip())
            for ln in non_hint_text.splitlines()
            if ln.strip()
        )
        if not has_data_lines:
            return ExtractionResult(
                exhibit_number=exhibit_span.exhibit_number,
                rows=[],
                rows_expected=rows_expected,
                confidence=0.0,
                graphical_only=True,
                note=(
                    f"Exhibit {exhibit_span.exhibit_number} appears to be graphical-only "
                    f"(no extractable text rows). Forward to Editorial for manual review."
                ),
            )

    # --- Line-by-line row extraction ---
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        row = _parse_line_to_row(line, schema)
        if row is not None:
            rows.append(row)

    # If nothing was extracted and the body is very short / sparse, treat as graphical.
    if not rows:
        return ExtractionResult(
            exhibit_number=exhibit_span.exhibit_number,
            rows=[],
            rows_expected=rows_expected,
            confidence=0.0,
            graphical_only=True,
            note=(
                f"Exhibit {exhibit_span.exhibit_number}: no text rows extracted. "
                f"Likely graphical-only or unparseable layout. Forward to Editorial."
            ),
        )

    confidence = min(1.0, len(rows) / max(1, rows_expected))
    note = ""
    if confidence < 0.5:
        note = (
            f"Exhibit {exhibit_span.exhibit_number}: low confidence "
            f"({len(rows)}/{rows_expected} rows). Manual review recommended."
        )

    return ExtractionResult(
        exhibit_number=exhibit_span.exhibit_number,
        rows=rows,
        rows_expected=rows_expected,
        confidence=confidence,
        graphical_only=False,
        note=note,
    )


# ---------------------------------------------------------------------------
# dedup_rows
# ---------------------------------------------------------------------------

def dedup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Deduplicate rows across exhibits.

    When the same company appears more than once, keep the row with the
    most-distinctive tier (integrator > component > material). For identical
    tier rank, keep the first occurrence (preserve ordering).
    """
    canonical: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = _company_key(row)
        if key not in canonical:
            canonical[key] = row
        else:
            existing_rank = _tier_rank(canonical[key].get("tier", "NA"))
            incoming_rank = _tier_rank(row.get("tier", "NA"))
            if incoming_rank > existing_rank:
                canonical[key] = row

    # Preserve original ordering of first occurrences.
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = _company_key(row)
        if key not in seen:
            seen.add(key)
            result.append(canonical[key])

    return result


def _company_key(row: dict[str, Any]) -> str:
    """Stable identity key for a row (prefer ticker, fall back to normalised name)."""
    ticker = row.get("ticker", "NA")
    if ticker and ticker != "NA":
        return ticker.upper()
    name = row.get("company_name", "NA")
    return re.sub(r"\s+", " ", name).strip().lower()


def _tier_rank(tier: str) -> int:
    """Return numeric tier preference rank (higher = more distinctive)."""
    if not tier or tier == "NA":
        return -1
    return _TIER_RANK.get(tier.lower().strip(), -1)
