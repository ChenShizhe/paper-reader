#!/usr/bin/env python3
"""Compatibility entrypoint for summary layer synthesis.

This wrapper keeps the legacy scripts/build_summary_layers.py path available
while delegating to the maintained implementation under summary_polish/.
The delegated implementation resolves section notes using filenames such as
intro.md, model.md, method.md, and related .md artifacts.
"""

from __future__ import annotations

from summary_polish.build_summary_layers import build_summary_layers, main

__all__ = ["build_summary_layers", "main"]


if __name__ == "__main__":
    main()
