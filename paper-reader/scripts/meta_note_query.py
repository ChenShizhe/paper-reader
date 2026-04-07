"""Layer B meta-note query helper for the paper-reader skill.

Scans the Citadel vault's meta-note directory for notes matching given
domain tags and section type.  Designed to be safe when the vault is
absent or empty.
"""

import os
from pathlib import Path
from typing import List

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from *text*.

    Returns an empty dict if frontmatter is absent or PyYAML is unavailable.
    """
    if yaml is None:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    raw = "\n".join(lines[1:end])
    try:
        result = yaml.safe_load(raw)
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def query_meta_notes(
    vault_root: str,
    domain_tags: List[str],
    section_type: str,
    top_k: int = 3,
) -> List[str]:
    """Return up to *top_k* meta-note contents matching *domain_tags* and *section_type*.

    Parameters
    ----------
    vault_root:
        Path to the Citadel vault root (e.g. ``~/Documents/citadel``).
        ``~`` is expanded automatically.
    domain_tags:
        List of domain tag strings to filter on.  If empty, all notes are
        eligible (no domain filtering).
    section_type:
        Value to match against the frontmatter ``aspect`` or
        ``section_type`` field.  A note without either field is always
        included (err on the side of inclusion).
    top_k:
        Maximum number of notes to return.

    Returns
    -------
    list of str
        Full file contents (including frontmatter) for matching notes.
        Returns ``[]`` when the meta directory is absent or no notes match.
    """
    meta_dir = Path(os.path.expanduser(vault_root)) / "literature" / "meta"
    if not meta_dir.exists():
        return []

    md_files = sorted(meta_dir.glob("*.md"))
    results: List[str] = []

    for path in md_files:
        if len(results) >= top_k:
            break
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        fm = _parse_frontmatter(content)

        # Domain filter (skip when domain_tags is empty)
        if domain_tags:
            note_domain = fm.get("domain")
            if note_domain not in domain_tags:
                continue

        # Section-type filter: include if field absent or matches
        note_aspect = fm.get("aspect") or fm.get("section_type")
        if note_aspect is not None and note_aspect != section_type:
            continue

        results.append(content)

    return results
