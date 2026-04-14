#!/usr/bin/env python3
"""Step 5.4 — Convergence Rate Extractor.

Transforms structured theory reading output (_theory_reading_output.json)
into a validated convergence_rates.yaml file in paper-bank.

Importable API
--------------
    from convergence_rate_extractor import extract_convergence_rates
    result = extract_convergence_rates("smith2024neural")

CLI
---
    python3 convergence_rate_extractor.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
THEORY_OUTPUT_JSON_NAME = "_theory_reading_output.json"
CONVERGENCE_RATES_YAML_NAME = "convergence_rates.yaml"

# Recognized bound/rate types for the output YAML
VALID_RATE_TYPES = {"upper_bound", "lower_bound", "exact", "minimax"}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _map_rate_type(raw_type: Optional[str]) -> str:
    """Map raw rate_type value to a canonical string for the YAML output."""
    if raw_type in VALID_RATE_TYPES:
        return raw_type
    return "upper_bound"


def _validate_and_normalize_rate(raw: dict) -> dict:
    """Validate a raw convergence rate entry and return a normalized dict.

    Logs warnings for missing optional fields but never discards the entry.
    Recognized raw fields (from _theory_reading_output.json):
        result_id, rate_expression, rate_type, conditions
    Also accepts already-normalized fields for round-trip safety:
        result, rate, type, regime
    """
    result_id = raw.get("result_id") or raw.get("result") or "unknown"
    rate_expression = raw.get("rate_expression") or raw.get("rate") or None
    raw_type = raw.get("rate_type") or raw.get("type")
    conditions = raw.get("conditions") or raw.get("regime") or None

    bound_type = _map_rate_type(raw_type)

    # Derived: if rate_type was "minimax" treat is_minimax as True by default
    default_is_minimax: Optional[bool] = True if bound_type == "minimax" else None
    is_minimax: Optional[bool] = raw.get("is_minimax", default_is_minimax)

    # Optional enrichment fields
    estimand: Optional[str] = raw.get("estimand") or None
    estimator: Optional[str] = raw.get("estimator") or None
    loss: Optional[str] = raw.get("loss") or None
    comparison: Optional[str] = raw.get("comparison") or None

    assumptions_used = raw.get("assumptions_used") or []
    if not isinstance(assumptions_used, list):
        assumptions_used = [str(assumptions_used)]

    # Warn for commonly expected optional fields that are absent
    for field_name, val in [("estimand", estimand), ("estimator", estimator), ("loss", loss)]:
        if val is None:
            warnings.warn(
                f"Rate entry '{result_id}': optional field '{field_name}' is missing.",
                UserWarning,
                stacklevel=3,
            )

    return {
        "result": result_id,
        "type": bound_type,
        "estimand": estimand,
        "estimator": estimator,
        "loss": loss,
        "rate": rate_expression,
        "regime": conditions,
        "is_minimax": is_minimax,
        "assumptions_used": assumptions_used,
        "comparison": comparison,
    }


def _check_optimality_verified(rates: list[dict]) -> bool:
    """Return True when a lower_bound entry matches an upper_bound on estimand+rate."""
    upper_bounds = [r for r in rates if r.get("type") in ("upper_bound", "exact", "minimax")]
    lower_bounds = [r for r in rates if r.get("type") == "lower_bound"]

    if not lower_bounds:
        return False

    for lb in lower_bounds:
        lb_estimand = lb.get("estimand")
        lb_rate = lb.get("rate")
        for ub in upper_bounds:
            if lb_estimand and lb_estimand == ub.get("estimand"):
                if lb_rate and lb_rate == ub.get("rate"):
                    return True
    return False


def _write_empty_rates_yaml(rates_yaml_path: Path, cite_key: str) -> None:
    """Write a convergence_rates.yaml with an empty rates list and an informative note."""
    output_data: dict[str, Any] = {
        "cite_key": cite_key,
        "rates": [],
        "note": "No formal theory section found",
    }
    rates_yaml_path.write_text(
        yaml.dump(output_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_convergence_rates(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Extract convergence rates from theory reading output and write convergence_rates.yaml.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    dry_run:
        When True, validate inputs and print a dry-run report as JSON without
        writing any files.

    Returns
    -------
    dict with keys:
        - ``cite_key``
        - ``theory_output_present``
        - ``rates_found``
        - ``inputs_valid``
        - ``convergence_rates_path``  (live run only)
        - ``rate_count``              (live run only)
        - ``optimality_verified``     (live run only)
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    paper_dir = bank_root / cite_key
    theory_output_path = paper_dir / THEORY_OUTPUT_JSON_NAME

    inputs_valid: bool = paper_dir.exists()
    theory_output_present: bool = theory_output_path.exists()

    # ------------------------------------------------------------------
    # Dry-run: validate inputs and report planned outcome; no file writes
    # ------------------------------------------------------------------
    if dry_run:
        rates_found = 0
        if theory_output_present:
            try:
                data = json.loads(theory_output_path.read_text(encoding="utf-8"))
                rates_found = len(data.get("convergence_rates_raw", []))
            except (json.JSONDecodeError, OSError):
                theory_output_present = False

        return {
            "cite_key": cite_key,
            "theory_output_present": theory_output_present,
            "rates_found": rates_found,
            "inputs_valid": inputs_valid,
        }

    # ------------------------------------------------------------------
    # Live run
    # ------------------------------------------------------------------
    if not inputs_valid:
        print(
            f"Error: paper-bank entry not found for cite_key '{cite_key}'. "
            f"Expected directory: {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    rates_yaml_path = paper_dir / CONVERGENCE_RATES_YAML_NAME

    # No theory output JSON: write empty YAML and return
    if not theory_output_present:
        _write_empty_rates_yaml(rates_yaml_path, cite_key)
        return {
            "cite_key": cite_key,
            "convergence_rates_path": str(rates_yaml_path),
            "rate_count": 0,
            "rates_found": 0,
            "theory_output_present": False,
            "inputs_valid": inputs_valid,
            "optimality_verified": False,
        }

    # Load theory reading output
    try:
        data = json.loads(theory_output_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading {theory_output_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    # theory_present: false → write empty rates YAML
    if not data.get("theory_present", True):
        _write_empty_rates_yaml(rates_yaml_path, cite_key)
        return {
            "cite_key": cite_key,
            "convergence_rates_path": str(rates_yaml_path),
            "rate_count": 0,
            "rates_found": 0,
            "theory_output_present": True,
            "inputs_valid": inputs_valid,
            "optimality_verified": False,
        }

    # Normalize each raw rate entry
    raw_rates: list[dict] = data.get("convergence_rates_raw", [])
    normalized_rates = [_validate_and_normalize_rate(r) for r in raw_rates]

    optimality_verified = _check_optimality_verified(normalized_rates)

    # Write convergence_rates.yaml — rates key is always present
    output_data: dict[str, Any] = {
        "cite_key": cite_key,
        "rates": normalized_rates,
    }
    if optimality_verified:
        output_data["optimality_verified"] = True

    rates_yaml_path.write_text(
        yaml.dump(output_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    return {
        "cite_key": cite_key,
        "convergence_rates_path": str(rates_yaml_path),
        "rate_count": len(normalized_rates),
        "rates_found": len(normalized_rates),
        "theory_output_present": True,
        "inputs_valid": inputs_valid,
        "optimality_verified": optimality_verified,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 5.4: Convergence Rate Extractor — transform _theory_reading_output.json "
            "into convergence_rates.yaml."
        )
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print dry-run report as JSON; no file writes.",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Root of paper bank (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    result = extract_convergence_rates(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        dry_run=args.dry_run,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
