#!/usr/bin/env python3
"""Compatibility CLI for catalog rendering.

Delegates to catalog.render_catalog which renders catalog.md with YAML
frontmatter including generated_at timestamp and catalog_version.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from catalog.render_catalog import main as _render_main


def main() -> None:
    _render_main()


if __name__ == "__main__":
    main()
