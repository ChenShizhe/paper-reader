"""Layer A context loader for the paper-reader skill.

Loads SKILL.md, reading-constitution.md, and proof-patterns.md from the
skill root into a dict consumed by the comprehension orchestrator.
"""

import os
from pathlib import Path


def load_layer_a(skill_root: str) -> dict:
    """Load Layer A skill context from *skill_root*.

    Parameters
    ----------
    skill_root:
        Path to the ``skills/paper-reader/`` directory.  Accepts relative
        or absolute paths; ``~`` is expanded automatically.

    Returns
    -------
    dict with keys:
        ``skill_md``       – contents of SKILL.md (str)
        ``constitution``   – contents of reading-constitution.md (str)
        ``proof_patterns`` – contents of proof-patterns.md (str), or
                             ``None`` if the file does not exist

    Raises
    ------
    FileNotFoundError
        If ``reading-constitution.md`` is absent (it is required).
    """
    root = Path(os.path.expanduser(skill_root))

    skill_md_path = root / "SKILL.md"
    constitution_path = root / "reading-constitution.md"
    proof_patterns_path = root / "proof-patterns.md"

    skill_md = skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists() else ""

    if not constitution_path.exists():
        raise FileNotFoundError(
            f"reading-constitution.md not found at {constitution_path}. "
            "Ensure Task 01 has been completed before running the orchestrator."
        )
    constitution = constitution_path.read_text(encoding="utf-8")

    proof_patterns = (
        proof_patterns_path.read_text(encoding="utf-8")
        if proof_patterns_path.exists()
        else None
    )

    return {
        "skill_md": skill_md,
        "constitution": constitution,
        "proof_patterns": proof_patterns,
    }
