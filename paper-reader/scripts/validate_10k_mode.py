#!/usr/bin/env python3
"""validate_10k_mode.py — validate 10-K mode outputs (proposal 07).

Checks performed against a completed 10-K-mode run:
  1. Summary note at <vault>/papers/<cite_key>.md exists and has frontmatter
     with all required fields; `filing_type: 10-K`, `mode: 10k`.
  2. All 14 required summary sections are present in canonical order.
  3. Per-Item notes exist at <vault>/papers/<cite_key>/item-<N>.md for every
     Item in items_present; incorporated-by-reference Items have stub notes.
  4. Claims sidecar at <vault>/claims/<cite_key>.json uses only the permitted
     10-K claim types (methodology, empirical, projection, limitation,
     data-availability, company-thesis, supply-chain-fact).
  5. Claim source_anchor.locator resolves to "<N>-<section-heading>" form.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_FRONTMATTER_KEYS = {
    "cite_key",
    "ticker",
    "filing_type",
    "fiscal_year",
    "period_end",
    "filed",
    "source_format",
    "mode",
    "items_present",
}

REQUIRED_SECTIONS = [
    "Company Snapshot",
    "Business and Segments",
    "Priority Risk Factors",
    "MD&A Synthesis",
    "Segment Performance",
    "Financial Position",
    "Cash Flow Quality",
    "Notes Highlights",
    "Controls and Governance",
    "Non-GAAP and KPI Reconciliation",
    "Evolving-Topic Coverage",
    "Textual-Analysis Flags",
    "Forward-Looking Statements",
    "Open Questions",
]

ALLOWED_CLAIM_TYPES = {
    "methodology",
    "empirical",
    "projection",
    "limitation",
    "data-availability",
    "company-thesis",
    "supply-chain-fact",
}

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
LOCATOR_RE = re.compile(r"^\d{1,2}[A-Z]?-.+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate 10-K mode outputs.")
    parser.add_argument("--cite-key", required=True)
    parser.add_argument(
        "--vault-root",
        required=True,
        help="Citadel vault root (e.g., ~/Documents/citadel).",
    )
    return parser.parse_args()


def resolve_papers_dir(vault_root: Path) -> Path:
    v2 = vault_root / "literature" / "papers"
    if v2.exists():
        return v2
    return vault_root / "papers"


def resolve_claims_dir(vault_root: Path) -> Path:
    v2 = vault_root / "literature" / "claims"
    if v2.exists():
        return v2
    return vault_root / "claims"


def parse_frontmatter(text: str) -> tuple[str, dict[str, str]]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return "", {}
    body = match.group(1)
    parsed: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line or line.startswith(" ") or line.startswith("-"):
            continue
        key, _, value = line.partition(":")
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return body, parsed


def check_sections(text: str) -> list[str]:
    errors: list[str] = []
    positions: list[int] = []
    for heading in REQUIRED_SECTIONS:
        pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
        m = pattern.search(text)
        if not m:
            errors.append(f"missing required section: ## {heading}")
        else:
            positions.append(m.start())
    # Order check among sections that are present.
    sorted_positions = sorted(positions)
    if positions != sorted_positions:
        errors.append("14 summary sections are not in canonical order")
    return errors


def check_per_item_notes(
    papers_dir: Path, cite_key: str, items_present: list[str]
) -> list[str]:
    errors: list[str] = []
    sub_dir = papers_dir / cite_key
    if not sub_dir.exists():
        errors.append(f"per-Item notes directory missing: {sub_dir}")
        return errors
    for number in items_present:
        note = sub_dir / f"item-{number}.md"
        if not note.exists():
            errors.append(f"per-Item note missing: {note.name}")
    return errors


def check_claims_sidecar(claims_path: Path, cite_key: str) -> list[str]:
    errors: list[str] = []
    if not claims_path.exists():
        errors.append(f"claims sidecar missing: {claims_path}")
        return errors
    try:
        payload = json.loads(claims_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"claims sidecar is not valid JSON: {exc}")
        return errors
    if not isinstance(payload, dict):
        errors.append("claims sidecar must be a JSON object")
        return errors
    if payload.get("cite_key") != cite_key:
        errors.append("claims sidecar cite_key mismatch")
    claims = payload.get("claims") or []
    if not isinstance(claims, list):
        errors.append("claims field must be a list")
        return errors
    for idx, claim in enumerate(claims):
        prefix = f"claims[{idx}]"
        if not isinstance(claim, dict):
            errors.append(f"{prefix}: not an object")
            continue
        ctype = claim.get("type")
        if ctype not in ALLOWED_CLAIM_TYPES:
            errors.append(
                f"{prefix}: type {ctype!r} not allowed for 10-K mode "
                f"(allowed: {sorted(ALLOWED_CLAIM_TYPES)})"
            )
        anchor = claim.get("source_anchor") or {}
        if not isinstance(anchor, dict):
            errors.append(f"{prefix}: missing source_anchor")
            continue
        locator = anchor.get("locator", "")
        if not locator or not LOCATOR_RE.match(locator):
            errors.append(
                f"{prefix}: source_anchor.locator {locator!r} does not match "
                "'<ItemNumber>-<section-heading>' form"
            )
    return errors


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root).expanduser()
    papers_dir = resolve_papers_dir(vault_root)
    claims_dir = resolve_claims_dir(vault_root)
    summary_path = papers_dir / f"{args.cite_key}.md"
    claims_path = claims_dir / f"{args.cite_key}.json"

    if not summary_path.exists():
        print(f"ERROR: 10-K summary note not found at {summary_path}")
        return 1

    text = summary_path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Frontmatter completeness.
    _, frontmatter = parse_frontmatter(text)
    missing = REQUIRED_FRONTMATTER_KEYS - frontmatter.keys()
    if missing:
        errors.append(f"frontmatter missing keys: {sorted(missing)}")
    if frontmatter.get("mode") and frontmatter["mode"] != "10k":
        errors.append(
            f"frontmatter mode must be '10k', got {frontmatter.get('mode')!r}"
        )
    if frontmatter.get("filing_type") and frontmatter["filing_type"] != "10-K":
        errors.append(
            f"frontmatter filing_type must be '10-K', got {frontmatter.get('filing_type')!r}"
        )

    # 2. Required 14 sections present + ordered.
    errors.extend(check_sections(text))

    # 3. Per-Item notes.
    items_present_raw = frontmatter.get("items_present", "[]")
    try:
        items_present = json.loads(items_present_raw)
        if not isinstance(items_present, list):
            items_present = []
    except Exception:
        items_present = []
    errors.extend(check_per_item_notes(papers_dir, args.cite_key, items_present))

    # 4-5. Claims sidecar.
    errors.extend(check_claims_sidecar(claims_path, args.cite_key))

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        return 1
    print(f"10-K mode outputs valid: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
