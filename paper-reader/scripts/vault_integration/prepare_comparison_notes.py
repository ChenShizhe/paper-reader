"""Comparison Note Preparation module.

Detects direct competitor papers from vault paper hits and stages comparison
notes in paper-bank/comparisons. Also writes _comparison_plan.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


PROBLEM_HINTS = {
    "point-process",
    "time-to-event",
    "survival",
    "time-series",
    "graph",
    "network",
    "stochastic-process",
    "diffusion",
    "causal",
}

METHOD_HINTS = {
    "kernel",
    "kernel-estimation",
    "lasso",
    "penalized",
    "regularized",
    "bayesian",
    "mcmc",
    "variational",
    "optimization",
    "maximum-likelihood",
    "likelihood",
    "em",
    "sgd",
    "neural",
    "deep-learning",
    "empirical-calibration",
    "simulation",
}

METHOD_SECTION_RE = re.compile(
    r"^##\s+Methodology\s*\n(.*?)(?=\n##\s|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


def _slug(value: str) -> str:
    text = value.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    text = re.sub(r"-+", "-", text)
    return text or "unknown"


def _comparison_slug(cite_key_a: str, cite_key_b: str) -> str:
    left, right = sorted([_slug(cite_key_a), _slug(cite_key_b)])
    return f"{left}-vs-{right}"


def _load_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_frontmatter(md_text: str) -> dict:
    if not md_text.startswith("---"):
        return {}
    end = md_text.find("\n---", 3)
    if end == -1:
        return {}
    block = md_text[3:end]
    try:
        loaded = yaml.safe_load(block)
        return loaded if isinstance(loaded, dict) else {}
    except yaml.YAMLError:
        return {}


def _extract_methodology(md_text: str) -> str:
    match = METHOD_SECTION_RE.search(md_text)
    if not match:
        return ""
    section = re.sub(r"\s+", " ", match.group(1)).strip()
    return section


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text.lower()))


def _extract_current_catalog_tags(catalog: dict) -> tuple[set[str], set[str], str]:
    """Return current paper problem tags, method tags, and positioning text."""
    paper = catalog.get("paper", {}) if isinstance(catalog, dict) else {}

    raw_tags = set()
    for tag in paper.get("vault_tags") or []:
        if tag:
            raw_tags.add(str(tag).strip().lower())

    positioning_chunks: list[str] = []
    for key in ("title",):
        val = paper.get(key)
        if val:
            positioning_chunks.append(str(val).strip())

    for section in catalog.get("sections", []) if isinstance(catalog, dict) else []:
        heading = section.get("heading")
        summary = section.get("summary")
        if heading:
            positioning_chunks.append(str(heading))
        if summary:
            positioning_chunks.append(str(summary))
        for term in section.get("key_terms") or []:
            if term:
                raw_tags.add(str(term).strip().lower())

    positioning_text = " ".join(positioning_chunks).strip()
    problem_tags, method_tags = _split_problem_method_tags(raw_tags, positioning_text)
    return problem_tags, method_tags, positioning_text


def _split_problem_method_tags(tags: set[str], extra_text: str = "") -> tuple[set[str], set[str]]:
    """Heuristically split tags into problem and method families."""
    problem_tags: set[str] = set()
    method_tags: set[str] = set()
    normalized = {t.lower().strip() for t in tags if t}
    for tag in normalized:
        tag_tokens = _tokenize(tag)
        if not tag_tokens:
            continue

        if tag in PROBLEM_HINTS or tag_tokens & _tokenize(" ".join(PROBLEM_HINTS)):
            problem_tags.add(tag)
        if tag in METHOD_HINTS or tag_tokens & _tokenize(" ".join(METHOD_HINTS)):
            method_tags.add(tag)

        if any(token in {"process", "market", "graph", "network", "causal"} for token in tag_tokens):
            problem_tags.add(tag)
        if any(
            token in {"method", "estimation", "estimator", "algorithm", "calibration", "likelihood", "kernel"}
            for token in tag_tokens
        ):
            method_tags.add(tag)

    extra_tokens = _tokenize(extra_text)
    for hint in PROBLEM_HINTS:
        if _tokenize(hint) <= extra_tokens:
            problem_tags.add(hint)
    for hint in METHOD_HINTS:
        if _tokenize(hint) <= extra_tokens:
            method_tags.add(hint)

    return problem_tags, method_tags


def _infer_competitor_cite_key(note_path: str, frontmatter: dict) -> str:
    cite_key = frontmatter.get("cite_key")
    if cite_key:
        return str(cite_key)
    path_obj = Path(note_path)
    if path_obj.stem.lower() == "index" and path_obj.parent.name:
        return path_obj.parent.name
    if path_obj.parent.name and path_obj.parent.name != "papers":
        return path_obj.parent.name
    return path_obj.stem


def _build_competitor_profile(note_text: str, frontmatter: dict) -> tuple[set[str], set[str], str]:
    tags = {str(t).strip().lower() for t in (frontmatter.get("tags") or []) if t}
    method_text = _extract_methodology(note_text)
    problem_tags, method_tags = _split_problem_method_tags(tags, method_text)
    return problem_tags, method_tags, method_text


def _load_convergence_rates(work_dir: Path) -> dict:
    path = work_dir / "convergence_rates.yaml"
    if not path.exists():
        return {}
    try:
        data = _load_yaml(path)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _note_title(frontmatter: dict, competitor_cite_key: str) -> str:
    title = frontmatter.get("title")
    if title:
        return str(title)
    return competitor_cite_key


def _format_title(current: str, competitor: str) -> str:
    return f"Comparison: {current} vs {competitor}"


def _build_comparison_frontmatter(
    title: str,
    today_iso: str,
    shared_problem_tags: set[str],
    papers_compared: list[str],
) -> str:
    data = {
        "type": "comparison",
        "title": title,
        "date": today_iso,
        "tags": sorted(shared_problem_tags) if shared_problem_tags else ["comparison"],
        "status": "active",
        "papers_compared": papers_compared,
    }
    return "---\n" + yaml.dump(data, default_flow_style=False, allow_unicode=True) + "---\n"


def _table(rows: list[str]) -> str:
    header = "| Dimension | Current Paper | Competitor |\n| --- | --- | --- |\n"
    body = "".join([f"| {row} | TODO | TODO |\n" for row in rows])
    return header + body


def _build_comparison_body(
    current_cite_key: str,
    competitor_cite_key: str,
    current_positioning: str,
    competitor_methodology: str,
    convergence_rates: dict,
) -> str:
    current_problem_text = current_positioning or f"TODO: Summarize problem framing for {current_cite_key}."
    competitor_problem_text = (
        competitor_methodology or f"TODO: Extract problem framing from vault note for {competitor_cite_key}."
    )

    current_rates = convergence_rates.get("rates") or convergence_rates.get("convergence_rates") or []
    if isinstance(current_rates, list) and current_rates:
        current_rate_text = "; ".join([str(item) for item in current_rates[:3]])
    else:
        current_rate_text = "TODO: Add current-paper convergence rates from convergence_rates.yaml."

    return (
        "\n## Shared Problem\n\n"
        f"- Current paper ({current_cite_key}): {current_problem_text}\n"
        f"- Competitor ({competitor_cite_key}): {competitor_problem_text}\n\n"
        "## Model Comparison\n\n"
        + _table(["Model formulation", "Key parameters"]) + "\n"
        "## Method Comparison\n\n"
        + _table(["Loss function", "Algorithm", "Tuning"]) + "\n"
        "## Assumption Comparison\n\n"
        + _table(["Key assumptions"]) + "\n"
        "## Convergence Rates\n\n"
        f"- Current paper: {current_rate_text}\n"
        f"- Competitor ({competitor_cite_key}): TODO: Populate from vault theory notes.\n\n"
        "## Assessment\n\n"
        "<!-- TODO: Manual assessment -->\n"
    )


def prepare_comparison_notes(
    work_dir: str | Path,
    vault_path: str | Path,
    vault_search_results_path: str | Path,
    cite_key: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Prepare comparison notes for direct competitor papers from vault search hits."""
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    vault_search_results_path = Path(vault_search_results_path)

    if not vault_search_results_path.exists():
        print(
            f"ERROR: vault search results file not found: {vault_search_results_path}\n"
            "Run Task 01 (search_vault) first to generate this file.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        vault_search_results = json.loads(vault_search_results_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(
            f"ERROR: invalid JSON in vault search results file: {vault_search_results_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not cite_key:
        cite_key = str(vault_search_results.get("cite_key") or work_dir.name)

    catalog_path = work_dir / "_catalog.yaml"
    if catalog_path.exists():
        catalog_data = _load_yaml(catalog_path) or {}
    else:
        catalog_data = {}
        if dry_run:
            return {
                "cite_key": cite_key,
                "competitors_found_count": 0,
                "comparisons_would_stage": 0,
                "inputs_valid": False,
            }
        print(
            f"ERROR: _catalog.yaml not found in work-dir: {work_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    current_problem_tags, current_method_tags, current_positioning = _extract_current_catalog_tags(catalog_data)
    convergence_rates = _load_convergence_rates(work_dir)

    paper_hits = vault_search_results.get("results", {}).get("papers") or []
    competitors_found: list[dict[str, Any]] = []
    comparisons_staged: list[dict[str, str]] = []
    staged_slugs: set[str] = set()
    today_iso = datetime.now(tz=timezone.utc).date().isoformat()

    for hit in paper_hits:
        note_path = str(hit.get("note_path") or "").strip()
        if not note_path:
            continue

        note_file = vault_path / note_path
        if not note_file.exists():
            continue

        try:
            note_text = note_file.read_text(encoding="utf-8")
        except OSError:
            continue

        frontmatter = _parse_frontmatter(note_text)
        if str(frontmatter.get("type") or "").lower() != "paper":
            # Skip nested files that are not canonical paper notes.
            continue

        competitor_cite_key = _infer_competitor_cite_key(note_path, frontmatter)
        if _slug(competitor_cite_key) == _slug(cite_key):
            continue

        comp_problem_tags, comp_method_tags, competitor_methodology = _build_competitor_profile(
            note_text, frontmatter
        )
        shared_problem = sorted(current_problem_tags & comp_problem_tags)
        shared_method = sorted(current_method_tags & comp_method_tags)

        if not shared_problem or not shared_method:
            continue

        match_reason = (
            f"Shared problem tags: {', '.join(shared_problem)}; "
            f"shared method tags: {', '.join(shared_method)}"
        )
        competitors_found.append({
            "vault_note_path": note_path,
            "competitor_cite_key": competitor_cite_key,
            "match_reason": match_reason,
        })

        slug = _comparison_slug(cite_key, competitor_cite_key)
        if slug in staged_slugs:
            continue
        staged_slugs.add(slug)

        if dry_run:
            continue

        staged_path = work_dir / "comparisons" / f"{slug}.md"
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        competitor_title = _note_title(frontmatter, competitor_cite_key)
        frontmatter_text = _build_comparison_frontmatter(
            title=_format_title(cite_key, competitor_title),
            today_iso=today_iso,
            shared_problem_tags=set(shared_problem),
            papers_compared=sorted([cite_key, competitor_cite_key]),
        )
        body_text = _build_comparison_body(
            current_cite_key=cite_key,
            competitor_cite_key=competitor_cite_key,
            current_positioning=current_positioning,
            competitor_methodology=competitor_methodology,
            convergence_rates=convergence_rates,
        )
        staged_path.write_text(frontmatter_text + body_text, encoding="utf-8")
        comparisons_staged.append({
            "slug": slug,
            "staged_path": str(staged_path),
        })

    if dry_run:
        return {
            "cite_key": cite_key,
            "competitors_found_count": len(competitors_found),
            "comparisons_would_stage": len(staged_slugs),
            "inputs_valid": True,
        }

    plan = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "competitors_found": competitors_found,
        "comparisons_staged": comparisons_staged,
        "zero_competitors": len(competitors_found) == 0,
    }
    plan_path = work_dir / "_comparison_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare staged comparison notes from vault paper hits using "
            "problem and method overlap heuristics."
        )
    )
    parser.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    parser.add_argument("--vault-path", required=True, help="Path to citadel vault root")
    parser.add_argument(
        "--vault-search-results",
        required=True,
        help="Path to _vault_search_results.json produced by Task 01.",
    )
    parser.add_argument(
        "--cite-key",
        required=False,
        default=None,
        help="Current paper cite key. Defaults to cite_key from vault search results.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and report counts; write no files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    result = prepare_comparison_notes(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        vault_search_results_path=args.vault_search_results,
        cite_key=args.cite_key,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
