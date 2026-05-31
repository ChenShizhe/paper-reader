#!/usr/bin/env python3
"""
oa_fetch.py — open-access PDF / full-text resolver cascade.

Given a DOI (or arXiv id), walk a prioritized list of *public* open-access
locator services and return the first real full text we can fetch. This is our
own implementation built against documented public APIs (Unpaywall, OpenAlex,
NCBI eutils, Europe PMC, arXiv, bioRxiv). No third-party scraper code is reused.

Cascade order (most general -> most specific):
    0. Unpaywall      — best OA PDF location across ALL publishers, keyed on DOI
    1. OpenAlex       — OA status + PDF locations (second general resolver)
    2. PMC eutils     — full JATS XML for PMC-indexed papers (bypasses PMC JS gate)
    3. Europe PMC     — hosted PDF via fulltextRepo (EPMC funder-mandate papers)
    4. arXiv          — direct /pdf/<id> for arXiv identifiers
    5. bioRxiv API    — metadata + abstract; flags for manual / browser fallback

Cloudflare handling: every candidate URL is fetched through `fetch_bytes`, which
first tries an ordinary browser-headed request and, on a Cloudflare-style block,
retries with curl_cffi's Chrome TLS-fingerprint impersonation. JavaScript
"interactive" challenges (Turnstile) still defeat this — those fall through to
the documented browser-agent hook (see acquire()).

CLI:
    python3 oa_fetch.py --doi 10.1101/2023.07.18.549575 --out /tmp/x --name fortunato2024
    python3 oa_fetch.py --arxiv 2305.19394 --out /tmp/x --name foo

Result is printed as one JSON line: {"route","path","format"} on success,
or {"error","note", ...} when no automated route worked.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from typing import Optional

import requests

try:
    from curl_cffi import requests as cffi_requests  # browser TLS impersonation
    _HAVE_CFFI = True
except Exception:                                    # pragma: no cover
    _HAVE_CFFI = False

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TOOL = "tda-neural-oa-fetch"
TIMEOUT = 60


# --------------------------------------------------------------------------- #
# Low-level fetch with Cloudflare fallback
# --------------------------------------------------------------------------- #
def _looks_blocked(status: int, body: bytes) -> bool:
    if status in (403, 429, 503):
        return True
    head = body[:2000].lower()
    return b"just a moment" in head or b"cf-chl" in head or b"cloudflare" in head


def fetch_bytes(url: str, accept: str = "*/*") -> Optional[bytes]:
    """Fetch raw bytes; retry through curl_cffi if a plain request is blocked."""
    headers = {"User-Agent": UA, "Accept": accept}
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200 and not _looks_blocked(r.status_code, r.content):
            return r.content
        blocked = _looks_blocked(r.status_code, r.content)
    except requests.RequestException:
        blocked = True

    if blocked and _HAVE_CFFI:
        try:
            r = cffi_requests.get(url, headers={"Accept": accept},
                                  impersonate="chrome", timeout=TIMEOUT)
            if r.status_code == 200 and not _looks_blocked(r.status_code, r.content):
                return r.content
        except Exception:
            return None
    return None


def _is_pdf(data: Optional[bytes], min_kb: int = 40) -> bool:
    return bool(data) and data[:4] == b"%PDF" and len(data) >= min_kb * 1024


def _get_json(url: str, params: dict | None = None) -> Optional[dict]:
    try:
        r = requests.get(url, params=params or {},
                         headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
# Tier 0 — Unpaywall
# --------------------------------------------------------------------------- #
def resolve_unpaywall(doi: str, email: str) -> Optional[str]:
    data = _get_json(f"https://api.unpaywall.org/v2/{doi}", {"email": email})
    if not data:
        return None
    candidates = []
    best = data.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        candidates.append(best["url_for_pdf"])
    for loc in data.get("oa_locations", []) or []:
        if loc.get("url_for_pdf"):
            candidates.append(loc["url_for_pdf"])
    # de-dupe, preserve order
    seen, out = set(), []
    for u in candidates:
        if u not in seen:
            seen.add(u); out.append(u)
    return out[0] if out else None


def resolve_unpaywall_all(doi: str, email: str) -> list[str]:
    data = _get_json(f"https://api.unpaywall.org/v2/{doi}", {"email": email})
    if not data:
        return []
    urls = []
    best = data.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        urls.append(best["url_for_pdf"])
    for loc in data.get("oa_locations", []) or []:
        if loc.get("url_for_pdf"):
            urls.append(loc["url_for_pdf"])
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out


# --------------------------------------------------------------------------- #
# Tier 1 — OpenAlex
# --------------------------------------------------------------------------- #
def resolve_openalex(doi: str) -> list[str]:
    data = _get_json(f"https://api.openalex.org/works/https://doi.org/{doi}")
    if not data:
        return []
    urls = []
    oa = data.get("open_access") or {}
    if oa.get("oa_url"):
        urls.append(oa["oa_url"])
    prim = data.get("primary_location") or {}
    if prim.get("pdf_url"):
        urls.append(prim["pdf_url"])
    for loc in data.get("locations", []) or []:
        if loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u); out.append(u)
    return out


# --------------------------------------------------------------------------- #
# Tier 2 — PMC eutils (full JATS XML)
# --------------------------------------------------------------------------- #
def resolve_pmc_xml(doi: str, email: str) -> Optional[str]:
    idc = _get_json(
        "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
        {"ids": doi, "format": "json", "email": email, "tool": TOOL},
    )
    pmcid = None
    if idc:
        recs = idc.get("records", [])
        if recs and recs[0].get("status") != "error":
            pmcid = recs[0].get("pmcid")
    if not pmcid:
        return None
    num = pmcid.replace("PMC", "")
    try:
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pmc", "id": num, "rettype": "full", "retmode": "xml",
                    "email": email, "tool": TOOL},
            headers={"User-Agent": UA}, timeout=TIMEOUT,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    xml = r.text
    if "does not allow downloading of the full text" in xml:
        return None
    if "<body" not in xml or len(xml) < 50_000:
        return None
    return xml


# --------------------------------------------------------------------------- #
# Tier 3 — Europe PMC fulltextRepo (hosted PDF)
# --------------------------------------------------------------------------- #
def resolve_epmc_pdf(doi: str) -> Optional[bytes]:
    data = _get_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {"query": f'DOI:"{doi}"', "format": "json", "resulttype": "core"},
    )
    if not data:
        return None
    for res in data.get("resultList", {}).get("result", []):
        if not res.get("hasPDF"):
            continue
        for ue in res.get("fullTextUrlList", {}).get("fullTextUrl", []):
            if ue.get("documentStyle") == "pdf" and ue.get("site") == "Europe_PMC":
                data_pdf = fetch_bytes(ue["url"], accept="application/pdf")
                if _is_pdf(data_pdf):
                    return data_pdf
    return None


# --------------------------------------------------------------------------- #
# Tier 4 — arXiv
# --------------------------------------------------------------------------- #
def resolve_arxiv_pdf(arxiv_id: str) -> Optional[bytes]:
    aid = arxiv_id.replace("arXiv:", "").strip()
    return None if not aid else fetch_bytes(
        f"https://arxiv.org/pdf/{aid}.pdf", accept="application/pdf")


# --------------------------------------------------------------------------- #
# Title -> DOI (for callers that have a title but no DOI, e.g. the acquisition
# list). Crossref bibliographic query, guarded by a word-overlap similarity
# check so we never resolve to the wrong paper.
# --------------------------------------------------------------------------- #
def _title_similarity(a: str, b: str) -> float:
    """Jaccard over word sets — cheap, dependency-free."""
    wa = {w for w in "".join(c if c.isalnum() else " " for c in a.lower()).split()}
    wb = {w for w in "".join(c if c.isalnum() else " " for c in b.lower()).split()}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def resolve_doi_from_title(title: str, email: str, min_sim: float = 0.6) -> Optional[str]:
    if not title:
        return None
    data = _get_json("https://api.crossref.org/works",
                     {"query.bibliographic": title, "rows": 3,
                      "select": "DOI,title", "mailto": email})
    if not data:
        return None
    for item in data.get("message", {}).get("items", []):
        cand = (item.get("title") or [""])[0]
        if cand and _title_similarity(title, cand) >= min_sim:
            return item.get("DOI")
    return None


# --------------------------------------------------------------------------- #
# Tier 5 — bioRxiv metadata (abstract-only flag)
# --------------------------------------------------------------------------- #
def biorxiv_abstract(doi: str) -> Optional[dict]:
    data = _get_json(f"https://api.biorxiv.org/details/biorxiv/{doi}")
    if not data:
        return None
    coll = data.get("collection", [])
    if not coll:
        return None
    last = coll[-1]
    return {"title": last.get("title", ""), "abstract": last.get("abstract", "")}


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def acquire(doi: Optional[str], out_dir: str, name: str,
            email: str, arxiv_id: Optional[str] = None,
            title: Optional[str] = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)

    # If we have no DOI but do have a title, try to resolve one first.
    if not doi and not arxiv_id and title:
        doi = resolve_doi_from_title(title, email)

    def save_pdf(data: bytes, route: str) -> dict:
        p = os.path.join(out_dir, f"{name}.pdf")
        with open(p, "wb") as f:
            f.write(data)
        return {"route": route, "path": p, "format": "pdf",
                "size_kb": round(len(data) / 1024)}

    def save_xml(text: str, route: str) -> dict:
        p = os.path.join(out_dir, f"{name}.jats.xml")
        with open(p, "w") as f:
            f.write(text)
        return {"route": route, "path": p, "format": "jats_xml",
                "size_kb": round(len(text.encode()) / 1024)}

    # arXiv shortcut
    if arxiv_id:
        data = resolve_arxiv_pdf(arxiv_id)
        if _is_pdf(data):
            return save_pdf(data, "arxiv")

    if doi:
        # Tier 0 + 1: general OA PDF resolvers
        for url in resolve_unpaywall_all(doi, email):
            data = fetch_bytes(url, accept="application/pdf")
            if _is_pdf(data):
                return save_pdf(data, "unpaywall")
        for url in resolve_openalex(doi):
            data = fetch_bytes(url, accept="application/pdf")
            if _is_pdf(data):
                return save_pdf(data, "openalex")

        # Tier 2: PMC full XML
        xml = resolve_pmc_xml(doi, email)
        if xml:
            return save_xml(xml, "pmc_eutils")

        # Tier 3: Europe PMC hosted PDF
        data = resolve_epmc_pdf(doi)
        if _is_pdf(data):
            return save_pdf(data, "epmc_pdf")

        # Tier 5: abstract-only / manual flag
        ab = biorxiv_abstract(doi)
        if ab:
            return {"error": "full_text_unavailable", "route": "abstract_only",
                    "title": ab["title"], "abstract": ab["abstract"],
                    "note": "No automated full text. Use browser-agent fallback "
                            "or manual download."}
        return {"error": "doi_not_found_or_no_oa", "doi": doi,
                "note": "No OA location from any resolver; manual/browser needed."}

    return {"error": "no_identifier_given"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Open-access full-text resolver cascade.")
    ap.add_argument("--doi")
    ap.add_argument("--arxiv", dest="arxiv_id")
    ap.add_argument("--title", help="paper title (used to resolve a DOI when none is given)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name", required=True, help="basename / cite_key for the file")
    ap.add_argument("--email", default="shizhe.chen@gmail.com",
                    help="contact email for Unpaywall / NCBI polite pools")
    a = ap.parse_args()
    if not a.doi and not a.arxiv_id and not a.title:
        ap.error("give --doi, --arxiv, or --title")
    result = acquire(a.doi, a.out, a.name, a.email, a.arxiv_id, a.title)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if "path" in result else 1


if __name__ == "__main__":
    sys.exit(main())
