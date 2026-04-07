#!/usr/bin/env bash
# download_arxiv_sources.sh - Download arXiv source tarballs from a schema-v1 paper manifest.
# Usage: ./download_arxiv_sources.sh paper_manifest.json download_dir/ [extract_dir/]

set -euo pipefail

MANIFEST="${1:?Usage: download_arxiv_sources.sh <paper_manifest.json> <download_dir> [extract_dir]}"
DOWNLOAD_DIR="${2:?Usage: download_arxiv_sources.sh <paper_manifest.json> <download_dir> [extract_dir]}"
EXTRACT_DIR="${3:-}"
DELAY="${DOWNLOAD_DELAY_SECONDS:-4}"

mkdir -p "$DOWNLOAD_DIR"
if [ -n "$EXTRACT_DIR" ]; then
  mkdir -p "$EXTRACT_DIR"
fi

python3 - "$MANIFEST" "$DOWNLOAD_DIR" "$EXTRACT_DIR" "$DELAY" <<'PY'
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path


manifest_path = Path(sys.argv[1])
download_dir = Path(sys.argv[2])
extract_dir_arg = sys.argv[3]
extract_dir = Path(extract_dir_arg) if extract_dir_arg else None
delay = float(sys.argv[4])

payload = json.loads(manifest_path.read_text(encoding="utf-8"))
entries = payload.get("entries") if isinstance(payload, dict) else payload
if not isinstance(entries, list):
    raise SystemExit("Manifest must be a schema-v1 object with an entries list or a list of records")

download_dir.mkdir(parents=True, exist_ok=True)
if extract_dir is not None:
    extract_dir.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
results: list[dict[str, str]] = []
downloaded = 0

for index, raw_entry in enumerate(entries):
    if not isinstance(raw_entry, dict):
        failures.append(f"entries[{index}] is not an object")
        continue

    arxiv_id = str(raw_entry.get("arxiv_id") or "").strip()
    cite_key = str(raw_entry.get("cite_key") or raw_entry.get("canonical_id") or f"paper_{index}").strip()
    title = str(raw_entry.get("title") or "unknown")
    tarball_path = download_dir / f"{arxiv_id or cite_key}.tar.gz"

    if not arxiv_id:
        failures.append(f"{cite_key}: missing arxiv_id")
        results.append({"file": tarball_path.name, "status": "missing-arxiv-id", "title": title})
        continue

    source_url = f"https://arxiv.org/e-print/{arxiv_id}"
    attempted_download = True

    if tarball_path.exists():
        downloaded += 1
        results.append({"file": tarball_path.name, "status": "already-present", "title": title})
    else:
        completed = subprocess.run(
            ["curl", "-fsSL", "-o", str(tarball_path), source_url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            tarball_path.unlink(missing_ok=True)
            failures.append(f"{cite_key}: source download failed")
            results.append({"file": tarball_path.name, "status": "download-failed", "title": title})
        else:
            downloaded += 1
            results.append({"file": tarball_path.name, "status": "downloaded", "title": title})

    if extract_dir is not None and tarball_path.exists():
        destination = extract_dir / arxiv_id
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(tarball_path, "r:*") as archive:
                if sys.version_info >= (3, 12):
                    archive.extractall(destination, filter="data")
                else:
                    archive.extractall(destination)
        except tarfile.TarError:
            failures.append(f"{cite_key}: invalid source tarball")
            shutil.rmtree(destination, ignore_errors=True)
            results.append({"file": tarball_path.name, "status": "invalid-tarball", "title": title})

    if attempted_download and index < len(entries) - 1:
        time.sleep(delay)

report = {
    "manifest": str(manifest_path),
    "download_dir": str(download_dir),
    "extract_dir": str(extract_dir) if extract_dir is not None else None,
    "total_entries": len(entries),
    "downloaded": downloaded,
    "failed": len(failures),
    "failures": failures,
    "results": results,
}

log_path = download_dir / "download_log.json"
log_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(f"Download log written: {log_path}")
PY
