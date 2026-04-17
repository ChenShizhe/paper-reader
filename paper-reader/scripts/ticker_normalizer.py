"""Normalize raw ticker strings to exchange-suffix format."""

import re
import sys

KNOWN_EXCHANGE_SUFFIXES = {
    ".SH", ".SZ", ".HK", ".TW", ".T", ".KS", ".KQ",
    ".L", ".PA", ".AS", ".MI", ".MC", ".F", ".DE",
    ".SI", ".AX", ".NZ", ".TO", ".V", ".BO", ".NS",
    ".SS", ".BK", ".JK", ".KL", ".TWO",
}

KNOWN_SUFFIX_PATTERN = re.compile(
    r"\.(?:SH|SZ|HK|TW|T|KS|KQ|L|PA|AS|MI|MC|F|DE|SI|AX|NZ|TO|V|BO|NS|SS|BK|JK|KL|TWO)$",
    re.IGNORECASE,
)

CN_SUFFIX_PATTERN = re.compile(
    r"^(\d+)\s*[-\s](?:CN|CH|SS)$", re.IGNORECASE
)

HK_SUFFIX_PATTERN = re.compile(
    r"^(\d{4,5})\s*[-\s]HK$", re.IGNORECASE
)

TW_SUFFIX_PATTERN = re.compile(
    r"^(\d{4})\s*[-\s]TW$", re.IGNORECASE
)

US_SUFFIX_PATTERN = re.compile(
    r"^([A-Z0-9./]+)\s*[-\s](?:US|UN|UW)$", re.IGNORECASE
)

CN_PREFIX_TO_EXCHANGE = {
    "6": ".SH",
    "0": ".SZ",
    "3": ".SZ",
    "4": ".SZ",
    "8": ".SZ",
    "9": ".SH",
}


def _map_cn_code(code: str) -> str:
    prefix = code[0] if code else ""
    return CN_PREFIX_TO_EXCHANGE.get(prefix, ".SH")


def normalize_ticker(raw: str) -> tuple:
    """Return (normalized, format_type) for a raw ticker string.

    format_type values:
      'preserved_known'   — already had a recognized exchange suffix
      'mapped_exchange'   — country suffix converted to exchange suffix
      'us_bare'           — US suffix stripped, bare ticker returned
      'preserved_unknown' — unrecognized format, returned as-is with warning
    """
    ticker = raw.strip()

    if KNOWN_SUFFIX_PATTERN.search(ticker):
        return (ticker, "preserved_known")

    m = CN_SUFFIX_PATTERN.match(ticker)
    if m:
        code = m.group(1)
        exchange = _map_cn_code(code)
        return (code + exchange, "mapped_exchange")

    m = HK_SUFFIX_PATTERN.match(ticker)
    if m:
        code = m.group(1).zfill(4)
        return (code + ".HK", "mapped_exchange")

    m = TW_SUFFIX_PATTERN.match(ticker)
    if m:
        code = m.group(1)
        return (code + ".TW", "mapped_exchange")

    m = US_SUFFIX_PATTERN.match(ticker)
    if m:
        return (m.group(1).upper(), "us_bare")

    print(f"WARNING: ticker_normalizer: unrecognized ticker format: {raw!r}", file=sys.stderr)
    return (ticker, "preserved_unknown")
