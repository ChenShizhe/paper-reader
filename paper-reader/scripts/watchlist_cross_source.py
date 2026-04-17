"""watchlist_cross_source.py — cross-reference a GFM watchlist against a chain_map inventory."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

# Allow import whether scripts/ is on sys.path or this file is run directly.
sys.path.insert(0, str(Path(__file__).parent))
from ticker_normalizer import normalize_ticker  # noqa: E402

_REQUIRED_COLS = {"ticker", "name"}
_SEP_ROW_RE = re.compile(r"^\|[-| :]+\|?\s*$")


# ---------------------------------------------------------------------------
# GFM table parser
# ---------------------------------------------------------------------------

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_gfm_table(text: str, source: str = "<input>") -> list[dict[str, str]]:
    """Parse the first GFM pipe table in *text*. Raises ValueError on malformed input."""
    table_lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    if not table_lines:
        raise ValueError(
            f"watchlist_cross_source: no GFM pipe table found in {source!r}"
        )

    headers = [h.lower().strip() for h in _cells(table_lines[0])]
    missing = _REQUIRED_COLS - set(headers)
    if missing:
        raise ValueError(
            f"watchlist_cross_source: GFM table in {source!r} missing required "
            f"columns: {sorted(missing)}"
        )

    if len(table_lines) < 2 or not _SEP_ROW_RE.match(table_lines[1].strip()):
        raise ValueError(
            f"watchlist_cross_source: row 2 in {source!r} is not a GFM separator "
            f"(expected |---|--- …); got: {table_lines[1] if len(table_lines) > 1 else '<missing>'!r}"
        )

    rows: list[dict[str, str]] = []
    for lineno, line in enumerate(table_lines[2:], start=3):
        cs = _cells(line)
        if len(cs) < len(headers):
            cs += [""] * (len(headers) - len(cs))
        row = dict(zip(headers, cs[: len(headers)]))
        if not row.get("ticker", "").strip():
            raise ValueError(
                f"watchlist_cross_source: row {lineno} in {source!r} has an empty ticker cell"
            )
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Public: parse_watchlist
# ---------------------------------------------------------------------------

def parse_watchlist(path: str | Path) -> list[dict[str, str]]:
    """Parse a GFM-table markdown watchlist file.

    Required columns: ticker, name. Optional: tier, track, notes.
    Raises FileNotFoundError or ValueError with a clear message on any error.
    Returns list of row dicts keyed by lowercased column name.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"watchlist_cross_source: watchlist file not found: {path!r}"
        )
    return _parse_gfm_table(p.read_text(encoding="utf-8"), source=str(path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(ticker: str) -> str:
    norm, _ = normalize_ticker(ticker)
    return norm.upper()


def _parse_mcap(raw: Any) -> float | None:
    if not raw or raw == "NA":
        return None
    m = re.search(
        r"(\d{1,6}(?:\.\d+)?)\s*(?:B|bn|billion|T|tn|trillion)",
        str(raw),
        re.IGNORECASE,
    )
    if not m:
        return None
    val = float(m.group(1))
    if re.search(r"(?:T|tn|trillion)", m.group(0), re.IGNORECASE):
        val *= 1000
    return val


def _standout_tickers(report_text: str) -> set[str]:
    """Return normalized tickers found under a '## Standouts' heading."""
    m = re.search(r"^##\s+Standouts?\b", report_text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return set()
    tail = report_text[m.end():]
    end = re.search(r"^##\s+", tail, re.MULTILINE)
    section = tail[: end.start()] if end else tail
    tokens = re.findall(r"\b([A-Z]{1,6}(?:\.[A-Z]{1,4})?)\b", section)
    return {_norm(t) for t in tokens}


# ---------------------------------------------------------------------------
# Public: cross_source
# ---------------------------------------------------------------------------

def cross_source(
    watchlist: list[dict[str, str]] | str | Path,
    inventory: list[dict[str, Any]],
    report_text: str = "",
) -> dict[str, Any]:
    """Compute overlap, gaps, and emphasis between a watchlist and a chain_map inventory.

    Args:
        watchlist:    Parsed rows from parse_watchlist(), or a path to parse.
        inventory:    company_inventory rows from the chain_map report.
        report_text:  Full report markdown used to locate ## Standouts.

    Returns dict with keys:
        overlap  — bullet strings for tickers in both watchlist and inventory.
        gaps     — bullet strings for inventory tickers absent from watchlist
                   (top 10 by market_cap_usd_bn desc, or first 10 alphabetically).
        emphasis — bullet strings for overlap tickers also in ## Standouts.
    """
    if not isinstance(watchlist, list):
        watchlist = parse_watchlist(watchlist)

    wl_map: dict[str, dict[str, str]] = {
        _norm(r["ticker"]): r for r in watchlist if r.get("ticker", "").strip()
    }

    inv_map: dict[str, dict[str, Any]] = {}
    for row in inventory:
        raw_t = (row.get("ticker") or row.get("normalized_ticker") or "").strip()
        if raw_t and raw_t != "NA":
            inv_map[_norm(raw_t)] = row

    wl_set, inv_set = set(wl_map), set(inv_map)

    # overlap: tickers present in both
    overlap_tickers = sorted(wl_set & inv_set)
    overlap = [f"- {t} ({wl_map[t].get('name', '')})" for t in overlap_tickers]

    # gaps: in inventory but not in watchlist
    gap_tickers = list(inv_set - wl_set)
    has_mcap = any(
        _parse_mcap(inv_map[t].get("market_cap_usd_bn")) is not None for t in gap_tickers
    )
    if has_mcap:
        gap_tickers.sort(key=lambda t: _parse_mcap(inv_map[t].get("market_cap_usd_bn")) or -1.0, reverse=True)
    else:
        gap_tickers.sort()
    gap_tickers = gap_tickers[:10]

    gaps = [
        f"- {t} ({inv_map[t].get('company_name', inv_map[t].get('name', ''))})"
        for t in gap_tickers
    ]

    # emphasis: overlap tickers that also appear in ## Standouts
    standouts = _standout_tickers(report_text) if report_text else set()
    emphasis = [f"- {t}" for t in sorted(t for t in overlap_tickers if t in standouts)]

    return {"overlap": overlap, "gaps": gaps, "emphasis": emphasis}
