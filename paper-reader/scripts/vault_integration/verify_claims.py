"""Claim Verification module.

Extracts section-level claims from Citadel paper notes, classifies each claim,
assigns a lightweight vault-consistency status, and upserts a
``## Verification`` table per section note.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SECTION_SOURCES: dict[str, str] = {
    "intro": "intro.md",
    "model": "model.md",
    "method": "method.md",
    "theory": "theory.md",
    "simulation": "simulation.md",
    "real_data": "real_data.md",
    "discussion": "discussion.md",
}

STATUS_CONSISTENT = "✅ Consistent"
STATUS_PARTIAL = "⚠️ Partially contradicted"
STATUS_CONTRADICTED = "❌ Contradicted"
STATUS_CANNOT_VERIFY = "❓ Cannot verify"

_BULLET_RE = re.compile(r"^\s*(?:[-*+]|(?:\d+[\.)]))\s+(.+?)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_THEOREM_LINE_RE = re.compile(
    r"^\s*(?:\*\*)?(?:theorem|lemma|proposition|corollary|assumption)\b.+$",
    re.IGNORECASE | re.MULTILINE,
)
_CONTRADICTION_HINTS = {
    "not",
    "never",
    "fails",
    "failure",
    "cannot",
    "impossible",
    "contradict",
    "counterexample",
}


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text.lower()))


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug or "unknown"


def _extract_heading_block(markdown: str, heading_phrase: str) -> str:
    """Return body text under a heading phrase (case-insensitive)."""
    headings = list(_HEADING_RE.finditer(markdown))
    phrase_lower = heading_phrase.lower()
    for idx, match in enumerate(headings):
        title = match.group(2).strip().lower()
        if title != phrase_lower:
            continue
        body_start = match.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(markdown)
        return markdown[body_start:body_end]
    return ""


def _extract_bullets(text: str) -> list[str]:
    claims: list[str] = []
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if not m:
            continue
        claim = re.sub(r"\s+", " ", m.group(1)).strip()
        claim = claim.strip("*").strip()
        if len(claim) >= 12:
            claims.append(claim)
    return claims


def _extract_theorem_lines(text: str) -> list[str]:
    claims: list[str] = []
    for m in _THEOREM_LINE_RE.finditer(text):
        line = re.sub(r"\s+", " ", m.group(0)).strip().strip("*")
        if line:
            claims.append(line)
    return claims


def _extract_keyword_sentences(text: str, keywords: set[str], max_claims: int = 8) -> list[str]:
    claims: list[str] = []
    normalized = re.sub(r"\s+", " ", text)
    for sentence in re.split(r"(?<=[.!?])\s+", normalized):
        s = sentence.strip()
        if len(s) < 20 or len(s) > 320:
            continue
        tokens = _tokenize(s)
        if tokens & keywords:
            claims.append(s)
        if len(claims) >= max_claims:
            break
    return claims


def _dedupe_keep_order(items: list[str], max_items: int = 24) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = re.sub(r"\s+", " ", item).strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= max_items:
            break
    return out


def _extract_claims(section_name: str, markdown: str) -> list[str]:
    """Best-effort extraction of explicit claims for a section note."""
    candidates: list[str] = []

    if section_name == "intro":
        block = _extract_heading_block(markdown, "Claimed Contributions")
        if block:
            candidates.extend(_extract_bullets(block))
            candidates.extend(_extract_keyword_sentences(block, {"novel", "contribution", "first", "new"}))

    if section_name == "model":
        for heading in ("Model Formulation", "Parameter Space"):
            block = _extract_heading_block(markdown, heading)
            if block:
                candidates.extend(_extract_bullets(block))
                candidates.extend(_extract_keyword_sentences(block, {"model", "parameter", "intensity"}))

    if section_name == "method":
        for heading in ("Algorithm", "Complexity", "Estimator Properties"):
            block = _extract_heading_block(markdown, heading)
            if block:
                candidates.extend(_extract_bullets(block))
                candidates.extend(_extract_keyword_sentences(block, {"algorithm", "complexity", "estimator"}))

    if section_name == "theory":
        for heading in ("Assumptions", "Main Theorem", "Convergence Rates", "Theory"):
            block = _extract_heading_block(markdown, heading)
            if block:
                candidates.extend(_extract_bullets(block))
                candidates.extend(_extract_theorem_lines(block))
                candidates.extend(
                    _extract_keyword_sentences(block, {"convergence", "rate", "assumption", "theorem", "bound"})
                )
        candidates.extend(_extract_theorem_lines(markdown))

    if section_name in {"simulation", "real_data", "discussion"}:
        for heading in ("Results", "Findings", "Discussion", "Interpretation"):
            block = _extract_heading_block(markdown, heading)
            if block:
                candidates.extend(_extract_bullets(block))
                candidates.extend(_extract_keyword_sentences(block, {"experiment", "empirical", "outperform"}))

    # Fallback for thin notes.
    if not candidates:
        candidates.extend(_extract_bullets(markdown))

    return _dedupe_keep_order(candidates)


def _classify_claim_type(section_name: str, claim_text: str) -> str:
    lc = claim_text.lower()
    if any(k in lc for k in ("assumption", "stationary", "ergodic", "boundedness")):
        return "assumption"
    if any(k in lc for k in ("convergence", "rate", "bound", "theorem", "lemma", "proposition", "corollary")):
        return "convergence"
    if any(k in lc for k in ("runtime", "complexity", "computational", "algorithm", "scalability")):
        return "computational"
    if section_name in {"simulation", "real_data", "discussion"} or any(
        k in lc for k in ("experiment", "empirical", "dataset", "outperform", "accuracy")
    ):
        return "empirical"
    return "novelty"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"ERROR: invalid JSON file: {path}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR: failed to read JSON file {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _load_staged_terms(directory: Path) -> set[str]:
    terms: set[str] = set()
    if not directory.exists():
        return terms
    for note in sorted(directory.glob("*.md")):
        stem = note.stem.replace("-", " ")
        terms |= _tokenize(stem)
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        title_match = re.search(r"^title:\s*(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
        if title_match:
            terms |= _tokenize(title_match.group(1))
    return terms


def _build_evidence_sets(work_dir: Path, vault_results: dict) -> dict[str, set[str] | bool]:
    results = vault_results.get("results", {}) if isinstance(vault_results, dict) else {}

    def _vault_terms(category: str) -> set[str]:
        terms: set[str] = set()
        for hit in results.get(category, []) or []:
            note_path = str(hit.get("note_path") or "")
            terms |= _tokenize(note_path.replace("/", " "))
            for mt in hit.get("match_terms") or []:
                terms |= _tokenize(str(mt))
        return terms

    concept_terms = _vault_terms("concepts") | _load_staged_terms(work_dir / "concepts")
    assumption_terms = _vault_terms("assumptions") | _load_staged_terms(work_dir / "assumptions")
    proof_pattern_terms = _vault_terms("proof_patterns") | _load_staged_terms(work_dir / "proof-patterns")
    paper_terms = _vault_terms("papers")
    all_terms = concept_terms | assumption_terms | proof_pattern_terms | paper_terms

    total_hits = int(vault_results.get("total_hits") or 0)
    fresh_vault = total_hits == 0

    return {
        "fresh_vault": fresh_vault,
        "concept_terms": concept_terms,
        "assumption_terms": assumption_terms,
        "proof_pattern_terms": proof_pattern_terms,
        "paper_terms": paper_terms,
        "all_terms": all_terms,
    }


def _evaluate_claim_status(
    claim: str,
    claim_type: str,
    evidence_sets: dict[str, set[str] | bool],
) -> tuple[str, str]:
    claim_tokens = _tokenize(claim)
    fresh_vault = bool(evidence_sets["fresh_vault"])
    all_terms = set(evidence_sets["all_terms"])  # type: ignore[arg-type]

    if claim_type == "assumption":
        scoped_terms = set(evidence_sets["assumption_terms"]) | set(evidence_sets["concept_terms"])  # type: ignore[arg-type]
    elif claim_type == "convergence":
        scoped_terms = set(evidence_sets["proof_pattern_terms"]) | set(evidence_sets["assumption_terms"])  # type: ignore[arg-type]
    elif claim_type == "computational":
        scoped_terms = set(evidence_sets["paper_terms"]) | set(evidence_sets["concept_terms"])  # type: ignore[arg-type]
    elif claim_type == "empirical":
        scoped_terms = set(evidence_sets["paper_terms"])  # type: ignore[arg-type]
    else:
        scoped_terms = all_terms

    broad_overlap = sorted(claim_tokens & all_terms)
    scoped_overlap = sorted(claim_tokens & scoped_terms)

    # Fresh-vault path should remain non-failing and mostly unverified.
    if fresh_vault and not broad_overlap:
        return STATUS_CANNOT_VERIFY, "Fresh vault search returned total_hits=0; insufficient prior evidence."

    if not broad_overlap:
        return STATUS_CANNOT_VERIFY, "No related vault or staged-note evidence matched this claim."

    if claim_tokens & _CONTRADICTION_HINTS and len(broad_overlap) >= 2:
        return STATUS_CONTRADICTED, f"Contradicted by overlapping evidence terms: {', '.join(broad_overlap[:4])}."

    if len(scoped_overlap) >= 2:
        return STATUS_CONSISTENT, f"Consistent with related terms: {', '.join(scoped_overlap[:4])}."

    if len(broad_overlap) >= 1:
        return STATUS_PARTIAL, f"Partially contradicted/overlapping evidence: {', '.join(broad_overlap[:4])}."

    return STATUS_CANNOT_VERIFY, "Insufficient overlap to verify confidently."


def _escape_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ").strip()


def _build_verification_section(rows: list[dict]) -> str:
    lines = [
        "## Verification",
        "",
        "| Claim | Type | Vault status | Notes |",
        "|-------|------|--------------|-------|",
    ]
    for row in rows:
        lines.append(
            "| "
            + _escape_cell(str(row["claim"]))
            + " | "
            + _escape_cell(str(row["type"]))
            + " | "
            + _escape_cell(str(row["vault_status"]))
            + " | "
            + _escape_cell(str(row["notes"]))
            + " |"
        )
    if not rows:
        lines.append(
            "| No explicit claims extracted. | novelty | "
            + STATUS_CANNOT_VERIFY
            + " | Section exists but no structured claims were detected. |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _upsert_verification_section(original_text: str, verification_section: str) -> str:
    pattern = re.compile(r"(?ms)^##\s+Verification\s*\n.*?(?=^##\s+|\Z)")
    replacement = verification_section + "\n"

    if pattern.search(original_text):
        return pattern.sub(replacement, original_text, count=1)

    suffix = "" if original_text.endswith("\n") else "\n"
    return f"{original_text}{suffix}\n{verification_section}\n"


def verify_claims(
    work_dir: str | Path,
    vault_path: str | Path,
    cite_key: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Verify per-section claims against vault search and staged note evidence."""
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)

    vault_results_path = work_dir / "_vault_search_results.json"
    if not vault_results_path.exists():
        print(
            f"ERROR: vault search results file not found: {vault_results_path}\n"
            "Run Task 01 (search_vault) first to generate this file.",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_results = _load_json(vault_results_path)
    if not cite_key:
        cite_key = str(vault_results.get("cite_key") or work_dir.name)

    paper_notes_dir = vault_path / "literature" / "papers" / cite_key
    if not paper_notes_dir.exists():
        if dry_run:
            return {
                "cite_key": cite_key,
                "sections_found": [],
                "estimated_claims": 0,
                "inputs_valid": False,
            }
        print(
            f"ERROR: paper section note directory not found: {paper_notes_dir}\n"
            "Run M5-M7 first so per-section notes exist in the vault.",
            file=sys.stderr,
        )
        sys.exit(1)

    evidence_sets = _build_evidence_sets(work_dir=work_dir, vault_results=vault_results)

    sections_found: list[str] = []
    sections_processed: list[str] = []
    notes_updated: list[str] = []
    estimated_claims = 0
    total_claims = 0
    status_counts = {
        "consistent": 0,
        "partial": 0,
        "contradicted": 0,
        "cannot_verify": 0,
    }

    for section_name, filename in SECTION_SOURCES.items():
        section_path = paper_notes_dir / filename
        if not section_path.exists():
            continue

        sections_found.append(section_name)
        try:
            text = section_path.read_text(encoding="utf-8")
        except OSError:
            continue

        claims = _extract_claims(section_name, text)
        estimated_claims += len(claims)

        if dry_run:
            continue

        rows: list[dict] = []
        for claim in claims:
            claim_type = _classify_claim_type(section_name, claim)
            status, note = _evaluate_claim_status(
                claim=claim,
                claim_type=claim_type,
                evidence_sets=evidence_sets,
            )
            rows.append(
                {
                    "claim": claim,
                    "type": claim_type,
                    "vault_status": status,
                    "notes": note,
                }
            )
            total_claims += 1
            if status == STATUS_CONSISTENT:
                status_counts["consistent"] += 1
            elif status == STATUS_PARTIAL:
                status_counts["partial"] += 1
            elif status == STATUS_CONTRADICTED:
                status_counts["contradicted"] += 1
            else:
                status_counts["cannot_verify"] += 1

        verification_md = _build_verification_section(rows)
        updated_text = _upsert_verification_section(text, verification_md)
        if updated_text != text:
            section_path.write_text(updated_text, encoding="utf-8")
            notes_updated.append(str(section_path.relative_to(vault_path)))

        sections_processed.append(section_name)

    if dry_run:
        return {
            "cite_key": cite_key,
            "sections_found": sections_found,
            "estimated_claims": estimated_claims,
            "inputs_valid": True,
        }

    report = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "sections_processed": sections_processed,
        "total_claims": total_claims,
        "status_counts": status_counts,
        "notes_updated": notes_updated,
    }
    report_path = work_dir / "_claim_verification_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify section claims against vault search results and staged notes, "
            "then upsert ## Verification tables in Citadel section notes."
        )
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--vault-path", required=True, help="Path to citadel vault root")
    parser.add_argument("--cite-key", required=False, default=None, help="Cite key for target paper.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and estimate claims without writing note/report updates.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = verify_claims(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        cite_key=args.cite_key,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
