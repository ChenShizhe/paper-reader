#!/usr/bin/env python3
"""Download arXiv source tarballs for pending entries in acquisition-list.md."""

import argparse
import io
import json
import os
import re
import shutil
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_PAPER_BANK = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))


# ---------------------------------------------------------------------------
# Acquisition-list parsing / writing
# ---------------------------------------------------------------------------

def _parse_acquisition_list(acq_path: Path) -> tuple[list[str], list[dict]]:
    """Return (raw_lines, rows) where rows are dicts for table data rows."""
    raw_lines = acq_path.read_text(encoding="utf-8").splitlines(keepends=True)
    rows = []
    for i, line in enumerate(raw_lines):
        stripped = line.strip()
        if (
            stripped.startswith("|")
            and not stripped.startswith("| cite_key")
            and not stripped.startswith("|---")
        ):
            parts = [p.strip() for p in stripped.strip("|").split("|")]
            if len(parts) >= 7:
                rows.append({
                    "line_index": i,
                    "cite_key": parts[0],
                    "arxiv_id": parts[1],
                    "title": parts[2],
                    "topic": parts[3],
                    "priority": parts[4],
                    "reason": parts[5],
                    "status": parts[6],
                    # source column may not exist in older files; default to 'user'
                    "source": (parts[7] if len(parts) > 7 and parts[7] else "user"),
                })
    return raw_lines, rows


def _update_row_status(raw_lines: list[str], line_index: int, new_status: str) -> None:
    """In-place update of a table row's status field in raw_lines."""
    line = raw_lines[line_index]
    parts = line.rstrip("\n").split("|")
    # parts layout: ['', cite_key, arxiv_id, title, topic, priority, reason, status, '']
    if len(parts) >= 8:
        parts[7] = f" {new_status} "
        raw_lines[line_index] = "|".join(parts).rstrip() + "\n"


def _write_acquisition_list(acq_path: Path, raw_lines: list[str]) -> None:
    acq_path.write_text("".join(raw_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_acquisition_log(log_path: Path, cite_key: str, arxiv_id: str, source_type: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = f"{_now_iso()} | downloaded | {cite_key} | {arxiv_id} | {source_type}\n"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def _append_failure_log(failures_path: Path, cite_key: str, arxiv_id: str, reason: str) -> None:
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    row = f"| {_now_iso()} | {cite_key} | {arxiv_id} | {reason} |\n"
    # Write markdown table header if file doesn't exist yet
    if not failures_path.exists():
        with failures_path.open("w", encoding="utf-8") as fh:
            fh.write("| timestamp | cite_key | arxiv_id | reason |\n")
            fh.write("|---|---|---|---|\n")
    with failures_path.open("a", encoding="utf-8") as fh:
        fh.write(row)


def _append_manual_download_list(
    manual_path: Path, cite_key: str, arxiv_id: str, title: str, reason: str
) -> None:
    """Append an entry to manual-download-list.md. No-op if cite_key already listed."""
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    if manual_path.exists() and f"| {cite_key} |" in manual_path.read_text(encoding="utf-8"):
        return
    if not manual_path.exists():
        manual_path.write_text(
            "| cite_key | arxiv_id | title | reason | status |\n"
            "|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with manual_path.open("a", encoding="utf-8") as fh:
        fh.write(f"| {cite_key} | {arxiv_id} | {title} | {reason} | pending |\n")


def _update_manual_download_status(manual_path: Path, cite_key: str, new_status: str) -> None:
    """Update the status column for cite_key in manual-download-list.md."""
    if not manual_path.exists():
        return
    lines = manual_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        parts = line.rstrip("\n").split("|")
        if len(parts) >= 6 and parts[1].strip() == cite_key:
            parts[5] = f" {new_status} "
            lines[i] = "|".join(parts).rstrip() + "\n"
            break
    manual_path.write_text("".join(lines), encoding="utf-8")


def _update_manifest(manifest_path: Path, cite_key: str, raw_dir: Path, source_type: str) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    source_files = sorted(str(p.name) for p in raw_dir.iterdir()) if raw_dir.exists() else []

    data[cite_key] = {
        "bank_path": str(raw_dir),
        "source_files": source_files,
        "source_type": source_type,
        "updated_at": _now_iso(),
    }
    manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Download logic
# ---------------------------------------------------------------------------

def _is_already_downloaded(raw_dir: Path) -> bool:
    """Return True if raw_dir exists and is non-empty."""
    if not raw_dir.exists():
        return False
    return any(raw_dir.iterdir())


def _fetch_arxiv_source(arxiv_id: str) -> tuple[bytes, str]:
    """
    Fetch https://arxiv.org/src/<arxiv_id>.
    Returns (content_bytes, detected_type) where detected_type is 'latex' or 'pdf'.
    Raises urllib.error.HTTPError on HTTP errors (including 429).
    """
    url = f"https://arxiv.org/src/{arxiv_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "download_papers/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content_type = resp.headers.get("Content-Type", "")
        data = resp.read()
    # Detect type from content-type or by sniffing magic bytes
    if "pdf" in content_type.lower():
        return data, "pdf"
    if data[:2] == b"\x1f\x8b":
        # gzip magic — likely tar.gz
        return data, "latex"
    if data[:4] == b"%PDF":
        return data, "pdf"
    # Default to latex/tar.gz
    return data, "latex"


def _download_with_backoff(arxiv_id: str) -> tuple[bytes, str] | None:
    """
    Try to download arXiv source with exponential backoff on 429.
    Returns (data, source_type) or None if rate-limited after all retries.
    """
    import urllib.error

    delays = [30, 60, 120]
    for attempt, delay in enumerate(delays):
        try:
            return _fetch_arxiv_source(arxiv_id)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                print(f"  429 rate limit. Backing off {delay}s (attempt {attempt + 1}/3)…")
                time.sleep(delay)
                continue
            raise
    # Final attempt after last backoff
    try:
        return _fetch_arxiv_source(arxiv_id)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return None  # Signal: mark rate-limited
        raise


def _extract_tarball(data: bytes, dest_dir: Path) -> None:
    """Extract a gzip tarball from bytes into dest_dir."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        tf.extractall(path=dest_dir)


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def _process_paper(
    row: dict,
    raw_lines: list[str],
    paper_bank: Path,
    dry_run: bool,
    failures_path: Path,
    log_path: Path,
    manifest_path: Path,
    manual_path: Path,
) -> str:
    """
    Process one paper row. Returns 'downloaded', 'skipped', 'rate-limited', 'manual', or 'failed'.
    Mutates raw_lines in-place to update statuses.
    """
    import urllib.error

    cite_key = row["cite_key"]
    arxiv_id = row["arxiv_id"]
    line_index = row["line_index"]

    raw_dir = paper_bank / "paper-bank" / "raw" / cite_key

    # (a) Missing arxiv_id — not on arXiv, route to manual download list
    if not arxiv_id:
        print(f"  [{cite_key}] No arxiv_id — adding to manual download list.")
        if not dry_run:
            _update_row_status(raw_lines, line_index, "manual-pending")
            _append_manual_download_list(manual_path, cite_key, "", row["title"], "arxiv_id blank")
        return "manual"

    # Already downloaded
    if _is_already_downloaded(raw_dir):
        print(f"  [{cite_key}] Already present at {raw_dir} — skipping re-download.")
        if not dry_run:
            _update_row_status(raw_lines, line_index, "downloaded")
        return "skipped"

    if dry_run:
        print(f"  [DRY-RUN] Would download arxiv_id={arxiv_id} → {raw_dir}")
        return "downloaded"

    # Attempt download
    print(f"  [{cite_key}] Downloading arXiv source for {arxiv_id}…")
    try:
        result = _download_with_backoff(arxiv_id)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"  [{cite_key}] arXiv returned 404 — not on arXiv. Adding to manual download list.")
            if not dry_run:
                _update_row_status(raw_lines, line_index, "manual-pending")
                _append_manual_download_list(manual_path, cite_key, arxiv_id, row["title"], "arXiv 404")
            return "manual"
        reason = f"HTTP {exc.code}"
        print(f"  [{cite_key}] HTTP error {exc.code}. Marking failed.")
        _update_row_status(raw_lines, line_index, "failed-incomplete")
        _append_failure_log(failures_path, cite_key, arxiv_id, reason)
        return "failed"
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
        print(f"  [{cite_key}] Network error: {reason}. Marking failed.")
        _update_row_status(raw_lines, line_index, "failed-incomplete")
        _append_failure_log(failures_path, cite_key, arxiv_id, reason)
        return "failed"

    if result is None:
        # Exhausted backoff on 429 — pause entire batch before moving on
        print(f"  [{cite_key}] Rate-limited after all retries. Pausing batch for 90s before next paper…")
        time.sleep(90)
        print(f"  [{cite_key}] Marking rate-limited.")
        _update_row_status(raw_lines, line_index, "rate-limited")
        _append_failure_log(failures_path, cite_key, arxiv_id, "429 rate-limited after backoff")
        return "rate-limited"

    data, source_type = result

    # Save / extract
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        if source_type == "latex":
            _extract_tarball(data, raw_dir)
        else:
            pdf_path = raw_dir / f"{cite_key}.pdf"
            pdf_path.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        # Partial extract failure — clean up
        print(f"  [{cite_key}] Extract/save failed: {exc}. Removing partial, marking failed.")
        if raw_dir.exists():
            shutil.rmtree(raw_dir, ignore_errors=True)
        _update_row_status(raw_lines, line_index, "failed-incomplete")
        _append_failure_log(failures_path, cite_key, arxiv_id, f"extract failed: {exc}")
        return "failed"

    # Update acquisition-list status
    _update_row_status(raw_lines, line_index, "downloaded")

    # Append to acquisition-log.md
    _append_acquisition_log(log_path, cite_key, arxiv_id, source_type)

    # Update _manifest.json
    _update_manifest(manifest_path, cite_key, raw_dir, source_type)

    print(f"  [{cite_key}] Downloaded ({source_type}) → {raw_dir}")
    return "downloaded"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download arXiv source for pending entries in acquisition-list.md."
    )
    parser.add_argument(
        "--acquisition-list",
        default=os.path.join(_PAPER_BANK, "acquisition-list.md"),
        help="Path to acquisition-list.md (default: $PAPER_BANK/acquisition-list.md).",
    )
    parser.add_argument(
        "--paper-bank",
        default=str(Path(_PAPER_BANK).parent),
        help=(
            "Root parent directory; paper-bank/raw/<cite_key>/ is resolved relative to this. "
            "Defaults to the parent of $PAPER_BANK."
        ),
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of papers to download in one run (default: unlimited).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded without performing any downloads.",
    )
    args = parser.parse_args()

    acq_path = Path(args.acquisition_list).expanduser().resolve()
    paper_bank = Path(args.paper_bank).expanduser().resolve()

    bank_root = paper_bank / "paper-bank"
    failures_path = bank_root / "download-failures.md"
    log_path = bank_root / "acquisition-log.md"
    manifest_path = bank_root / "_manifest.json"
    manual_path = bank_root / "manual-download-list.md"

    if not acq_path.exists():
        print(f"Error: acquisition list not found at {acq_path}", file=sys.stderr)
        sys.exit(1)

    raw_lines, all_rows = _parse_acquisition_list(acq_path)

    # Auto-detect manually placed files and promote their status.
    _promoted = 0
    for _row in [r for r in all_rows if r["status"] == "manual-pending"]:
        _raw_dir = paper_bank / "paper-bank" / "raw" / _row["cite_key"]
        if _is_already_downloaded(_raw_dir):
            print(f"  [{_row['cite_key']}] File placed manually — promoting to downloaded.")
            if not args.dry_run:
                _update_row_status(raw_lines, _row["line_index"], "downloaded")
                _update_manual_download_status(manual_path, _row["cite_key"], "placed")
                _promoted += 1
    if not args.dry_run and _promoted:
        _write_acquisition_list(acq_path, raw_lines)

    # Include 'rate-limited' rows so they are retried automatically on next run.
    # Do NOT persist this status change before the attempt; status is only updated
    # to 'downloaded' or 'failed' after the attempt resolves.
    pending_rows = [r for r in all_rows if r["status"] in ("pending", "rate-limited")]

    # Sort so 'user'-sourced rows come before 'reference-queue' rows.
    # Stable sort preserves original file order within each tier.
    # Missing source column already defaults to 'user' during parsing.
    _SOURCE_ORDER = {"user": 0, "reference-queue": 1}
    pending_rows.sort(key=lambda r: _SOURCE_ORDER.get(r["source"], 0))

    if args.max_downloads is not None:
        pending_rows = pending_rows[: args.max_downloads]

    if not pending_rows:
        print("No pending papers found in acquisition list.")
        print("Downloaded 0 papers. Failed: 0. Skipped (already present): 0.")
        return

    n_downloaded = 0
    n_failed = 0
    n_skipped = 0
    n_rate_limited = 0
    n_manual = 0

    for idx, row in enumerate(pending_rows):
        outcome = _process_paper(
            row=row,
            raw_lines=raw_lines,
            paper_bank=paper_bank,
            dry_run=args.dry_run,
            failures_path=failures_path,
            log_path=log_path,
            manifest_path=manifest_path,
            manual_path=manual_path,
        )
        if outcome == "downloaded":
            n_downloaded += 1
        elif outcome == "skipped":
            n_skipped += 1
        elif outcome == "rate-limited":
            n_rate_limited += 1
        elif outcome == "manual":
            n_manual += 1
        else:
            n_failed += 1

        # Persist acquisition-list after each paper (so partial progress survives crashes)
        if not args.dry_run:
            _write_acquisition_list(acq_path, raw_lines)

        # Courtesy delay between downloads (skip after last paper)
        if not args.dry_run and idx < len(pending_rows) - 1:
            time.sleep(30)

    n_total_failed = n_failed + n_rate_limited
    print(
        f"Downloaded {n_downloaded} papers. "
        f"Failed: {n_total_failed}. "
        f"Skipped (already present): {n_skipped}. "
        f"Manual (not on arXiv): {n_manual}."
    )
    if n_manual:
        print(f"  Manual download list: {manual_path}")


if __name__ == "__main__":
    main()
