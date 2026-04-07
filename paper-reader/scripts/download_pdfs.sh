#!/usr/bin/env bash
# download_pdfs.sh - Download PDFs from a schema-v1 paper manifest.
# Usage: ./download_pdfs.sh paper_manifest.json output_dir/

set -euo pipefail

MANIFEST="${1:?Usage: download_pdfs.sh <paper_manifest.json> <output_dir>}"
OUTPUT_DIR="${2:?Usage: download_pdfs.sh <paper_manifest.json> <output_dir>}"
DELAY="${DOWNLOAD_DELAY_SECONDS:-4}"

mkdir -p "$OUTPUT_DIR"

python3 - "$MANIFEST" "$OUTPUT_DIR" "$DELAY" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


manifest_path = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
delay = float(sys.argv[3])

payload = json.loads(manifest_path.read_text(encoding="utf-8"))
entries = payload.get("entries") if isinstance(payload, dict) else payload
if not isinstance(entries, list):
    raise SystemExit("Manifest must be a schema-v1 object with an entries list or a list of records")

output_dir.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
results: list[dict[str, str]] = []
success = 0

def file_stem(entry: dict[str, object], index: int) -> str:
    for key in ("cite_key", "arxiv_id", "canonical_id"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.replace(":", "_").replace("/", "_")
    return f"paper_{index}"


def resolve_pdf_url(entry: dict[str, object]) -> str:
    pdf_url = entry.get("pdf_url")
    if isinstance(pdf_url, str) and pdf_url.strip():
        return pdf_url.strip()
    arxiv_id = entry.get("arxiv_id")
    if isinstance(arxiv_id, str) and arxiv_id.strip():
        return f"https://arxiv.org/pdf/{arxiv_id.strip()}.pdf"
    return ""


for index, raw_entry in enumerate(entries):
    if not isinstance(raw_entry, dict):
        failures.append(f"entries[{index}] is not an object")
        continue

    title = str(raw_entry.get("title") or "unknown")
    pdf_url = resolve_pdf_url(raw_entry)
    filename = f"{file_stem(raw_entry, index)}.pdf"
    destination = output_dir / filename

    attempted_download = bool(pdf_url)
    if not pdf_url:
        failures.append(f"{filename}: no PDF URL")
        results.append({"file": filename, "status": "missing-url", "title": title})
        continue

    if destination.exists():
        success += 1
        results.append({"file": filename, "status": "already-present", "title": title})
        continue

    print(f"Downloading {filename} from {urlparse(pdf_url).netloc or pdf_url}")
    completed = subprocess.run(
        ["curl", "-fsSL", "-o", str(destination), pdf_url],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        if destination.exists():
            destination.unlink()
        failures.append(f"{filename}: download failed")
        results.append({"file": filename, "status": "download-failed", "title": title})
    else:
        with destination.open("rb") as handle:
            magic = handle.read(4)
        if magic != b"%PDF":
            destination.unlink(missing_ok=True)
            failures.append(f"{filename}: invalid PDF payload")
            results.append({"file": filename, "status": "invalid-pdf", "title": title})
        else:
            success += 1
            results.append({"file": filename, "status": "downloaded", "title": title})

    if attempted_download and index < len(entries) - 1:
        time.sleep(delay)

report = {
    "manifest": str(manifest_path),
    "output_dir": str(output_dir),
    "total_entries": len(entries),
    "downloaded": success,
    "failed": len(failures),
    "failures": failures,
    "results": results,
}

log_path = output_dir / "download_log.json"
log_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(f"Download log written: {log_path}")
PY
